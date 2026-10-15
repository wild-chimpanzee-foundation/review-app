from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from nicegui import run, ui

from review_app.app.pages.distance_review.player import (
    capture_offset,
    playback_shortcuts,
    seek_paused,
    step_frames,
    stop_player,
)
from review_app.app.pages.review.video_player import render_custom_video_player
from review_app.app.translations import t


@dataclass(frozen=True)
class ClockDialogContext:
    client: object
    provider: object
    video_id: str
    source: str | None
    duration_sec: float
    frame_rate: float
    initial_offset_ms: int | float
    clock_description: str | None
    proposed_camera_time: datetime | None
    opening_even_second: bool
    locked: bool
    unavailable_message: str
    show_estimated_steps: bool
    guard: Callable
    is_current: Callable[[], bool]
    is_busy: Callable[[], bool]
    set_busy: Callable[[bool], None]
    annotator: str
    on_calibrated: Callable[[], object]
    on_refresh: Callable[[], None]


async def open_clock_alignment_dialog(context: ClockDialogContext) -> None:
    with (
        context.client,
        ui.dialog().props('data-distance-clock-dialog="true"') as dialog,
        ui.card().classes("distance-dialog distance-player") as panel,
    ):
        with ui.row().classes("w-full items-center"):
            ui.label(t("distance_clock_alignment")).classes("text-h6")
            ui.space()
            ui.button(icon="close", on_click=dialog.close).props("flat round")
        opening = None
        if context.source is not None:
            opening = render_custom_video_player(
                context.source,
                context.duration_sec,
                str(uuid.uuid4()),
                autoplay=False,
                keyboard=False,
                compact=True,
            )
            seek_paused(opening, context.initial_offset_ms)
            playback_shortcuts(panel, opening)
            dialog.on("hide", lambda: stop_player(opening))
        else:
            ui.label(context.unavailable_message).classes("text-negative")
        if context.clock_description:
            ui.label(context.clock_description).classes("text-caption")
            if context.locked:
                ui.label(t("distance_clock_locked")).classes("text-caption text-grey-6")
                dialog.open()
                return
        proposed = context.proposed_camera_time
        value = proposed.replace(tzinfo=None).isoformat(timespec="seconds") if proposed else ""
        clock = (
            ui.input(t("distance_clock_frame_time"), value=value)
            .props('type="datetime-local" step="1" data-testid="distance-clock-time"')
            .classes("w-full")
        )
        ui.label(t("distance_clock_help")).classes("text-caption text-grey-6")
        ui.label(t("distance_clock_shortcuts")).classes("text-caption text-primary")
        opening_even = ui.checkbox(
            t("distance_clock_opening_even"), value=context.opening_even_second
        )

        @context.guard
        async def move_clock_frames(delta: int):
            if context.is_busy() or not dialog.value or opening is None:
                return
            context.set_busy(True)
            try:
                await step_frames(opening, delta, context.frame_rate)
            except (ValueError, TimeoutError):
                ui.notify(t("distance_player_not_ready"), type="warning")
            finally:
                context.set_busy(False)

        with ui.row().classes("w-full items-center justify-center gap-xs"):
            ui.button(t("distance_clock_first_frame"), on_click=lambda: reset_clock_frame()).props(
                "flat dense"
            ).set_enabled(opening is not None)
            for delta in (-20, -5, -1, 1, 5, 20):
                ui.button(
                    f"{delta:+d}", on_click=lambda _e, delta=delta: move_clock_frames(delta)
                ).props(f'outline dense data-distance-clock-step="{delta}"').set_enabled(
                    opening is not None
                )
        if opening is not None and context.show_estimated_steps:
            ui.label(t("distance_clock_estimated_steps")).classes("text-caption text-grey-6")

        def reset_clock_frame():
            if not context.is_busy() and opening is not None:
                seek_paused(opening, 0)

        @context.guard
        async def save_clock():
            if (
                context.is_busy()
                or not dialog.value
                or not context.is_current()
                or opening is None
            ):
                return
            context.set_busy(True)
            verify_button.set_enabled(False)
            try:
                if not clock.value:
                    ui.notify(t("distance_clock_confirm_required"), type="warning")
                    return
                try:
                    camera_time = datetime.fromisoformat(clock.value)
                except ValueError:
                    ui.notify(t("distance_clock_timestamp_invalid"), type="warning")
                    return
                playback_offset = await capture_offset(opening, exact=True)
                if not dialog.value or not context.is_current():
                    return
                await run.io_bound(
                    context.provider.calibrate_distance_clock,
                    context.video_id,
                    camera_time,
                    playback_offset,
                    context.annotator,
                    opening_even_second=bool(opening_even.value),
                )
                if not dialog.value or not context.is_current():
                    return
                await context.on_calibrated()
                dialog.close()
            except (ValueError, TimeoutError) as exc:
                clock_errors = {
                    "Enter the visible even whole-second camera timestamp": "distance_clock_even_timestamp_required",
                    "Select the frame where the timestamp first changes to an even second": "distance_clock_even_transition_required",
                    "Select a frame within the video": "distance_clock_frame_out_of_range",
                    "No even camera second occurs within this video": "distance_clock_no_even_sample",
                }
                message = (
                    t("distance_player_not_ready")
                    if isinstance(exc, TimeoutError) or str(exc) == "distance_player_not_ready"
                    else t(clock_errors[str(exc)])
                    if str(exc) in clock_errors
                    else str(exc)
                )
                ui.notify(message, type="warning" if isinstance(exc, ValueError) else "negative")
            finally:
                context.set_busy(False)
                verify_button.set_enabled(opening is not None)
                context.on_refresh()

        verify_button = ui.button(
            t("distance_clock_save"), icon="schedule", on_click=save_clock
        ).props('data-distance-clock-action="confirm"')
        verify_button.set_enabled(opening is not None)
    dialog.open()
