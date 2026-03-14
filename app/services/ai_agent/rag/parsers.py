from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass

import docx2txt
from pypdf import PdfReader


@dataclass(frozen=True)
class PageText:
    page: int
    text: str


def _decode_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except Exception:
        return data.decode("utf-8", errors="ignore")


def extract_text_pages(*, filename: str, data: bytes) -> list[PageText]:
    """
    Extract text as pages (page numbering starts at 1).

    - PDF: one PageText per PDF page
    - DOCX/TXT/MD: single PageText(page=1)
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        pages: list[PageText] = []
        for idx, p in enumerate(reader.pages):
            # p.extract_text() can return None
            text = (p.extract_text() or "").strip()
            pages.append(PageText(page=idx + 1, text=text))
        return pages

    if name.endswith(".docx"):
        # docx2txt expects a path
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=True) as tmp:
            tmp.write(data)
            tmp.flush()
            text = (docx2txt.process(tmp.name) or "").strip()
        return [PageText(page=1, text=text)]

    # md/txt/other
    return [PageText(page=1, text=_decode_bytes(data).strip())]

