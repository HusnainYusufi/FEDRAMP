import json

import pytest

from app.api.ai_agent import routes as ai_agent_routes
from app.db.models import Document, DocumentChunk, DocumentStatus, FedRAMPControl
from app.db.session import async_session_factory
from app.services.ai_agent.rag import service as rag_service


async def _seed_control(*, control_id: str, title: str, family: str, nist_description: str) -> None:
    async with async_session_factory() as session:
        existing = await session.get(FedRAMPControl, control_id)
        if existing is None:
            session.add(
                FedRAMPControl(
                    control_id=control_id,
                    title=title,
                    family=family,
                    nist_description=nist_description,
                    guidance="",
                    baseline="HIGH",
                )
            )
        await session.commit()


async def _seed_document_with_chunks(*, title: str, doc_type: str, chunk_texts: list[str]) -> str:
    async with async_session_factory() as session:
        doc = Document(
            title=title,
            doc_type=doc_type,
            source_path=title,
            content_sha256=f"sha-{title}",
            status=DocumentStatus.EMBEDDED,
            doc_metadata={},
        )
        session.add(doc)
        await session.flush()

        for idx, text in enumerate(chunk_texts):
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=idx,
                    text=text,
                    embedding=[],
                    page_start=idx + 1,
                    page_end=idx + 1,
                    section=None,
                )
            )

        await session.commit()
        return str(doc.id)


@pytest.mark.anyio
async def test_docs_map_controls_from_text(client, monkeypatch):
    await _seed_control(
        control_id="IA-2",
        title="Identification and Authentication",
        family="IDENTIFICATION AND AUTHENTICATION",
        nist_description="Identify users and enforce multifactor authentication for privileged access.",
    )
    await _seed_control(
        control_id="AC-2",
        title="Account Management",
        family="ACCESS CONTROL",
        nist_description="Manage user accounts, provisioning, and account lifecycle.",
    )

    async def fake_invoke_text(**kwargs):
        return json.dumps(
            {
                "summary": "The text most strongly aligns to authentication controls.",
                "controls": [
                    {
                        "control_id": "IA-2",
                        "reason": "The document discusses multifactor authentication for user access.",
                        "confidence": 0.94,
                        "evidence_ids": [1],
                    }
                ],
            }
        )

    monkeypatch.setattr(ai_agent_routes.llm_client, "invoke_text", fake_invoke_text)

    res = await client.post(
        "/ai/docs/map-controls",
        json={
            "text": "Administrators must authenticate with multifactor authentication before accessing the management console.",
            "max_controls": 3,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["summary"] == "The text most strongly aligns to authentication controls."
    assert data["document"] is None
    assert len(data["controls"]) == 1
    assert data["controls"][0]["control_id"] == "IA-2"
    assert data["controls"][0]["title"] == "Identification and Authentication"
    assert data["controls"][0]["evidence"][0]["id"] == 1


@pytest.mark.anyio
async def test_docs_map_controls_from_document_id_fallback(client, monkeypatch):
    await _seed_control(
        control_id="AC-8",
        title="System Use Notification",
        family="ACCESS CONTROL",
        nist_description="Display an approved system use banner before granting system access.",
    )
    await _seed_control(
        control_id="IA-2",
        title="Identification and Authentication",
        family="IDENTIFICATION AND AUTHENTICATION",
        nist_description="Identify users and enforce multifactor authentication.",
    )

    document_id = await _seed_document_with_chunks(
        title="Customer Access Procedure",
        doc_type="procedure",
        chunk_texts=[
            (
                "AC-8 System Use Notification: The platform displays an approved warning banner "
                "before users can continue to the sign-in screen."
            ),
            "Users are reminded that unauthorized use is prohibited and monitored.",
        ],
    )

    async def failing_invoke_text(**kwargs):
        raise RuntimeError("openai unavailable")

    monkeypatch.setattr(ai_agent_routes.llm_client, "invoke_text", failing_invoke_text)

    res = await client.post(
        "/ai/docs/map-controls",
        json={"document_id": document_id, "max_controls": 1},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["document"]["document_id"] == document_id
    assert data["controls"][0]["control_id"] == "AC-8"
    assert "explicitly references AC-8" in data["controls"][0]["reason"]
    assert "warning banner" in data["controls"][0]["evidence"][0]["text"]


@pytest.mark.anyio
async def test_docs_map_controls_requires_text_or_document_id(client):
    res = await client.post("/ai/docs/map-controls", json={})
    assert res.status_code == 400
    assert res.json()["detail"] == "Provide either text or document_id."


@pytest.mark.anyio
async def test_docs_ingest_auto_detects_doc_type(client, monkeypatch):
    async def fake_embed_texts(texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def fake_invoke_text(**kwargs):
        return json.dumps({"doc_type": "ssp"})

    monkeypatch.setattr(rag_service, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(rag_service.llm_client, "invoke_text", fake_invoke_text)

    res = await client.post(
        "/ai/docs/ingest",
        files={
            "file": (
                "sample.md",
                b"# System Security Plan\n\nThis SSP describes system boundaries and inherited controls.\n",
                "text/markdown",
            )
        },
        data={"title": "FastTrack SSP"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["doc_type"] == "ssp"
    assert data["document_id"]
