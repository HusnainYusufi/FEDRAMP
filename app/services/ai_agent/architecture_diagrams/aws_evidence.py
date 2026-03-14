from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.aws.evidence_service import AWSEvidenceService


async def build_architecture_evidence(
    *,
    db: AsyncSession,
    account_id: str,
    ingestion_run_id: str | None,
    sample_limit: int = 50,
) -> dict[str, Any]:
    """
    Purpose-fit evidence JSON for architecture diagrams.

    Notes:
    - Uses only *ingested* evidence (Postgres), never live AWS APIs.
    - Returns deterministic, capped lists for stable diagram generation.
    """
    svc = AWSEvidenceService(db)
    run_uuid = await svc.resolve_run_id(account_id, ingestion_run_id)
    resolved_run_id = str(run_uuid) if run_uuid else None

    async def _list(table: str, resource_type: str) -> list[dict[str, Any]]:
        return await svc.list_records(
            table=table,  # type: ignore[arg-type]
            account_id=account_id,
            ingestion_run_id=run_uuid,
            resource_type=resource_type,
            limit=sample_limit,
        )

    async def _counts(table: str) -> list[dict[str, Any]]:
        return await svc.counts_by_resource_type(table, account_id, run_uuid)  # type: ignore[arg-type]

    # Network
    vpcs = await _list("network_components", "vpc")
    subnets = await _list("network_components", "subnet")
    internet_gateways = await _list("network_components", "internet_gateway")
    nat_gateways = await _list("network_components", "nat_gateway")
    vpc_endpoints = await _list("network_components", "vpc_endpoint")

    # Workloads + data
    ec2_instances = await _list("assets", "ec2_instance")
    rds_instances = await _list("data_stores", "rds_instance")
    s3_buckets = await _list("data_stores", "s3_bucket")

    # Logging/security services (only what is ingested today)
    cloudtrail_trails = await _list("assets", "cloudtrail_trail")
    cloudwatch_log_groups = await _list("assets", "cloudwatch_log_group")
    vpc_flow_logs = await _list("network_components", "vpc_flow_log")

    return {
        "account_id": account_id,
        "ingestion_run_id": resolved_run_id,
        "counts": {
            "identities": await _counts("identities"),
            "assets": await _counts("assets"),
            "network_components": await _counts("network_components"),
            "data_stores": await _counts("data_stores"),
        },
        "resources": {
            "vpcs": vpcs,
            "subnets": subnets,
            "internet_gateways": internet_gateways,
            "nat_gateways": nat_gateways,
            "vpc_endpoints": vpc_endpoints,
            "ec2_instances": ec2_instances,
            "rds_instances": rds_instances,
            "s3_buckets": s3_buckets,
            "cloudtrail_trails": cloudtrail_trails,
            "cloudwatch_log_groups": cloudwatch_log_groups,
            "vpc_flow_logs": vpc_flow_logs,
        },
        "notes": {
            "sample_limit": sample_limit,
            "resource_types_supported": [
                "vpc",
                "subnet",
                "internet_gateway",
                "nat_gateway",
                "vpc_endpoint",
                "vpc_flow_log",
                "ec2_instance",
                "rds_instance",
                "s3_bucket",
                "cloudtrail_trail",
                "cloudwatch_log_group",
            ],
            "no_hallucination": True,
        },
    }

