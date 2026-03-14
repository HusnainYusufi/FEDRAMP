"""
Pydantic schemas for Identity (IAM) resources.

Covers IAM users, roles, and policies. Normalized to capture the
attributes most relevant to access control families:
  AC-2 (Account Management)
  AC-3 (Access Enforcement)
  AC-6 (Least Privilege)
  IA-2 (Identification and Authentication)
"""

from datetime import datetime

from pydantic import BaseModel, Field


class IAMUserData(BaseModel):
    user_name: str
    user_id: str
    arn: str
    path: str = "/"
    create_date: datetime | None = None
    password_last_used: datetime | None = Field(
        default=None,
        description="Detects inactive accounts — AC-2(3)",
    )
    mfa_enabled: bool = Field(
        default=False,
        description="Multi-factor auth status — IA-2(1)",
    )
    access_keys: list[dict] = Field(
        default_factory=list,
        description="Access key metadata (ID, status, last used) — no secrets",
    )
    attached_policies: list[str] = Field(
        default_factory=list,
        description="ARNs of directly attached managed policies",
    )
    inline_policy_names: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)


class IAMRoleData(BaseModel):
    role_name: str
    role_id: str
    arn: str
    path: str = "/"
    create_date: datetime | None = None
    assume_role_policy_document: dict = Field(
        default_factory=dict,
        description="Trust policy — who can assume this role",
    )
    attached_policies: list[str] = Field(default_factory=list)
    inline_policy_names: list[str] = Field(default_factory=list)
    max_session_duration: int = Field(
        default=3600,
        description="Maximum AssumeRole session length in seconds",
    )
    tags: dict[str, str] = Field(default_factory=dict)


class IAMPolicyData(BaseModel):
    policy_name: str
    policy_id: str
    arn: str
    path: str = "/"
    attachment_count: int = 0
    is_attachable: bool = True
    create_date: datetime | None = None
    update_date: datetime | None = None
    default_version_id: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class IdentityRecord(BaseModel):
    id: str
    resource_id: str
    account_id: str
    region: str
    resource_type: str
    source: str
    data: dict
    created_at: datetime
    updated_at: datetime
