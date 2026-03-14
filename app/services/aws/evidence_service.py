"""
AWS Evidence Service — evidence retrieval for AI agent and APIs.

This is the backend interface for fetching *ingested* AWS evidence from Postgres.
It does NOT call AWS APIs directly.

Design goals:
  - Deterministic: same DB state → same evidence output
  - Safe: returns normalized data only (never secrets)
  - Generic: supports arbitrary controls chosen by a frontend
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Asset, DataStore, Identity, IngestionRun, NetworkComponent, RunStatus
from app.config.logging_config import get_logger

logger = get_logger(__name__)

TableName = Literal["identities", "assets", "network_components", "data_stores"]


class AWSEvidenceService:
    """Read-only service for querying ingested AWS evidence."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve_run_id(
        self, account_id: str, ingestion_run_id: str | None
    ) -> UUID | None:
        """
        Resolve which ingestion run to use. If `ingestion_run_id` is None,
        pick the latest successful run for the account.
        """
        # Some tool planners may send sentinel strings like "LATEST".
        # Treat these as "latest successful run".
        if ingestion_run_id is not None:
            candidate = str(ingestion_run_id).strip()
            if candidate and candidate.upper() != "LATEST":
                try:
                    return UUID(candidate)
                except Exception as exc:
                    raise ValueError(
                        f"ingestion_run_id must be a UUID (or omitted / 'LATEST'), got: {ingestion_run_id!r}"
                    ) from exc

        stmt = (
            select(IngestionRun.id)
            .where(
                IngestionRun.account_id == account_id,
                IngestionRun.status == RunStatus.SUCCESS,
            )
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Generic accessors (used by tools)
    # ------------------------------------------------------------------
    async def counts_by_resource_type(
        self,
        table: TableName,
        account_id: str,
        ingestion_run_id: UUID | None,
    ) -> list[dict[str, Any]]:
        model = self._model_for_table(table)
        stmt = select(model.resource_type, func.count(model.id)).where(
            model.account_id == account_id
        )
        if ingestion_run_id:
            stmt = stmt.where(model.ingestion_run_id == ingestion_run_id)
        stmt = stmt.group_by(model.resource_type).order_by(model.resource_type)
        rows = (await self.db.execute(stmt)).all()
        return [{"resource_type": rt, "count": cnt} for rt, cnt in rows]

    async def list_records(
        self,
        table: TableName,
        account_id: str,
        ingestion_run_id: UUID | None,
        resource_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        List a capped set of records for the given table.
        Deterministic order: created_at asc.
        """
        model = self._model_for_table(table)
        stmt = select(model).where(model.account_id == account_id)
        if ingestion_run_id:
            stmt = stmt.where(model.ingestion_run_id == ingestion_run_id)
        if resource_type:
            stmt = stmt.where(model.resource_type == resource_type)

        stmt = stmt.order_by(model.created_at).limit(limit)
        records = (await self.db.execute(stmt)).scalars().all()

        return [
            {
                "id": str(r.id),
                "resource_id": r.resource_id,
                "account_id": r.account_id,
                "region": r.region,
                "resource_type": r.resource_type,
                "data": r.data or {},
            }
            for r in records
        ]

    async def summarize_iam_users(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
        sample_limit: int = 10,
    ) -> dict[str, Any]:
        """
        Deterministic summary for IAM users that is frequently useful across
        AC / IA families.
        """
        base = select(Identity).where(
            Identity.account_id == account_id,
            Identity.resource_type == "iam_user",
        )
        if ingestion_run_id:
            base = base.where(Identity.ingestion_run_id == ingestion_run_id)

        records = (await self.db.execute(base.order_by(Identity.created_at))).scalars().all()

        total = len(records)
        mfa_enabled = 0
        active_access_keys = 0
        sample_users: list[dict[str, Any]] = []

        for r in records:
            d = r.data or {}
            if d.get("mfa_enabled"):
                mfa_enabled += 1
            for key in d.get("access_keys", []) or []:
                if key.get("status") == "Active":
                    active_access_keys += 1
            if len(sample_users) < sample_limit:
                sample_users.append(
                    {
                        "user_name": d.get("user_name", "unknown"),
                        "arn": d.get("arn", ""),
                        "mfa_enabled": d.get("mfa_enabled", False),
                        "password_last_used": str(d.get("password_last_used", "N/A")),
                        "groups": d.get("groups", []),
                        "access_keys_count": len(d.get("access_keys", []) or []),
                    }
                )

        return {
            "total_users": total,
            "mfa_enabled_count": mfa_enabled,
            "mfa_disabled_count": total - mfa_enabled,
            "active_access_keys_count": active_access_keys,
            "sample_users": sample_users,
        }

    async def summarize_iam_policy_attachments(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
        sample_limit: int = 10,
        top_n: int = 10,
    ) -> dict[str, Any]:
        """
        Summarize policy attachments across IAM users and roles.

        Notes:
          - Includes AWS-managed and customer-managed policy ARNs because they are stored
            on the ingested iam_user / iam_role records as attached_policies.
          - Customer-managed policy *objects* may or may not exist as `iam_policy` records
            (we ingest Scope=Local only), so this tool focuses on attachments.
        """
        base = select(Identity).where(
            Identity.account_id == account_id,
            Identity.resource_type.in_(["iam_user", "iam_role"]),
        )
        if ingestion_run_id:
            base = base.where(Identity.ingestion_run_id == ingestion_run_id)

        records = (await self.db.execute(base.order_by(Identity.created_at))).scalars().all()

        def _as_list(v: Any) -> list:
            if not v:
                return []
            if isinstance(v, list):
                return v
            return [v]

        users_total = 0
        roles_total = 0
        users_with_attached = 0
        roles_with_attached = 0
        users_with_inline = 0
        roles_with_inline = 0

        policy_freq: dict[str, int] = {}
        inline_name_freq: dict[str, int] = {}

        sample_users: list[dict[str, Any]] = []
        sample_roles: list[dict[str, Any]] = []

        for r in records:
            d = r.data or {}
            attached = _as_list(d.get("attached_policies"))
            inline_names = _as_list(d.get("inline_policy_names"))

            for arn in attached:
                if isinstance(arn, str) and arn:
                    policy_freq[arn] = policy_freq.get(arn, 0) + 1

            for nm in inline_names:
                if isinstance(nm, str) and nm:
                    inline_name_freq[nm] = inline_name_freq.get(nm, 0) + 1

            if r.resource_type == "iam_user":
                users_total += 1
                if attached:
                    users_with_attached += 1
                if inline_names:
                    users_with_inline += 1
                if len(sample_users) < sample_limit:
                    sample_users.append(
                        {
                            "user_name": d.get("user_name", "unknown"),
                            "arn": d.get("arn", r.resource_id),
                            "groups": d.get("groups", []),
                            "attached_policies": attached[:25],
                            "inline_policy_names": inline_names[:25],
                        }
                    )
            else:
                roles_total += 1
                if attached:
                    roles_with_attached += 1
                if inline_names:
                    roles_with_inline += 1
                if len(sample_roles) < sample_limit:
                    sample_roles.append(
                        {
                            "role_name": d.get("role_name", "unknown"),
                            "arn": d.get("arn", r.resource_id),
                            "attached_policies": attached[:25],
                            "inline_policy_names": inline_names[:25],
                            "max_session_duration": d.get("max_session_duration"),
                        }
                    )

        top_policies = sorted(policy_freq.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        top_inline = sorted(inline_name_freq.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

        return {
            "users_total": users_total,
            "roles_total": roles_total,
            "users_with_attached_policies_count": users_with_attached,
            "roles_with_attached_policies_count": roles_with_attached,
            "users_with_inline_policies_count": users_with_inline,
            "roles_with_inline_policies_count": roles_with_inline,
            "top_attached_policies": [{"policy_arn": arn, "count": cnt} for arn, cnt in top_policies],
            "top_inline_policy_names": [{"name": nm, "count": cnt} for nm, cnt in top_inline],
            "sample_users": sample_users,
            "sample_roles": sample_roles,
        }

    async def summarize_ec2_instances(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
        sample_limit: int = 10,
    ) -> dict[str, Any]:
        """Deterministic summary for EC2 instances (inventory + key posture)."""
        model = Asset
        stmt = select(model).where(
            model.account_id == account_id,
            model.resource_type == "ec2_instance",
        )
        if ingestion_run_id:
            stmt = stmt.where(model.ingestion_run_id == ingestion_run_id)
        records = (await self.db.execute(stmt.order_by(model.created_at))).scalars().all()

        total = len(records)
        states: dict[str, int] = {}
        monitoring_enabled = 0
        sample: list[dict[str, Any]] = []

        for r in records:
            d = r.data or {}
            state = d.get("state", "unknown")
            states[state] = states.get(state, 0) + 1
            if d.get("monitoring_enabled"):
                monitoring_enabled += 1
            if len(sample) < sample_limit:
                sample.append(
                    {
                        "instance_id": d.get("instance_id") or r.resource_id,
                        "instance_type": d.get("instance_type"),
                        "state": state,
                        "region": r.region,
                        "vpc_id": d.get("vpc_id"),
                        "subnet_id": d.get("subnet_id"),
                        "iam_instance_profile": d.get("iam_instance_profile"),
                        "security_group_ids": d.get("security_group_ids", []),
                        "monitoring_enabled": d.get("monitoring_enabled", False),
                        "tags": d.get("tags", {}),
                    }
                )

        return {
            "total_instances": total,
            "states": states,
            "monitoring_enabled_count": monitoring_enabled,
            "sample_instances": sample,
        }

    async def summarize_security_groups_exposure(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
        sample_limit: int = 10,
    ) -> dict[str, Any]:
        """Summarize world-open ingress patterns in security groups."""
        model = NetworkComponent
        stmt = select(model).where(
            model.account_id == account_id,
            model.resource_type == "security_group",
        )
        if ingestion_run_id:
            stmt = stmt.where(model.ingestion_run_id == ingestion_run_id)
        records = (await self.db.execute(stmt.order_by(model.created_at))).scalars().all()

        def is_world_open(rule: dict) -> bool:
            cidrs = rule.get("cidr_blocks", []) or []
            ipv6 = rule.get("ipv6_cidr_blocks", []) or []
            return ("0.0.0.0/0" in cidrs) or ("::/0" in ipv6)

        total = len(records)
        sgs_with_world_open = 0
        world_open_rules = 0
        sample: list[dict[str, Any]] = []

        for r in records:
            d = r.data or {}
            ingress = d.get("ingress_rules", []) or []
            open_rules = [rule for rule in ingress if is_world_open(rule)]
            if open_rules:
                sgs_with_world_open += 1
                world_open_rules += len(open_rules)
                if len(sample) < sample_limit:
                    sample.append(
                        {
                            "group_id": d.get("group_id") or r.resource_id,
                            "group_name": d.get("group_name"),
                            "vpc_id": d.get("vpc_id"),
                            "open_ingress_rules": [
                                {
                                    "protocol": rr.get("protocol"),
                                    "from_port": rr.get("from_port"),
                                    "to_port": rr.get("to_port"),
                                    "cidr_blocks": rr.get("cidr_blocks", []),
                                    "ipv6_cidr_blocks": rr.get("ipv6_cidr_blocks", []),
                                    "description": rr.get("description"),
                                }
                                for rr in open_rules[:5]
                            ],
                        }
                    )

        return {
            "total_security_groups": total,
            "security_groups_with_world_open_ingress_count": sgs_with_world_open,
            "world_open_ingress_rule_count": world_open_rules,
            "sample_world_open_security_groups": sample,
        }

    async def summarize_s3_buckets(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
        sample_limit: int = 10,
    ) -> dict[str, Any]:
        """Summarize S3 encryption + public access posture."""
        model = DataStore
        stmt = select(model).where(
            model.account_id == account_id,
            model.resource_type == "s3_bucket",
        )
        if ingestion_run_id:
            stmt = stmt.where(model.ingestion_run_id == ingestion_run_id)
        records = (await self.db.execute(stmt.order_by(model.created_at))).scalars().all()

        total = len(records)
        encrypted = 0
        unencrypted = 0
        public_access_block_missing = 0
        sample_unencrypted: list[dict[str, Any]] = []

        for r in records:
            d = r.data or {}
            algo = d.get("encryption_algorithm")
            if algo:
                encrypted += 1
            else:
                unencrypted += 1
                if len(sample_unencrypted) < sample_limit:
                    sample_unencrypted.append(
                        {
                            "bucket_name": d.get("bucket_name"),
                            "region": r.region,
                            "public_access_block": d.get("public_access_block"),
                            "logging_enabled": d.get("logging_enabled", False),
                            "tags": d.get("tags", {}),
                        }
                    )
            if d.get("public_access_block") is None:
                public_access_block_missing += 1

        return {
            "total_buckets": total,
            "encrypted_buckets_count": encrypted,
            "unencrypted_buckets_count": unencrypted,
            "public_access_block_missing_count": public_access_block_missing,
            "sample_unencrypted_buckets": sample_unencrypted,
        }

    async def summarize_rds_instances(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
        sample_limit: int = 10,
    ) -> dict[str, Any]:
        """Summarize RDS encryption + exposure posture."""
        model = DataStore
        stmt = select(model).where(
            model.account_id == account_id,
            model.resource_type == "rds_instance",
        )
        if ingestion_run_id:
            stmt = stmt.where(model.ingestion_run_id == ingestion_run_id)
        records = (await self.db.execute(stmt.order_by(model.created_at))).scalars().all()

        total = len(records)
        encrypted = 0
        unencrypted = 0
        publicly_accessible = 0
        sample_unencrypted: list[dict[str, Any]] = []

        for r in records:
            d = r.data or {}
            if d.get("storage_encrypted"):
                encrypted += 1
            else:
                unencrypted += 1
                if len(sample_unencrypted) < sample_limit:
                    sample_unencrypted.append(
                        {
                            "db_instance_identifier": d.get("db_instance_identifier"),
                            "engine": d.get("engine"),
                            "engine_version": d.get("engine_version"),
                            "region": r.region,
                            "publicly_accessible": d.get("publicly_accessible", False),
                            "kms_key_id": d.get("kms_key_id"),
                        }
                    )
            if d.get("publicly_accessible"):
                publicly_accessible += 1

        return {
            "total_rds_instances": total,
            "encrypted_rds_instances_count": encrypted,
            "unencrypted_rds_instances_count": unencrypted,
            "publicly_accessible_rds_instances_count": publicly_accessible,
            "sample_unencrypted_rds_instances": sample_unencrypted,
        }

    async def default_evidence_snapshot(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
        sample_limit: int = 10,
    ) -> dict[str, Any]:
        """Produce a broad, deterministic evidence snapshot suitable as a safe default."""
        return {
            "counts": {
                "identities": await self.counts_by_resource_type("identities", account_id, ingestion_run_id),
                "assets": await self.counts_by_resource_type("assets", account_id, ingestion_run_id),
                "network_components": await self.counts_by_resource_type("network_components", account_id, ingestion_run_id),
                "data_stores": await self.counts_by_resource_type("data_stores", account_id, ingestion_run_id),
            },
            "summaries": {
                "iam_users": await self.summarize_iam_users(account_id, ingestion_run_id, sample_limit=sample_limit),
                "iam_policy_attachments": await self.summarize_iam_policy_attachments(account_id, ingestion_run_id, sample_limit=sample_limit),
                "iam_authentication_posture": await self.summarize_iam_authentication_posture(account_id, ingestion_run_id),
                "ec2_instances": await self.summarize_ec2_instances(account_id, ingestion_run_id, sample_limit=sample_limit),
                "security_groups": await self.summarize_security_groups_exposure(account_id, ingestion_run_id, sample_limit=sample_limit),
                "cloudtrail": await self.summarize_cloudtrail_posture(account_id, ingestion_run_id),
                "cloudwatch_logs": await self.summarize_cloudwatch_log_groups(account_id, ingestion_run_id),
                "vpc_flow_logs": await self.summarize_vpc_flow_logs(account_id, ingestion_run_id),
                "network_boundary": await self.summarize_network_boundary(account_id, ingestion_run_id),
                "cm8_inventory": await self.summarize_cm8_inventory(account_id, ingestion_run_id),
                "s3_buckets": await self.summarize_s3_buckets(account_id, ingestion_run_id, sample_limit=sample_limit),
                "rds_instances": await self.summarize_rds_instances(account_id, ingestion_run_id, sample_limit=sample_limit),
            },
        }

    async def summarize_iam_authentication_posture(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
    ) -> dict[str, Any]:
        """
        Summarize IAM authentication posture (IA-2):
          - password policy existence / key settings
          - credential report summary (root MFA, MFA active coverage, active access keys)
        """
        stmt = select(Identity).where(
            Identity.account_id == account_id,
            Identity.resource_type.in_(["iam_password_policy", "iam_credential_report"]),
        )
        if ingestion_run_id:
            stmt = stmt.where(Identity.ingestion_run_id == ingestion_run_id)
        records = (await self.db.execute(stmt.order_by(Identity.created_at))).scalars().all()
        out: dict[str, Any] = {"password_policy": None, "credential_report": None}
        for r in records:
            if r.resource_type == "iam_password_policy" and out["password_policy"] is None:
                out["password_policy"] = r.data or {}
            if r.resource_type == "iam_credential_report" and out["credential_report"] is None:
                out["credential_report"] = r.data or {}
        return out

    async def summarize_cloudtrail_posture(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
    ) -> dict[str, Any]:
        """Summarize CloudTrail posture (AU-2/AU-6)."""
        stmt = select(Asset).where(
            Asset.account_id == account_id,
            Asset.resource_type == "cloudtrail_trail",
        )
        if ingestion_run_id:
            stmt = stmt.where(Asset.ingestion_run_id == ingestion_run_id)
        trails = (await self.db.execute(stmt.order_by(Asset.created_at))).scalars().all()

        total = len(trails)
        multi_region = 0
        logging_enabled = 0
        log_validation = 0
        kms_enabled = 0
        cw_logs_integrations = 0
        sample: list[dict[str, Any]] = []

        for t in trails:
            d = t.data or {}
            if d.get("is_multi_region_trail"):
                multi_region += 1
            if d.get("is_logging") is True:
                logging_enabled += 1
            if d.get("log_file_validation_enabled"):
                log_validation += 1
            if d.get("kms_key_id"):
                kms_enabled += 1
            if d.get("cloud_watch_logs_log_group_arn"):
                cw_logs_integrations += 1
            if len(sample) < 10:
                sample.append(
                    {
                        "name": d.get("name"),
                        "home_region": d.get("home_region"),
                        "is_multi_region_trail": d.get("is_multi_region_trail"),
                        "is_logging": d.get("is_logging"),
                        "log_file_validation_enabled": d.get("log_file_validation_enabled"),
                        "kms_key_id_present": bool(d.get("kms_key_id")),
                        "s3_bucket_name": d.get("s3_bucket_name"),
                        "cloud_watch_logs_log_group_arn": d.get("cloud_watch_logs_log_group_arn"),
                    }
                )

        return {
            "total_trails": total,
            "multi_region_trails_count": multi_region,
            "logging_enabled_trails_count": logging_enabled,
            "log_file_validation_enabled_trails_count": log_validation,
            "kms_enabled_trails_count": kms_enabled,
            "cloudwatch_logs_integration_trails_count": cw_logs_integrations,
            "sample_trails": sample,
        }

    async def summarize_cloudwatch_log_groups(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
    ) -> dict[str, Any]:
        """Summarize CloudWatch log group retention posture (AU-2/AU-6)."""
        stmt = select(Asset).where(
            Asset.account_id == account_id,
            Asset.resource_type == "cloudwatch_log_group",
        )
        if ingestion_run_id:
            stmt = stmt.where(Asset.ingestion_run_id == ingestion_run_id)
        groups = (await self.db.execute(stmt.order_by(Asset.created_at))).scalars().all()

        total = len(groups)
        with_retention = 0
        without_retention = 0
        kms_enabled = 0
        sample_without_retention: list[dict[str, Any]] = []

        for g in groups:
            d = g.data or {}
            if d.get("retention_in_days") is None:
                without_retention += 1
                if len(sample_without_retention) < 10:
                    sample_without_retention.append(
                        {
                            "log_group_name": d.get("log_group_name"),
                            "region": g.region,
                            "stored_bytes": d.get("stored_bytes"),
                        }
                    )
            else:
                with_retention += 1
            if d.get("kms_key_id"):
                kms_enabled += 1

        return {
            "total_log_groups": total,
            "log_groups_with_retention_count": with_retention,
            "log_groups_without_retention_count": without_retention,
            "kms_encrypted_log_groups_count": kms_enabled,
            "sample_log_groups_without_retention": sample_without_retention,
        }

    async def summarize_vpc_flow_logs(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
    ) -> dict[str, Any]:
        """Summarize VPC Flow Logs coverage (AU-2/AU-6, SC-7)."""
        stmt = select(NetworkComponent).where(
            NetworkComponent.account_id == account_id,
            NetworkComponent.resource_type == "vpc_flow_log",
        )
        if ingestion_run_id:
            stmt = stmt.where(NetworkComponent.ingestion_run_id == ingestion_run_id)
        logs = (await self.db.execute(stmt.order_by(NetworkComponent.created_at))).scalars().all()

        total = len(logs)
        delivered_ok = 0
        sample: list[dict[str, Any]] = []
        for fl in logs:
            d = fl.data or {}
            if str(d.get("deliver_logs_status", "")).lower() in {"success", "ok"}:
                delivered_ok += 1
            if len(sample) < 10:
                sample.append(
                    {
                        "flow_log_id": d.get("flow_log_id") or fl.resource_id,
                        "resource_id": d.get("resource_id"),
                        "traffic_type": d.get("traffic_type"),
                        "destination_type": d.get("log_destination_type"),
                        "log_group_name": d.get("log_group_name"),
                        "deliver_logs_status": d.get("deliver_logs_status"),
                        "region": fl.region,
                    }
                )
        return {
            "total_flow_logs": total,
            "deliver_logs_success_count": delivered_ok,
            "sample_flow_logs": sample,
        }

    async def summarize_network_boundary(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
    ) -> dict[str, Any]:
        """Summarize network boundary components (SC-7)."""
        stmt = select(NetworkComponent).where(NetworkComponent.account_id == account_id)
        if ingestion_run_id:
            stmt = stmt.where(NetworkComponent.ingestion_run_id == ingestion_run_id)
        records = (await self.db.execute(stmt.order_by(NetworkComponent.created_at))).scalars().all()

        counts: dict[str, int] = {}
        sample: dict[str, list[dict[str, Any]]] = {
            "route_table": [],
            "network_acl": [],
            "internet_gateway": [],
            "nat_gateway": [],
            "vpc_endpoint": [],
        }

        for r in records:
            rt = r.resource_type
            counts[rt] = counts.get(rt, 0) + 1
            if rt in sample and len(sample[rt]) < 5:
                d = r.data or {}
                sample[rt].append(
                    {
                        "id": r.resource_id,
                        "region": r.region,
                        "vpc_id": d.get("vpc_id")
                        or (
                            d.get("attachments", [{}])[0].get("VpcId")
                            if isinstance(d.get("attachments"), list)
                            else None
                        ),
                        "summary": d,
                    }
                )

        return {"counts_by_resource_type": counts, "sample": sample}

    async def summarize_cm8_inventory(
        self,
        account_id: str,
        ingestion_run_id: UUID | None,
    ) -> dict[str, Any]:
        """Summarize component inventory across ingested resources (CM-8)."""
        counts = {
            "identities": await self.counts_by_resource_type("identities", account_id, ingestion_run_id),
            "assets": await self.counts_by_resource_type("assets", account_id, ingestion_run_id),
            "network_components": await self.counts_by_resource_type("network_components", account_id, ingestion_run_id),
            "data_stores": await self.counts_by_resource_type("data_stores", account_id, ingestion_run_id),
        }
        stmt = select(Asset).where(
            Asset.account_id == account_id,
            Asset.resource_type == "ebs_volume",
        )
        if ingestion_run_id:
            stmt = stmt.where(Asset.ingestion_run_id == ingestion_run_id)
        vols = (await self.db.execute(stmt)).scalars().all()
        total_vols = len(vols)
        encrypted = 0
        sample_unencrypted: list[dict[str, Any]] = []
        for v in vols:
            d = v.data or {}
            if d.get("encrypted"):
                encrypted += 1
            else:
                if len(sample_unencrypted) < 10:
                    sample_unencrypted.append(
                        {
                            "volume_id": d.get("volume_id") or v.resource_id,
                            "region": v.region,
                            "size_gb": d.get("size_gb"),
                            "attachments": d.get("attachments", []),
                        }
                    )
        return {
            "counts": counts,
            "ebs_volumes": {
                "total_volumes": total_vols,
                "encrypted_volumes_count": encrypted,
                "unencrypted_volumes_count": total_vols - encrypted,
                "sample_unencrypted_volumes": sample_unencrypted,
            },
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    @staticmethod
    def _model_for_table(table: TableName):
        if table == "identities":
            return Identity
        if table == "assets":
            return Asset
        if table == "network_components":
            return NetworkComponent
        if table == "data_stores":
            return DataStore
        raise ValueError(f"Unsupported table: {table}")

