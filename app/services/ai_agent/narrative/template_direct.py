"""
Template-direct narrative generation.

Instead of trying to deterministically hydrate a complex blueprint, we:
  1) Use the ingested FedRAMP template Markdown for the control as the skeleton
  2) Ask the LLM to fill in Dragon implementation + parameters + checkbox selections
  3) Run an evaluator pass (pass/fail + reasons); if failed, retry once with feedback
"""

from __future__ import annotations

import json
import re
import html
from datetime import datetime
from typing import Any

from app.services.ai_agent.narrative import llm_client
from app.config import settings
from app.services.templates.template_parser import build_llm_friendly_skeleton


def _s(v: Any) -> str:
    return str(v or "").replace("\r", "")


def _as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _stable_base_id(control_id: str) -> str:
    base = (control_id or "").strip().lower().replace(" ", "")
    base = base.replace("(", "-").replace(")", "")
    base = base.replace("--", "-").strip("-")
    return base


def _display_param_ref(control_id: str, stable_param_id: str) -> str:
    """
    Convert a stable id like 'ac-2-h-1' back into 'AC-2(h)(1)' for display.
    """
    cid = (control_id or "").strip()
    pid = (stable_param_id or "").strip().lower()
    if not cid or not pid:
        return cid or stable_param_id or ""
    base = _stable_base_id(cid)
    suffix = pid[len(base) + 1 :] if pid.startswith(base + "-") else pid
    segs = [s for s in suffix.split("-") if s]
    if not segs:
        return cid
    return cid + "".join(f"({s})" for s in segs)


_PART_START_RE = re.compile(r"^\s*(?:#{1,6}\s*)?Part\s+(?P<part>[a-z])\b", re.IGNORECASE)
_PARTS_SECTION_RE = re.compile(r"what is the solution and how is it implemented\??", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")

_RESP_ROLE_RE = re.compile(r"Responsible Role\s*:\s*(?P<v>.+)$", re.IGNORECASE)
_PARAM_RE = re.compile(r"Parameter\s+(?P<id>[A-Z]{2,3}-\d+(?:\s*\(\s*\d+\s*\))?(?:\([^)]+\))*)\s*:\s*(?P<v>.+)$")
_CTRL_SUMMARY_RE = re.compile(r"control summary information", re.IGNORECASE)


def _escape_table_cell(text: str) -> str:
    # Prevent pipes from breaking Markdown tables and keep content readable.
    t = (text or "").strip()
    t = t.replace("|", "\\|")
    # Markdown tables don't support multi-line well; use <br>.
    t = re.sub(r"\n{2,}", "\n", t)
    t = t.replace("\n", "<br>")
    return t


def _strip_html_to_text(md: str) -> str:
    """
    Convert common HTML-in-Markdown (tables, <br/>) into plain text lines to enable regex extraction.
    This is intentionally lossy; it's used only to build a clean skeleton.
    """
    s = (md or "").replace("\r", "")
    # Normalize common line breaks.
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    # Drop remaining tags.
    s = re.sub(r"</?[^>]+>", "", s)
    # Unescape entities (e.g., &gt;).
    s = html.unescape(s)
    # Normalize whitespace.
    s = re.sub(r"[ \t]+\n", "\n", s)
    return s


def _extract_requirement_text(plain: str) -> str:
    """
    Best-effort extraction of the "question" (control text) for the skeleton.
    """
    s = (plain or "").strip()
    if not s:
        return ""

    # Start at "The organization:" if present.
    m0 = re.search(r"(?im)^\s*The organization\s*:\s*$", s)
    start = m0.start() if m0 else 0

    # End before first "Control Summary Information" block if present.
    m1 = re.search(r"(?im)^\s*.*Control Summary Information.*$", s[start:])
    end = start + m1.start() if m1 else min(len(s), start + 4000)

    out = s[start:end].strip()
    return out


def _extract_roles_and_params(plain: str) -> tuple[str, list[str]]:
    roles = ""
    params: list[str] = []
    for ln in (plain or "").splitlines():
        ln2 = ln.strip()
        if not ln2:
            continue
        m = _RESP_ROLE_RE.search(ln2)
        if m and not roles:
            roles = (m.group("v") or "").strip()
            continue
        m2 = _PARAM_RE.match(ln2)
        if m2:
            pid = (m2.group("id") or "").strip()
            pv = (m2.group("v") or "").strip()
            if pid:
                params.append(f"Parameter {pid}: {pv}".strip())
    # De-dupe while preserving order.
    seen: set[str] = set()
    out_params: list[str] = []
    for p in params:
        if p in seen:
            continue
        seen.add(p)
        out_params.append(p)
    return roles, out_params


def _split_lettered_requirements(req_text: str) -> dict[str, str]:
    """
    Split requirement text into (a)/(b)/(c)... chunks when present.
    Handles both newline-separated and single-line "(a) ... (b) ..." cases.
    """
    s = (req_text or "").replace("\r", "")
    if not s:
        return {}
    # Insert newlines before each "(x)" token if they appear mid-line.
    s2 = re.sub(r"(?<!\n)\s*(\(\s*[a-z]\s*\))\s*", r"\n\1 ", s, flags=re.IGNORECASE)
    # Extract segments in order. We DO NOT trust the source letter labels to be
    # sequential/correct (some template conversions duplicate "(a)" etc.).
    token_re = re.compile(r"(?im)^\s*\(\s*(?P<l>[a-z])\s*\)\s+")
    matches = list(token_re.finditer(s2))
    if not matches:
        return {}

    segments: list[str] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(s2)
        seg = s2[start:end].strip()
        if seg:
            segments.append(seg)

    # Assign sequential letters by position (a, b, c...).
    out: dict[str, str] = {}
    for idx, seg in enumerate(segments):
        if idx >= 26:
            break
        letter = chr(ord("a") + idx)
        # Normalize whitespace inside the segment.
        seg2 = re.sub(r"\s+", " ", seg).strip()
        out[letter] = seg2
    return out


def _strip_triple_backtick_fences(md: str) -> str:
    """
    If the skeleton contains ``` fences, remove the fence lines but keep the content.
    This prevents the UI from rendering large sections as code blocks.
    """
    out: list[str] = []
    for ln in (md or "").splitlines():
        if ln.strip().startswith("```"):
            continue
        out.append(ln)
    return "\n".join(out).strip() + "\n"


def _extract_inherited_auth_from_template(template_md: str) -> tuple[str | None, str | None]:
    """
    Best-effort extraction of the pre-existing FedRAMP authorization name/date from the stored template markdown.
    Common pattern:
      "Inherited from pre-existing FedRAMP Authorization for AGENCYAMAZONEW, 05/01/2023"
    """
    s = (template_md or "").replace("\r", "")
    if not s.strip():
        return None, None

    # Match: ... Authorization for <name>, <date>
    m = re.search(
        r"(?is)Inherited\s+from\s+pre-existing\s+FedRAMP\s+Authorization\s+for\s+"
        r"(?P<name>[^,\n]+?)\s*,\s*(?P<date>\d{2}/\d{2}/\d{4})",
        s,
    )
    if m:
        name = (m.group("name") or "").strip()
        date = (m.group("date") or "").strip()
        if name and "click here" not in name.lower():
            return name, date

    # Fallback to org-configured values.
    cfg_name = (getattr(settings, "fedramp_inherited_auth_name", "") or "").strip()
    cfg_date = (getattr(settings, "fedramp_inherited_auth_date", "") or "").strip()
    if cfg_name:
        if not cfg_date:
            cfg_date = datetime.now().strftime("%m/%d/%Y")
        return cfg_name, cfg_date

    return None, None


def _apply_inherited_auth_placeholders(md: str, *, name: str | None, date: str | None) -> str:
    """
    Replace inheritance placeholders in the skeleton with real values (or blanks).
    This prevents placeholders from leaking into the final narrative.
    """
    out = md or ""
    nm = (name or "").strip()
    dt = (date or "").strip()
    # If missing, fall back to config (if set).
    if not nm:
        nm = (getattr(settings, "fedramp_inherited_auth_name", "") or "").strip()
    if not dt:
        dt = (getattr(settings, "fedramp_inherited_auth_date", "") or "").strip()
    if nm and not dt:
        dt = datetime.now().strftime("%m/%d/%Y")
    out = out.replace("{{FILL_INHERITED_AUTH_NAME}}", nm)
    out = out.replace("{{FILL_INHERITED_AUTH_DATE}}", dt)
    return out


def _deterministic_validation_failures(narrative_markdown: str) -> list[str]:
    lines = (narrative_markdown or "").splitlines()
    checked = _extract_checked(lines)
    failures: list[str] = []
    if "Control Summary Information" not in narrative_markdown:
        failures.append("Missing section: Control Summary Information")
    if "What is the solution and how is it implemented" not in narrative_markdown:
        failures.append("Missing section: What is the solution and how is it implemented?")
    if "Implementation Status" not in narrative_markdown:
        failures.append("Missing Implementation Status checkbox block")
    if "Control Origination" not in narrative_markdown:
        failures.append("Missing Control Origination checkbox block")
    if not checked["status"]:
        failures.append("No Implementation Status checkbox selected (no ☒ found).")
    if not checked["origination"]:
        failures.append("No Control Origination checkbox selected (no ☒ found).")
    if "{{FILL_DRAGON_IMPLEMENTATION}}" in narrative_markdown:
        failures.append("One or more Part sections were not filled (placeholder remains).")
    if re.search(r"\{\{FILL_[A-Z0-9_]+\}\}", narrative_markdown):
        failures.append("One or more template placeholders remain unfilled ({{FILL_...}}).")
    if "Requirement (from template)" in narrative_markdown:
        failures.append("Found forbidden phrase: 'Requirement (from template)' (use 'Requirement').")
    # Disallow vendor leakage in the finalized narrative.
    if re.search(r"\b(VGC|FTGS|FastTrack|Virtual Government Cloud)\b", narrative_markdown, flags=re.IGNORECASE):
        failures.append("Found forbidden vendor/program name (VGC/FTGS/FastTrack). Use Dragon terminology.")
    # Disallow meta-evaluation blocks in final output.
    if re.search(r"(Critical Errors|The Critical Error|The Wins|Evaluation of Changes|✅|🔴)", narrative_markdown):
        failures.append("Narrative contains meta-evaluation content; output must be narrative only.")
    # Not applicable should not be selected when narrative claims non-implementation / missing evidence.
    if any("not applicable" in (x or "").lower() for x in (checked.get("status") or [])) and re.search(
        r"\bNot evidenced\b", narrative_markdown, flags=re.IGNORECASE
    ):
        failures.append("Implementation Status is 'Not applicable' but narrative contains 'Not evidenced' findings.")
    # Inheritance is a platform fact on AWS-hosted systems; do NOT mark it as "Not evidenced".
    inheritance_cells = _extract_parts_table_inheritance_cells(narrative_markdown)
    if inheritance_cells:
        if any(re.search(r"\bnot evidenced\b", c, flags=re.IGNORECASE) for c in inheritance_cells):
            failures.append("Inheritance column contains 'Not evidenced' (inheritance is a platform fact; use AWS boilerplate).")
        # Enforce AWS inheritance boilerplate concept (don't drift into evidence language).
        bad = [
            c
            for c in inheritance_cells
            if not (re.search(r"\binherit", c, flags=re.IGNORECASE) and re.search(r"\baws\b|\bfedramp\b", c, flags=re.IGNORECASE))
        ]
        if bad:
            failures.append("Inheritance column must reference AWS/FedRAMP inheritance (platform fact) in every Part row.")
    if re.search(r"FedRAMP authorized\s*\(\s*,\s*\)", narrative_markdown):
        failures.append("Inheritance authorization name/date is empty (found 'FedRAMP authorized (, )').")
    return failures


def _extract_parts_table_inheritance_cells(markdown: str) -> list[str]:
    """
    Extract the Inheritance column text from the Parts markdown table (if present).
    Table format:
      | Part | Requirement | Dragon Implementation | Inheritance | Customer Responsibility |
      |---|---|---|---|---|
      | a | ... | ... | <inheritance> | ... |
    """
    lines = (markdown or "").splitlines()
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("| part |") and "inheritance" in ln.lower():
            header_idx = i
            break
    if header_idx is None:
        return []
    out: list[str] = []
    for ln in lines[header_idx + 2 :]:
        if not ln.strip().startswith("|"):
            break
        # Split table row cells.
        parts = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(parts) < 5:
            continue
        inherit_cell = parts[3]
        if inherit_cell:
            out.append(inherit_cell)
    return out


def _derive_part_ids(template_plain: str, requirement_text: str) -> list[str]:
    """
    Determine which parts to render (a, b, c...).
    Preference order:
      1) Explicit "Part a"/"Part b" tokens
      2) Lettered requirements (a)(b)(c) in the control text
      3) None
    """
    found: list[str] = []
    for m in re.finditer(r"(?im)^\s*Part\s+(?P<p>[a-z])\b", template_plain or ""):
        pid = (m.group("p") or "").lower()
        if pid and pid not in found:
            found.append(pid)
    if found:
        return found
    lettered = _split_lettered_requirements(requirement_text)
    if lettered:
        return list(lettered.keys())
    return []


def _convert_parts_section_to_table(template_md: str) -> str:
    """
    Convert a Parts section with "Part a/b/..." blocks into a Markdown table.
    This keeps the UI clean without requiring HTML hacks.
    """
    lines = (template_md or "").splitlines()
    if not lines:
        return template_md

    # Find the Parts section heading.
    start_idx = None
    for i, ln in enumerate(lines):
        if _PARTS_SECTION_RE.search(ln or ""):
            start_idx = i
            break
    if start_idx is None:
        return template_md

    # Find where the section ends (next heading of same/higher level).
    # We conservatively end at the next "### " or "## " heading after the Parts heading.
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if _HEADING_RE.match(lines[j] or "") and not _PART_START_RE.match(lines[j] or ""):
            end_idx = j
            break

    section = lines[start_idx:end_idx]
    # Collect part blocks.
    part_starts: list[tuple[int, str]] = []
    for k, ln in enumerate(section):
        m = _PART_START_RE.match(ln or "")
        if m:
            part_starts.append((k, m.group("part").lower()))
    if not part_starts:
        return template_md

    part_starts.append((len(section), ""))  # sentinel
    rows: list[dict[str, str]] = []
    for idx in range(len(part_starts) - 1):
        a, pid = part_starts[idx]
        b, _ = part_starts[idx + 1]
        block = "\n".join(section[a:b]).strip()
        if not pid or not block:
            continue
        # Best-effort extraction: requirement is the first non-heading lines until "Dragon Implementation".
        blk_lines = [x.rstrip() for x in block.splitlines()]
        # Drop the "Part x" header line.
        if blk_lines and _PART_START_RE.match(blk_lines[0] or ""):
            blk_lines = blk_lines[1:]
        joined = "\n".join(blk_lines).strip()
        req = ""
        # Prefer explicit label.
        m_req = re.search(r"Requirement.*?\n", joined, flags=re.IGNORECASE)
        if m_req:
            # take from after requirement label until next label
            after = joined[m_req.end() :].strip()
            req = re.split(r"\n\s*(Dragon Implementation|Inheritance|Customer Responsibility)\s*:?", after, flags=re.IGNORECASE)[0].strip()
        else:
            req = re.split(r"\n\s*(Dragon Implementation|Inheritance|Customer Responsibility)\s*:?", joined, flags=re.IGNORECASE)[0].strip()

        rows.append(
            {
                "part": pid,
                "requirement": req or "(requirement text)",
            }
        )

    if not rows:
        return template_md

    inheritance_default = (
        "The system inherits applicable control implementations from underlying platform and shared services as "
        "defined by the organization's boundary. Inherited implementations are documented in the SSP inheritance matrix."
    )
    customer_default = (
        "The customer is responsible for implementing any customer-managed portions of this control, including "
        "process controls and configuration within customer-owned accounts, as applicable."
    )

    table_lines: list[str] = []
    table_lines.append("| Part | Requirement (from template) | Dragon Implementation | Inheritance | Customer Responsibility |")
    table_lines.append("|---|---|---|---|---|")
    for r in rows:
        table_lines.append(
            "| "
            + _escape_table_cell(r["part"])
            + " | "
            + _escape_table_cell(r["requirement"])
            + " | "
            + "{{FILL_DRAGON_IMPLEMENTATION}}"
            + " | "
            + _escape_table_cell(inheritance_default)
            + " | "
            + _escape_table_cell(customer_default)
            + " |"
        )

    new_section = [section[0].rstrip(), ""] + table_lines + [""]
    out_lines = lines[:start_idx] + new_section + lines[end_idx:]
    return "\n".join(out_lines).strip() + "\n"


def _inject_part_placeholders(template_md: str) -> str:
    """
    Ensure each 'Part a:' style section has an explicit Dragon Implementation slot.
    This is intentionally light-touch (no deep parsing) to keep the template intact.
    """
    lines = (template_md or "").splitlines()
    out: list[str] = []
    for i, ln in enumerate(lines):
        out.append(ln)
        m = re.match(r"^\s*Part\s+(?P<part>[a-z])\s*:?\s*$", (ln or ""), re.IGNORECASE)
        if not m:
            continue
        window = "\n".join(lines[i + 1 : i + 10]).lower()
        if "dragon implementation" in window:
            continue
        out.append("Dragon Implementation:")
        out.append("")
        out.append("{{FILL_DRAGON_IMPLEMENTATION}}")
        out.append("")
    return "\n".join(out).strip() + "\n"


def build_template_skeleton_markdown(
    *,
    control: dict[str, Any],
    template_markdown: str,
) -> str:
    """
    Return a strict narrative template skeleton in Markdown.
    The LLM must preserve this structure and fill placeholders.
    """
    control_id = _s(control.get("control_id")).strip()
    title = _s(control.get("title")).strip()
    baseline = _s(control.get("baseline")).strip()
    header = f"{control_id} {title}".strip() if control_id or title else (control_id or title or "Control")
    if baseline:
        header = f"{header} - {baseline.title()}"

    md = (template_markdown or "").strip()
    if not md:
        raise ValueError("template_markdown_empty")

    plain = _strip_html_to_text(md)
    req = _extract_requirement_text(plain)
    roles, params = _extract_roles_and_params(plain)

    # Derive part list and requirement per part (when possible).
    lettered = _split_lettered_requirements(req)
    part_ids = _derive_part_ids(template_plain=plain, requirement_text=req)
    if not part_ids:
        # Enhancement controls often don't have parts; render one row.
        part_ids = ["a"]
        if not req:
            # Fallback: take first non-empty paragraph after the heading line(s).
            for ln in plain.splitlines():
                if ln.strip():
                    req = ln.strip()
                    break

    inheritance_default = (
        "Partially inherited from the underlying FedRAMP-authorized cloud platform "
        "({{FILL_INHERITED_AUTH_NAME}}, {{FILL_INHERITED_AUTH_DATE}})."
    )
    customer_default = "Customer implements customer-managed portions as applicable."

    out: list[str] = []
    out.append(f"## {header}")
    out.append("")

    out.append("### FedRAMP Template Control Text")
    out.append(req.strip() if req.strip() else "(template control text not found)")
    out.append("")

    out.append(f"### {control_id} Control Summary Information")
    out.append(f"Responsible Role: {roles}".rstrip())
    for p in params:
        out.append(p)
    out.append("Implementation Status (check all that apply):")
    out.extend(
        [
            "☐ Implemented",
            "☐ Partially implemented",
            "☐ Planned",
            "☐ Alternative implementation",
            "☐ Not applicable",
        ]
    )
    out.append("Control Origination (check all that apply):")
    out.extend(
        [
            "☐ Service Provider Corporate",
            "☐ Service Provider System Specific",
            "☐ Service Provider Hybrid (Corporate and System Specific)",
            "☐ Configured by Customer (Customer System Specific)",
            "☐ Provided by Customer (Customer System Specific)",
            "☐ Shared (Service Provider and Customer Responsibility)",
            "☐ Inherited from pre-existing FedRAMP Authorization for {{FILL_INHERITED_AUTH_NAME}}, Date of Authorization {{FILL_INHERITED_AUTH_DATE}}",
        ]
    )
    out.append("")

    out.append(f"### {control_id} What is the solution and how is it implemented?")
    out.append("")
    out.append("| Part | Requirement | Dragon Implementation | Inheritance | Customer Responsibility |")
    out.append("|---|---|---|---|---|")
    for pid in part_ids:
        reqp = lettered.get(pid, "").strip() if isinstance(lettered, dict) else ""
        if not reqp:
            reqp = "(see control text)"
        out.append(
            "| "
            + _escape_table_cell(pid)
            + " | "
            + _escape_table_cell(reqp)
            + " | "
            + "{{FILL_DRAGON_IMPLEMENTATION}}"
            + " | "
            + _escape_table_cell(inheritance_default)
            + " | "
            + _escape_table_cell(customer_default)
            + " |"
        )
    out.append("")

    return "\n".join(out).strip() + "\n"


def _extract_checked(lines: list[str]) -> dict[str, list[str]]:
    """
    Pull checked items out of a checkbox block for validation.
    """
    status: list[str] = []
    orig: list[str] = []
    mode = None
    for ln in lines:
        low = ln.lower()
        if "implementation status" in low:
            mode = "status"
            continue
        if "control origination" in low:
            mode = "orig"
            continue
        m = re.match(r"^\s*☒\s+(.*)$", ln)
        if m and mode == "status":
            status.append(m.group(1).strip())
        if m and mode == "orig":
            orig.append(m.group(1).strip())
    return {"status": status, "origination": orig}


def _minify_evidence_snapshot(snapshot: dict[str, Any], *, max_list_items: int = 10) -> dict[str, Any]:
    """
    Reduce evidence payload size deterministically so prompts stay fast/stable.
    Keeps only the most useful high-level information for narrative writing.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    out: dict[str, Any] = {
        "control_id": snap.get("control_id"),
        "account_id": snap.get("account_id"),
        "ingestion_run_id": snap.get("ingestion_run_id"),
        "analysis": snap.get("analysis", {}),
    }

    tool_outputs = snap.get("tool_outputs")
    if not isinstance(tool_outputs, list):
        return out

    trimmed: list[dict[str, Any]] = []
    for item in tool_outputs[:25]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        result = item.get("result")
        err = item.get("error")
        if err and not result:
            trimmed.append({"name": name, "error": err})
            continue
        if not isinstance(result, dict):
            trimmed.append({"name": name, "result_type": type(result).__name__})
            continue

        # Trim common list fields inside results.
        res2: dict[str, Any] = {}
        for k, v in result.items():
            if isinstance(v, list):
                res2[k] = v[:max_list_items]
            else:
                res2[k] = v
        trimmed.append({"name": name, "result": res2})

    out["tool_outputs"] = trimmed
    return out


async def evaluate_narrative(
    *,
    template_skeleton: str,
    narrative_markdown: str,
    control_id: str,
    compliance_evaluation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    LLM-assisted structural evaluation. Returns JSON:
      { pass: bool, failures: [...], suggestions: [...] }
    """
    system_msg = (
        "FedRAMP Auditor Persona:\n"
        "- You are a strict FedRAMP assessor performing QA on SSP narrative output.\n"
        "- Be conservative and evidence-driven; prefer failing with clear reasons over passing ambiguous output.\n"
        "\n"
        "You are a strict FedRAMP SSP narrative QA checker.\n"
        "Return ONLY valid JSON with keys: pass (bool), failures (array), suggestions (array).\n"
        "Do NOT include prose outside JSON."
    )
    user_msg = (
        f"Control: {control_id}\n\n"
        f"Compliance evaluation (judge):\n{json.dumps(compliance_evaluation or {}, default=str)}\n\n"
        "Template skeleton (structure that must be preserved):\n"
        f"{template_skeleton}\n\n"
        "Candidate narrative:\n"
        f"{narrative_markdown}\n\n"
        "Rules:\n"
        "- Must preserve the skeleton structure and headings (do not delete major sections).\n"
        "- Must include sections: 'Control Summary Information' and 'What is the solution and how is it implemented?'\n"
        "- Must include checkbox lines and at least ONE ☒ under Implementation Status.\n"
        "- Must include at least ONE ☒ under Control Origination.\n"
        "- Must include a Dragon Implementation one-liner for every Part row (keep it short).\n"
        "- Inheritance MUST NOT be 'Not evidenced' (inheritance is a platform fact on AWS).\n"
        "- For AWS-hosted Dragon, Inheritance SHOULD use the boilerplate: 'Dragon partially inherits this control from the underlying AWS FedRAMP-authorized infrastructure.'\n"
        "- Control Origination MUST be consistent with the narrative (e.g., if you claim Shared responsibilities, Shared should be selected).\n"
        "- Must NOT contain the phrase 'Requirement (from template)' anywhere.\n"
        "- Must NOT contain '{{FILL_DRAGON_IMPLEMENTATION}}' anywhere (all placeholders must be replaced).\n"
        "- Implementation Status checkbox MUST be consistent with the narrative content and the judge status:\n"
        "  * If the narrative contains multiple 'Not evidenced' statements and/or critical gaps (e.g., CloudTrail=0, MFA=0), it MUST NOT be marked 'Implemented'.\n"
        "  * Prefer 'Partially implemented' in that case.\n"
        "- Must NOT mark 'Not applicable' unless the narrative includes a clear, scoped justification.\n"
        "- Must NOT contain vendor/program names like VGC/FTGS/FastTrack; use Dragon terminology.\n"
        "- Must NOT include meta sections like 'Critical Errors'/'Wins'/'Evaluation'.\n"
        "- If anything is missing, set pass=false and explain the exact missing items.\n"
    )
    raw = await llm_client.invoke_text(system_message=system_msg, user_message=user_msg, temperature=0.0)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("not_object")
        parsed.setdefault("pass", False)
        parsed.setdefault("failures", [])
        parsed.setdefault("suggestions", [])
        # Always enforce deterministic validation (LLM judge can be too lenient).
        det = _deterministic_validation_failures(narrative_markdown)
        if det:
            merged = list(parsed.get("failures") or []) + det
            # De-dupe while preserving order
            seen: set[str] = set()
            out_fail: list[str] = []
            for f in merged:
                fs = str(f)
                if fs in seen:
                    continue
                seen.add(fs)
                out_fail.append(fs)
            parsed["failures"] = out_fail
            parsed["pass"] = False
        return parsed
    except Exception:
        failures = _deterministic_validation_failures(narrative_markdown)
        return {"pass": len(failures) == 0, "failures": failures, "suggestions": []}


async def generate_narrative_from_template(
    *,
    control: dict[str, Any],
    template_markdown: str,
    evidence_snapshot: dict[str, Any],
    compliance_evaluation: dict[str, Any] | None = None,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """
    Returns { markdown, evaluation }.
    """
    control_id = _s(control.get("control_id") or "").strip()
    # Prefer LLM-friendly skeletonization of the template (removes vendor example narrative,
    # normalizes tables/line wraps, and produces a consistent structure).
    skeleton = await build_llm_friendly_skeleton(
        control_id=_s(control.get("control_id")).strip(),
        control_title=_s(control.get("title")).strip() or None,
        control_baseline=_s(control.get("baseline")).strip() or None,
        raw_template_markdown=template_markdown,
    )
    if not skeleton.strip():
        raise RuntimeError("Template skeletonization returned empty markdown")

    # Enforce "no code fences" even if the skeletonizer ignored instructions.
    skeleton = _strip_triple_backtick_fences(skeleton)
    # Fill inheritance auth placeholders from template (so placeholders never leak).
    auth_name, auth_date = _extract_inherited_auth_from_template(template_markdown)
    skeleton = _apply_inherited_auth_placeholders(skeleton, name=auth_name, date=auth_date)

    system_msg = (
        "FedRAMP Auditor Persona:\n"
        "- You are a FedRAMP assessor writing SSP control narratives.\n"
        "- Be evidence-driven, precise, and conservative: if evidence does not show it, say 'Not evidenced'.\n"
        "- Keep wording short and audit-ready (no marketing language, no speculation).\n"
        "- Replace any vendor/program names used as examples (e.g., VGC, FTGS, FastTrack, Virtual Government Cloud) with 'Dragon'.\n"
        "- Do NOT include meta critique sections (no 'Critical Errors', 'Wins', 'Evaluation'). Output only the completed narrative.\n"
        "\n"
        "You are writing a FedRAMP SSP narrative for the system named Dragon.\n"
        "You MUST output ONLY Markdown.\n"
        "CRITICAL:\n"
        "- Preserve ALL headings, checkbox lines, and tables from the provided skeleton.\n"
        "- Replace ALL placeholders of the form '{{FILL_...}}' with appropriate content.\n"
        "- For each Part row, replace '{{FILL_DRAGON_IMPLEMENTATION}}' with Dragon implementation text.\n"
        "- The Parts table column header MUST be 'Requirement' (NOT 'Requirement (from template)').\n"
        "- The Parts section is a Markdown table: DO NOT add raw newlines inside a cell. Use '<br><br>' for paragraphs.\n"
        "- Do not include the '|' character in table cell content (or escape it as '\\|').\n"
        "- Keep sentences wrapped with blank lines between sections (readable line breaks).\n"
        "- In the summary table checkbox blocks, change the appropriate boxes from ☐ to ☒.\n"
        "- Implementation Status checkbox selection MUST match the judge status and the narrative findings.\n"
        "  * Do NOT mark 'Implemented' if you write 'Not evidenced' for multiple parts or cite critical gaps (CloudTrail=0, MFA=0).\n"
        "  * Do NOT mark 'Not applicable' unless you explicitly justify out-of-scope applicability.\n"
        "- Fill the Parameter table 'Dragon Value' cells with SHORT one-liners (auditor style, <= 18 words).\n"
        "- In the Parts table:\n"
        "  * Dragon Implementation MUST be short (<= 2 sentences, <= 35 words) AND include one concrete evidence detail when available (counts/flags/tool result).\n"
        "  * Inheritance is NOT evidence-derived on AWS-hosted systems. It MUST NOT say 'Not evidenced'.\n"
        "  * For AWS-hosted Dragon, set the Inheritance cell in EVERY Part row to:\n"
        "    'Dragon partially inherits this control from the underlying AWS FedRAMP-authorized infrastructure.'\n"
        "    - You may append the authorization name/date shown in the template in parentheses.\n"
        "  * Customer Responsibility MUST be a short one-liner (<= 18 words).\n"
        "- Use the evidence snapshot to justify claims; if not demonstrated, say so clearly.\n"
        "- Do NOT introduce vendor names like VGC/FTGS unless they appear in evidence.\n"
        "- Ensure Control Origination aligns with your narrative (avoid contradictions).\n"
        "- Ensure Implementation Status selection aligns with the Judge status when possible.\n"
        "- For process controls (policies, emails, ticketing), do NOT use CloudTrail/CloudWatch absence as evidence of non-existence; say 'Not evidenced in snapshot; requires procedural artifacts'.\n"
    )

    # Attempt loop with feedback.
    feedback = ""
    last_md = ""
    last_eval: dict[str, Any] = {"pass": False, "failures": [], "suggestions": []}

    for attempt in range(max_attempts):
        evidence_min = _minify_evidence_snapshot(evidence_snapshot or {}, max_list_items=10)
        user_msg = (
            f"Skeleton (do not delete or reorder sections):\n{skeleton}\n\n"
            f"Compliance evaluation (judge):\n{json.dumps(compliance_evaluation or {}, default=str)}\n\n"
            f"Evidence snapshot (minified):\n{json.dumps(evidence_min, default=str)}\n\n"
        )
        if feedback:
            user_msg += f"Evaluator feedback to fix:\n{feedback}\n\n"
        user_msg += "Now return the completed narrative Markdown."

        md = await llm_client.invoke_text(system_message=system_msg, user_message=user_msg, temperature=0.1)
        last_md = md or ""
        last_eval = await evaluate_narrative(
            template_skeleton=skeleton,
            narrative_markdown=last_md,
            control_id=control_id,
            compliance_evaluation=compliance_evaluation,
        )
        if last_eval.get("pass") is True:
            break
        failures = last_eval.get("failures") or []
        suggestions = last_eval.get("suggestions") or []
        feedback = json.dumps({"failures": failures, "suggestions": suggestions}, indent=2)

    return {"markdown": last_md, "evaluation": last_eval}

