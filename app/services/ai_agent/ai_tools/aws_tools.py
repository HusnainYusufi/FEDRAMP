"""
AWS evidence tools (dynamic toolset).

These tools are generic over the ingested evidence schema and can be used
for *any* control the frontend selects. The LLM chooses which tools to call.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.config import settings
from app.services.aws.evidence_service import AWSEvidenceService, TableName
from app.services.ai_agent.ai_tools.registry import ToolSpec, register_tool


class BaseArgs(BaseModel):
    account_id: str = Field(..., description="12-digit AWS account ID")
    ingestion_run_id: str | None = Field(
        None, description="Optional run UUID; uses latest successful run if omitted"
    )


class CountsArgs(BaseArgs):
    table: TableName = Field(..., description="Which evidence table to query")


class ListRecordsArgs(BaseArgs):
    table: TableName = Field(..., description="Which evidence table to query")
    resource_type: str | None = Field(
        None, description="Optional filter, e.g. iam_user, ec2_instance, security_group"
    )
    limit: int = Field(
        default_factory=lambda: settings.assessment_ai_default_sample_limit,
        ge=1,
        le=50,
        description="Max records to return (capped)",
    )


class SampleLimitArgs(BaseArgs):
    sample_limit: int = Field(
        default_factory=lambda: settings.assessment_ai_default_sample_limit,
        ge=1,
        le=20,
        description="Max sample records to include",
    )


class PolicyAttachmentArgs(BaseArgs):
    sample_limit: int = Field(
        default_factory=lambda: settings.assessment_ai_default_sample_limit,
        ge=1,
        le=20,
        description="Max sample principals to include (users and roles)",
    )
    top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Top-N policies / inline policy names to return",
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_iam_authentication_posture",
        purpose="Summarize IAM authentication posture (password policy + credential report summary) for IA-2.",
    )
)
def tool_summarize_iam_authentication_posture(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = BaseArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_iam_authentication_posture(args.account_id, run_id)

    return StructuredTool.from_function(
        name="aws_summarize_iam_authentication_posture",
        description="Summarize IAM authentication posture (password policy, credential report).",
        args_schema=BaseArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_counts_by_resource_type",
        purpose="Counts resources by resource_type for a given evidence table (identities/assets/network_components/data_stores).",
    )
)
def tool_counts_by_resource_type(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> list[dict[str, Any]]:
        args = CountsArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.counts_by_resource_type(args.table, args.account_id, run_id)

    return StructuredTool.from_function(
        name="aws_counts_by_resource_type",
        description="Counts resources by resource_type for a given evidence table.",
        args_schema=CountsArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_list_records",
        purpose="List capped normalized records from an evidence table, optionally filtered by resource_type.",
    )
)
def tool_list_records(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> list[dict[str, Any]]:
        args = ListRecordsArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.list_records(
            table=args.table,
            account_id=args.account_id,
            ingestion_run_id=run_id,
            resource_type=args.resource_type,
            limit=args.limit,
        )

    return StructuredTool.from_function(
        name="aws_list_records",
        description="List capped normalized records from an evidence table, optionally filtered by resource_type.",
        args_schema=ListRecordsArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_iam_users",
        purpose="Summarize IAM users: totals, MFA coverage, active access keys, and sample users.",
    )
)
def tool_summarize_iam_users(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = SampleLimitArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_iam_users(
            account_id=args.account_id,
            ingestion_run_id=run_id,
            sample_limit=args.sample_limit,
        )

    return StructuredTool.from_function(
        name="aws_summarize_iam_users",
        description="Summarize IAM users: totals, MFA coverage, active access keys, and sample users.",
        args_schema=SampleLimitArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_iam_policy_attachments",
        purpose="Summarize IAM policy attachments across users and roles (AWS-managed + customer-managed) and inline policy name counts.",
    )
)
def tool_summarize_iam_policy_attachments(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = PolicyAttachmentArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_iam_policy_attachments(
            account_id=args.account_id,
            ingestion_run_id=run_id,
            sample_limit=args.sample_limit,
            top_n=args.top_n,
        )

    return StructuredTool.from_function(
        name="aws_summarize_iam_policy_attachments",
        description="Summarize attached policies and inline policy names across IAM users and roles.",
        args_schema=PolicyAttachmentArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_cloudtrail_posture",
        purpose="Summarize CloudTrail posture (AU-2/AU-6): trails, multi-region, logging enabled, log validation, KMS, CloudWatch integration.",
    )
)
def tool_summarize_cloudtrail_posture(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = BaseArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_cloudtrail_posture(args.account_id, run_id)

    return StructuredTool.from_function(
        name="aws_summarize_cloudtrail_posture",
        description="Summarize CloudTrail posture (AU-2/AU-6).",
        args_schema=BaseArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_cloudwatch_log_groups",
        purpose="Summarize CloudWatch Logs retention posture (AU-2/AU-6): retention configured, missing retention, KMS encryption.",
    )
)
def tool_summarize_cloudwatch_log_groups(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = BaseArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_cloudwatch_log_groups(args.account_id, run_id)

    return StructuredTool.from_function(
        name="aws_summarize_cloudwatch_log_groups",
        description="Summarize CloudWatch log group retention posture.",
        args_schema=BaseArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_vpc_flow_logs",
        purpose="Summarize VPC Flow Logs coverage and delivery status (AU-2/AU-6, SC-7).",
    )
)
def tool_summarize_vpc_flow_logs(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = BaseArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_vpc_flow_logs(args.account_id, run_id)

    return StructuredTool.from_function(
        name="aws_summarize_vpc_flow_logs",
        description="Summarize VPC Flow Logs coverage and delivery status.",
        args_schema=BaseArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_network_boundary",
        purpose="Summarize network boundary components (SC-7): route tables, NACLs, IGWs, NAT gateways, VPC endpoints, plus samples.",
    )
)
def tool_summarize_network_boundary(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = BaseArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_network_boundary(args.account_id, run_id)

    return StructuredTool.from_function(
        name="aws_summarize_network_boundary",
        description="Summarize network boundary components (SC-7).",
        args_schema=BaseArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_cm8_inventory",
        purpose="Summarize component inventory across ingested resources (CM-8), including EBS encryption posture and counts by resource type.",
    )
)
def tool_summarize_cm8_inventory(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = BaseArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_cm8_inventory(args.account_id, run_id)

    return StructuredTool.from_function(
        name="aws_summarize_cm8_inventory",
        description="Summarize component inventory and key posture items (CM-8).",
        args_schema=BaseArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_ec2_instances",
        purpose="Summarize EC2 inventory: total instances, state breakdown, monitoring enabled count, and sample instances.",
    )
)
def tool_summarize_ec2_instances(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = SampleLimitArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_ec2_instances(
            account_id=args.account_id,
            ingestion_run_id=run_id,
            sample_limit=args.sample_limit,
        )

    return StructuredTool.from_function(
        name="aws_summarize_ec2_instances",
        description="Summarize EC2 inventory and posture.",
        args_schema=SampleLimitArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_security_groups_exposure",
        purpose="Summarize security group exposure: world-open ingress counts and sample rules.",
    )
)
def tool_summarize_security_groups_exposure(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = SampleLimitArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_security_groups_exposure(
            account_id=args.account_id,
            ingestion_run_id=run_id,
            sample_limit=args.sample_limit,
        )

    return StructuredTool.from_function(
        name="aws_summarize_security_groups_exposure",
        description="Summarize world-open ingress exposure patterns in security groups.",
        args_schema=SampleLimitArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_s3_buckets",
        purpose="Summarize S3 posture: encryption, public access block presence, and sample unencrypted buckets.",
    )
)
def tool_summarize_s3_buckets(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = SampleLimitArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_s3_buckets(
            account_id=args.account_id,
            ingestion_run_id=run_id,
            sample_limit=args.sample_limit,
        )

    return StructuredTool.from_function(
        name="aws_summarize_s3_buckets",
        description="Summarize S3 encryption and public access posture.",
        args_schema=SampleLimitArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_summarize_rds_instances",
        purpose="Summarize RDS posture: encryption at rest, public accessibility, and sample unencrypted instances.",
    )
)
def tool_summarize_rds_instances(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = SampleLimitArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.summarize_rds_instances(
            account_id=args.account_id,
            ingestion_run_id=run_id,
            sample_limit=args.sample_limit,
        )

    return StructuredTool.from_function(
        name="aws_summarize_rds_instances",
        description="Summarize RDS encryption and exposure posture.",
        args_schema=SampleLimitArgs,
        coroutine=_run,
    )


@register_tool(
    spec=ToolSpec(
        name="aws_default_evidence_snapshot",
        purpose="Broad default snapshot across IAM/EC2/Network/S3/RDS plus counts for all tables.",
        is_default=True,
    )
)
def tool_default_evidence_snapshot(svc: AWSEvidenceService) -> StructuredTool:
    async def _run(**kwargs) -> dict[str, Any]:
        args = SampleLimitArgs(**kwargs)
        run_id = await svc.resolve_run_id(args.account_id, args.ingestion_run_id)
        return await svc.default_evidence_snapshot(
            account_id=args.account_id,
            ingestion_run_id=run_id,
            sample_limit=args.sample_limit,
        )

    return StructuredTool.from_function(
        name="aws_default_evidence_snapshot",
        description="Return a broad, deterministic evidence snapshot (safe default).",
        args_schema=SampleLimitArgs,
        coroutine=_run,
    )

