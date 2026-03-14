import pytest
from sqlalchemy import select

from app.api.ai_agent import routes as ai_agent_routes
from app.db.models import ArchitectureDiagram
from app.db.session import async_session_factory
from app.services.ai_agent.architecture_diagrams.models import InfraSpec


def _install_diagram_stubs(monkeypatch, *, render_result=None):
    async def fake_build_architecture_evidence(**kwargs):
        return {
            "account_id": kwargs["account_id"],
            "ingestion_run_id": "00000000-0000-0000-0000-000000000001",
            "counts": {},
            "resources": {},
            "notes": {},
        }

    async def fake_summarize_architecture_context(*, evidence_json):
        return {
            "title": "AWS Authorization Boundary Diagram",
            "account_id": evidence_json["account_id"],
        }

    def fake_build_infra_spec_from_evidence(*, evidence_json, context_summary):
        return InfraSpec(
            account_id=evidence_json["account_id"],
            ingestion_run_id=evidence_json.get("ingestion_run_id"),
            title=context_summary.get("title") or "AWS Authorization Boundary Diagram",
            boundary_label="FedRAMP Authorization Boundary",
            context_summary=context_summary,
            evidence=evidence_json,
        )

    async def fake_generate_mermaid_with_feedback(**kwargs):
        return {
            "mermaid_code": 'flowchart LR\nA["Client"] --> B["Application"]\n',
            "mermaid_prompt": "stub-mermaid-prompt",
            "evaluation": {"score": 91, "must_fix": [], "suggestions": []},
            "attempts": 1,
            "used_fallback": False,
        }

    async def fake_render_svg_from_mermaid_with_feedback(**kwargs):
        return render_result or {
            "svg_markup": '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="#ffffff"/></svg>',
            "evaluation": {"score": 88, "must_fix": [], "suggestions": []},
            "attempts": 1,
            "renderer_version": "mermaid_svg_ai_v1",
            "render_error": None,
        }

    monkeypatch.setattr(ai_agent_routes, "build_architecture_evidence", fake_build_architecture_evidence)
    monkeypatch.setattr(ai_agent_routes, "summarize_architecture_context", fake_summarize_architecture_context)
    monkeypatch.setattr(ai_agent_routes, "build_infra_spec_from_evidence", fake_build_infra_spec_from_evidence)
    monkeypatch.setattr(ai_agent_routes, "generate_mermaid_with_feedback", fake_generate_mermaid_with_feedback)
    monkeypatch.setattr(
        ai_agent_routes,
        "render_svg_from_mermaid_with_feedback",
        fake_render_svg_from_mermaid_with_feedback,
    )


@pytest.mark.anyio
async def test_architecture_diagram_prompts_validation_error(client):
    """POST /ai/architecture-diagrams/prompts rejects invalid account_id."""
    res = await client.post(
        "/ai/architecture-diagrams/prompts",
        json={"account_id": "short"},
    )
    assert res.status_code == 422


@pytest.mark.anyio
async def test_architecture_diagram_prompts_empty_db_ok(client):
    """
    With an empty DB, evidence lists should be empty but the endpoint should still return prompts.
    """
    res = await client.post(
        "/ai/architecture-diagrams/prompts",
        json={"account_id": "123456789012"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["account_id"] == "123456789012"
    assert "evidence_json" in data
    assert "summarizer_prompt" in data
    assert "system_message" in data["summarizer_prompt"]
    assert "user_message" in data["summarizer_prompt"]


@pytest.mark.anyio
async def test_architecture_diagram_generate_svg_empty_db_ok(client, monkeypatch):
    _install_diagram_stubs(monkeypatch)
    res = await client.post(
        "/ai/architecture-diagrams/generate",
        json={"account_id": "123456789012", "persist": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["account_id"] == "123456789012"
    assert data["svg_markup"].startswith("<svg")
    assert data["mermaid_code"].startswith("flowchart LR")
    assert "diagram_spec" in data
    assert isinstance(data["evaluation"], dict)


@pytest.mark.anyio
async def test_architecture_diagram_generate_persists_mermaid_and_svg(client, monkeypatch):
    _install_diagram_stubs(monkeypatch)
    created = await client.post(
        "/ai/architecture-diagrams/generate",
        json={"account_id": "123456789012", "persist": True},
    )
    assert created.status_code == 200

    async with async_session_factory() as session:
        result = await session.execute(select(ArchitectureDiagram))
        diagrams = result.scalars().all()
        assert diagrams
        diagram = diagrams[-1]
        assert (diagram.mermaid_code or "").startswith("flowchart LR")
        assert diagram.mermaid_prompt == "stub-mermaid-prompt"
        assert diagram.model_mermaid
        assert diagram.mermaid_score == 91
        assert diagram.mermaid_iterations == 1
        assert (diagram.svg_markup or "").startswith("<svg")


@pytest.mark.anyio
async def test_architecture_diagram_generate_svg_render_fallback(client, monkeypatch):
    _install_diagram_stubs(
        monkeypatch,
        render_result={
            "svg_markup": '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><text x="10" y="25">fallback</text></svg>',
            "evaluation": {
                "score": 0,
                "must_fix": ["Mermaid rendering failed; returned deterministic fallback SVG."],
                "suggestions": [],
            },
            "attempts": 2,
            "renderer_version": "mermaid_svg_ai_v1",
            "render_error": "mermaid_render_failed",
        },
    )
    res = await client.post(
        "/ai/architecture-diagrams/generate",
        json={"account_id": "123456789012", "persist": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["svg_markup"].startswith("<svg")
    assert data["renderer_version"] == "mermaid_svg_ai_v1"
    assert "Mermaid rendering failed" in " ".join(data["evaluation"]["must_fix"])


@pytest.mark.anyio
async def test_architecture_diagram_list_and_get_svg(client, monkeypatch):
    _install_diagram_stubs(monkeypatch)
    created = await client.post(
        "/ai/architecture-diagrams/generate",
        json={"account_id": "123456789012", "persist": True},
    )
    assert created.status_code == 200
    created_data = created.json()
    diagram_id = created_data["id"]
    assert diagram_id

    listed = await client.get("/ai/architecture-diagrams")
    assert listed.status_code == 200
    list_data = listed.json()
    assert list_data["total"] >= 1
    assert any(item["id"] == diagram_id for item in list_data["items"])

    detail = await client.get(f"/ai/architecture-diagrams/{diagram_id}")
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["id"] == diagram_id
    assert detail_data["svg_markup"].startswith("<svg")
    assert detail_data["mermaid_code"].startswith("flowchart LR")

