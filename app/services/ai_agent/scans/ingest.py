from __future__ import annotations

import csv
import io
import json
from typing import Any


def parse_nessus_csv(data: bytes) -> list[dict[str, Any]]:
    """
    Best-effort parser for Nessus/Tenable CSV exports.
    Returns a normalized list of finding dicts (title, description, severity, raw).
    """
    text = data.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    out: list[dict[str, Any]] = []
    for row in reader:
        title = row.get("Name") or row.get("Plugin Name") or row.get("Title") or "Unnamed Finding"
        desc = row.get("Description") or row.get("Synopsis") or row.get("Plugin Output") or ""
        severity = row.get("Severity") or row.get("Risk") or row.get("Risk Factor") or ""
        finding_key = row.get("Plugin ID") or row.get("PluginID") or row.get("Plugin Id") or None
        resource_id = row.get("Host") or row.get("IP Address") or row.get("DNS Name") or None
        out.append(
            {
                "source": "nessus",
                "finding_key": str(finding_key) if finding_key else None,
                "title": str(title),
                "description": str(desc) if desc is not None else None,
                "severity": str(severity) if severity else None,
                "resource_id": str(resource_id) if resource_id else None,
                "raw": row,
            }
        )
    return out


def parse_securityhub_json(data: bytes) -> list[dict[str, Any]]:
    """
    Parse AWS Security Hub findings JSON (GetFindings output or exported bundle).
    """
    obj = json.loads(data.decode("utf-8", errors="ignore") or "{}")
    findings = obj.get("Findings") if isinstance(obj, dict) else None
    if findings is None and isinstance(obj, list):
        findings = obj
    if not isinstance(findings, list):
        return []

    out: list[dict[str, Any]] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        title = f.get("Title") or f.get("GeneratorId") or "Unnamed Finding"
        desc = f.get("Description") or ""
        sev = (f.get("Severity") or {}).get("Label") if isinstance(f.get("Severity"), dict) else f.get("Severity")
        finding_key = f.get("Id") or f.get("FindingId") or f.get("GeneratorId")
        res = None
        resources = f.get("Resources")
        if isinstance(resources, list) and resources:
            r0 = resources[0] or {}
            if isinstance(r0, dict):
                res = r0.get("Id") or r0.get("Arn")
        out.append(
            {
                "source": "securityhub",
                "finding_key": str(finding_key) if finding_key else None,
                "title": str(title),
                "description": str(desc) if desc is not None else None,
                "severity": str(sev) if sev else None,
                "resource_id": str(res) if res else None,
                "raw": f,
            }
        )
    return out

