"""
Blueprint models for FedRAMP narrative templates.

These models are JSON-serializable and designed to preserve a strict split between:
- Requirement text (the "question") extracted from the template definition section
- Implementation text (the "answer") to be filled/hydrated later
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Parameter(BaseModel):
    """
    A single control parameter entry.

    - `value` is intended to be hydrated (nullable placeholder).
    - `assignment` is optional metadata (e.g., "FedRAMP assignment") and may be null.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable parameter id (e.g., 'ac-2-a')")
    text: str = Field(..., description="Parameter text/description from template")
    value: str | None = Field(None, description="Fillable value (nullable placeholder)")
    assignment: str | None = Field(
        None,
        description="Optional fixed assignment; treat as read-only if set",
    )


class SummaryTable(BaseModel):
    """Parsed representation of the 'Control Summary Information' area."""

    model_config = ConfigDict(extra="forbid")

    responsible_role: str | None = Field(
        None, description="Fillable responsible role (nullable placeholder)"
    )
    parameters: list[Parameter] = Field(default_factory=list)
    implementation_status: list[str] | None = Field(
        None,
        description="Fillable implementation status selection(s)",
        examples=[["Implemented"], ["Not Implemented"]],
    )
    origination: list[str] | None = Field(
        None,
        description="Origination selection(s) (e.g., 'Service Provider Corporate')",
    )


class ControlPart(BaseModel):
    """
    A control requirement part/paragraph, typically labeled (a), (b), ...

    IMPORTANT: `requirement_text` is the source of truth extracted from Zone A
    (the definition section). `dragon_implementation` comes from Zone B (the
    implementation section) and must never overwrite `requirement_text`.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable part id within the control (e.g., 'a')")
    requirement_text: str = Field(
        ...,
        description="Template requirement text for this part (Zone A; source of truth)",
    )
    parameter_placeholder: str | None = Field(
        None,
        description="Bracketed parameter placeholder extracted from the requirement text, if present",
        examples=["[Assignment: organization-defined frequency]"],
    )
    dragon_implementation: str | None = Field(
        None, description="Fillable implementation narrative (nullable placeholder)"
    )
    inheritance_text: str = Field(..., description="Static inheritance boilerplate")
    customer_responsibility: str = Field(
        ..., description="Static customer responsibility boilerplate"
    )


class ControlBlueprint(BaseModel):
    """Full per-control blueprint extracted from a template document."""

    model_config = ConfigDict(extra="forbid")

    control_id: str = Field(..., description="NIST control identifier, e.g. 'AC-2'")
    title: str = Field(..., description="Control title from the template header")
    summary_table: SummaryTable = Field(default_factory=SummaryTable)
    parts: list[ControlPart] = Field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        """Return a JSON-serializable dict suitable for JSONB storage."""
        return self.model_dump(mode="json")

