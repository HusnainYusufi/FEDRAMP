from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ImplementationStatus = Literal["Implemented", "Partially Implemented", "Planned", "Not Implemented"]


class ValidationFindings(BaseModel):
    """
    Internal compliance validator output.

    IMPORTANT: This object is intentionally structured and does not contain
    narrative prose. It is safe to pass to a narrative strategy layer.
    """

    control_id: str = Field(..., description="NIST 800-53 control ID (e.g., AC-2)")
    implementation_status: ImplementationStatus
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    inherited_controls: list[str] = Field(default_factory=list)
    customer_responsibilities: list[str] = Field(default_factory=list)
    remediation_items: list[str] = Field(default_factory=list)

