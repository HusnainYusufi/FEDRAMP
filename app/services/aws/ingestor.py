"""
AWS Ingestor — orchestrates fetching, normalizing, and persisting
AWS configuration data for compliance analysis.

This is the core engine. It:
1. Assumes a read-only role in the target account (SecurityAudit).
2. Calls AWS APIs to enumerate resources.
3. Normalizes raw responses into canonical compliance objects.
4. Persists normalized records to PostgreSQL.
5. Returns deterministic counts for the ingestion run.

Every step is logged for audit trail (AU-2, AU-3).
No data is modified in the target AWS account (read-only).
"""

from datetime import datetime, timezone
from uuid import UUID
import csv
import io

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Asset,
    DataStore,
    Identity,
    IngestionRun,
    NetworkComponent,
    RunStatus,
)
from app.config.logging_config import get_logger
from app.services.aws.client import AWSClientFactory
from app.services.aws import normalizer

logger = get_logger(__name__)


class AWSIngestor:
    """
    Stateless ingestor — one instance per ingestion run.
    """

    def __init__(
        self,
        db: AsyncSession,
        account_id: str,
        role_arn: str,
        regions: list[str],
        run_id: UUID,
    ) -> None:
        self.db = db
        self.account_id = account_id
        self.role_arn = role_arn
        self.regions = regions
        self.run_id = run_id
        self.factory = AWSClientFactory(role_arn, account_id)

        # Counters
        self.assets_count = 0
        self.identities_count = 0
        self.networks_count = 0
        self.data_stores_count = 0

    async def run(self) -> dict:
        """Execute the full ingestion pipeline."""
        logger.info(
            "ingestion_started",
            account_id=self.account_id,
            regions=self.regions,
            run_id=str(self.run_id),
        )

        try:
            # IAM is global — ingest once regardless of regions
            await self._ingest_iam()
            await self._ingest_iam_password_policy()
            await self._ingest_iam_credential_report()

            # CloudTrail is effectively global but has a home region; ingest once
            await self._ingest_cloudtrail()

            # Regional resources
            for region in self.regions:
                await self._ingest_ec2(region)
                await self._ingest_ebs_volumes(region)
                await self._ingest_vpcs(region)
                await self._ingest_subnets(region)
                await self._ingest_security_groups(region)
                await self._ingest_route_tables(region)
                await self._ingest_network_acls(region)
                await self._ingest_internet_gateways(region)
                await self._ingest_nat_gateways(region)
                await self._ingest_vpc_endpoints(region)
                await self._ingest_vpc_flow_logs(region)
                await self._ingest_cloudwatch_log_groups(region)
                await self._ingest_s3(region)
                await self._ingest_rds(region)

            return {
                "status": "success",
                "assets_ingested": self.assets_count,
                "identities_ingested": self.identities_count,
                "networks_ingested": self.networks_count,
                "data_stores_ingested": self.data_stores_count,
            }

        except Exception as exc:
            logger.error(
                "ingestion_failed",
                account_id=self.account_id,
                error=str(exc),
                run_id=str(self.run_id),
            )
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _dump(normalized) -> dict:
        """Accept either a Pydantic model or a plain dict and return JSON-safe dict."""
        if normalized is None:
            return {}
        if isinstance(normalized, dict):
            return normalized
        # Pydantic v2
        if hasattr(normalized, "model_dump"):
            return normalized.model_dump(mode="json")
        # Fallback
        return dict(normalized)

    # ------------------------------------------------------------------
    # IAM (global)
    # ------------------------------------------------------------------
    async def _ingest_iam(self) -> None:
        iam = self.factory.get_client("iam", "us-east-1")

        # --- Users ---
        paginator = iam.get_paginator("list_users")
        for page in paginator.paginate():
            for user in page["Users"]:
                user_name = user["UserName"]
                try:
                    mfa = iam.list_mfa_devices(UserName=user_name).get("MFADevices", [])
                    keys = iam.list_access_keys(UserName=user_name).get("AccessKeyMetadata", [])
                    attached = iam.list_attached_user_policies(UserName=user_name).get("AttachedPolicies", [])
                    inline = iam.list_user_policies(UserName=user_name).get("PolicyNames", [])
                    groups = iam.list_groups_for_user(UserName=user_name).get("Groups", [])

                    normalized = normalizer.normalize_iam_user(
                        user, mfa, keys, attached, inline, groups
                    )
                    record = Identity(
                        resource_id=user["Arn"],
                        account_id=self.account_id,
                        region="global",
                        resource_type="iam_user",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.identities_count += 1
                except Exception as exc:
                    logger.warning("iam_user_error", user=user_name, error=str(exc))

        # --- Roles ---
        paginator = iam.get_paginator("list_roles")
        for page in paginator.paginate():
            for role in page["Roles"]:
                role_name = role["RoleName"]
                try:
                    attached = iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", [])
                    inline = iam.list_role_policies(RoleName=role_name).get("PolicyNames", [])

                    normalized = normalizer.normalize_iam_role(role, attached, inline)
                    record = Identity(
                        resource_id=role["Arn"],
                        account_id=self.account_id,
                        region="global",
                        resource_type="iam_role",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.identities_count += 1
                except Exception as exc:
                    logger.warning("iam_role_error", role=role_name, error=str(exc))

        # --- Policies (customer-managed only — skip AWS-managed) ---
        paginator = iam.get_paginator("list_policies")
        for page in paginator.paginate(Scope="Local"):
            for policy in page["Policies"]:
                try:
                    normalized = normalizer.normalize_iam_policy(policy)
                    record = Identity(
                        resource_id=policy["Arn"],
                        account_id=self.account_id,
                        region="global",
                        resource_type="iam_policy",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.identities_count += 1
                except Exception as exc:
                    logger.warning("iam_policy_error", policy=policy.get("PolicyName"), error=str(exc))

        await self.db.flush()
        logger.info("iam_ingestion_complete", identities=self.identities_count)

    async def _ingest_iam_password_policy(self) -> None:
        iam = self.factory.get_client("iam", "us-east-1")
        policy = None
        try:
            policy = iam.get_account_password_policy().get("PasswordPolicy")
        except Exception:
            policy = None

        normalized = normalizer.normalize_iam_password_policy(policy)
        record = Identity(
            resource_id=f"iam_password_policy::{self.account_id}",
            account_id=self.account_id,
            region="global",
            resource_type="iam_password_policy",
            data=self._dump(normalized),
            ingestion_run_id=self.run_id,
        )
        self.db.add(record)
        self.identities_count += 1
        await self.db.flush()

    async def _ingest_iam_credential_report(self) -> None:
        iam = self.factory.get_client("iam", "us-east-1")
        try:
            iam.generate_credential_report()
            resp = iam.get_credential_report()
            content = resp.get("Content")
            if not content:
                return

            text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else str(content)
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            normalized = normalizer.normalize_iam_credential_report_summary(rows)

            record = Identity(
                resource_id=f"iam_credential_report::{self.account_id}",
                account_id=self.account_id,
                region="global",
                resource_type="iam_credential_report",
                data=self._dump(normalized),
                ingestion_run_id=self.run_id,
            )
            self.db.add(record)
            self.identities_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("iam_credential_report_error", error=str(exc))

    # ------------------------------------------------------------------
    # Audit / Logging (AU-2/AU-6)
    # ------------------------------------------------------------------
    async def _ingest_cloudtrail(self) -> None:
        ct = self.factory.get_client("cloudtrail", "us-east-1")
        try:
            trails = ct.describe_trails(includeShadowTrails=False).get("trailList", [])
            for t in trails:
                arn = t.get("TrailARN") or t.get("TrailArn") or t.get("Name")
                status = None
                try:
                    if t.get("Name"):
                        status = ct.get_trail_status(Name=t["Name"])
                except Exception:
                    status = None

                normalized = normalizer.normalize_cloudtrail_trail(t, status=status)
                record = Asset(
                    resource_id=arn or f"cloudtrail::{self.account_id}::{t.get('Name')}",
                    account_id=self.account_id,
                    region=normalized.get("home_region") or "us-east-1",
                    resource_type="cloudtrail_trail",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.assets_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("cloudtrail_error", error=str(exc))

    async def _ingest_cloudwatch_log_groups(self, region: str) -> None:
        logs = self.factory.get_client("logs", region)
        try:
            paginator = logs.get_paginator("describe_log_groups")
            for page in paginator.paginate():
                for lg in page.get("logGroups", []):
                    normalized = normalizer.normalize_cloudwatch_log_group(lg)
                    record = Asset(
                        resource_id=lg.get("arn") or f"log_group::{region}::{lg.get('logGroupName')}",
                        account_id=self.account_id,
                        region=region,
                        resource_type="cloudwatch_log_group",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.assets_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("cloudwatch_log_groups_error", region=region, error=str(exc))

    # ------------------------------------------------------------------
    # EC2 Instances
    # ------------------------------------------------------------------
    async def _ingest_ec2(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        paginator = ec2.get_paginator("describe_instances")

        for page in paginator.paginate():
            for reservation in page["Reservations"]:
                for instance in reservation["Instances"]:
                    try:
                        normalized = normalizer.normalize_ec2_instance(instance)
                        record = Asset(
                            resource_id=instance["InstanceId"],
                            account_id=self.account_id,
                            region=region,
                            resource_type="ec2_instance",
                            data=self._dump(normalized),
                            ingestion_run_id=self.run_id,
                        )
                        self.db.add(record)
                        self.assets_count += 1
                    except Exception as exc:
                        logger.warning("ec2_error", instance=instance.get("InstanceId"), error=str(exc))

        await self.db.flush()

    async def _ingest_ebs_volumes(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        paginator = ec2.get_paginator("describe_volumes")
        for page in paginator.paginate():
            for vol in page.get("Volumes", []):
                try:
                    normalized = normalizer.normalize_ebs_volume(vol)
                    record = Asset(
                        resource_id=vol.get("VolumeId"),
                        account_id=self.account_id,
                        region=region,
                        resource_type="ebs_volume",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.assets_count += 1
                except Exception as exc:
                    logger.warning("ebs_volume_error", volume=vol.get("VolumeId"), error=str(exc))
        await self.db.flush()

    # ------------------------------------------------------------------
    # VPCs
    # ------------------------------------------------------------------
    async def _ingest_vpcs(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        response = ec2.describe_vpcs()

        for vpc in response.get("Vpcs", []):
            try:
                normalized = normalizer.normalize_vpc(vpc)
                record = NetworkComponent(
                    resource_id=vpc["VpcId"],
                    account_id=self.account_id,
                    region=region,
                    resource_type="vpc",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.networks_count += 1
            except Exception as exc:
                logger.warning("vpc_error", vpc=vpc.get("VpcId"), error=str(exc))

        await self.db.flush()

    # ------------------------------------------------------------------
    # Subnets
    # ------------------------------------------------------------------
    async def _ingest_subnets(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        response = ec2.describe_subnets()

        for subnet in response.get("Subnets", []):
            try:
                normalized = normalizer.normalize_subnet(subnet)
                record = NetworkComponent(
                    resource_id=subnet["SubnetId"],
                    account_id=self.account_id,
                    region=region,
                    resource_type="subnet",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.networks_count += 1
            except Exception as exc:
                logger.warning("subnet_error", subnet=subnet.get("SubnetId"), error=str(exc))

        await self.db.flush()

    # ------------------------------------------------------------------
    # Security Groups
    # ------------------------------------------------------------------
    async def _ingest_security_groups(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        response = ec2.describe_security_groups()

        for sg in response.get("SecurityGroups", []):
            try:
                normalized = normalizer.normalize_security_group(sg)
                record = NetworkComponent(
                    resource_id=sg["GroupId"],
                    account_id=self.account_id,
                    region=region,
                    resource_type="security_group",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.networks_count += 1
            except Exception as exc:
                logger.warning("sg_error", sg=sg.get("GroupId"), error=str(exc))

        await self.db.flush()

    async def _ingest_route_tables(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        try:
            resp = ec2.describe_route_tables()
            for rt in resp.get("RouteTables", []):
                normalized = normalizer.normalize_route_table(rt)
                record = NetworkComponent(
                    resource_id=rt.get("RouteTableId"),
                    account_id=self.account_id,
                    region=region,
                    resource_type="route_table",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.networks_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("route_table_error", region=region, error=str(exc))

    async def _ingest_network_acls(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        try:
            resp = ec2.describe_network_acls()
            for nacl in resp.get("NetworkAcls", []):
                normalized = normalizer.normalize_network_acl(nacl)
                record = NetworkComponent(
                    resource_id=nacl.get("NetworkAclId"),
                    account_id=self.account_id,
                    region=region,
                    resource_type="network_acl",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.networks_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("network_acl_error", region=region, error=str(exc))

    async def _ingest_internet_gateways(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        try:
            resp = ec2.describe_internet_gateways()
            for igw in resp.get("InternetGateways", []):
                normalized = normalizer.normalize_internet_gateway(igw)
                record = NetworkComponent(
                    resource_id=igw.get("InternetGatewayId"),
                    account_id=self.account_id,
                    region=region,
                    resource_type="internet_gateway",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.networks_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("internet_gateway_error", region=region, error=str(exc))

    async def _ingest_nat_gateways(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        try:
            paginator = ec2.get_paginator("describe_nat_gateways")
            for page in paginator.paginate():
                for ngw in page.get("NatGateways", []):
                    normalized = normalizer.normalize_nat_gateway(ngw)
                    record = NetworkComponent(
                        resource_id=ngw.get("NatGatewayId"),
                        account_id=self.account_id,
                        region=region,
                        resource_type="nat_gateway",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.networks_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("nat_gateway_error", region=region, error=str(exc))

    async def _ingest_vpc_endpoints(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        try:
            paginator = ec2.get_paginator("describe_vpc_endpoints")
            for page in paginator.paginate():
                for ep in page.get("VpcEndpoints", []):
                    normalized = normalizer.normalize_vpc_endpoint(ep)
                    record = NetworkComponent(
                        resource_id=ep.get("VpcEndpointId"),
                        account_id=self.account_id,
                        region=region,
                        resource_type="vpc_endpoint",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.networks_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("vpc_endpoints_error", region=region, error=str(exc))

    async def _ingest_vpc_flow_logs(self, region: str) -> None:
        ec2 = self.factory.get_client("ec2", region)
        try:
            paginator = ec2.get_paginator("describe_flow_logs")
            for page in paginator.paginate():
                for fl in page.get("FlowLogs", []):
                    normalized = normalizer.normalize_vpc_flow_log(fl)
                    record = NetworkComponent(
                        resource_id=fl.get("FlowLogId"),
                        account_id=self.account_id,
                        region=region,
                        resource_type="vpc_flow_log",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.networks_count += 1
            await self.db.flush()
        except Exception as exc:
            logger.warning("vpc_flow_logs_error", region=region, error=str(exc))

    # ------------------------------------------------------------------
    # S3 Buckets (metadata only — no object listing)
    # ------------------------------------------------------------------
    async def _ingest_s3(self, region: str) -> None:
        if region != self.regions[0]:
            return

        s3 = self.factory.get_client("s3", region)
        buckets = s3.list_buckets().get("Buckets", [])

        for bucket in buckets:
            bucket_name = bucket["Name"]
            try:
                loc = s3.get_bucket_location(Bucket=bucket_name)
                bucket_region = loc.get("LocationConstraint") or "us-east-1"

                if bucket_region not in self.regions:
                    continue

                versioning = self._safe_call(s3.get_bucket_versioning, Bucket=bucket_name)
                encryption = self._safe_call(s3.get_bucket_encryption, Bucket=bucket_name)
                if encryption:
                    encryption = encryption.get("ServerSideEncryptionConfiguration", encryption)

                public_access = self._safe_call(s3.get_public_access_block, Bucket=bucket_name)
                if public_access:
                    public_access = public_access.get("PublicAccessBlockConfiguration", public_access)

                logging_config = self._safe_call(s3.get_bucket_logging, Bucket=bucket_name)

                tag_response = self._safe_call(s3.get_bucket_tagging, Bucket=bucket_name)
                tags = tag_response.get("TagSet", []) if tag_response else []

                normalized = normalizer.normalize_s3_bucket(
                    bucket,
                    location=bucket_region,
                    versioning=versioning,
                    encryption=encryption,
                    public_access=public_access,
                    logging_config=logging_config,
                    tags=tags,
                )
                record = DataStore(
                    resource_id=f"arn:aws:s3:::{bucket_name}",
                    account_id=self.account_id,
                    region=bucket_region,
                    resource_type="s3_bucket",
                    data=self._dump(normalized),
                    ingestion_run_id=self.run_id,
                )
                self.db.add(record)
                self.data_stores_count += 1
            except Exception as exc:
                logger.warning("s3_error", bucket=bucket_name, error=str(exc))

        await self.db.flush()

    # ------------------------------------------------------------------
    # RDS Instances
    # ------------------------------------------------------------------
    async def _ingest_rds(self, region: str) -> None:
        rds = self.factory.get_client("rds", region)
        paginator = rds.get_paginator("describe_db_instances")

        for page in paginator.paginate():
            for instance in page["DBInstances"]:
                try:
                    normalized = normalizer.normalize_rds_instance(instance)
                    arn = instance.get(
                        "DBInstanceArn",
                        f"arn:aws:rds:{region}:{self.account_id}:db:{instance['DBInstanceIdentifier']}",
                    )
                    record = DataStore(
                        resource_id=arn,
                        account_id=self.account_id,
                        region=region,
                        resource_type="rds_instance",
                        data=self._dump(normalized),
                        ingestion_run_id=self.run_id,
                    )
                    self.db.add(record)
                    self.data_stores_count += 1
                except Exception as exc:
                    logger.warning("rds_error", db=instance.get("DBInstanceIdentifier"), error=str(exc))

        await self.db.flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_call(func, **kwargs) -> dict | None:
        """Call an AWS API and return None on client errors (e.g. 404)."""
        try:
            return func(**kwargs)
        except Exception:
            return None

