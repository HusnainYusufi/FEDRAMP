"""
Tests for the /aws/ingestions endpoint — request validation.

These tests do NOT call real AWS APIs. They verify input validation
(ARN format, account_id consistency, region format) which is critical
for preventing confused-deputy and misrouting attacks.
"""

import pytest


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_account_id(client):
    """Account ID must be exactly 12 digits."""
    response = await client.post("/aws/ingestions", json={
        "account_id": "12345",
        "role_arn": "arn:aws:iam::123456789012:role/SecurityAudit",
        "regions": ["us-east-1"],
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_arn(client):
    """Role ARN must be a valid IAM role ARN."""
    response = await client.post("/aws/ingestions", json={
        "account_id": "123456789012",
        "role_arn": "not-an-arn",
        "regions": ["us-east-1"],
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ingest_rejects_mismatched_account(client):
    """Account in ARN must match provided account_id."""
    response = await client.post("/aws/ingestions", json={
        "account_id": "123456789012",
        "role_arn": "arn:aws:iam::999999999999:role/SecurityAudit",
        "regions": ["us-east-1"],
    })
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_ingest_rejects_empty_regions(client):
    """At least one region must be specified."""
    response = await client.post("/aws/ingestions", json={
        "account_id": "123456789012",
        "role_arn": "arn:aws:iam::123456789012:role/SecurityAudit",
        "regions": [],
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_region_format(client):
    """Region must match AWS region format."""
    response = await client.post("/aws/ingestions", json={
        "account_id": "123456789012",
        "role_arn": "arn:aws:iam::123456789012:role/SecurityAudit",
        "regions": ["invalid-region-format-123"],
    })
    assert response.status_code == 422
