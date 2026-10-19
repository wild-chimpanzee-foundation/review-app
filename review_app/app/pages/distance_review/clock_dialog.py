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
    review: dict | None
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
        realign = None
        if context.review:
            if context.review.get("alignment_mode", "exact") == "exact":
                ui.label(t("distance_clock_correction_help")).classes("text-caption")
            ui.label(t("distance_clock_mode_reset_help")).classes("text-caption")
            realign = ui.checkbox(t("distance_clock_realign"), on_change=lambda: update_mode())
        saved_mode = (context.review or {}).get("alignment_mode", "exact")
        mode = ui.radio(
            {"exact": t("distance_clock_exact"), "simplified": t("distance_clock_simplified")},
            value=saved_mode,
            on_change=lambda: update_mode(),
        ).props('inline data-testid="distance-clock-mode"')
        proposed = context.proposed_camera_time
        value = proposed.strftime("%Y-%m-%d %H:%M:%S") if proposed else ""
        clock = (
            ui.input(t("distance_clock_frame_time"), value=value)
            .props(
                'type="text" mask="####-##-## ##:##:##" '
                'placeholder="YYYY-MM-DD HH:mm:ss" data-testid="distance-clock-time"'
            )
            .classes("w-full")
        )
        alignment_help = ui.label(t("distance_clock_help")).classes("text-caption text-grey-6")
        shortcuts = ui.label(t("distance_clock_shortcuts")).classes("text-caption text-primary")
        opening_even = ui.checkbox(
            t("distance_clock_opening_even"), value=context.opening_even_second
        )

        parity_help = ui.label(t("distance_clock_parity_help")).classes("text-caption text-grey-6")
        parity = ui.radio(
            {"even": t("distance_clock_even"), "odd": t("distance_clock_odd")},
            value=("even" if context.opening_even_second else "odd")
            if saved_mode == "simplified"
            else None,
        ).props('inline data-testid="distance-clock-parity"')

        @context.guard
        async def move_clock_frames(delta: int):
            if (
                context.is_busy()
                or not dialog.value
                or opening is None
                or not alignment_mode()
                or mode.value != "exact"
            ):
                return
            context.set_busy(True)
            try:
                await step_frames(opening, delta, context.frame_rate)
            except (ValueError, TimeoutError):
                ui.notify(t("distance_player_not_ready"), type="warning")
            finally:
                context.set_busy(False)

        frame_buttons = []
        with ui.row().classes("w-full items-center justify-center gap-xs"):
            frame_buttons.append(
                ui.button(
                    t("distance_clock_first_frame"), on_click=lambda: reset_clock_frame()
                ).props("flat dense")
            )
            for delta in (-20, -5, -1, 1, 5, 20):
                frame_buttons.append(
                    ui.button(
                        f"{delta:+d}", on_click=lambda _e, delta=delta: move_clock_frames(delta)
                    ).props(f'outline dense data-distance-clock-step="{delta}"')
                )
        if opening is not None and context.show_estimated_steps:
            ui.label(t("distance_clock_estimated_steps")).classes("text-caption text-grey-6")

        def alignment_mode():
            return context.review is None or bool(realign and realign.value)

        def update_mode():
            aligning = alignment_mode()
            if not aligning:
                mode.set_value(saved_mode)
                parity.set_value(
                    ("even" if context.opening_even_second else "odd")
                    if saved_mode == "simplified"
                    else None
                )
                opening_even.set_value(context.opening_even_second)
            exact = mode.value == "exact"
            mode.set_enabled(aligning)
            parity.set_enabled(aligning)
            parity.set_visibility(not exact)
            parity_help.set_visibility(not exact)
            clock.set_visibility(exact)
            opening_even.set_enabled(aligning)
            opening_even.set_visibility(exact)
            alignment_help.set_visibility(aligning and exact)
            shortcuts.set_visibility(aligning and exact)
            verify_button.set_enabled(opening is not None or not aligning)
            for button in frame_buttons:
                button.set_enabled(aligning and exact and opening is not None)
                button.set_visibility(exact)
            clock.set_label(
                t("distance_clock_frame_time" if aligning else "distance_clock_saved_time")
            )
            verify_button.set_text(
                t("distance_clock_save" if aligning or not exact else "distance_clock_correct")
            )
            if opening is not None:
                seek_paused(opening, context.initial_offset_ms if exact else 0)

        def reset_clock_frame():
            if not context.is_busy() and opening is not None and alignment_mode():
                seek_paused(opening, 0)

        @context.guard
        async def save_clock():
            if (
                context.is_busy()
                or not dialog.value
                or not context.is_current()
                or (alignment_mode() and opening is None)
            ):
                return
            context.set_busy(True)
            verify_button.set_enabled(False)
            if realign is not None:
                realign.set_enabled(False)
            mode.set_enabled(False)
            parity.set_enabled(False)
            opening_even.set_enabled(False)
            try:
                simplified = mode.value == "simplified"
                camera_time = None
                if simplified:
                    if parity.value not in ("even", "odd"):
                        ui.notify(t("distance_clock_parity_required"), type="warning")
                        return
                else:
                    if not clock.value:
                        ui.notify(t("distance_clock_confirm_required"), type="warning")
                        return
                    try:
                        camera_time = datetime.strptime(clock.value, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        ui.notify(t("distance_clock_timestamp_invalid"), type="warning")
                        return
                if alignment_mode() or simplified:
                    playback_offset = (
                        None if simplified else await capture_offset(opening, exact=True)
                    )
                    if not dialog.value or not context.is_current():
                        return
                    reset_tracks = context.review is not None and alignment_mode()
                    new_opening_even = bool(opening_even.value)
                    if reset_tracks:
                        with ui.dialog() as confirmation, ui.card().classes("distance-dialog"):
                            ui.label(t("distance_clock_reset_title")).classes("text-h6")
                            ui.label(t("distance_clock_reset_warning"))
                            with ui.row().classes("w-full justify-end"):
                                ui.button(
                                    t("cancel"), on_click=lambda: confirmation.submit(False)
                                ).props("flat")
                                ui.button(
                                    t("distance_clock_reset_confirm"),
                                    on_click=lambda: confirmation.submit(True),
                                ).props("color=negative")
                        confirmed = await confirmation
                        confirmation.delete()
                        if not confirmed or not dialog.value or not context.is_current():
                            return
                    if simplified:
                        await run.io_bound(
                            context.provider.align_distance_by_parity,
                            context.video_id,
                            parity.value == "even",
                            context.annotator,
                            reset_tracks=reset_tracks,
                        )
                    else:
                        await run.io_bound(
                            context.provider.calibrate_distance_clock,
                            context.video_id,
                            camera_time,
                            playback_offset,
                            context.annotator,
                            opening_even_second=new_opening_even,
                            reset_tracks=reset_tracks,
                        )
                else:
                    if (
                        camera_time.tzinfo is not None
                        or (camera_time - proposed).total_seconds() % 2
                    ):
                        raise ValueError("Timestamp corrections must use whole two-second steps")
                    review = context.review
                    await run.io_bound(
                        context.provider.verify_distance_recording_time,
                        context.video_id,
                        review["recorded_start"] + (camera_time - proposed),
                        context.annotator,
                        clock_anchor_offset_ms=review["clock_anchor_offset_ms"],
                        clock_anchor_playback_ms=review["clock_anchor_playback_ms"],
                        opening_even_second=review["opening_even_second"],
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
                    "Timestamp corrections must use whole two-second steps": "distance_clock_correction_invalid",
                    "Changing clock alignment requires resetting this video's Distance review": "distance_clock_reset_required",
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
                if realign is not None:
                    realign.set_enabled(True)
                mode.set_enabled(alignment_mode())
                parity.set_enabled(alignment_mode())
                opening_even.set_enabled(alignment_mode())
                verify_button.set_enabled(opening is not None or not alignment_mode())
                context.on_refresh()

        verify_button = ui.button(
            t("distance_clock_save"), icon="schedule", on_click=save_clock
        ).props('data-distance-clock-action="confirm"')
        verify_button.set_enabled(opening is not None or context.review is not None)
        update_mode()
    dialog.open()
