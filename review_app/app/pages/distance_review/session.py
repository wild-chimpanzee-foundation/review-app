from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ZERO_DISTANCE_ID = "zero-distance"
ZERO_DISTANCE_CHOICE = {"id": ZERO_DISTANCE_ID, "distance_m": 0}


@dataclass(frozen=True)
class SampleSelection:
    """The active distance or visibility choice for a sample."""

    kind: Literal["frame", "zero", "visibility"]
    value: str

    @classmethod
    def from_legacy_id(cls, value: str | None, visibility_ids: set[str]) -> SampleSelection | None:
        if value is None:
            return None
        if value == ZERO_DISTANCE_ID:
            return cls("zero", value)
        return cls("visibility" if value in visibility_ids else "frame", value)
