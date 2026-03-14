from __future__ import annotations

import re
from pathlib import Path

from app.services.ai_agent.policies.templates import TEMPLATES


def _sanitize_filename(s: str) -> str:
    s2 = re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip().lower())
    return s2.strip("_") or "policy"


def render_policy_markdown(*, policy_id: str, vendor_map: dict[str, list[str]] | None = None) -> str:
    tpl = TEMPLATES.get(policy_id)
    if not tpl:
        raise ValueError(f"Unknown policy_id: {policy_id}")

    md = tpl.skeleton_markdown
    vm = vendor_map or {}
    for k, v in vm.items():
        md = md.replace("{{" + k + "}}", ", ".join(v or []) or "TBD")

    # Fill any remaining placeholders with TBD.
    md = re.sub(r"\{\{[^}]+\}\}", "TBD", md)
    return md.strip() + "\n"


def write_generated_policy(
    *,
    workspace_root: Path,
    policy_id: str,
    markdown: str,
) -> Path:
    out_dir = workspace_root / "@docs" / "generated_policies"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = _sanitize_filename(policy_id) + ".md"
    path = out_dir / filename
    path.write_text(markdown, encoding="utf-8")
    return path

