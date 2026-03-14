"""
Tests for the narrative output parser.

Validates that:
  - Valid narratives are correctly parsed
  - Missing headings are detected
  - Implementation status is extracted
  - Code fence stripping works
"""

import pytest

from app.services.ai_agent.narrative.output_parser import parse_narrative


class TestParseNarrative:
    """Tests for parse_narrative()."""

    VALID_NARRATIVE = """\
## Control Summary
AC-2 requires the organization to manage system accounts.

## Implementation Status
Implemented

The organization has fully implemented account management controls.

## What is the solution and how is it implemented?
The organization uses AWS IAM to manage accounts. 12 users identified.

## Deviations and Observations
No deviations identified.
"""

    def test_valid_narrative_is_parsed(self):
        result = parse_narrative(self.VALID_NARRATIVE)
        assert result["is_valid"] is True
        assert result["missing_headings"] == []
        assert result["implementation_status"] == "Implemented"
        assert "AC-2" in result["markdown"]

    def test_missing_heading_detected(self):
        incomplete = """\
## Control Summary
Some text.

## Implementation Status
Partially Implemented

## What is the solution and how is it implemented?
Details here.
"""
        result = parse_narrative(incomplete)
        assert result["is_valid"] is False
        assert "Deviations and Observations" in result["missing_headings"]

    def test_partially_implemented_extracted(self):
        narrative = """\
## Control Summary
Test.

## Implementation Status
Partially Implemented

Some evidence gaps exist.

## What is the solution and how is it implemented?
Details.

## Deviations and Observations
None.
"""
        result = parse_narrative(narrative)
        assert result["implementation_status"] == "Partially Implemented"

    def test_code_fence_stripping(self):
        fenced = "```markdown\n" + self.VALID_NARRATIVE + "\n```"
        result = parse_narrative(fenced)
        assert result["is_valid"] is True
        assert "```" not in result["markdown"]

    def test_empty_input(self):
        result = parse_narrative("")
        assert result["is_valid"] is False
        assert len(result["missing_headings"]) == 4
        assert result["implementation_status"] == "Unknown"


class TestStatusExtraction:
    """Edge cases for implementation status extraction."""

    def test_status_not_implemented(self):
        narrative = """\
## Control Summary
Test.

## Implementation Status
Not Implemented

No evidence found.

## What is the solution and how is it implemented?
N/A.

## Deviations and Observations
Full implementation required.
"""
        result = parse_narrative(narrative)
        assert result["implementation_status"] == "Not Implemented"

    def test_status_planned(self):
        narrative = """\
## Control Summary
Test.

## Implementation Status
Planned

Implementation scheduled for Q3.

## What is the solution and how is it implemented?
N/A.

## Deviations and Observations
None yet.
"""
        result = parse_narrative(narrative)
        assert result["implementation_status"] == "Planned"
