"""
Pydantic schemas for NetworkComponent resources.

Covers VPCs, subnets, and security groups. These are critical for:
  SC-7  (Boundary Protection)
  AC-4  (Information Flow Enforcement)
  SC-7(5) (Deny by Default)
"""

from datetime import datetime

from pydantic import BaseModel, Field


class VPCData(BaseModel):
    vpc_id: str
    cidr_block: str
    state: str
    is_default: bool = False
    dhcp_options_id: str | None = None
    instance_tenancy: str = "default"
    tags: dict[str, str] = Field(default_factory=dict)


class SubnetData(BaseModel):
    subnet_id: str
    vpc_id: str
    cidr_block: str
    availability_zone: str
    state: str
    map_public_ip_on_launch: bool = Field(
        default=False,
        description="If True, instances get public IPs — SC-7 concern",
    )
    available_ip_address_count: int = 0
    tags: dict[str, str] = Field(default_factory=dict)


class SecurityGroupRuleData(BaseModel):
    protocol: str
    from_port: int | None = None
    to_port: int | None = None
    cidr_blocks: list[str] = Field(default_factory=list)
    ipv6_cidr_blocks: list[str] = Field(default_factory=list)
    prefix_list_ids: list[str] = Field(default_factory=list)
    security_group_refs: list[str] = Field(
        default_factory=list,
        description="Referenced security groups (peer rules)",
    )
    description: str | None = None


class SecurityGroupData(BaseModel):
    group_id: str
    group_name: str
    vpc_id: str | None = None
    description: str = ""
    ingress_rules: list[SecurityGroupRuleData] = Field(default_factory=list)
    egress_rules: list[SecurityGroupRuleData] = Field(default_factory=list)
    tags: dict[str, str] = Field(default_factory=dict)


class NetworkRecord(BaseModel):
    id: str
    resource_id: str
    account_id: str
    region: str
    resource_type: str
    source: str
    data: dict
    created_at: datetime
    updated_at: datetime
