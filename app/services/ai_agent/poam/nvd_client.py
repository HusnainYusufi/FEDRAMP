from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


async def fetch_cve(*, cve_id: str) -> dict[str, Any] | None:
    """
    Query NVD CVE API v2.0 by CVE ID.
    Returns the JSON payload or None if not found.
    """
    cid = (cve_id or "").strip().upper()
    if not cid.startswith("CVE-"):
        return None

    url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    headers: dict[str, str] = {}
    if settings.nvd_api_key:
        headers["apiKey"] = settings.nvd_api_key

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params={"cveId": cid}, headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

