"""
Normalizer — transforms raw AWS API responses into canonical compliance objects.

Design principles:
1. Deterministic: Same input always produces the same output.
2. Lossless for compliance: Every field relevant to NIST 800-53 controls
   is extracted; irrelevant fields are dropped.
3. Explainable: The mapping from raw field → normalized field is obvious
   from reading this code.

No AI, no heuristics, no guessing. This is a pure data transformation layer.
"""

from datetime import datetime, timezone

from app.config.logging_config import get_logger
from app.models.asset import AssetData
from app.models.identity import IAMUserData, IAMRoleData, IAMPolicyData
from app.models.network import (
    VPCData,
    SubnetData,
    SecurityGroupData,
    SecurityGroupRuleData,
)
from app.models.datastore import S3BucketData, RDSInstanceData

logger = get_logger(__name__)


def normalize_cloudtrail_trail(raw: dict, status: dict | None = None) -> dict:
    """Normalize CloudTrail trail configuration (AU-2/AU-6)."""
    status = status or {}
    return {
        "name": raw.get("Name"),
        "trail_arn": raw.get("TrailARN") or raw.get("TrailArn"),
        "home_region": raw.get("HomeRegion"),
        "is_multi_region_trail": bool(raw.get("IsMultiRegionTrail")),
        "is_organization_trail": bool(raw.get("IsOrganizationTrail")),
        "log_file_validation_enabled": bool(raw.get("LogFileValidationEnabled")),
        "kms_key_id": raw.get("KmsKeyId"),
        "s3_bucket_name": raw.get("S3BucketName"),
        "s3_key_prefix": raw.get("S3KeyPrefix"),
        "cloud_watch_logs_log_group_arn": raw.get("CloudWatchLogsLogGroupArn"),
        "cloud_watch_logs_role_arn": raw.get("CloudWatchLogsRoleArn"),
        # Status indicators (best-effort)
        "is_logging": bool(status.get("IsLogging")) if "IsLogging" in status else None,
        "latest_delivery_time": str(status.get("LatestDeliveryTime")) if status.get("LatestDeliveryTime") else None,
        "latest_cloud_watch_logs_delivery_time": str(status.get("LatestCloudWatchLogsDeliveryTime"))
        if status.get("LatestCloudWatchLogsDeliveryTime")
        else None,
        "latest_notification_time": str(status.get("LatestNotificationTime"))
        if status.get("LatestNotificationTime")
        else None,
    }


def normalize_cloudwatch_log_group(raw: dict) -> dict:
    """Normalize CloudWatch log group configuration (AU-2/AU-6)."""
    return {
        "log_group_name": raw.get("logGroupName"),
        "arn": raw.get("arn"),
        "retention_in_days": raw.get("retentionInDays"),
        "kms_key_id": raw.get("kmsKeyId"),
        "stored_bytes": raw.get("storedBytes"),
        "creation_time": raw.get("creationTime"),
    }


def normalize_vpc_flow_log(raw: dict) -> dict:
    """Normalize VPC Flow Log configuration (AU-2/AU-6, SC-7)."""
    return {
        "flow_log_id": raw.get("FlowLogId"),
        "resource_id": raw.get("ResourceId"),
        "traffic_type": raw.get("TrafficType"),
        "log_destination_type": raw.get("LogDestinationType"),
        "log_destination": raw.get("LogDestination"),
        "log_group_name": raw.get("LogGroupName"),
        "deliver_logs_status": raw.get("DeliverLogsStatus"),
        "max_aggregation_interval": raw.get("MaxAggregationInterval"),
        "creation_time": raw.get("CreationTime"),
        "tags": _tags_list_to_dict(raw.get("Tags", [])),
    }


def normalize_route_table(raw: dict) -> dict:
    """Normalize route table (SC-7 boundary protection)."""
    associations = raw.get("Associations", []) or []
    routes = raw.get("Routes", []) or []
    return {
        "route_table_id": raw.get("RouteTableId"),
        "vpc_id": raw.get("VpcId"),
        "associations": [
            {
                "main": a.get("Main", False),
                "subnet_id": a.get("SubnetId"),
                "gateway_id": a.get("GatewayId"),
                "association_id": a.get("RouteTableAssociationId"),
            }
            for a in associations
        ],
        "routes": [
            {
                "destination_cidr_block": r.get("DestinationCidrBlock"),
                "destination_ipv6_cidr_block": r.get("DestinationIpv6CidrBlock"),
                "gateway_id": r.get("GatewayId"),
                "nat_gateway_id": r.get("NatGatewayId"),
                "transit_gateway_id": r.get("TransitGatewayId"),
                "instance_id": r.get("InstanceId"),
                "vpc_peering_connection_id": r.get("VpcPeeringConnectionId"),
                "state": r.get("State"),
                "origin": r.get("Origin"),
            }
            for r in routes
        ],
        "tags": _extract_tags(raw),
    }


def normalize_network_acl(raw: dict) -> dict:
    """Normalize network ACL (SC-7)."""
    entries = raw.get("Entries", []) or []
    associations = raw.get("Associations", []) or []
    return {
        "network_acl_id": raw.get("NetworkAclId"),
        "vpc_id": raw.get("VpcId"),
        "is_default": raw.get("IsDefault", False),
        "associations": [
            {
                "subnet_id": a.get("SubnetId"),
                "network_acl_association_id": a.get("NetworkAclAssociationId"),
            }
            for a in associations
        ],
        "entries": [
            {
                "rule_number": e.get("RuleNumber"),
                "protocol": e.get("Protocol"),
                "rule_action": e.get("RuleAction"),
                "egress": e.get("Egress", False),
                "cidr_block": e.get("CidrBlock"),
                "ipv6_cidr_block": e.get("Ipv6CidrBlock"),
                "port_range": e.get("PortRange"),
            }
            for e in entries
        ],
        "tags": _extract_tags(raw),
    }


def normalize_internet_gateway(raw: dict) -> dict:
    """Normalize Internet Gateway attachment state (SC-7)."""
    return {
        "internet_gateway_id": raw.get("InternetGatewayId"),
        "attachments": raw.get("Attachments", []) or [],
        "tags": _extract_tags(raw),
    }


def normalize_nat_gateway(raw: dict) -> dict:
    """Normalize NAT Gateway (SC-7)."""
    addrs = raw.get("NatGatewayAddresses", []) or []
    return {
        "nat_gateway_id": raw.get("NatGatewayId"),
        "vpc_id": raw.get("VpcId"),
        "subnet_id": raw.get("SubnetId"),
        "state": raw.get("State"),
        "connectivity_type": raw.get("ConnectivityType"),
        "public_ips": [a.get("PublicIp") for a in addrs if a.get("PublicIp")],
        "private_ips": [a.get("PrivateIp") for a in addrs if a.get("PrivateIp")],
        "create_time": str(raw.get("CreateTime")) if raw.get("CreateTime") else None,
        "tags": _tags_list_to_dict(raw.get("Tags", [])),
    }


def normalize_vpc_endpoint(raw: dict) -> dict:
    """Normalize VPC Endpoint (SC-7)."""
    return {
        "vpc_endpoint_id": raw.get("VpcEndpointId"),
        "vpc_id": raw.get("VpcId"),
        "service_name": raw.get("ServiceName"),
        "vpc_endpoint_type": raw.get("VpcEndpointType"),
        "state": raw.get("State"),
        "private_dns_enabled": raw.get("PrivateDnsEnabled"),
        "route_table_ids": raw.get("RouteTableIds", []) or [],
        "subnet_ids": raw.get("SubnetIds", []) or [],
        "security_group_ids": [g.get("GroupId") for g in (raw.get("Groups") or []) if g.get("GroupId")],
        "has_policy_document": bool(raw.get("PolicyDocument")),
        "dns_entries": raw.get("DnsEntries", []) or [],
        "tags": _tags_list_to_dict(raw.get("Tags", [])),
    }


def normalize_ebs_volume(raw: dict) -> dict:
    """Normalize EBS volume posture (CM-8, SC-28)."""
    atts = raw.get("Attachments", []) or []
    return {
        "volume_id": raw.get("VolumeId"),
        "size_gb": raw.get("Size"),
        "state": raw.get("State"),
        "volume_type": raw.get("VolumeType"),
        "encrypted": bool(raw.get("Encrypted")),
        "kms_key_id": raw.get("KmsKeyId"),
        "multi_attach_enabled": bool(raw.get("MultiAttachEnabled", False)),
        "iops": raw.get("Iops"),
        "throughput": raw.get("Throughput"),
        "attachments": [
            {
                "instance_id": a.get("InstanceId"),
                "device": a.get("Device"),
                "state": a.get("State"),
                "attach_time": str(a.get("AttachTime")) if a.get("AttachTime") else None,
                "delete_on_termination": a.get("DeleteOnTermination"),
            }
            for a in atts
        ],
        "tags": _extract_tags(raw),
    }


def normalize_iam_password_policy(raw: dict | None) -> dict:
    """Normalize IAM account password policy (IA-2)."""
    if not raw:
        return {"exists": False}
    return {
        "exists": True,
        "minimum_password_length": raw.get("MinimumPasswordLength"),
        "require_symbols": raw.get("RequireSymbols"),
        "require_numbers": raw.get("RequireNumbers"),
        "require_uppercase_characters": raw.get("RequireUppercaseCharacters"),
        "require_lowercase_characters": raw.get("RequireLowercaseCharacters"),
        "allow_users_to_change_password": raw.get("AllowUsersToChangePassword"),
        "expire_passwords": raw.get("ExpirePasswords"),
        "max_password_age": raw.get("MaxPasswordAge"),
        "password_reuse_prevention": raw.get("PasswordReusePrevention"),
        "hard_expiry": raw.get("HardExpiry"),
    }


def normalize_iam_credential_report_summary(csv_rows: list[dict]) -> dict:
    """
    Normalize IAM credential report into a safe summary (IA-2).
    Intentionally avoids storing access key IDs.
    """
    total = len(csv_rows)
    mfa_active = 0
    password_enabled = 0
    access_key_1_active = 0
    access_key_2_active = 0
    root_mfa_active = None
    sample = []

    for row in csv_rows:
        username = row.get("user")
        if username == "<root_account>":
            root_mfa_active = row.get("mfa_active")

        if str(row.get("mfa_active", "")).lower() == "true":
            mfa_active += 1
        if str(row.get("password_enabled", "")).lower() == "true":
            password_enabled += 1
        if str(row.get("access_key_1_active", "")).lower() == "true":
            access_key_1_active += 1
        if str(row.get("access_key_2_active", "")).lower() == "true":
            access_key_2_active += 1

        if len(sample) < 10 and username and username != "<root_account>":
            sample.append(
                {
                    "user": username,
                    "mfa_active": row.get("mfa_active"),
                    "password_enabled": row.get("password_enabled"),
                    "access_key_1_active": row.get("access_key_1_active"),
                    "access_key_2_active": row.get("access_key_2_active"),
                    "password_last_used": row.get("password_last_used"),
                }
            )

    return {
        "total_rows": total,
        "mfa_active_count": mfa_active,
        "password_enabled_count": password_enabled,
        "access_key_1_active_count": access_key_1_active,
        "access_key_2_active_count": access_key_2_active,
        "root_mfa_active": root_mfa_active,
        "sample_users": sample,
    }


def normalize_ec2_instance(raw: dict) -> AssetData:
    """Transform a raw EC2 DescribeInstances reservation instance."""
    profile = raw.get("IamInstanceProfile", {})
    monitoring = raw.get("Monitoring", {})

    return AssetData(
        instance_id=raw["InstanceId"],
        instance_type=raw["InstanceType"],
        state=raw["State"]["Name"],
        vpc_id=raw.get("VpcId"),
        subnet_id=raw.get("SubnetId"),
        private_ip=raw.get("PrivateIpAddress"),
        public_ip=raw.get("PublicIpAddress"),
        iam_instance_profile=profile.get("Arn"),
        launch_time=raw.get("LaunchTime"),
        tags=_extract_tags(raw),
        security_group_ids=[sg["GroupId"] for sg in raw.get("SecurityGroups", [])],
        ebs_optimized=raw.get("EbsOptimized", False),
        monitoring_enabled=monitoring.get("State") == "enabled",
    )


def normalize_iam_user(
    raw: dict,
    mfa_devices: list,
    access_keys: list,
    attached_policies: list,
    inline_policies: list,
    groups: list,
) -> IAMUserData:
    """Transform raw IAM GetUser + supplementary calls."""
    return IAMUserData(
        user_name=raw["UserName"],
        user_id=raw["UserId"],
        arn=raw["Arn"],
        path=raw.get("Path", "/"),
        create_date=raw.get("CreateDate"),
        password_last_used=raw.get("PasswordLastUsed"),
        mfa_enabled=len(mfa_devices) > 0,
        access_keys=[
            {
                "access_key_id": k["AccessKeyId"],
                "status": k["Status"],
                "create_date": str(k.get("CreateDate", "")),
            }
            for k in access_keys
        ],
        attached_policies=[p["PolicyArn"] for p in attached_policies],
        inline_policy_names=inline_policies,
        groups=[g["GroupName"] for g in groups],
        tags=_extract_tags(raw),
    )


def normalize_iam_role(
    raw: dict,
    attached_policies: list,
    inline_policies: list,
) -> IAMRoleData:
    return IAMRoleData(
        role_name=raw["RoleName"],
        role_id=raw["RoleId"],
        arn=raw["Arn"],
        path=raw.get("Path", "/"),
        create_date=raw.get("CreateDate"),
        assume_role_policy_document=raw.get("AssumeRolePolicyDocument", {}),
        attached_policies=[p["PolicyArn"] for p in attached_policies],
        inline_policy_names=inline_policies,
        max_session_duration=raw.get("MaxSessionDuration", 3600),
        tags=_extract_tags(raw),
    )


def normalize_iam_policy(raw: dict) -> IAMPolicyData:
    return IAMPolicyData(
        policy_name=raw["PolicyName"],
        policy_id=raw["PolicyId"],
        arn=raw["Arn"],
        path=raw.get("Path", "/"),
        attachment_count=raw.get("AttachmentCount", 0),
        is_attachable=raw.get("IsAttachable", True),
        create_date=raw.get("CreateDate"),
        update_date=raw.get("UpdateDate"),
        default_version_id=raw.get("DefaultVersionId"),
        tags=_extract_tags(raw),
    )


def normalize_vpc(raw: dict) -> VPCData:
    return VPCData(
        vpc_id=raw["VpcId"],
        cidr_block=raw["CidrBlock"],
        state=raw["State"],
        is_default=raw.get("IsDefault", False),
        dhcp_options_id=raw.get("DhcpOptionsId"),
        instance_tenancy=raw.get("InstanceTenancy", "default"),
        tags=_extract_tags(raw),
    )


def normalize_subnet(raw: dict) -> SubnetData:
    return SubnetData(
        subnet_id=raw["SubnetId"],
        vpc_id=raw["VpcId"],
        cidr_block=raw["CidrBlock"],
        availability_zone=raw["AvailabilityZone"],
        state=raw["State"],
        map_public_ip_on_launch=raw.get("MapPublicIpOnLaunch", False),
        available_ip_address_count=raw.get("AvailableIpAddressCount", 0),
        tags=_extract_tags(raw),
    )


def normalize_security_group(raw: dict) -> SecurityGroupData:
    return SecurityGroupData(
        group_id=raw["GroupId"],
        group_name=raw["GroupName"],
        vpc_id=raw.get("VpcId"),
        description=raw.get("Description", ""),
        ingress_rules=[_normalize_sg_rule(r) for r in raw.get("IpPermissions", [])],
        egress_rules=[_normalize_sg_rule(r) for r in raw.get("IpPermissionsEgress", [])],
        tags=_extract_tags(raw),
    )


def _normalize_sg_rule(raw: dict) -> SecurityGroupRuleData:
    return SecurityGroupRuleData(
        protocol=raw.get("IpProtocol", "-1"),
        from_port=raw.get("FromPort"),
        to_port=raw.get("ToPort"),
        cidr_blocks=[r["CidrIp"] for r in raw.get("IpRanges", [])],
        ipv6_cidr_blocks=[r["CidrIpv6"] for r in raw.get("Ipv6Ranges", [])],
        prefix_list_ids=[p["PrefixListId"] for p in raw.get("PrefixListIds", [])],
        security_group_refs=[g.get("GroupId", "") for g in raw.get("UserIdGroupPairs", [])],
        description=next(
            (r.get("Description") for r in raw.get("IpRanges", []) if r.get("Description")),
            None,
        ),
    )


def normalize_s3_bucket(
    raw: dict,
    location: str | None,
    versioning: dict | None,
    encryption: dict | None,
    public_access: dict | None,
    logging_config: dict | None,
    tags: list | None,
) -> S3BucketData:
    enc_algo = None
    kms_key = None
    if encryption:
        rules = encryption.get("Rules", [])
        if rules:
            sse = rules[0].get("ApplyServerSideEncryptionByDefault", {})
            enc_algo = sse.get("SSEAlgorithm")
            kms_key = sse.get("KMSMasterKeyID")

    return S3BucketData(
        bucket_name=raw["Name"],
        creation_date=raw.get("CreationDate"),
        region=location,
        versioning_status=versioning.get("Status") if versioning else None,
        encryption_algorithm=enc_algo,
        kms_key_id=kms_key,
        public_access_block=public_access,
        logging_enabled=bool(logging_config and logging_config.get("LoggingEnabled")),
        tags=_tags_list_to_dict(tags) if tags else {},
    )


def normalize_rds_instance(raw: dict) -> RDSInstanceData:
    endpoint = raw.get("Endpoint", {})
    vpc_sgs = raw.get("VpcSecurityGroups", [])
    return RDSInstanceData(
        db_instance_identifier=raw["DBInstanceIdentifier"],
        db_instance_class=raw["DBInstanceClass"],
        engine=raw["Engine"],
        engine_version=raw["EngineVersion"],
        db_instance_status=raw["DBInstanceStatus"],
        master_username=raw.get("MasterUsername", ""),
        endpoint_address=endpoint.get("Address"),
        endpoint_port=endpoint.get("Port"),
        vpc_id=raw.get("DBSubnetGroup", {}).get("VpcId"),
        multi_az=raw.get("MultiAZ", False),
        storage_encrypted=raw.get("StorageEncrypted", False),
        kms_key_id=raw.get("KmsKeyId"),
        auto_minor_version_upgrade=raw.get("AutoMinorVersionUpgrade", False),
        backup_retention_period=raw.get("BackupRetentionPeriod", 0),
        publicly_accessible=raw.get("PubliclyAccessible", False),
        security_group_ids=[sg["VpcSecurityGroupId"] for sg in vpc_sgs],
        subnet_group_name=raw.get("DBSubnetGroup", {}).get("DBSubnetGroupName"),
        tags=_tags_list_to_dict(raw.get("TagList", [])),
    )


def _extract_tags(raw: dict) -> dict[str, str]:
    """Convert AWS Tags list [{Key, Value}] to a flat dict."""
    return _tags_list_to_dict(raw.get("Tags", []))


def _tags_list_to_dict(tags: list | None) -> dict[str, str]:
    if not tags:
        return {}
    return {t["Key"]: t["Value"] for t in tags if "Key" in t and "Value" in t}

