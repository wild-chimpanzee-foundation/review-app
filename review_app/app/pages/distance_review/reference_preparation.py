from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Callable

from nicegui import run, ui
from sqlalchemy.exc import IntegrityError

from review_app.app.pages.distance_review.player import (
    capture_offset,
    playback_shortcuts,
    seek_paused,
    step_frames,
    stop_player,
)
from review_app.app.pages.distance_review.reference_media import frame_time, reference_frame_offset
from review_app.app.pages.review.video_player import render_custom_video_player
from review_app.app.translations import t


@dataclass(frozen=True)
class ReferencePreparationContext:
    client: object
    provider: object
    reference: dict
    source: str
    duration: float
    frame_rate: float | None
    prepared_frames: list[dict]
    guard: Callable
    is_current: Callable[[str], bool]
    on_frames_saved: Callable[[list[dict]], None]


async def open_reference_preparation(context: ReferencePreparationContext) -> None:
    reference = context.reference
    reference_id = reference["id"]
    source = context.source
    frame_rate = context.frame_rate
    prepared_frames = context.prepared_frames
    latest = max(prepared_frames, key=lambda frame: frame["offset_ms"], default=None)
    initial_offset = reference_frame_offset(latest) if latest else 0

    def next_distance(previous: float = 0) -> int:
        candidate = int(previous) + 1
        used = {frame["distance_m"] for frame in prepared_frames}
        while candidate in used:
            candidate += 1
        return candidate

    initial_distance = next_distance(latest["distance_m"] if latest else 0)
    saving = False
    with (
        context.client,
        ui.dialog() as dialog,
        ui.card().classes("distance-dialog distance-player") as panel,
    ):
        with ui.row().classes("w-full items-center"):
            ui.label(t("distance_prepare_reference")).classes("text-h6")
            ui.space()
            ui.button(icon="close", on_click=dialog.close).props("flat round")
        ui.label(t("distance_reference_tag_help")).classes("text-caption text-grey-6")
        player = render_custom_video_player(
            source,
            context.duration,
            str(uuid.uuid4()),
            autoplay=False,
            keyboard=False,
            compact=True,
        )
        playback_shortcuts(panel, player)
        seek_paused(player, initial_offset)
        dialog.on("hide", lambda: stop_player(player))
        step_buttons = []

        @context.guard
        async def move_frames(delta: int):
            if (
                saving
                or frame_rate is None
                or not dialog.value
                or not context.is_current(reference_id)
            ):
                return
            try:
                await step_frames(player, delta, frame_rate)
            except (ValueError, TimeoutError):
                ui.notify(t("distance_player_not_ready"), type="warning")

        with ui.row().classes("w-full items-center justify-center gap-xs"):
            ui.label(t("distance_frame_steps")).classes("text-caption text-grey-6")
            for delta in (-20, -5, -1, 1, 5, 20):
                button = ui.button(
                    f"{delta:+d}", on_click=lambda _e, delta=delta: move_frames(delta)
                ).props(f'outline dense data-reference-frame-step="{delta}"')
                button.set_enabled(frame_rate is not None)
                step_buttons.append(button)
            if frame_rate is None:
                ui.label(t("distance_frame_rate_unavailable")).classes("text-caption text-grey-6")
        with ui.row().classes("w-full items-end"):
            distance = (
                ui.number(
                    t("distance_known_distance"),
                    value=initial_distance,
                    min=1,
                    step=1,
                    precision=0,
                    format="%.0f",
                )
                .classes("flex-1")
                .props("dense outlined")
            )

            @context.guard
            async def save_frame():
                nonlocal saving, prepared_frames
                if saving or not dialog.value or not context.is_current(reference_id):
                    return
                saving = True
                save.set_enabled(False)
                distance.set_enabled(False)
                for button in step_buttons:
                    button.set_enabled(False)
                try:
                    if distance.value is None:
                        ui.notify(t("distance_enter_distance"), type="warning")
                        return
                    value = float(distance.value)
                    if not value.is_integer() or value < 1:
                        ui.notify(t("distance_whole_distance_required"), type="warning")
                        return
                    playback_offset = await capture_offset(player, exact=True)
                    if not dialog.value or not context.is_current(reference_id):
                        return
                    offset = math.floor(playback_offset + 0.5)
                    await run.io_bound(
                        context.provider.add_reference_frame,
                        reference_id,
                        offset,
                        int(value),
                        playback_offset_ms=playback_offset,
                    )
                    prepared_frames = await run.io_bound(
                        context.provider.list_reference_frames, reference_id
                    )
                    if not dialog.value or not context.is_current(reference_id):
                        return
                    distance.set_value(next_distance(value))
                    render_prepared_frames.refresh()
                    context.on_frames_saved(prepared_frames)
                    ui.notify(t("distance_frame_saved"), type="positive")
                except (ValueError, TimeoutError) as exc:
                    ui.notify(
                        t("distance_player_not_ready")
                        if isinstance(exc, TimeoutError) or str(exc) == "distance_player_not_ready"
                        else str(exc),
                        type="warning",
                    )
                except IntegrityError:
                    ui.notify(t("distance_duplicate_frame"), type="warning")
                finally:
                    saving = False
                    save.set_enabled(True)
                    distance.set_enabled(True)
                    for button in step_buttons:
                        button.set_enabled(frame_rate is not None)

            save = ui.button(t("distance_save_frame"), icon="bookmark_add", on_click=save_frame)
            distance.on("keydown.enter", save_frame)

        def inspect_frame(frame: dict) -> None:
            if not saving:
                seek_paused(player, reference_frame_offset(frame))

        @ui.refreshable
        def render_prepared_frames():
            if not prepared_frames:
                return
            ui.label(t("distance_prepared_frames")).classes("text-caption text-weight-medium")
            with ui.row().classes("w-full gap-xs").style("max-height:120px;overflow-y:auto"):
                for frame in sorted(prepared_frames, key=lambda row: row["offset_ms"]):
                    ui.button(
                        f"{frame['distance_m']:g} m · {frame_time(frame['offset_ms'])}",
                        on_click=lambda _e, frame=frame: inspect_frame(frame),
                    ).props("outline dense no-caps")

        render_prepared_frames()
        ui.label(t("distance_no_frames")).classes("text-caption text-grey-6")
    dialog.open()
