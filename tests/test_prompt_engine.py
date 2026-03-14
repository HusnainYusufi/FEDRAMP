"""
Tests for the prompt engine.

Validates that:
  - System and user messages are correctly assembled
  - Control data and evidence are embedded
  - Template placeholders are filled
"""

import pytest

from app.services.ai_agent.narrative.prompt_engine import build_prompt


class TestBuildPrompt:
    """Tests for build_prompt()."""

    SAMPLE_CONTROL = {
        "control_id": "AC-2",
        "title": "Account Management",
        "family": "ACCESS CONTROL",
        "baseline": "HIGH",
        "nist_description": "The organization manages system accounts.",
        "fedramp_parameters": "Review annually.",
        "guidance": "Use automated mechanisms.",
    }

    SAMPLE_EVIDENCE = {
        "control_id": "AC-2",
        "iam_users": {
            "total_users": 5,
            "mfa_enabled_count": 4,
            "mfa_disabled_count": 1,
        },
    }

    def test_returns_tuple_of_two_strings(self):
        system, user = build_prompt(self.SAMPLE_CONTROL, self.SAMPLE_EVIDENCE)
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_system_message_contains_role(self):
        system, _ = build_prompt(self.SAMPLE_CONTROL, self.SAMPLE_EVIDENCE)
        assert "Senior FedRAMP Security Assessor" in system

    def test_system_message_contains_structure(self):
        system, _ = build_prompt(self.SAMPLE_CONTROL, self.SAMPLE_EVIDENCE)
        assert "## Control Summary" in system
        assert "## Implementation Status" in system

    def test_user_message_contains_control_data(self):
        _, user = build_prompt(self.SAMPLE_CONTROL, self.SAMPLE_EVIDENCE)
        assert "AC-2" in user
        assert "Account Management" in user
        assert "ACCESS CONTROL" in user

    def test_user_message_contains_evidence(self):
        _, user = build_prompt(self.SAMPLE_CONTROL, self.SAMPLE_EVIDENCE)
        assert "total_users" in user
        assert "mfa_enabled_count" in user

    def test_user_message_contains_nist_description(self):
        _, user = build_prompt(self.SAMPLE_CONTROL, self.SAMPLE_EVIDENCE)
        assert "manages system accounts" in user

    def test_fedramp_params_in_user_message(self):
        _, user = build_prompt(self.SAMPLE_CONTROL, self.SAMPLE_EVIDENCE)
        assert "Review annually" in user

    def test_empty_evidence_still_valid(self):
        system, user = build_prompt(self.SAMPLE_CONTROL, {})
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert "AC-2" in user
