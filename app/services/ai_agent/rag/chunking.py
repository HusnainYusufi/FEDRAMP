from __future__ import annotations

from dataclasses import dataclass

from app.services.ai_agent.rag.parsers import PageText


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    text: str
    page_start: int
    page_end: int


def chunk_pages(
    pages: list[PageText],
    *,
    max_chars: int = 1400,
    overlap_chars: int = 150,
) -> list[Chunk]:
    """
    Chunk pages into roughly paragraph-sized chunks with a soft max_chars cap.

    We prioritize auditor-friendly citations:
    - keep paragraph boundaries when possible
    - store page_start/page_end for each chunk
    """
    out: list[Chunk] = []
    buf: str = ""
    buf_page_start: int | None = None
    buf_page_end: int | None = None

    def _flush() -> None:
        nonlocal buf, buf_page_start, buf_page_end
        t = (buf or "").strip()
        if t and buf_page_start is not None and buf_page_end is not None:
            out.append(
                Chunk(
                    chunk_index=len(out),
                    text=t,
                    page_start=buf_page_start,
                    page_end=buf_page_end,
                )
            )
        buf = ""
        buf_page_start = None
        buf_page_end = None

    for p in pages:
        page_txt = (p.text or "").replace("\r", "").strip()
        if not page_txt:
            continue

        # Split by blank lines into paragraphs.
        paras = [s.strip() for s in page_txt.split("\n\n") if s.strip()]
        for para in paras:
            if not buf:
                buf = para
                buf_page_start = p.page
                buf_page_end = p.page
                continue

            candidate = buf + "\n\n" + para
            if len(candidate) <= max_chars:
                buf = candidate
                buf_page_end = p.page
                continue

            # Flush current buffer and start a new chunk with overlap.
            _flush()
            if overlap_chars > 0 and len(para) > overlap_chars:
                # Keep a short prefix to preserve context for embedding.
                buf = para[: max_chars]
            else:
                buf = para
            buf_page_start = p.page
            buf_page_end = p.page

    _flush()
    return out

