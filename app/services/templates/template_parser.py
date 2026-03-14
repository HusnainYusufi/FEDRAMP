"""
Template parsing utilities.

Primary path (used by the app today):
- Parse a FedRAMP SSP template DOCX to Markdown and split it into per-control
  Markdown blocks stored on `fedramp_controls.template_markdown`.

Secondary path (used by tests + "template-first" workflows):
- Best-effort parsing of text/markdown into structured control blueprints while
  preserving a strict "Questions vs Answers" split:

  Zone A (Definition / Requirements):
    - Text BEFORE "Control Summary Information"
    - Source of truth for requirement text (parts a, b, c)

  Zone B (Implementation):
    - Text AFTER "What is the solution and how is it implemented?"
    - May contain filled narratives in an SSP; must NEVER overwrite Zone A
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings
from app.config.logging_config import get_logger
from app.models.template_blueprint import ControlBlueprint, ControlPart, Parameter, SummaryTable
from app.services.ai_agent.narrative import llm_client

logger = get_logger(__name__)

# Matches: AC-2, AC-2 (1), AU-2, SC-13, etc.
#
# IMPORTANT: do NOT use \b after the optional "(n)" group because the closing ')'
# is a non-word character and would break the word-boundary match. Use a lookahead
# for whitespace/end instead so enhancements are captured correctly.
_CONTROL_ID_RE = re.compile(r"^(?P<id>[A-Z]{2,3}-\d+(?:\s*\(\s*\d+\s*\))?)(?=\s|$)")

_PART_RE = re.compile(r"^\((?P<part>[a-z])\)\s+(?P<text>.+)$", re.IGNORECASE)

DEFAULT_INHERITANCE_TEXT = (
    "The system inherits applicable control implementations from underlying "
    "platform and shared services as defined by the organization's boundary. "
    "Inherited implementations are documented in the SSP inheritance matrix."
)

DEFAULT_CUSTOMER_RESPONSIBILITY = (
    "The customer is responsible for implementing any customer-managed portions "
    "of this control, including process controls and configuration within "
    "customer-owned accounts, as applicable."
)


def _normalize_control_id(cid: str) -> str:
    s = (cid or "").strip().upper()
    # Normalize spacing inside parentheses: "AC-2(1)" -> "AC-2 (1)"
    m = re.match(r"^(?P<base>[A-Z]{2,3}-\d+)\s*\(\s*(?P<num>\d+)\s*\)\s*$", s)
    if m:
        return f"{m.group('base')} ({m.group('num')})"
    return s


def _clean_extracted_text(text: str) -> str:
    """
    Some ingestion paths (or exported markdown) contain HTML table tags.
    Strip these so the line-based parser can operate on readable text.
    """
    t = (text or "")
    if "<" not in t or ">" not in t:
        return t
    # Preserve common line breaks before stripping tags.
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"</\s*(p|tr|div|li)\s*>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<\s*(p|tr|div|li)\b[^>]*>", "\n", t, flags=re.IGNORECASE)
    # Strip remaining tags.
    t = re.sub(r"<[^>]+>", " ", t)
    # Basic entity cleanup.
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    # Normalize whitespace.
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def _parameter_stable_id(control_id: str, suffix: str) -> str:
    base = _normalize_control_id(control_id).lower().replace(" ", "")
    base = base.replace("(", "-").replace(")", "")
    base = base.replace("--", "-")
    suffix = (suffix or "").strip().lower()
    suffix = re.sub(r"[^a-z0-9]+", "-", suffix).strip("-")
    return f"{base}-{suffix}" if suffix else base


def _split_csvish(value: str) -> list[str]:
    parts: list[str] = []
    for p in re.split(r"[;,]\s*|\s{2,}", (value or "").strip()):
        p = p.strip()
        if p:
            parts.append(p)
    return parts


@dataclass(frozen=True)
class ParseMeta:
    backend: str
    used_llamaparse: bool


def _load_docx_text(docx_path: str) -> tuple[str, ParseMeta]:
    """Return (text, meta) using a local DOCX reader."""
    p = Path(docx_path)
    if not p.exists():
        raise FileNotFoundError(f"DOCX not found: {docx_path}")

    try:
        try:
            from llama_index.readers.file import DocxReader  # type: ignore
        except Exception:  # pragma: no cover
            from llama_index.readers.file.docs import DocxReader  # type: ignore

        reader = DocxReader()
        docs = reader.load_data(file=Path(docx_path))
        text = "\n\n".join((d.text or "") for d in docs)
        return text, ParseMeta(backend="llamaindex-docxreader", used_llamaparse=False)
    except Exception as exc:
        detail = str(exc) or exc.__class__.__name__
        if "docx2txt" in detail:
            raise RuntimeError(
                "Failed to parse DOCX: missing dependency `docx2txt`. "
                "Install it with `pip install docx2txt` and restart the server."
            ) from exc
        raise RuntimeError(
            "Failed to parse DOCX. Ensure `llama-index-readers-file` and `docx2txt` "
            "are installed."
        ) from exc


def parse_docx_to_control_markdown(
    docx_path: str,
    *,
    tier: str = "agentic",
    version: str = "latest",
) -> tuple[dict[str, str], dict[str, Any]]:
    """
    Parse a FedRAMP template DOCX to Markdown using LlamaCloud parsing (LlamaParse),
    then split the Markdown into per-control blocks.

    Returns:
      (control_markdown_by_id, parse_meta)
    """
    api_key = (settings.llama_cloud_api_key or "").strip() or (os.getenv("LLAMA_CLOUD_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "LLAMA_CLOUD_API_KEY is not set (required for template markdown parsing). "
            "Set it in `.env` or your process environment."
        )

    try:
        from llama_cloud import LlamaCloud  # type: ignore
        from llama_cloud.types import parsing_create_params as p  # type: ignore
    except Exception as exc:
        raise RuntimeError("Failed to import llama-cloud SDK. Install `llama-cloud>=1.0`.") from exc

    client = LlamaCloud(api_key=api_key)

    # Configure Markdown output with tables preserved as Markdown tables.
    output_options = p.OutputOptions(
        markdown=p.OutputOptionsMarkdown(
            tables=p.OutputOptionsMarkdownTables(
                output_tables_as_markdown=True,
                merge_continued_tables=True,
                compact_markdown_tables=False,
            )
        )
    )

    with open(docx_path, "rb") as f:
        resp = client.parsing.parse(
            tier=tier,  # type: ignore[arg-type]
            version=version,  # type: ignore[arg-type]
            # llama-cloud v1.4 requires a non-empty expand list.
            # We request the full markdown payload for fidelity (tables, layout).
            expand=["markdown_full", "markdown", "metadata"],
            upload_file=f,
            output_options=output_options,
            timeout=float(settings.llama_cloud_parse_timeout_seconds or 600),
            verbose=False,
        )

    md = _extract_markdown_text(resp).strip()
    if not md:
        raise RuntimeError("LlamaCloud parsing returned empty markdown")

    controls = split_markdown_by_control(md)
    meta = {
        "backend": "llama-cloud-parsing",
        "tier": tier,
        "version": version,
        "controls": len(controls),
    }
    return controls, meta


def _extract_markdown_text(resp: Any) -> str:
    """
    llama-cloud response shapes vary by expand options and SDK version:
      - resp.markdown_full: Optional[str]
      - resp.markdown: Optional[Markdown] where Markdown.pages[].markdown is str

    This helper normalizes any supported response into a single markdown string.
    """
    if resp is None:
        return ""

    v = getattr(resp, "markdown_full", None)
    if isinstance(v, str) and v.strip():
        return v

    v2 = getattr(resp, "markdown", None)
    if isinstance(v2, str) and v2.strip():
        return v2

    # Handle the structured Markdown object: { pages: [ { success, markdown, ... }, ... ] }
    pages = getattr(v2, "pages", None)
    if isinstance(pages, list) and pages:
        parts: list[str] = []
        for p in pages:
            if not getattr(p, "success", True):
                continue
            m = getattr(p, "markdown", None)
            if isinstance(m, str) and m.strip():
                parts.append(m.strip())
        return "\n\n".join(parts).strip()

    # Last resort.
    try:
        return str(v2 or v or "")
    except Exception:
        return ""


def split_markdown_by_control(markdown: str) -> dict[str, str]:
    """
    Split a full-template Markdown string into per-control Markdown blocks.

    Strategy:
      - Prefer splitting on Markdown headings that start with a control id
        (e.g. '# AC-2 ...', '## AC-2 (1) ...'). This avoids false positives from
        table-of-contents lines like 'AC-2 (1) ... 65'.
      - If no such headings are found, fall back to a best-effort heuristic.
    """
    md_lines = (markdown or "").splitlines()
    if not md_lines:
        return {}

    # Pass 1: heading-based splitting (preferred).
    heading_starts: list[tuple[int, str]] = []
    heading_line_re = re.compile(r"^\s*#{1,6}\s+(?P<title>.+?)\s*$")
    for i, raw in enumerate(md_lines):
        m0 = heading_line_re.match(raw or "")
        if not m0:
            continue
        title = (m0.group("title") or "").strip()
        m = _CONTROL_ID_RE.match(title)
        if not m:
            continue
        heading_starts.append((i, _normalize_control_id(m.group("id"))))

    if heading_starts:
        blocks: dict[str, str] = {}
        for idx, (start_i, cid) in enumerate(heading_starts):
            end_i = heading_starts[idx + 1][0] if idx + 1 < len(heading_starts) else len(md_lines)
            chunk = "\n".join(md_lines[start_i:end_i]).strip()
            if not chunk:
                continue
            blocks[cid] = (blocks.get(cid, "").rstrip() + "\n\n" + chunk).strip() + "\n"
        return blocks

    # Pass 2: heuristic fallback (no headings found).
    # This is intentionally conservative: only start on lines that look like standalone
    # control identifiers (to avoid TOC-like content with trailing page numbers).
    blocks2: dict[str, list[str]] = {}
    current_id: str | None = None
    buf: list[str] = []

    def flush2() -> None:
        nonlocal current_id, buf
        if current_id and buf:
            blocks2[current_id] = (blocks2.get(current_id, []) + buf)
        current_id, buf = None, []

    for raw in md_lines:
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            if current_id is not None:
                buf.append(line)
            continue

        m = _CONTROL_ID_RE.match(stripped)
        if m:
            # Require the line to be "just a heading", not a TOC row with trailing page numbers.
            rest = stripped[m.end() :].strip()
            if rest and re.fullmatch(r"\d{1,4}", rest):
                # e.g. "AC-2 57" or "AC-2 (1) 65" -> likely TOC
                pass
            else:
                flush2()
                current_id = _normalize_control_id(m.group("id"))
                buf = [line]
                continue

        if current_id is not None:
            buf.append(line)

    flush2()

    out2: dict[str, str] = {}
    for cid, lines in blocks2.items():
        text = "\n".join(lines).strip()
        if text:
            out2[cid] = text + "\n"
    return out2


def _iter_control_blocks(text: str) -> list[tuple[str, str]]:
    """
    Return a list of (header_line, full_text) blocks for each control.

    CRITICAL: only start a new control on top-level headings (`#` or `##`) to avoid
    splitting on repeated sub-section headings like "### AC-2 Control Summary Information".
    """
    lines = (text or "").splitlines()
    if not lines:
        return []

    # Prefer heading-based splitting on ONLY level 1-2 headings.
    heading_line_re = re.compile(r"^\s*(?P<hashes>#{1,2})\s+(?P<title>.+?)\s*$")
    starts: list[tuple[int, str]] = []
    for i, raw in enumerate(lines):
        m0 = heading_line_re.match(raw or "")
        if not m0:
            continue
        title = (m0.group("title") or "").strip()
        m = _CONTROL_ID_RE.match(title)
        if not m:
            continue
        starts.append((i, title))

    blocks: list[tuple[str, str]] = []
    if starts:
        for idx, (start_i, header) in enumerate(starts):
            end_i = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
            chunk_lines = lines[start_i:end_i]
            chunk = "\n".join(chunk_lines).strip()
            if chunk:
                blocks.append((header.strip(), chunk))
        return blocks

    # Fallback: no markdown headings found. Start on standalone control-id lines.
    current_header: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal current_header, buf
        if current_header and buf:
            blocks.append((current_header, "\n".join(buf).strip()))
        current_header, buf = None, []

    for raw in lines:
        ln = (raw or "").rstrip("\n")
        s = ln.strip()
        if not s:
            if current_header is not None:
                buf.append("")
            continue
        m = _CONTROL_ID_RE.match(s)
        if m:
            # Avoid starting blocks on common sub-section labels.
            low = s.lower()
            if "control summary information" in low or "what is the solution" in low:
                if current_header is not None:
                    buf.append(ln)
                continue
            flush()
            current_header = s
            buf = [ln]
            continue
        if current_header is not None:
            buf.append(ln)
    flush()
    return blocks


def _parse_header(header_line: str) -> tuple[str, str]:
    """
    Parse a control header line into (control_id, title).

    Accepts:
      - "AC-2 Account Management"
      - "## AC-2 Account Management"
      - "AC-2 (1) ...", "## SC-7 (1) ..."
    """
    h = (header_line or "").strip()
    # Drop leading markdown hashes if present.
    h = re.sub(r"^\s*#{1,6}\s+", "", h).strip()
    m = _CONTROL_ID_RE.match(h)
    if not m:
        return _normalize_control_id(h), ""
    cid = _normalize_control_id(m.group("id"))
    rest = h[m.end() :].strip()
    rest = re.sub(r"^[\-\u2013\u2014:]+\s*", "", rest)
    return cid, rest.strip()


def _split_control_zones(full_text: str) -> tuple[str, str]:
    """
    Split a control block into:
      - definition_text (Zone A): before "Control Summary Information"
      - implementation_text (Zone B): from "What is the solution..." onward (preferred)
    """
    t = full_text or ""
    low = t.lower()
    idx_summary = low.find("control summary information")
    idx_solution = low.find("what is the solution")

    # Zone A ends at the summary table start when present.
    if idx_summary >= 0:
        definition = t[:idx_summary]
    elif idx_solution >= 0:
        definition = t[:idx_solution]
    else:
        definition = t

    # Zone B should begin at "What is the solution..." when present.
    if idx_solution >= 0:
        implementation = t[idx_solution:]
    elif idx_summary >= 0:
        implementation = t[idx_summary:]
    else:
        implementation = ""

    return definition.strip(), implementation.strip()


def _extract_requirements(definition_text: str) -> list[tuple[str, str, str | None]]:
    """
    Extract (part_id, requirement_text, parameter_placeholder) from Zone A.

    Requirement markers are lettered parts:
      (a) ...
      (b) ...
    """
    lines = (definition_text or "").splitlines()
    out: list[tuple[str, str, str | None]] = []
    current: tuple[str, list[str]] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        pid, text_lines = current
        raw = " ".join(x.strip() for x in text_lines if x.strip()).strip()
        if not raw:
            current = None
            return
        placeholder = None
        # Prefer assignment-style bracket parameters.
        bracket_matches = list(re.finditer(r"\[[^\]]+\]", raw))
        if bracket_matches:
            picked = None
            for m in bracket_matches:
                inner = m.group(0)
                if re.search(r"\bassignment\b", inner, re.IGNORECASE) or re.search(
                    r"\bfedramp assignment\b", inner, re.IGNORECASE
                ):
                    picked = inner
                    break
            placeholder = picked or bracket_matches[0].group(0)
        if placeholder:
            cleaned = raw.replace(placeholder, " ")
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
        else:
            cleaned = raw
        out.append((pid, cleaned, placeholder))
        current = None

    for raw in lines:
        s = (raw or "").strip()
        if not s:
            continue
        if "control summary information" in s.lower():
            break
        m = _PART_RE.match(s)
        if m:
            flush()
            pid = m.group("part").lower()
            current = (pid, [m.group("text").strip()])
            continue
        if current:
            current[1].append(s)
    flush()
    return out


def _extract_implementations(implementation_text: str) -> dict[str, str]:
    """
    Extract filled implementation text per part from Zone B.

    We support both:
      - "(a) ..." markers
      - "Part a ..." markers
    """
    lines = (implementation_text or "").splitlines()
    by_part: dict[str, str] = {}

    current: tuple[str, list[str]] | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        pid, seg = current
        text = " ".join(x.strip() for x in seg if x.strip()).strip()
        if text:
            by_part[pid] = text
        current = None

    # Pass 1: (a) markers.
    for raw in lines:
        s = (raw or "").strip()
        if not s:
            continue
        m = _PART_RE.match(s)
        if m:
            flush()
            pid = m.group("part").lower()
            current = (pid, [m.group("text").strip()])
            continue
        if current:
            current[1].append(s)
    flush()
    if by_part:
        return by_part

    # Pass 2: "Part a" segmentation over the whole implementation section.
    joined = "\n".join((ln or "").strip() for ln in lines if (ln or "").strip())
    matches = list(re.finditer(r"\bpart\s+(?P<part>[a-z])\b", joined, re.IGNORECASE))
    if not matches:
        return {}
    for i, m in enumerate(matches):
        pid = m.group("part").lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(joined)
        seg = joined[start:end].strip()
        seg = re.sub(r"^[\s:\-\)\.]+", "", seg).strip()
        if seg:
            by_part[pid] = seg
    return by_part


def _extract_summary_table(text: str) -> SummaryTable:
    """
    Best-effort parsing of the "Control Summary Information" section.
    """
    joined = text or ""
    idx = joined.lower().find("control summary information")
    if idx < 0:
        return SummaryTable()

    tail = joined[idx : idx + 6000]
    tail_lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]

    responsible_role: str | None = None
    implementation_status: list[str] | None = None
    origination: list[str] | None = None
    parameters: list[Parameter] = []

    def _value_after_colon(ln: str) -> str | None:
        if ":" not in ln:
            return None
        val = ln.split(":", 1)[1]
        if "|" in val:
            val = val.split("|", 1)[0]
        val = val.strip()
        return val or None

    for ln in tail_lines[:250]:
        low = ln.lower()
        if "responsible role" in low:
            val = _value_after_colon(ln)
            if val:
                responsible_role = val

        if "implementation status" in low:
            val = _value_after_colon(ln)
            if val:
                implementation_status = _split_csvish(val)

        if "origination" in low:
            val = _value_after_colon(ln)
            if val:
                origination = _split_csvish(val)

    for ln in tail_lines[:200]:
        # Common formats:
        # - "Parameter (a): organization-defined frequency"
        # - "Parameter AC-2(a): organization-defined frequency"
        m = re.search(
            r"parameter\s+(?:[A-Z]{2,3}-\d+)?\s*\((?P<id>[a-z0-9]+)\)\s*[:\-]\s*(?P<txt>.+)$",
            ln,
            re.IGNORECASE,
        )
        if m:
            pid = m.group("id").strip()
            txt = m.group("txt").strip()
            parameters.append(Parameter(id=pid, text=txt, value=None, assignment=None))
            continue

    return SummaryTable(
        responsible_role=responsible_role,
        parameters=parameters,
        implementation_status=implementation_status,
        origination=origination,
    )


def parse_text_to_blueprints(text: str, template_tag: str | None = None) -> dict[str, ControlBlueprint]:
    """
    Parse plain text/markdown into structured control blueprints.

    CRITICAL behavior:
    - Requirement text is extracted ONLY from Zone A (definition). If the input
      document is a filled SSP, any vendor implementation content must NOT
      overwrite the requirement text.
    """
    t = _clean_extracted_text(text)
    blocks = _iter_control_blocks(t)
    out: dict[str, ControlBlueprint] = {}

    for header, full in blocks:
        control_id, title = _parse_header(header)
        if not control_id:
            continue

        definition, implementation = _split_control_zones(full)
        reqs = _extract_requirements(definition)
        impls = _extract_implementations(implementation)

        summary = _extract_summary_table(full)

        # Normalize summary parameters and link Zone A placeholders.
        normalized_params: list[Parameter] = []
        params_by_letter: dict[str, Parameter] = {}
        for p in list(summary.parameters or []):
            raw_id = (p.id or "").strip()
            stable = _parameter_stable_id(control_id, raw_id)
            np = Parameter(id=stable, text=p.text, value=None, assignment=p.assignment)
            normalized_params.append(np)
            if len(raw_id) == 1 and raw_id.isalnum():
                params_by_letter[raw_id.lower()] = np

        # Add parameter definitions found inside Zone A requirements (bracket placeholders),
        # ensuring they link to the summary table entry when possible.
        for pid, _req_text, placeholder in reqs:
            if not placeholder:
                continue
            if pid in params_by_letter:
                continue
            stable = _parameter_stable_id(control_id, pid)
            normalized_params.append(
                Parameter(
                    id=stable,
                    text=placeholder.strip("[]").strip(),
                    value=None,
                    assignment=None,
                )
            )
            params_by_letter[pid] = normalized_params[-1]

        summary.parameters = normalized_params

        parts: list[ControlPart] = []
        if reqs:
            for pid, req_text, placeholder in reqs:
                parts.append(
                    ControlPart(
                        id=pid,
                        requirement_text=req_text,
                        parameter_placeholder=placeholder,
                        dragon_implementation=(impls.get(pid) or None),
                        inheritance_text=DEFAULT_INHERITANCE_TEXT,
                        customer_responsibility=DEFAULT_CUSTOMER_RESPONSIBILITY,
                    )
                )
        else:
            # If no explicit (a)/(b) parts were found in Zone A, treat the definition
            # text itself as the requirement.
            fallback_req = re.sub(r"\s+", " ", (definition or "").strip())
            if fallback_req:
                parts = [
                    ControlPart(
                        id="main",
                        requirement_text=fallback_req[:2000],
                        parameter_placeholder=None,
                        dragon_implementation=None,
                        inheritance_text=DEFAULT_INHERITANCE_TEXT,
                        customer_responsibility=DEFAULT_CUSTOMER_RESPONSIBILITY,
                    )
                ]

        bp = ControlBlueprint(
            control_id=control_id,
            title=(title or control_id).strip(),
            summary_table=summary,
            parts=parts,
        )
        out[control_id] = bp

    logger.info("template_blueprints_parsed", controls=len(out), template_tag=template_tag)
    return out


def parse_docx_to_blueprints(
    docx_path: str, template_tag: str | None = None
) -> tuple[dict[str, ControlBlueprint], dict[str, Any]]:
    """
    Parse a DOCX to control blueprints.

    This is best-effort and prioritizes preserving Zone A requirements even when
    the DOCX is a filled SSP.
    """
    text, meta = _load_docx_text(docx_path)
    blueprints = parse_text_to_blueprints(text=text, template_tag=template_tag)
    return (
        blueprints,
        {
            "backend": meta.backend,
            "used_llamaparse": meta.used_llamaparse,
            "template_tag": template_tag,
        },
    )


async def build_llm_friendly_skeleton(
    *,
    control_id: str,
    control_title: str | None,
    control_baseline: str | None,
    raw_template_markdown: str,
) -> str:
    """
    Use an LLM to convert the raw template markdown (often containing HTML tables,
    vendor examples, and awkward line wraps) into a clean, *LLM-friendly* skeleton
    markdown that we can reliably fill during generation.

    Output MUST be Markdown (no code fences) and MUST include placeholders:
      - {{FILL_PARAM_<ID>}} for parameters
      - {{FILL_DRAGON_IMPLEMENTATION}} in each Part row
      - {{FILL_INHERITED_AUTH_NAME}}, {{FILL_INHERITED_AUTH_DATE}} if inheritance option present
    """
    cid = (control_id or "").strip()
    title = (control_title or "").strip()
    baseline = (control_baseline or "").strip()
    header = f"{cid} {title}".strip() if (cid or title) else "Control"
    if baseline:
        header = f"{header} - {baseline.title()}"

    system_msg = (
        "FedRAMP Auditor Persona:\n"
        "- You are a FedRAMP assessor preparing an SSP template skeleton for evidence-based completion.\n"
        "- Be precise and conservative: do not invent content.\n"
        "\n"
        "You are converting a FedRAMP SSP control template into a clean Markdown skeleton.\n"
        "Return ONLY Markdown. Do not wrap content in code fences.\n"
        "Target system name is Dragon.\n"
        "- Replace any vendor/program names used as examples (e.g., VGC, FTGS, FastTrack, Virtual Government Cloud) with 'Dragon' when they appear in roles/labels.\n"
        "- Do not alter the regulatory requirement text.\n"
        "Do not include any vendor/system names (e.g., VGC/FTGS) unless they are part of the control requirement.\n"
        "Do not invent checkbox options, parameters, or roles. Use ONLY what appears in the raw template.\n"
    )

    user_msg = (
        f"Control header: {header}\n\n"
        "Raw template markdown for this control:\n"
        f"{raw_template_markdown}\n\n"
        "Produce a clean skeleton with EXACT sections in this order:\n"
        "1) '## <header>'\n"
        "2) '### FedRAMP Template Control Text' followed by the requirement text as normal Markdown paragraphs/lists.\n"
        "   - If the requirement uses (a)(b)(c) lettered items, ensure they are sequential (fix duplicate letters).\n"
        "   - If any item includes sub-items (e.g., 1/2/3), format them on separate lines (not all on one line).\n"
        "3) '### <CONTROL_ID> Control Summary Information' with:\n"
        "   - 'Responsible Role: ...' (single line)\n"
        "   - A Markdown table with columns: Parameter | Prompt | Dragon Value\n"
        "     * The Parameter column MUST preserve the exact parameter ID from the raw template (e.g., 'AC-2(c)', 'AC-2(d)(3)', 'AC-2(h)(1)').\n"
        "     * Dragon Value MUST be a placeholder using the normalized ID format: {{FILL_PARAM_<ID>}} where <ID> uppercases and replaces non-alphanumerics with underscores.\n"
        "       Examples:\n"
        "         - AC-2(c) -> {{FILL_PARAM_AC_2_C}}\n"
        "         - AC-2(d)(3) -> {{FILL_PARAM_AC_2_D_3}}\n"
        "         - AC-2(h)(1) -> {{FILL_PARAM_AC_2_H_1}}\n"
        "     * Prompt should come from bracketed Assignment/FedRAMP Assignment text where possible.\n"
        "     * Do NOT copy template example values into Dragon Value; Dragon Value must be a placeholder.\n"
        "   - 'Implementation Status (check all that apply):' followed by ☒/☐ lines (unicode boxes).\n"
        "     * Copy the checkbox option labels exactly as in the raw template.\n"
        "     * ALL boxes MUST be unchecked (☐) in the skeleton.\n"
        "   - 'Control Origination (check all that apply):' followed by ☒/☐ lines.\n"
        "     * Copy the checkbox option labels exactly as in the raw template.\n"
        "     * ALL boxes MUST be unchecked (☐) in the skeleton.\n"
        "     * If there is an inheritance option line, keep it and use placeholders {{FILL_INHERITED_AUTH_NAME}} and {{FILL_INHERITED_AUTH_DATE}} for the authorization.\n"
        "4) '### <CONTROL_ID> What is the solution and how is it implemented?'\n"
        "   - A Markdown table with columns EXACTLY: Part | Requirement | Dragon Implementation | Inheritance | Customer Responsibility\n"
        "   - One row per Part letter (a, b, c...) derived from the control text.\n"
        "   - Dragon Implementation cell MUST be exactly '{{FILL_DRAGON_IMPLEMENTATION}}' for each row.\n"
        "   - Inheritance cell MUST be the placeholder {{FILL_INHERITANCE_BOILERPLATE}}.\n"
        "   - Customer Responsibility cell MUST be the placeholder {{FILL_CUSTOMER_RESP_ONE_LINER}}.\n"
        "\nRules:\n"
        "- Keep wording short and auditor-style.\n"
        "- Preserve the actual control requirement text, but fix broken line wraps.\n"
        "- Do NOT include any long narrative examples from the raw template.\n"
        "- Do NOT use code fences.\n"
        "- The skeleton MUST NOT include any filled Dragon values; those must be placeholders.\n"
        "- The skeleton MUST NOT contain the phrase 'Requirement (from template)'. Use 'Requirement' only.\n"
    )

    md = await llm_client.invoke_text(system_message=system_msg, user_message=user_msg, temperature=0.0)
    return (md or "").strip() + "\n"

