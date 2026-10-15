from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class DraftState:
    """Own unsaved sample drafts and per-individual carried-forward defaults."""

    drafts: dict[tuple[str, int], dict] = field(default_factory=dict)
    defaults: dict[str, dict] = field(default_factory=dict)

    def remember(self, individual_id: str, draft: dict) -> None:
        if draft is None:
            return
        self.defaults[individual_id] = {
            **draft,
            "behavior_ids": list(draft["behavior_ids"]),
        }

    def draft(
        self,
        *,
        key: tuple[str, int],
        default: dict,
        selection_id: str | None,
        skip_reason: str | None,
        reference_video_id: str | None,
    ) -> dict:
        return self.drafts.setdefault(
            key,
            {
                **default,
                "behavior_ids": list(default["behavior_ids"]),
                "reference_frame_id": None if skip_reason else selection_id,
                "skip_reason": skip_reason,
                "reference_video_id": reference_video_id,
            },
        )
