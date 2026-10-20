from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from review_app.app.pages.distance_review.player import (
    anchor_mode,
    configure_anchor,
    frame_tolerance_ms,
    install_anchor_editor,
    seek_paused,
    stop_player,
)
from review_app.app.pages.distance_review.reference_media import individual_frame_offset
from review_app.app.pages.review.video_player import render_custom_video_player
from review_app.app.translations import t


@dataclass(frozen=True)
class FixedFrameAnchorContext:
    player_source: str
    duration_sec: float
    frame_rate: float | None
    guard: Callable
    is_current: Callable[[], bool]
    on_anchor: Callable[[dict[str, float]], None]


def render_fixed_frame_anchor_preview(
    context: FixedFrameAnchorContext,
    dialog: ui.dialog,
    draft: dict,
    filters: dict,
) -> None:
    """Draw and validate a box on the captured first-seen frame only."""
    offset_ms = individual_frame_offset(draft)
    preview = render_custom_video_player(
        context.player_source,
        context.duration_sec,
        str(uuid.uuid4()),
        autoplay=False,
        keyboard=False,
        compact=True,
        image_state=filters,
        paused_frame_only=True,
        fixed_offset_ms=offset_ms,
    )
    seek_paused(preview, offset_ms)
    preview.on("loadedmetadata", lambda: seek_paused(preview, offset_ms))
    ui.button(
        t("distance_mark_box"),
        icon="crop_free",
        on_click=lambda: anchor_mode(preview, "box"),
    )
    dialog.on("hide", lambda: stop_player(preview))

    @context.guard
    async def draw(event):
        payload = event.args
        if (
            not context.is_current()
            or payload.get("individual_id") != draft["id"]
            or abs(payload.get("offset_ms", -1) - offset_ms)
            >= frame_tolerance_ms(context.frame_rate)
        ):
            return
        anchor = payload["anchor"]
        draft.update({f"anchor_{key}": value for key, value in anchor.items()})
        configure_anchor(preview, draft)
        anchor_mode(preview, None)
        context.on_anchor(anchor)

    install_anchor_editor(preview, draw, draft, frame_rate=context.frame_rate, initial_mode="box")
