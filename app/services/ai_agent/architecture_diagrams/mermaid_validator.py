from __future__ import annotations

import re


_FLOWCHART_RE = re.compile(r"^\s*flowchart\s+(LR|RL|TB|BT|TD)\s*$", re.IGNORECASE)
_SUBGRAPH_RE = re.compile(r"^\s*subgraph\b", re.IGNORECASE)
_END_RE = re.compile(r"^\s*end\s*$", re.IGNORECASE)

# Mermaid is permissive, but we enforce safe identifiers to avoid parse edge cases.
_SAFE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def validate_mermaid_flowchart(code: str) -> tuple[bool, str | None]:
    """
    Lightweight Mermaid flowchart validator.

    This is not a full Mermaid parser. It catches the common causes of Mermaid 10
    "Syntax error in text" for our generated diagrams:
    - Missing/invalid `flowchart <dir>` header
    - Unbalanced `subgraph`/`end`
    - Unbalanced quotes/brackets in node declarations
    - Unsafe node IDs in edge statements (enforce [A-Za-z][A-Za-z0-9_]*)
    """
    text = (code or "").strip()
    if not text:
        return False, "empty_mermaid"

    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False, "empty_mermaid"

    if not _FLOWCHART_RE.match(lines[0]):
        return False, "missing_or_invalid_flowchart_header"

    # Track subgraph nesting
    depth = 0
    for idx, ln in enumerate(lines[1:], start=2):
        if _SUBGRAPH_RE.match(ln):
            depth += 1
        elif _END_RE.match(ln):
            depth -= 1
            if depth < 0:
                return False, f"unbalanced_end_at_line_{idx}"

        # Check for unbalanced brackets/quotes on the line (common failure point)
        if ln.count('"') % 2 != 0:
            return False, f"unbalanced_quotes_at_line_{idx}"
        if _count_unescaped(ln, "[") != _count_unescaped(ln, "]"):
            return False, f"unbalanced_brackets_at_line_{idx}"
        if _count_unescaped(ln, "(") != _count_unescaped(ln, ")"):
            return False, f"unbalanced_parens_at_line_{idx}"
        if _count_unescaped(ln, "{") != _count_unescaped(ln, "}"):
            return False, f"unbalanced_braces_at_line_{idx}"

        # Validate IDs in edge statements (best-effort)
        if "-->" in ln or "-.->" in ln or "==>" in ln:
            ids = _extract_edge_ids(ln)
            for node_id in ids:
                if node_id and not _SAFE_ID_RE.match(node_id):
                    return False, f"unsafe_node_id_{node_id}_at_line_{idx}"
                # Mermaid grammar can treat `end` as a reserved token in ways that break parsing
                # when node IDs start with "end" (e.g., "endIDP --> ...").
                if node_id and node_id.lower().startswith("end"):
                    return False, f"node_id_starts_with_reserved_end_{node_id}_at_line_{idx}"

    if depth != 0:
        return False, "unbalanced_subgraph_end"

    return True, None


def _count_unescaped(s: str, ch: str) -> int:
    # Mermaid doesn't really have escape semantics like this, but this avoids
    # miscounting in simple cases like \" within labels.
    count = 0
    escaped = False
    for c in s:
        if escaped:
            escaped = False
            continue
        if c == "\\":
            escaped = True
            continue
        if c == ch:
            count += 1
    return count


def _extract_edge_ids(line: str) -> list[str]:
    """
    Extract likely node IDs from a Mermaid edge statement.
    Handles forms like:
      A --> B
      A -->|label| B
      A["x"] --> B["y"]
    """
    # Remove label pipes
    cleaned = re.sub(r"\|.*?\|", " ", line)
    # Strip node shape declarations: A["..."] -> A
    cleaned = re.sub(r'([A-Za-z][A-Za-z0-9_]*)\s*\[[^\]]*\]', r"\1", cleaned)
    cleaned = re.sub(r'([A-Za-z][A-Za-z0-9_]*)\s*\([^)]*\)', r"\1", cleaned)
    cleaned = re.sub(r'([A-Za-z][A-Za-z0-9_]*)\s*\{[^}]*\}', r"\1", cleaned)
    # Find first two ids around arrow
    m = re.search(r"([A-Za-z][A-Za-z0-9_]*)\s*(?:-->|-\.->|==>)\s*([A-Za-z][A-Za-z0-9_]*)", cleaned)
    if not m:
        return []
    return [m.group(1), m.group(2)]

