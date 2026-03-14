"""
Tests for the Assessment API endpoints.

Uses the shared async test client from conftest.
These tests verify routing and schema validation — LLM calls
are tested separately via mocking.
"""

import pytest


@pytest.mark.anyio
async def test_list_controls_empty(client):
    """GET /ai/controls returns empty list when no controls loaded."""
    response = await client.get("/ai/controls")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.anyio
async def test_list_narratives_empty(client):
    """GET /ai/narratives returns empty list initially."""
    response = await client.get("/ai/narratives")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.anyio
async def test_get_narrative_not_found(client):
    """GET /ai/narratives/{id} returns 404 for unknown UUID."""
    response = await client.get(
        "/ai/narratives/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_generate_validation_error(client):
    """POST /ai/narratives/generate rejects invalid account_id."""
    response = await client.post(
        "/ai/narratives/generate",
        json={
            "control_id": "AC-2",
            "account_id": "short",  # too short
        },
    )
    assert response.status_code == 422
