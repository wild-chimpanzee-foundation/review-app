from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from nicegui import run, ui

from review_app.app import config as app_config
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
    expected_recorded_start: datetime | None = None


def timestamp_input(label: str, value: str, test_id: str, on_change: Callable | None = None):
    """Calendar and 24-hour time controls, independent of native browser locale."""
    field = (
        ui.input(label, value=value, on_change=on_change)
        .props(
            'type="text" mask="####-##-## ##:##:##" '
            f'placeholder="YYYY-MM-DD HH:mm:ss" data-testid="{test_id}"'
        )
        .classes("col min-w-0")
    )
    with field.add_slot("append"):
        with (
            ui.button(icon="event", on_click=lambda: date_menu.open())
            .props(f'flat round dense data-testid="{test_id}-date-button"')
            .tooltip(t("distance_clock_choose_date"))
            .bind_enabled_from(field)
        ):
            with ui.menu() as date_menu:
                ui.date(mask="YYYY-MM-DD HH:mm:ss").props(
                    f'data-testid="{test_id}-date"'
                ).bind_value(field).bind_enabled_from(field)
        with (
            ui.button(icon="schedule", on_click=lambda: time_menu.open())
            .props(f'flat round dense data-testid="{test_id}-time-button"')
            .tooltip(t("distance_clock_choose_time"))
            .bind_enabled_from(field)
        ):
            with ui.menu() as time_menu:
                ui.time(mask="YYYY-MM-DD HH:mm:ss").props(
                    f'format24h with-seconds data-testid="{test_id}-time"'
                ).bind_value(field).bind_enabled_from(field)
    return field


async def open_clock_alignment_dialog(context: ClockDialogContext) -> None:
    configured_mode = app_config.DISTANCE_CLOCK_ALIGNMENT_MODE
    if configured_mode not in ("simplified", "exact"):
        raise ValueError("Choose exact or simplified alignment in app config")
    review = context.review
    saved_mode = (review or {}).get("alignment_mode", "exact")
    saved_anchor = (review or {}).get("clock_anchor_playback_ms")
    legacy_parity = saved_mode == "simplified" and saved_anchor is None
    simple_initial = (
        saved_anchor
        if saved_mode == "simplified" and saved_anchor is not None
        else context.duration_sec * 500
    )
    position_ms = context.initial_offset_ms
    correction_explicit = False
    setting_correction = False
    last_mode = None
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
            playback_shortcuts(panel, opening)
            dialog.on("hide", lambda: stop_player(opening))
        else:
            ui.label(context.unavailable_message).classes("text-negative")
        choose_even = (
            ui.label(t("distance_clock_choose_even_frame"))
            .classes("text-subtitle2 text-weight-bold")
            .props('data-testid="distance-clock-choose-even"')
        )
        frame_buttons = []
        with (
            ui.row()
            .classes("w-full items-center justify-center gap-xs")
            .props('data-testid="distance-clock-frame-controls"')
        ):
            frame_buttons.append(
                ui.button(
                    t("distance_clock_first_frame"), on_click=lambda: reset_clock_frame(0)
                ).props("flat dense")
            )
            midpoint = ui.button(
                t("distance_clock_midpoint"),
                on_click=lambda: reset_clock_frame(context.duration_sec * 500),
            ).props("flat dense")
            for delta in (-20, -5, -1, 1, 5, 20):
                frame_buttons.append(
                    ui.button(
                        f"{delta:+d}", on_click=lambda _e, delta=delta: move_clock_frames(delta)
                    ).props(f'outline dense data-distance-clock-step="{delta}"')
                )
        if opening is not None and context.show_estimated_steps:
            ui.label(t("distance_clock_estimated_steps")).classes("text-caption text-grey-6")

        shortcuts = ui.label(t("distance_clock_shortcuts")).classes("text-caption text-primary")
        if context.clock_description:
            ui.label(context.clock_description).classes("text-caption")
        realign = None
        if review:
            if saved_mode == "exact":
                ui.label(t("distance_clock_correction_help")).classes("text-caption")
            ui.label(t("distance_clock_mode_reset_help")).classes("text-caption")
            realign = ui.checkbox(t("distance_clock_realign"), on_change=lambda: update_mode())
        second_buttons = []

        def render_second_buttons():
            for delta in (-1, 1):
                label = t(
                    "distance_clock_decrease_second"
                    if delta < 0
                    else "distance_clock_increase_second"
                )
                second_buttons.append(
                    ui.button(
                        f"{delta:+d} s", on_click=lambda _e, delta=delta: adjust_second(delta)
                    )
                    .props(
                        f'outline dense no-caps aria-label="{label}" '
                        f'data-distance-clock-second="{delta}"'
                    )
                    .tooltip(label)
                )

        proposed = context.proposed_camera_time

        def correction_changed(event):
            nonlocal correction_explicit
            if not setting_correction:
                correction_explicit = bool(event.value)

        with ui.row().classes("w-full items-center no-wrap gap-xs"):
            clock = timestamp_input(
                t("distance_clock_frame_time"),
                proposed.strftime("%Y-%m-%d %H:%M:%S") if proposed else "",
                "distance-clock-time",
                correction_changed,
            )
            render_second_buttons()
        alignment_help = (
            ui.label(t("distance_clock_help")).classes("text-caption text-grey-6")
            if configured_mode == "exact"
            else None
        )
        legacy_help = ui.label(t("distance_clock_legacy_parity")).classes(
            "text-caption text-warning"
        )
        expected_label = (
            ui.label().classes("text-caption").props('data-testid="distance-clock-expected"')
        )
        opening_even = (
            ui.checkbox(t("distance_clock_opening_even"), value=context.opening_even_second)
            if configured_mode == "exact"
            else None
        )

        def alignment_mode():
            return review is None or bool(realign and realign.value)

        def clock_mode():
            # Corrections preserve saved timing; only a reset uses the code default.
            return configured_mode if alignment_mode() else saved_mode

        def expected_time():
            start = context.expected_recorded_start
            if start is None:
                return None
            # A verified anchor maps its precise playback phase to the whole camera second.
            if review and review.get("recorded_start") is not None and saved_anchor is not None:
                return start + timedelta(
                    milliseconds=review["clock_anchor_offset_ms"] + position_ms - saved_anchor
                )
            return start + timedelta(milliseconds=position_ms)

        def set_correction_estimate(expected):
            nonlocal setting_correction
            setting_correction = True
            try:
                clock.set_value(expected.strftime("%Y-%m-%d %H:%M:%S") if expected else "")
            finally:
                setting_correction = False

        def update_expected():
            expected = expected_time()
            if clock_mode() == "simplified" and not correction_explicit:
                set_correction_estimate(expected)
            expected_label.set_text(
                t("distance_clock_expected", time=expected.strftime("%Y-%m-%d %H:%M:%S"))
                if expected
                else t("distance_clock_expected_unavailable")
            )

        def parse_camera_time(value):
            value = value.replace("T", " ")
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return datetime.strptime(value, "%Y-%m-%d %H:%M")

        def adjust_second(delta):
            field = clock
            if (
                context.is_busy()
                or not dialog.value
                or not context.is_current()
                or not field.enabled
            ):
                return
            try:
                adjusted = parse_camera_time(field.value or "") + timedelta(seconds=delta)
            except (ValueError, OverflowError):
                ui.notify(t("distance_clock_datetime_invalid"), type="warning")
                return
            field.set_value(adjusted.isoformat(sep=" ", timespec="seconds"))

        def update_position(offset_ms):
            nonlocal position_ms, correction_explicit
            if not math.isfinite(offset_ms) or not 0 <= offset_ms < context.duration_sec * 1000:
                return
            if abs(position_ms - offset_ms) > 0.0001 and alignment_mode():
                correction_explicit = False
            position_ms = offset_ms
            update_expected()

        def seek_clock(offset_ms):
            if opening is not None:
                update_position(offset_ms)
                seek_paused(opening, offset_ms)

        def on_clock_position(event):
            offset_ms = event.args["offset_ms"]
            if saved_mode == "simplified" and saved_anchor is not None and not alignment_mode():
                # A timestamp-only correction always belongs to the saved frame,
                # even if native media controls attempt an inspection seek.
                if math.isfinite(offset_ms) and abs(offset_ms - saved_anchor) > 0.01:
                    seek_paused(opening, saved_anchor)
                return
            update_position(offset_ms)

        if opening is not None:
            for event in ("timeupdate", "seeked"):
                opening.on(
                    event,
                    on_clock_position,
                    js_handler="(event) => emit({offset_ms: event.target.currentTime * 1000})",
                )

        @context.guard
        async def move_clock_frames(delta: int):
            if context.is_busy() or not dialog.value or opening is None or not alignment_mode():
                return
            context.set_busy(True)
            try:
                await step_frames(opening, delta, context.frame_rate)
            except (ValueError, TimeoutError):
                ui.notify(t("distance_player_not_ready"), type="warning")
            finally:
                context.set_busy(False)
                update_expected()

        def reset_clock_frame(offset_ms):
            if not context.is_busy() and alignment_mode():
                seek_clock(offset_ms)

        def update_mode(*, seek=True):
            nonlocal correction_explicit, last_mode
            aligning = alignment_mode()
            exact = clock_mode() == "exact"
            if clock_mode() != last_mode:
                correction_explicit = False
                if exact:
                    set_correction_estimate(proposed)
                last_mode = clock_mode()
            if not aligning:
                if opening_even is not None:
                    opening_even.set_value(context.opening_even_second)
            choose_even.set_visibility(not exact and aligning)
            legacy_help.set_visibility(not exact and legacy_parity and not aligning)
            expected_label.set_visibility(not exact)
            clock.set_enabled(not context.is_busy() and (exact or aligning or not legacy_parity))
            for button in second_buttons:
                button.set_enabled(clock.enabled)
            if opening_even is not None:
                opening_even.set_enabled(aligning)
                opening_even.set_visibility(exact and aligning)
            if alignment_help is not None:
                alignment_help.set_visibility(aligning and exact)
            shortcuts.set_visibility(aligning)
            verify_button.set_enabled(opening is not None or not aligning)
            for button in [*frame_buttons, midpoint]:
                button.set_enabled(aligning and opening is not None)
            midpoint.set_visibility(not exact)
            clock.set_label(
                t("distance_clock_optional_time")
                if not exact
                else t("distance_clock_frame_time" if aligning else "distance_clock_saved_time")
            )
            verify_button.set_text(
                t("distance_clock_even_frame_confirm")
                if not exact
                else t("distance_clock_save" if aligning else "distance_clock_correct")
            )
            if seek:
                seek_clock(context.initial_offset_ms if exact else simple_initial)
            update_expected()

        @context.guard
        async def save_clock():
            aligning = alignment_mode()
            if (
                context.is_busy()
                or not dialog.value
                or not context.is_current()
                or (aligning and opening is None)
            ):
                return
            context.set_busy(True)
            verify_button.set_enabled(False)
            for control in (
                realign,
                opening_even,
                clock,
                *second_buttons,
            ):
                if control is not None:
                    control.set_enabled(False)
            try:
                simplified = clock_mode() == "simplified"
                camera_time = None
                if simplified:
                    value = clock.value if correction_explicit else ""
                    if not value and not aligning and review["recorded_start"] is not None:
                        camera_time = review["recorded_start"] + timedelta(
                            milliseconds=review["clock_anchor_offset_ms"]
                        )
                else:
                    value = clock.value
                    if not value:
                        ui.notify(t("distance_clock_confirm_required"), type="warning")
                        return
                if value:
                    try:
                        camera_time = parse_camera_time(value)
                    except ValueError:
                        ui.notify(t("distance_clock_datetime_invalid"), type="warning")
                        return
                    if simplified and camera_time.second % 2:
                        raise ValueError("Enter the visible even whole-second camera timestamp")
                if aligning:
                    playback_offset = await capture_offset(opening, exact=True)
                    if not dialog.value or not context.is_current():
                        return
                    # Timestamp confirmation is frame-specific; reject a stale selection.
                    if simplified and abs(playback_offset - position_ms) > 0.01:
                        update_position(playback_offset)
                        ui.notify(t("distance_clock_frame_changed"), type="warning")
                        return
                    reset_tracks = review is not None
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
                            context.provider.align_distance_on_even_frame,
                            context.video_id,
                            playback_offset,
                            context.annotator,
                            camera_time=camera_time,
                            reset_tracks=reset_tracks,
                        )
                    else:
                        await run.io_bound(
                            context.provider.calibrate_distance_clock,
                            context.video_id,
                            camera_time,
                            playback_offset,
                            context.annotator,
                            opening_even_second=bool(opening_even and opening_even.value),
                            reset_tracks=reset_tracks,
                        )
                elif simplified:
                    if legacy_parity:
                        await run.io_bound(
                            context.provider.align_distance_by_parity,
                            context.video_id,
                            context.opening_even_second,
                            context.annotator,
                        )
                    else:
                        await run.io_bound(
                            context.provider.align_distance_on_even_frame,
                            context.video_id,
                            saved_anchor,
                            context.annotator,
                            camera_time=camera_time,
                        )
                else:
                    if (camera_time - proposed).total_seconds() % 2:
                        raise ValueError("Timestamp corrections must use whole two-second steps")
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
                    "Select an anchor frame within the video": "distance_clock_frame_out_of_range",
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
                update_mode(seek=False)
                context.on_refresh()

        verify_button = ui.button(
            t("distance_clock_save"), icon="schedule", on_click=save_clock
        ).props('data-distance-clock-action="confirm"')
        update_mode()
    dialog.open()
