from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyTemplate:
    policy_id: str
    title: str
    skeleton_markdown: str


TEMPLATES: dict[str, PolicyTemplate] = {
    "access_control": PolicyTemplate(
        policy_id="access_control",
        title="Access Control Policy",
        skeleton_markdown=(
            "# Access Control Policy\n\n"
            "## Purpose\n"
            "Define access control requirements for the system.\n\n"
            "## Scope\n"
            "Applies to all workforce members, contractors, and system components.\n\n"
            "## Policy\n"
            "- Authentication shall use MFA where supported.\n"
            "- Access shall follow least privilege.\n"
            "- Privileged access shall be reviewed at least quarterly.\n\n"
            "## Technologies\n"
            "- IAM: {{IAM}}\n"
            "- OS: {{Operating System}}\n\n"
            "## Roles and Responsibilities\n"
            "- Security: Define and review access requirements.\n"
            "- IT/Ops: Implement IAM policies and enforce MFA.\n\n"
            "## Review Cadence\n"
            "This policy is reviewed at least annually.\n"
        ),
    ),
    "incident_response": PolicyTemplate(
        policy_id="incident_response",
        title="Incident Response Policy",
        skeleton_markdown=(
            "# Incident Response Policy\n\n"
            "## Purpose\n"
            "Define incident response requirements for the system.\n\n"
            "## Policy\n"
            "- Incidents shall be detected, analyzed, contained, eradicated, and recovered.\n"
            "- Security incidents affecting Federal data shall be reported per contractual requirements.\n\n"
            "## Technologies\n"
            "- Logging / SIEM: {{Logging / SIEM}}\n"
            "- Endpoint Protection: {{Endpoint Protection}}\n\n"
            "## Testing\n"
            "Tabletop exercises are performed at least annually.\n"
        ),
    ),
    "vulnerability_management": PolicyTemplate(
        policy_id="vulnerability_management",
        title="Vulnerability Management Policy",
        skeleton_markdown=(
            "# Vulnerability Management Policy\n\n"
            "## Purpose\n"
            "Define vulnerability scanning, remediation, and exception handling.\n\n"
            "## Policy\n"
            "- Vulnerability scans are performed regularly and after significant changes.\n"
            "- Findings are triaged by severity and remediated within defined timelines.\n"
            "- Exceptions (POA&M items) are reviewed at least every 30 days.\n\n"
            "## Technologies\n"
            "- Vulnerability Scanning: {{Vulnerability Scanning}}\n"
        ),
    ),
}

