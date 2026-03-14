import pytest

from app.services.templates.template_parser import parse_text_to_blueprints


def test_parse_text_to_blueprints_splits_controls_and_parts():
    text = """
## AC-2 Account Management

(a) The organization manages information system accounts. [Assignment: organization-defined account types]
(b) The organization reviews accounts within the defined time period. [Assignment: organization-defined time period for account review]

### AC-2 Control Summary Information
Responsible Role: IAM Administrator
Implementation Status: Implemented
Origination: Service Provider Corporate
Parameter (b): organization-defined time period for account review

### AC-2 What is the solution and how is it implemented?
(a) Dragon uses AWS IAM users/roles and disables accounts when no longer needed.
(b) Dragon performs quarterly IAM access reviews and documents results in tickets.

## SC-7 (1) Boundary Protection Enhancement

(a) The system monitors inbound and outbound communications.

### SC-7 (1) Control Summary Information
Implementation Status: Not Implemented
"""

    bps = parse_text_to_blueprints(text)
    assert "AC-2" in bps
    assert "SC-7 (1)" in bps

    ac2 = bps["AC-2"]
    assert ac2.title.lower().startswith("account")
    assert ac2.summary_table.responsible_role == "IAM Administrator"
    assert ac2.summary_table.implementation_status == ["Implemented"]
    assert ac2.summary_table.origination == ["Service Provider Corporate"]
    assert len(ac2.summary_table.parameters) == 2
    assert sorted([p.id for p in ac2.summary_table.parameters]) == ["ac-2-a", "ac-2-b"]
    assert all(p.value is None for p in ac2.summary_table.parameters)

    assert [p.id for p in ac2.parts] == ["a", "b"]
    assert "manages information system accounts" in ac2.parts[0].requirement_text.lower()
    assert ac2.parts[0].parameter_placeholder and "assignment" in ac2.parts[0].parameter_placeholder.lower()
    assert ac2.parts[0].dragon_implementation and "aws iam" in ac2.parts[0].dragon_implementation.lower()
    assert ac2.parts[1].dragon_implementation and "quarterly" in ac2.parts[1].dragon_implementation.lower()


def test_parse_text_to_blueprints_creates_main_part_when_no_markers():
    text = """
AC-6 Least Privilege
This control requires least privilege across all system components.
Additional narrative text here.

Control Summary Information
Implementation Status: Implemented
"""
    bps = parse_text_to_blueprints(text)
    assert "AC-6" in bps
    bp = bps["AC-6"]
    assert len(bp.parts) == 1
    assert bp.parts[0].id == "main"
    assert "least privilege" in bp.parts[0].requirement_text.lower()

