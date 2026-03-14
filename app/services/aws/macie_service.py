from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import ClientError

from app.config.logging_config import get_logger
from app.services.aws.client import AWSClientFactory

logger = get_logger(__name__)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class MacieService:
    """
    Best-effort Macie (macie2) client wrapper.

    Notes:
    - Macie must be enabled in the account/region
    - The assumed role must have macie2 read permissions (SecurityAudit may not include this)
    """

    def __init__(self, *, role_arn: str, account_id: str, region: str) -> None:
        self.factory = AWSClientFactory(role_arn, account_id)
        self.account_id = account_id
        self.region = region

    def _client(self):
        return self.factory.get_client("macie2", self.region)

    def _ensure_enabled(self) -> None:
        c = self._client()
        try:
            c.get_macie_session()
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in {"AccessDeniedException", "UnauthorizedOperation"}:
                raise PermissionError(
                    "Access denied calling Macie. Ensure the assumed role has macie2:GetMacieSession, "
                    "macie2:ListFindings, and macie2:GetFindings permissions."
                ) from e
            if code in {"ResourceNotFoundException"}:
                raise RuntimeError(
                    f"Macie is not enabled in account {self.account_id} region {self.region}."
                ) from e
            raise

    def fetch_findings_summary(
        self,
        *,
        max_findings: int = 50,
        since_days: int = 30,
    ) -> dict[str, Any]:
        """
        Return a summary + sample findings relevant to sensitive data discovery.
        """
        self._ensure_enabled()
        c = self._client()

        max_findings = max(1, min(int(max_findings or 50), 200))
        since_days = max(1, min(int(since_days or 30), 365))
        since = datetime.now(timezone.utc) - timedelta(days=since_days)

        finding_ids: list[str] = []
        next_token: str | None = None

        # Best-effort: some accounts may not support filterCriteria shapes consistently.
        # If filtering fails, fall back to unfiltered listing.
        filter_criteria = {
            "criterion": {
                "createdAt": {"gte": _iso(since)},
            }
        }

        try:
            while len(finding_ids) < max_findings:
                resp = c.list_findings(
                    filterCriteria=filter_criteria,
                    maxResults=min(50, max_findings - len(finding_ids)),
                    nextToken=next_token,
                )
                finding_ids.extend(resp.get("findingIds", []) or [])
                next_token = resp.get("nextToken")
                if not next_token:
                    break
        except ClientError:
            # Fall back to unfiltered list
            finding_ids = []
            next_token = None
            while len(finding_ids) < max_findings:
                resp = c.list_findings(
                    maxResults=min(50, max_findings - len(finding_ids)),
                    nextToken=next_token,
                )
                finding_ids.extend(resp.get("findingIds", []) or [])
                next_token = resp.get("nextToken")
                if not next_token:
                    break

        findings: list[dict[str, Any]] = []
        if finding_ids:
            # macie2.get_findings caps at 50 IDs per call
            for i in range(0, len(finding_ids), 50):
                batch = finding_ids[i : i + 50]
                fresp = c.get_findings(findingIds=batch)
                findings.extend(fresp.get("findings", []) or [])

        # Summarize
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        s3_buckets: dict[str, int] = {}

        samples: list[dict[str, Any]] = []
        for f in findings:
            ftype = str(f.get("type") or "unknown")
            by_type[ftype] = by_type.get(ftype, 0) + 1

            sev = (f.get("severity") or {}).get("description") if isinstance(f.get("severity"), dict) else None
            sev2 = str(sev or (f.get("severity") or "unknown"))
            by_severity[sev2] = by_severity.get(sev2, 0) + 1

            ra = f.get("resourcesAffected") or {}
            s3 = ra.get("s3Bucket") if isinstance(ra, dict) else None
            bucket_name = None
            if isinstance(s3, dict):
                bucket_name = s3.get("name")
            if bucket_name:
                s3_buckets[bucket_name] = s3_buckets.get(bucket_name, 0) + 1

            if len(samples) < 20:
                samples.append(
                    {
                        "id": f.get("id"),
                        "type": f.get("type"),
                        "title": f.get("title"),
                        "description": f.get("description"),
                        "createdAt": f.get("createdAt"),
                        "severity": f.get("severity"),
                        "bucket": bucket_name,
                    }
                )

        logger.info(
            "macie_findings_summary",
            account_id=self.account_id,
            region=self.region,
            findings=len(findings),
            buckets=len(s3_buckets),
        )

        top_buckets = sorted(s3_buckets.items(), key=lambda kv: kv[1], reverse=True)[:25]
        return {
            "account_id": self.account_id,
            "region": self.region,
            "since_days": since_days,
            "findings_count": len(findings),
            "by_type": by_type,
            "by_severity": by_severity,
            "top_buckets": [{"bucket": b, "findings": n} for b, n in top_buckets],
            "sample_findings": samples,
        }

