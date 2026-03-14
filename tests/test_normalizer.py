"""
Tests for the normalizer — verifies deterministic transformation
of raw AWS responses into canonical compliance objects.

These tests are critical: they prove that the system produces
explainable, repeatable output — a FedRAMP requirement.
"""

from datetime import datetime, timezone

from app.services.aws.normalizer import (
    normalize_ec2_instance,
    normalize_iam_user,
    normalize_iam_role,
    normalize_iam_policy,
    normalize_vpc,
    normalize_subnet,
    normalize_security_group,
    normalize_s3_bucket,
    normalize_rds_instance,
)


class TestEC2Normalization:
    def test_basic_instance(self):
        raw = {
            "InstanceId": "i-0123456789abcdef0",
            "InstanceType": "t3.micro",
            "State": {"Name": "running"},
            "VpcId": "vpc-abc123",
            "SubnetId": "subnet-abc123",
            "PrivateIpAddress": "10.0.1.5",
            "PublicIpAddress": "54.1.2.3",
            "SecurityGroups": [{"GroupId": "sg-111"}, {"GroupId": "sg-222"}],
            "IamInstanceProfile": {"Arn": "arn:aws:iam::123456789012:instance-profile/WebServer"},
            "LaunchTime": datetime(2024, 1, 15, tzinfo=timezone.utc),
            "EbsOptimized": True,
            "Monitoring": {"State": "enabled"},
            "Tags": [{"Key": "Name", "Value": "web-01"}],
        }
        result = normalize_ec2_instance(raw)

        assert result.instance_id == "i-0123456789abcdef0"
        assert result.instance_type == "t3.micro"
        assert result.state == "running"
        assert result.vpc_id == "vpc-abc123"
        assert result.public_ip == "54.1.2.3"
        assert result.security_group_ids == ["sg-111", "sg-222"]
        assert result.iam_instance_profile == "arn:aws:iam::123456789012:instance-profile/WebServer"
        assert result.ebs_optimized is True
        assert result.monitoring_enabled is True
        assert result.tags == {"Name": "web-01"}

    def test_minimal_instance(self):
        """Instance with only required fields."""
        raw = {
            "InstanceId": "i-minimal",
            "InstanceType": "t3.nano",
            "State": {"Name": "stopped"},
        }
        result = normalize_ec2_instance(raw)
        assert result.instance_id == "i-minimal"
        assert result.state == "stopped"
        assert result.vpc_id is None
        assert result.tags == {}


class TestIAMNormalization:
    def test_user_with_mfa(self):
        raw = {
            "UserName": "admin",
            "UserId": "AIDAEXAMPLE",
            "Arn": "arn:aws:iam::123456789012:user/admin",
            "Path": "/",
            "CreateDate": datetime(2023, 6, 1, tzinfo=timezone.utc),
            "Tags": [{"Key": "Team", "Value": "Security"}],
        }
        result = normalize_iam_user(
            raw,
            mfa_devices=[{"SerialNumber": "arn:aws:iam::123456789012:mfa/admin"}],
            access_keys=[
                {"AccessKeyId": "AKIAEXAMPLE", "Status": "Active", "CreateDate": datetime(2023, 6, 1, tzinfo=timezone.utc)}
            ],
            attached_policies=[{"PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess"}],
            inline_policies=["custom-inline"],
            groups=[{"GroupName": "Admins"}],
        )
        assert result.user_name == "admin"
        assert result.mfa_enabled is True
        assert len(result.access_keys) == 1
        assert result.attached_policies == ["arn:aws:iam::aws:policy/ReadOnlyAccess"]
        assert result.groups == ["Admins"]

    def test_user_without_mfa(self):
        raw = {
            "UserName": "nokeys",
            "UserId": "AIDANOKEYS",
            "Arn": "arn:aws:iam::123456789012:user/nokeys",
        }
        result = normalize_iam_user(raw, [], [], [], [], [])
        assert result.mfa_enabled is False
        assert result.access_keys == []

    def test_role(self):
        raw = {
            "RoleName": "SecurityAudit",
            "RoleId": "AROAEXAMPLE",
            "Arn": "arn:aws:iam::123456789012:role/SecurityAudit",
            "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": []},
            "MaxSessionDuration": 7200,
            "Tags": [],
        }
        result = normalize_iam_role(
            raw,
            attached_policies=[{"PolicyArn": "arn:aws:iam::aws:policy/SecurityAudit"}],
            inline_policies=[],
        )
        assert result.role_name == "SecurityAudit"
        assert result.max_session_duration == 7200

    def test_policy(self):
        raw = {
            "PolicyName": "CustomPolicy",
            "PolicyId": "ANPAEXAMPLE",
            "Arn": "arn:aws:iam::123456789012:policy/CustomPolicy",
            "AttachmentCount": 3,
            "IsAttachable": True,
            "DefaultVersionId": "v2",
            "Tags": [],
        }
        result = normalize_iam_policy(raw)
        assert result.policy_name == "CustomPolicy"
        assert result.attachment_count == 3


class TestNetworkNormalization:
    def test_vpc(self):
        raw = {
            "VpcId": "vpc-abc123",
            "CidrBlock": "10.0.0.0/16",
            "State": "available",
            "IsDefault": False,
            "DhcpOptionsId": "dopt-abc123",
            "Tags": [{"Key": "Environment", "Value": "Production"}],
        }
        result = normalize_vpc(raw)
        assert result.vpc_id == "vpc-abc123"
        assert result.cidr_block == "10.0.0.0/16"
        assert result.is_default is False
        assert result.tags == {"Environment": "Production"}

    def test_subnet(self):
        raw = {
            "SubnetId": "subnet-abc123",
            "VpcId": "vpc-abc123",
            "CidrBlock": "10.0.1.0/24",
            "AvailabilityZone": "us-east-1a",
            "State": "available",
            "MapPublicIpOnLaunch": True,
            "AvailableIpAddressCount": 251,
            "Tags": [],
        }
        result = normalize_subnet(raw)
        assert result.subnet_id == "subnet-abc123"
        assert result.map_public_ip_on_launch is True

    def test_security_group(self):
        raw = {
            "GroupId": "sg-abc123",
            "GroupName": "web-sg",
            "VpcId": "vpc-abc123",
            "Description": "Web tier security group",
            "IpPermissions": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS from anywhere"}],
                    "Ipv6Ranges": [],
                    "PrefixListIds": [],
                    "UserIdGroupPairs": [],
                }
            ],
            "IpPermissionsEgress": [],
            "Tags": [],
        }
        result = normalize_security_group(raw)
        assert result.group_id == "sg-abc123"
        assert len(result.ingress_rules) == 1
        assert result.ingress_rules[0].from_port == 443
        assert result.ingress_rules[0].cidr_blocks == ["0.0.0.0/0"]


class TestDataStoreNormalization:
    def test_s3_bucket(self):
        raw = {"Name": "my-compliance-bucket", "CreationDate": datetime(2024, 1, 1, tzinfo=timezone.utc)}
        result = normalize_s3_bucket(
            raw,
            location="us-east-1",
            versioning={"Status": "Enabled"},
            encryption={"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms", "KMSMasterKeyID": "arn:aws:kms:us-east-1:123456789012:key/abc"}}]},
            public_access={"BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True},
            logging_config={"LoggingEnabled": {"TargetBucket": "log-bucket"}},
            tags=[{"Key": "DataClass", "Value": "Confidential"}],
        )
        assert result.bucket_name == "my-compliance-bucket"
        assert result.versioning_status == "Enabled"
        assert result.encryption_algorithm == "aws:kms"
        assert result.kms_key_id == "arn:aws:kms:us-east-1:123456789012:key/abc"
        assert result.logging_enabled is True
        assert result.tags == {"DataClass": "Confidential"}

    def test_rds_instance(self):
        raw = {
            "DBInstanceIdentifier": "prod-db",
            "DBInstanceClass": "db.r5.large",
            "Engine": "postgres",
            "EngineVersion": "15.4",
            "DBInstanceStatus": "available",
            "MasterUsername": "admin",
            "Endpoint": {"Address": "prod-db.abc123.us-east-1.rds.amazonaws.com", "Port": 5432},
            "DBSubnetGroup": {"VpcId": "vpc-abc123", "DBSubnetGroupName": "prod-subnet-grp"},
            "MultiAZ": True,
            "StorageEncrypted": True,
            "KmsKeyId": "arn:aws:kms:us-east-1:123456789012:key/rds-key",
            "AutoMinorVersionUpgrade": True,
            "BackupRetentionPeriod": 7,
            "PubliclyAccessible": False,
            "VpcSecurityGroups": [{"VpcSecurityGroupId": "sg-rds1"}],
            "TagList": [{"Key": "Compliance", "Value": "FedRAMP-High"}],
        }
        result = normalize_rds_instance(raw)
        assert result.db_instance_identifier == "prod-db"
        assert result.multi_az is True
        assert result.storage_encrypted is True
        assert result.backup_retention_period == 7
        assert result.publicly_accessible is False
        assert result.tags == {"Compliance": "FedRAMP-High"}
