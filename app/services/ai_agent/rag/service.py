from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentChunk, DocumentStatus
from app.services.ai_agent.narrative import llm_client
from app.services.ai_agent.rag.chunking import chunk_pages
from app.services.ai_agent.rag.embeddings import embed_query, embed_texts
from app.services.ai_agent.rag.parsers import extract_text_pages


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _infer_doc_type_heuristic(*, title: str, filename: str, text: str) -> str:
    sample = " ".join([title or "", filename or "", text[:4000] or ""]).lower()

    if "system security plan" in sample or re.search(r"\bssp\b", sample):
        return "ssp"
    if any(tok in sample for tok in ["procedure", "procedures", "step 1", "step 2", "workflow", "process for"]):
        return "procedure"
    if any(
        tok in sample
        for tok in [
            "policy",
            "policies",
            "purpose",
            "scope",
            "responsibilities",
            "standards",
            "controls",
            "statement of policy",
        ]
    ):
        return "policy"
    return "other"


async def _infer_doc_type(*, title: str, filename: str, text: str) -> str:
    heuristic = _infer_doc_type_heuristic(title=title, filename=filename, text=text)

    system_message = (
        "Classify the uploaded security document into one of these types only: "
        "policy, procedure, ssp, other. "
        "Return ONLY valid JSON with a single key: doc_type."
    )
    user_message = (
        "Document title:\n"
        f"{title or filename or 'document'}\n\n"
        "Filename:\n"
        f"{filename or 'document'}\n\n"
        "Excerpt:\n"
        f"{text[:5000]}\n\n"
        "Return JSON like {\"doc_type\":\"policy\"}."
    )

    try:
        raw = await llm_client.invoke_text(
            system_message=system_message,
            user_message=user_message,
            temperature=0.1,
        )
        parsed = json.loads(raw)
        doc_type = str((parsed or {}).get("doc_type") or "").strip().lower()
        if doc_type in {"policy", "procedure", "ssp", "other"}:
            return doc_type
    except Exception:
        pass

    return heuristic


async def ingest_document(
    db: AsyncSession,
    *,
    title: str,
    filename: str,
    data: bytes,
    doc_type: str | None = None,
    account_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pages = extract_text_pages(filename=filename, data=data)
    full_text = "\n\n".join(p.text for p in pages if p.text).strip()
    if not full_text:
        raise ValueError("No extractable text found in uploaded document.")

    resolved_doc_type = (doc_type or "").strip().lower()
    if not resolved_doc_type or resolved_doc_type == "auto":
        resolved_doc_type = await _infer_doc_type(title=title, filename=filename, text=full_text)
    if resolved_doc_type not in {"policy", "procedure", "ssp", "other"}:
        resolved_doc_type = "other"

    content_sha256 = _sha256_text(full_text)

    # Basic dedupe: same hash + same account scope + same doc_type
    stmt = select(Document).where(
        Document.content_sha256 == content_sha256,
        Document.doc_type == resolved_doc_type,
    )
    if account_id is None:
        stmt = stmt.where(Document.account_id.is_(None))
    else:
        stmt = stmt.where(Document.account_id == account_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return {
            "document_id": str(existing.id),
            "deduped": True,
            "chunks_created": 0,
            "doc_type": existing.doc_type,
        }

    doc = Document(
        account_id=account_id,
        doc_type=resolved_doc_type,
        title=title or filename,
        source_path=filename,
        content_sha256=content_sha256,
        status=DocumentStatus.UPLOADED,
        doc_metadata=metadata or {},
    )
    db.add(doc)
    await db.flush()  # assign doc.id

    chunks = chunk_pages(pages)
    if not chunks:
        doc.status = DocumentStatus.FAILED
        await db.flush()
        raise ValueError("Failed to chunk extracted text.")

    doc.status = DocumentStatus.PARSED
    await db.flush()

    embeddings = await embed_texts([c.text for c in chunks])
    for c, emb in zip(chunks, embeddings):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                chunk_index=c.chunk_index,
                text=c.text,
                embedding=emb,
                page_start=c.page_start,
                page_end=c.page_end,
                section=None,
            )
        )

    doc.status = DocumentStatus.EMBEDDED
    await db.flush()
    return {
        "document_id": str(doc.id),
        "deduped": False,
        "chunks_created": len(chunks),
        "doc_type": doc.doc_type,
    }


async def search(
    db: AsyncSession,
    *,
    query: str,
    top_k: int = 8,
    account_id: str | None = None,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    if not query or not query.strip():
        return []

    q_emb = await embed_query(query)

    # Prefer DB-side vector search on Postgres/pgvector.
    dialect_name = ""
    try:
        bind = db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "") or ""
    except Exception:
        dialect_name = ""

    if dialect_name == "postgresql" and hasattr(DocumentChunk.embedding, "cosine_distance"):
        stmt = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.status == DocumentStatus.EMBEDDED)
        )
        if doc_type:
            stmt = stmt.where(Document.doc_type == doc_type)
        if account_id is None:
            stmt = stmt.where(Document.account_id.is_(None))
        else:
            stmt = stmt.where(Document.account_id == account_id)

        # Lowest cosine distance = most similar
        stmt = stmt.order_by(DocumentChunk.embedding.cosine_distance(q_emb)).limit(top_k)
        rows = (await db.execute(stmt)).all()
        out: list[dict[str, Any]] = []
        for ch, doc in rows:
            out.append(
                {
                    "document_id": str(doc.id),
                    "title": doc.title,
                    "doc_type": doc.doc_type,
                    "source_path": doc.source_path,
                    "chunk_id": str(ch.id),
                    "chunk_index": ch.chunk_index,
                    "page_start": ch.page_start,
                    "page_end": ch.page_end,
                    "text": ch.text,
                    "score": None,  # distance is DB-side; omit for now
                }
            )
        return out

    # Fallback: fetch and score in Python (SQLite/local dev).
    stmt = (
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.status == DocumentStatus.EMBEDDED)
        .order_by(Document.updated_at.desc())
        .limit(500)
    )
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if account_id is None:
        stmt = stmt.where(Document.account_id.is_(None))
    else:
        stmt = stmt.where(Document.account_id == account_id)

    rows = (await db.execute(stmt)).all()
    scored: list[tuple[float, DocumentChunk, Document]] = []
    for ch, doc in rows:
        emb = ch.embedding
        if isinstance(emb, list):
            score = _cosine_similarity(q_emb, emb)
        else:
            score = 0.0
        scored.append((score, ch, doc))

    scored.sort(key=lambda t: t[0], reverse=True)
    out: list[dict[str, Any]] = []
    for score, ch, doc in scored[:top_k]:
        out.append(
            {
                "document_id": str(doc.id),
                "title": doc.title,
                "doc_type": doc.doc_type,
                "source_path": doc.source_path,
                "chunk_id": str(ch.id),
                "chunk_index": ch.chunk_index,
                "page_start": ch.page_start,
                "page_end": ch.page_end,
                "text": ch.text,
                "score": score,
            }
        )
    return out

