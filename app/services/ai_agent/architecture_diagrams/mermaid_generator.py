from __future__ import annotations

import re
from typing import Any

from app.config.logging_config import get_logger
from app.services.ai_agent.narrative.llm_client import invoke_text

logger = get_logger(__name__)


MERMAID_SYSTEM = """\
You are a senior cloud architect and FedRAMP assessor.
Generate a Mermaid flowchart for a FedRAMP Authorization Boundary Diagram (ABD) + Dataflow Diagram (DFD).

STRICT RULES:
- Evidence is the source of truth. Do NOT hallucinate components as implemented.
- If a required FedRAMP diagram element is NOT present in evidence (e.g., management path, external IdP), you MUST still include it as a placeholder labeled: "NOT EVIDENCED / PLANNED".
- If a component type has count=0 in evidence, do NOT include it as implemented. Only include it as "NOT EVIDENCED / PLANNED" if it is required for audit completeness.

OUTPUT RULES:
- Return ONLY Mermaid syntax (no markdown fences, no explanations).
- Start with: flowchart LR
- Use subgraphs for: Authorization Boundary, VPC, Public Subnets, Private Subnets, App Tier, Data Tier, External Services, Management Path, Legend.
- Keep labels short and readable.
- Make the diagram presentation-ready using Mermaid classDef/class/style statements.
- Prefer a polished ABD layout similar to a professional architecture board: grouped side panels, internal tiers, legend, and balanced spacing.
- Use dark blue for implemented service boxes, pale blue/grey for containers, and light muted styling for "NOT EVIDENCED / PLANNED" placeholders.
- Keep component labels concise, with IDs as secondary text lines where useful.
- IMPORTANT: Use ONLY safe Mermaid node IDs matching regex: [A-Za-z][A-Za-z0-9_]*
- Put AWS IDs (like vpc-..., subnet-...) inside labels, not node IDs.
- IMPORTANT: Node IDs MUST NOT start with "end" (reserved keyword in Mermaid).
- IMPORTANT: Avoid parentheses in edge labels. Use "Auth - NOT EVIDENCED" not "Auth (NOT EVIDENCED)".
- For multi-line labels, use HTML line breaks: <br/> (do NOT use literal \\n).
"""


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    # Remove ```mermaid fences if present
    t = re.sub(r"^```(?:mermaid)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def build_mermaid_user_message(*, evidence_json: dict[str, Any]) -> str:
    import json

    return (
        "Create a Mermaid diagram for a FedRAMP Authorization Boundary and Dataflow.\n"
        "Mandatory placeholders (must be included even if not evidenced):\n"
        '- Management/Admin path: "Administrative Access (VPN/Bastion/SSM) — NOT EVIDENCED / PLANNED"\n'
        '- External IdP / interconnection: "External IdP (Okta) — NOT EVIDENCED / PLANNED"\n'
        '- Legend box explaining: boundary dashed line, arrows, and NOT EVIDENCED / PLANNED style\n'
        "Styling expectations:\n"
        "- Include Mermaid classDef rules for implemented services, containers, placeholders, and legend.\n"
        "- Use a structured, visually balanced ABD overview layout closer to an executive architecture diagram than a plain flowchart.\n"
        "- Keep arrows readable and labels short.\n"
        "Do NOT invent other components.\n\n"
        "Evidence JSON:\n"
        + json.dumps(evidence_json, indent=2, default=str)
    )


def _count(evidence_json: dict[str, Any], table: str, resource_type: str) -> int:
    counts = evidence_json.get("counts") or {}
    arr = counts.get(table) or []
    for item in arr:
        if isinstance(item, dict) and item.get("resource_type") == resource_type:
            try:
                return int(item.get("count") or 0)
            except Exception:
                return 0
    return 0


def deterministic_mermaid_from_evidence(*, evidence_json: dict[str, Any]) -> str:
    """
    Deterministic Mermaid (audit-ready Option 1) used as fallback if LLM output is invalid.
    """
    resources = evidence_json.get("resources") or {}
    vpcs = resources.get("vpcs") or []
    subnets = resources.get("subnets") or []
    igws = resources.get("internet_gateways") or []
    ec2 = resources.get("ec2_instances") or []

    vpc_id = None
    vpc_cidr = None
    region = None
    if vpcs:
        d = (vpcs[0] or {}).get("data") or {}
        vpc_id = d.get("vpc_id") or (vpcs[0] or {}).get("resource_id")
        vpc_cidr = d.get("cidr_block")
        region = (vpcs[0] or {}).get("region")

    igw_id = None
    if igws:
        d = (igws[0] or {}).get("data") or {}
        igw_id = d.get("internet_gateway_id") or (igws[0] or {}).get("resource_id")

    public = []
    private = []
    for s in subnets:
        d = (s or {}).get("data") or {}
        sid = d.get("subnet_id") or (s or {}).get("resource_id")
        az = d.get("availability_zone")
        cidr = d.get("cidr_block")
        label = f"{sid}" + (f"<br/>{az}" if az else "") + (f"<br/>{cidr}" if cidr else "")
        if d.get("map_public_ip_on_launch") is True:
            public.append(label)
        else:
            private.append(label)

    ec2_labels = []
    for inst in ec2[:3]:
        d = (inst or {}).get("data") or {}
        iid = d.get("instance_id") or (inst or {}).get("resource_id")
        nm = (d.get("tags") or {}).get("Name")
        state = d.get("state")
        label = (nm or "EC2") + f"<br/>{iid}"
        if state:
            label += f"<br/>[{state}]"
        ec2_labels.append(label)

    sg_count = _count(evidence_json, "network_components", "security_group")
    rt_count = _count(evidence_json, "network_components", "route_table")
    nacl_count = _count(evidence_json, "network_components", "network_acl")

    lines: list[str] = []
    lines.append("flowchart LR")
    lines.append("classDef service fill:#173f6b,stroke:#0f2f50,color:#ffffff,stroke-width:1.5px;")
    lines.append("classDef container fill:#eef4fa,stroke:#a7bfd7,color:#17324d,stroke-width:1.2px;")
    lines.append("classDef placeholder fill:#f1eef7,stroke:#b7aacd,color:#4a4a63,stroke-dasharray: 4 3;")
    lines.append("classDef legend fill:#f8fbff,stroke:#b7c9da,color:#203040;")
    lines.append("linkStyle default stroke:#6a89a7,stroke-width:1.6px;")
    lines.append('subgraph B["FedRAMP Authorization Boundary"]')
    lines.append('  subgraph EXT["External Services (Outside Boundary)"]')
    lines.append('    IDP["External IdP (Okta)<br/>NOT EVIDENCED / PLANNED"]')
    lines.append("  end")
    lines.append('  subgraph MGMT["Management Path"]')
    lines.append('    ADM["Administrative Access (VPN/Bastion/SSM)<br/>NOT EVIDENCED / PLANNED"]')
    lines.append("  end")
    lines.append('  subgraph VPC["VPC' + (f'\\n{vpc_id}' if vpc_id else "") + (f'\\n{vpc_cidr}' if vpc_cidr else "") + (f'\\n{region}' if region else "") + '"]')
    lines.append(f'    IGW["Internet Gateway\\n{igw_id or "igw (not evidenced)"}"]')
    if public:
        lines.append('    subgraph PUB["Public Subnets"]')
        for i, s in enumerate(public[:3], start=1):
            lines.append(f'      PUB{i}["{s}"]')
        if len(public) > 3:
            lines.append(f'      PUBMORE["(+{len(public)-3} more)"]')
        lines.append("    end")
    if private:
        lines.append('    subgraph PRIV["Private Subnets"]')
        for i, s in enumerate(private[:3], start=1):
            lines.append(f'      PR{i}["{s}"]')
        if len(private) > 3:
            lines.append(f'      PRMORE["(+{len(private)-3} more)"]')
        lines.append("    end")
    lines.append('    subgraph APP["App Tier"]')
    if ec2_labels:
        for i, e in enumerate(ec2_labels, start=1):
            lines.append(f'      EC2{i}["{e}"]')
    else:
        lines.append('      EC2X["EC2 Instances\\n(none evidenced)"]')
    lines.append("    end")
    lines.append('    SEC["Security Controls\\nSG: ' + str(sg_count) + ' | RT: ' + str(rt_count) + ' | NACL: ' + str(nacl_count) + '"]')
    lines.append("  end")
    lines.append('  LEG["Legend<br/>Dashed boundary = Auth Boundary<br/>Arrows = Data Flows<br/>Grey dotted = NOT EVIDENCED/PLANNED"]')
    lines.append("end")

    # Flows
    lines.append("IDP -->|Auth - NOT EVIDENCED| IGW")
    lines.append("ADM -->|Mgmt - NOT EVIDENCED| IGW")
    if public:
        lines.append("IGW --> PUB1")
        lines.append("PUB1 --> EC21" if ec2_labels else "PUB1 --> EC2X")
    else:
        lines.append("IGW --> APP")
    lines.append("APP --> SEC")
    lines.append("class IDP,ADM placeholder")
    lines.append("class IGW,SEC service")
    if public:
        public_ids = ",".join([f"PUB{i}" for i in range(1, min(len(public), 3) + 1)])
        if public_ids:
            lines.append(f"class {public_ids} container")
        if len(public) > 3:
            lines.append("class PUBMORE legend")
    if private:
        private_ids = ",".join([f"PR{i}" for i in range(1, min(len(private), 3) + 1)])
        if private_ids:
            lines.append(f"class {private_ids} container")
        if len(private) > 3:
            lines.append("class PRMORE legend")
    if ec2_labels:
        ec2_ids = ",".join([f"EC2{i}" for i in range(1, len(ec2_labels) + 1)])
        lines.append(f"class {ec2_ids} service")
    else:
        lines.append("class EC2X placeholder")
    lines.append("class LEG legend")

    return "\n".join(lines).strip() + "\n"


def _looks_invalid(*, code: str, evidence_json: dict[str, Any]) -> bool:
    """
    Heuristic validation: if code includes clearly non-evidenced implemented components, reject.
    """
    t = (code or "").lower()
    # If evidence says count is 0 but code mentions the service as implemented, reject.
    checks = [
        ("data_stores", "rds_instance", "rds"),
        ("data_stores", "s3_bucket", "s3"),
        ("assets", "cloudtrail_trail", "cloudtrail"),
        ("assets", "cloudwatch_log_group", "cloudwatch"),
        ("network_components", "nat_gateway", "nat"),
    ]
    for table, rt, needle in checks:
        if _count(evidence_json, table, rt) == 0 and needle in t and "not evidenced" not in t:
            return True
    # Must include mandatory placeholders
    if "not evidenced" not in t:
        return True
    if "administrative access" not in t or "okta" not in t or "legend" not in t:
        return True
    return False


async def generate_mermaid_from_evidence(*, evidence_json: dict[str, Any]) -> dict[str, str]:
    """
    Generate Mermaid syntax from evidence using the text model.
    Returns { mermaid_code, mermaid_prompt }.
    """
    user = build_mermaid_user_message(evidence_json=evidence_json)
    raw = await invoke_text(system_message=MERMAID_SYSTEM, user_message=user, temperature=0.1)
    code = _strip_fences(raw)
    if not code.lower().startswith("flowchart"):
        # Best-effort normalize
        code = "flowchart LR\n" + code

    if _looks_invalid(code=code, evidence_json=evidence_json):
        fallback = deterministic_mermaid_from_evidence(evidence_json=evidence_json)
        logger.warning("mermaid_invalid_fallback_used", llm_len=len(code), fallback_len=len(fallback))
        return {"mermaid_code": fallback, "mermaid_prompt": user}

    logger.info("mermaid_generated", length=len(code))
    return {"mermaid_code": code + ("\n" if not code.endswith("\n") else ""), "mermaid_prompt": user}

