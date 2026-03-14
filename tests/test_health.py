"""
Tests for the /health endpoint.
"""

import pytest


@pytest.mark.asyncio
async def test_health_returns_200(client):
    """Health endpoint should return 200 with service info."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "fedramp-ingestion-core"
    assert "version" in data
    assert "database" in data
