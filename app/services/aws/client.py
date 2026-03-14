"""
AWS client factory using STS AssumeRole.

Security architecture:
- This service NEVER stores long-lived credentials.
- It assumes a cross-account role with the SecurityAudit managed policy.
- Temporary credentials are scoped to the minimum session duration.
- No write operations are performed — all API calls are read-only.

FedRAMP controls addressed:
  AC-3   Access Enforcement (role-based, least-privilege)
  AC-6   Least Privilege (SecurityAudit is read-only)
  IA-2   Identification and Authentication (STS federation)
  AU-12  Audit Record Generation (CloudTrail logs every AssumeRole)
"""

import boto3
from botocore.config import Config

from app.config import settings
from app.config.logging_config import get_logger

logger = get_logger(__name__)

# Retry configuration — AWS API throttling is expected at scale
_BOTO_CONFIG = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=30,
)


class AWSClientFactory:
    """
    Creates boto3 clients using temporary credentials from STS AssumeRole.

    Usage:
        factory = AWSClientFactory(role_arn, account_id)
        ec2 = factory.get_client("ec2", "us-east-1")
    """

    def __init__(self, role_arn: str, account_id: str) -> None:
        self.role_arn = role_arn
        self.account_id = account_id
        self._credentials: dict | None = None

    def _assume_role(self) -> dict:
        """
        Assume the target role via STS and cache temporary credentials.

        The session name includes the account ID for CloudTrail attribution.
        """
        if self._credentials is not None:
            return self._credentials

        logger.info(
            "assuming_role",
            role_arn=self.role_arn,
            account_id=self.account_id,
        )

        aws_access_key_id = (
            settings.aws_access_key_id.get_secret_value()
            if settings.aws_access_key_id
            else None
        )
        aws_secret_access_key = (
            settings.aws_secret_access_key.get_secret_value()
            if settings.aws_secret_access_key
            else None
        )
        aws_session_token = (
            settings.aws_session_token.get_secret_value()
            if settings.aws_session_token
            else None
        )

        if aws_access_key_id and aws_secret_access_key:
            session = boto3.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
                region_name=settings.aws_default_region,
            )
            sts = session.client("sts", config=_BOTO_CONFIG)
        else:
            sts = boto3.client(
                "sts",
                region_name=settings.aws_default_region,
                config=_BOTO_CONFIG,
            )

        assume_kwargs: dict = {
            "RoleArn": self.role_arn,
            "RoleSessionName": f"fedramp-ingest-{self.account_id}",
            "DurationSeconds": settings.aws_session_duration,
        }

        if settings.aws_default_external_id:
            assume_kwargs["ExternalId"] = settings.aws_default_external_id

        response = sts.assume_role(**assume_kwargs)
        self._credentials = response["Credentials"]

        logger.info(
            "role_assumed",
            role_arn=self.role_arn,
            expiration=str(self._credentials["Expiration"]),
        )
        return self._credentials

    def get_client(self, service: str, region: str):
        """Return a boto3 client for the given service and region."""
        creds = self._assume_role()
        return boto3.client(
            service,
            region_name=region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            config=_BOTO_CONFIG,
        )

