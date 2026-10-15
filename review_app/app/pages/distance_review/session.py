from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SampleSelection:
    """A distance frame and a visibility choice are distinct selections."""

    kind: Literal["frame", "visibility"]
    value: str

    @classmethod
    def from_legacy_id(cls, value: str | None, visibility_ids: set[str]) -> SampleSelection | None:
        if value is None:
            return None
        return cls("visibility" if value in visibility_ids else "frame", value)
