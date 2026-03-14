from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import settings
from app.config.logging_config import get_logger
from app.services.ai_agent.architecture_diagrams.prompt_builder import (
    SUMMARIZER_SYSTEM_MESSAGE,
    build_artist_prompt_with_mermaid,
    build_summarizer_user_message,
)
from app.services.ai_agent.narrative.llm_client import invoke_text

logger = get_logger(__name__)


def _fallback_summary(*, evidence_json: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic fallback if the Summarizer output can't be parsed as JSON.
    Keeps strictly to evidence counts/types (no hallucinations).
    """
    counts = evidence_json.get("counts") or {}
    nc = {x.get("resource_type"): x.get("count") for x in (counts.get("network_components") or []) if isinstance(x, dict)}
    ac = {x.get("resource_type"): x.get("count") for x in (counts.get("assets") or []) if isinstance(x, dict)}
    dc = {x.get("resource_type"): x.get("count") for x in (counts.get("data_stores") or []) if isinstance(x, dict)}

    infra = [
        'Outer Boundary: "FedRAMP Authorization Boundary"',
        f'Network: {nc.get("vpc", 0)} VPC(s), {nc.get("subnet", 0)} subnet(s), {nc.get("internet_gateway", 0)} internet gateway(s)',
        f'Compute: {ac.get("ec2_instance", 0)} EC2 instance(s)',
        f'Data Stores: {dc.get("rds_instance", 0)} RDS instance(s), {dc.get("s3_bucket", 0)} S3 bucket(s)',
    ]
    flows = ["Internet -> Internet Gateway -> VPC -> Subnets -> EC2"]
    if dc.get("rds_instance", 0):
        flows.append("EC2 -> RDS")
    if dc.get("s3_bucket", 0):
        flows.append("EC2 -> S3")
    return {
        "boundary_label": "FedRAMP Authorization Boundary",
        "infrastructure_to_draw": [b for b in infra if b],
        "data_flows": flows,
    }


async def summarize_for_diagram(*, evidence_json: dict[str, Any]) -> dict[str, Any]:
    """Run Step 1 Summarizer and parse JSON safely."""
    user_message = build_summarizer_user_message(evidence_json=evidence_json)
    raw = await invoke_text(system_message=SUMMARIZER_SYSTEM_MESSAGE, user_message=user_message, temperature=0.2)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("summarizer_not_object")
        if not isinstance(parsed.get("infrastructure_to_draw"), list):
            raise ValueError("missing_infrastructure_to_draw")
        if not isinstance(parsed.get("data_flows"), list):
            raise ValueError("missing_data_flows")
        parsed["boundary_label"] = str(parsed.get("boundary_label") or "FedRAMP Authorization Boundary")
        parsed["infrastructure_to_draw"] = [str(x) for x in parsed.get("infrastructure_to_draw") if str(x).strip()]
        parsed["data_flows"] = [str(x) for x in parsed.get("data_flows") if str(x).strip()]
        return parsed
    except Exception as exc:
        logger.warning("diagram_summarizer_parse_failed", error=str(exc))
        return _fallback_summary(evidence_json=evidence_json)


def deterministic_summary_from_evidence(*, evidence_json: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic (non-LLM) summary to prevent hallucinations.

    Uses only what exists in evidence_json.resources + counts.
    """
    resources = evidence_json.get("resources") or {}
    counts = evidence_json.get("counts") or {}

    # Pull canonical items (IDs + key attrs)
    vpcs = resources.get("vpcs") or []
    subnets = resources.get("subnets") or []
    igws = resources.get("internet_gateways") or []
    ec2 = resources.get("ec2_instances") or []

    def _cnt(table_key: str, resource_type: str) -> int:
        arr = counts.get(table_key) or []
        for item in arr:
            if isinstance(item, dict) and item.get("resource_type") == resource_type:
                try:
                    return int(item.get("count") or 0)
                except Exception:
                    return 0
        return 0

    infra: list[str] = []
    infra.append('Outer Boundary: Draw a large dashed line representing the "FedRAMP Authorization Boundary".')

    # VPC (first / only)
    if vpcs:
        v = (vpcs[0] or {}).get("data") or {}
        vpc_id = v.get("vpc_id") or (vpcs[0] or {}).get("resource_id")
        cidr = v.get("cidr_block")
        region = (vpcs[0] or {}).get("region")
        label = f'Network: 1 VPC ("{vpc_id}")'
        if cidr:
            label += f" CIDR {cidr}"
        if region:
            label += f" ({region})"
        infra.append(label)
    else:
        infra.append("Network: VPC (not found in evidence)")

    # Internet Gateway
    if igws:
        g = (igws[0] or {}).get("data") or {}
        igw_id = g.get("internet_gateway_id") or (igws[0] or {}).get("resource_id")
        infra.append(f"Network Perimeter: 1 Internet Gateway ({igw_id}) attached to the VPC")
    else:
        c = _cnt("network_components", "internet_gateway")
        if c:
            infra.append(f"Network Perimeter: {c} Internet Gateway(s)")

    # Subnets: treat as public if map_public_ip_on_launch true, else private.
    public = []
    private = []
    for s in subnets:
        d = (s or {}).get("data") or {}
        sid = d.get("subnet_id") or (s or {}).get("resource_id")
        az = d.get("availability_zone")
        cidr = d.get("cidr_block")
        item = f"{sid}" + (f" ({az})" if az else "") + (f" {cidr}" if cidr else "")
        if d.get("map_public_ip_on_launch") is True:
            public.append(item)
        else:
            private.append(item)

    if public:
        # Keep it readable: list up to 4, then say +N more.
        head = public[:4]
        more = len(public) - len(head)
        msg = f"Public Subnets ({len(public)}): " + "; ".join(head)
        if more > 0:
            msg += f"; +{more} more"
        infra.append(msg)
    if private:
        head = private[:4]
        more = len(private) - len(head)
        msg = f"Private Subnets ({len(private)}): " + "; ".join(head)
        if more > 0:
            msg += f"; +{more} more"
        infra.append(msg)

    # EC2 instances
    if ec2:
        items = []
        for inst in ec2[:5]:
            d = (inst or {}).get("data") or {}
            iid = d.get("instance_id") or (inst or {}).get("resource_id")
            nm = (d.get("tags") or {}).get("Name")
            subnet_id = d.get("subnet_id")
            state = d.get("state")
            part = f"{iid}"
            if nm:
                part = f'{nm} ({iid})'
            if subnet_id:
                part += f" in {subnet_id}"
            if state:
                part += f" [{state}]"
            items.append(part)
        infra.append(f"App Tier: EC2 Instances ({len(ec2)}): " + "; ".join(items))

    # Other counts (do not invent; just summarize)
    sg_count = _cnt("network_components", "security_group")
    if sg_count:
        infra.append(f"Security: Security Groups ({sg_count})")
    nacl_count = _cnt("network_components", "network_acl")
    if nacl_count:
        infra.append(f"Security: Network ACLs ({nacl_count})")
    rt_count = _cnt("network_components", "route_table")
    if rt_count:
        infra.append(f"Network: Route Tables ({rt_count})")

    # Data stores (present?)
    rds_count = _cnt("data_stores", "rds_instance")
    s3_count = _cnt("data_stores", "s3_bucket")
    if rds_count or s3_count:
        infra.append(f"Data Tier: RDS ({rds_count}), S3 ({s3_count})")
    else:
        infra.append("Data Tier: (none found in evidence)")

    # --- FedRAMP audit-required elements (Option 1) ---
    # Management/admin path (separate from public traffic)
    infra.append('Management Path: "Administrative Access" (VPN/Bastion/SSM) — NOT EVIDENCED / PLANNED')

    # External service providers / interconnections
    infra.append('External Interconnections: "External IdP (Okta)" — NOT EVIDENCED / PLANNED')

    # Security logging services (show if present, otherwise mark not evidenced)
    ct = _cnt("assets", "cloudtrail_trail")
    cw = _cnt("assets", "cloudwatch_log_group")
    fl = _cnt("network_components", "vpc_flow_log")
    if (ct + cw + fl) > 0:
        infra.append(f"Security Services: CloudTrail ({ct}), CloudWatch Logs ({cw}), VPC Flow Logs ({fl})")
    else:
        infra.append("Security Services: CloudTrail/CloudWatch/VPC Flow Logs — NOT EVIDENCED / PLANNED")

    # Legend
    infra.append("Legend: Boundary (dashed), Data Flow (arrows), Not Evidenced/Planned (grey dotted box)")

    flows = []
    if igws and (public or private) and ec2:
        flows.append("Internet -> Internet Gateway -> Subnets -> EC2 Instances (Encryption in transit: NOT EVIDENCED)")
    elif igws and ec2:
        flows.append("Internet -> Internet Gateway -> VPC -> EC2 Instances (Encryption in transit: NOT EVIDENCED)")
    elif ec2:
        flows.append("VPC -> Subnets -> EC2 Instances (Encryption in transit: NOT EVIDENCED)")
    else:
        flows.append("Internet -> VPC (best-effort)")

    # Management flow (explicit)
    flows.append('Admin (Ops) -> "Administrative Access" -> VPC (Management traffic) — NOT EVIDENCED')

    # Interconnection flow (explicit)
    flows.append('Users -> "External IdP (Okta)" -> Application authentication — NOT EVIDENCED')

    return {
        "boundary_label": "FedRAMP Authorization Boundary",
        "infrastructure_to_draw": infra[:16],
        "data_flows": flows[:10],
    }


async def generate_diagram_png_base64(*, artist_prompt: str) -> dict[str, Any]:
    """
    Call OpenAI Images API and return base64 PNG (best-effort).

    Note: This uses prompt-only generation. Some OpenAI image models support reference images,
    but support varies by model/endpoint; we keep this path deterministic and robust.
    """
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openai_image_model,
        "prompt": artist_prompt,
        "size": settings.openai_image_size,
    }

    # Endpoint name is stable across SDKs; response may include b64_json or url.
    async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
        resp = await client.post("https://api.openai.com/v1/images/generations", headers=headers, json=payload)
        if resp.status_code >= 400:
            hint = (
                f"OpenAI Images API error {resp.status_code}: {resp.text[:500]}\n\n"
                "Hint: Your configured image model may not be image-capable for this endpoint.\n"
                f"- Current OPENAI_IMAGE_MODEL: {settings.openai_image_model}\n"
                "Try setting OPENAI_IMAGE_MODEL to an image-capable model (e.g. 'gpt-image-1')."
            )
            raise RuntimeError(hint)
        data = resp.json()

    item = (data.get("data") or [{}])[0] if isinstance(data, dict) else {}
    b64 = item.get("b64_json")
    url = item.get("url")
    if b64:
        return {"mime_type": "image/png", "base64": b64}
    if url:
        # Fallback: fetch url (if the API returns one)
        async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
            img = await client.get(url)
            img.raise_for_status()
            import base64 as _b64

            return {"mime_type": "image/png", "base64": _b64.b64encode(img.content).decode("utf-8")}

    raise RuntimeError("OpenAI Images API response did not include b64_json or url")


async def build_artist_prompt_from_evidence(
    *, evidence_json: dict[str, Any], use_llm_summarizer: bool = False
) -> dict[str, Any]:
    """Convenience: evidence -> (deterministic|LLM) summary -> artist prompt (no Mermaid)."""
    if use_llm_summarizer:
        summary = await summarize_for_diagram(evidence_json=evidence_json)
    else:
        summary = deterministic_summary_from_evidence(evidence_json=evidence_json)
    artist_prompt = build_artist_prompt_with_mermaid(
        infrastructure_to_draw=summary.get("infrastructure_to_draw") or [],
        data_flows=summary.get("data_flows") or [],
        mermaid_code="",
    )
    return {"summary": summary, "artist_prompt": artist_prompt}


async def build_artist_prompt_from_summary_and_mermaid(
    *,
    summary: dict[str, Any],
    mermaid_code: str,
) -> str:
    return build_artist_prompt_with_mermaid(
        infrastructure_to_draw=summary.get("infrastructure_to_draw") or [],
        data_flows=summary.get("data_flows") or [],
        mermaid_code=mermaid_code,
    )

