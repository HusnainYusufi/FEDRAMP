from __future__ import annotations

import enum


class GenerationMode(str, enum.Enum):
    AUDIT_MODE = "AUDIT_MODE"
    SSP_NARRATIVE_MODE = "SSP_NARRATIVE_MODE"


class ToneTier(str, enum.Enum):
    low = "low"
    meduim = "meduim"
    high = "high"

