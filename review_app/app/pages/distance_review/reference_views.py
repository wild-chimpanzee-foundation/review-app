from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nicegui import ui

from review_app.app.config import DISTANCE_REFERENCE_METERS
from review_app.app.pages.distance_review.reference_media import frame_time
from review_app.app.pages.distance_review.session import ZERO_DISTANCE_CHOICE, ZERO_DISTANCE_ID
from review_app.app.translations import t


@dataclass(frozen=True)
class ReferenceViewsContext:
    has_camera: bool
    references: list[dict]
    reference: dict | None
    reference_duration: float
    frames: list[dict]
    selection_id: str | None
    skip_choices: tuple[dict, ...]
    busy_state: object
    distance_locked: bool
    reference_player: object | None
    render_player: Callable[[dict, float, object], object]
    reference_name: Callable[[dict], str]
    prepare_reference: Callable
    refresh_references: Callable
    cycle_reference: Callable[[int], object]
    open_reference: Callable[[dict], object]
    preview_frame: Callable[[dict], object]
    configure_distance: Callable[[float], object]
    add_distance: Callable
    step_reference: Callable[[int], object]
    move_distance: Callable[[int], object]
    reference_thumbnail_path: Callable[[dict], Path | None]
    load_reference_thumbnail: Callable[[dict, object], object]
    render_controls: Callable
    render_frames: Callable
    render_samples: Callable
    render_actions: Callable
    saved_reference_unavailable: bool = False


def frames_by_distance(frames: list[dict], selected_id: str | None) -> dict[float, dict]:
    choices = {frame["distance_m"]: frame for frame in reversed(frames)}
    selected = next((frame for frame in frames if frame["id"] == selected_id), None)
    if selected:
        choices[selected["distance_m"]] = selected
    return choices


def render_references(context: ReferenceViewsContext) -> None:
    with ui.card().classes("distance-panel distance-player") as panel:
        with ui.row().classes("distance-panel-heading w-full items-center gap-1 no-wrap"):
            ui.label(t("distance_references")).classes(
                "text-subtitle1 ellipsis col min-w-0"
            ).tooltip(t("distance_references"))
            ui.space()
            ui.button(icon="refresh", on_click=context.refresh_references).props(
                "flat round dense"
            ).tooltip(t("distance_refresh_references")).bind_enabled_from(
                context.busy_state, "busy", backward=lambda busy: context.has_camera and not busy
            )
        if context.reference:
            context.render_player(context.reference, context.reference_duration, panel)
        else:
            with ui.column().classes(
                "distance-placeholder w-full items-center justify-center q-pa-md"
            ):
                ui.icon("compare", size="lg").classes("text-grey-5")
                ui.label(
                    t("distance_no_references")
                    if not context.references
                    else t("distance_select_clip")
                ).classes("text-caption text-grey-5 text-center")
        context.render_controls()


def render_reference_controls(context: ReferenceViewsContext) -> None:
    with ui.column().classes("distance-reference-controls w-full"):
        context.render_frames()
        context.render_samples()
        context.render_actions()
        with ui.row().classes("distance-reference-browser"):
            ui.button(
                t("distance_previous_reference_shortcut"),
                icon="skip_previous",
                on_click=lambda: context.cycle_reference(-1),
            ).props('flat dense size=sm data-distance-action="previous-reference"').tooltip(
                t("distance_previous_reference")
            ).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: len(context.references) > 1 and not busy,
            )
            with ui.row().classes("distance-reference-clips"):
                for reference in context.references:
                    name = context.reference_name(reference)
                    active = bool(context.reference and context.reference["id"] == reference["id"])
                    with (
                        ui.button(on_click=lambda _e, row=reference: context.open_reference(row))
                        .classes("distance-reference-clip" + (" active" if active else ""))
                        .props(f'flat no-caps aria-pressed="{str(active).lower()}"')
                        .tooltip(name)
                        .bind_enabled_from(
                            context.busy_state, "busy", backward=lambda busy: not busy
                        )
                    ):
                        thumbnail = context.reference_thumbnail_path(reference)
                        image = (
                            ui.image(thumbnail if thumbnail and thumbnail.is_file() else "")
                            .classes("distance-reference-thumbnail")
                            .props("no-spinner no-transition")
                        )
                        ui.label(name).classes("text-caption ellipsis w-full")
                        if not reference["is_tagged"]:
                            ui.label(t("distance_saved_reference")).classes(
                                "text-caption text-grey-6"
                            )
                        if thumbnail and not thumbnail.is_file():
                            asyncio.create_task(context.load_reference_thumbnail(reference, image))
            ui.button("Tab", icon="skip_next", on_click=lambda: context.cycle_reference(1)).props(
                'flat dense size=sm data-distance-action="next-reference"'
            ).tooltip(t("distance_cycle_references")).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: len(context.references) > 1 and not busy,
            )


def render_frames(context: ReferenceViewsContext) -> None:
    if context.reference_player:
        dimmed = context.saved_reference_unavailable or context.selection_id in {
            ZERO_DISTANCE_ID,
            *(choice["id"] for choice in context.skip_choices),
        }
        context.reference_player.classes(
            add="distance-reference-dimmed" if dimmed else "",
            remove="" if dimmed else "distance-reference-dimmed",
        )
    if context.reference and context.reference_player:
        ui.label(t("distance_on_demand_help")).classes("text-caption text-grey-6")
        with ui.row().classes("items-center gap-1"):
            ui.label(t("distance_frame_steps")).classes("text-caption text-grey-6")
            for delta in (-5, -1, 1, 5):
                ui.button(
                    f"{delta:+d}",
                    on_click=lambda _e, delta=delta: context.step_reference(delta),
                ).props(f'flat dense data-reference-frame-step="{delta}"').bind_enabled_from(
                    context.busy_state, "busy", backward=lambda busy: not busy
                )
            ui.button(icon="bookmark_add", on_click=context.prepare_reference).props(
                'flat round dense data-distance-action="edit-reference"'
            ).tooltip(t("distance_prepare_reference")).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: bool(context.reference and not busy),
            )
    configured = frames_by_distance(context.frames, context.selection_id)
    configured[0] = ZERO_DISTANCE_CHOICE
    choice_count = len(configured) + len(context.skip_choices)
    with ui.element("div").classes("distance-choice-toolbar"):
        with ui.row().classes("distance-choice-navigation items-center gap-1 no-wrap"):
            ui.button(
                icon="keyboard_arrow_down", on_click=lambda: context.move_distance(-1)
            ).props('flat round dense data-distance-action="distance-down"').tooltip(
                t("distance_decrease")
            ).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: bool(
                    choice_count > 1 and not busy and not context.distance_locked
                ),
            )
            ui.button(icon="keyboard_arrow_up", on_click=lambda: context.move_distance(1)).props(
                'flat round dense data-distance-action="distance-up"'
            ).tooltip(t("distance_increase")).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: bool(
                    choice_count > 1 and not busy and not context.distance_locked
                ),
            )
            ui.icon("keyboard", size="xs").classes("text-grey-6").tooltip(t("distance_arrow_help"))
        with ui.row().classes("distance-frames") as distance_strip:
            meters = sorted(
                configured.keys()
                | (set(DISTANCE_REFERENCE_METERS) if context.reference else set())
            )
            for distance in meters:
                frame = configured.get(distance)
                if frame is None:
                    ui.button(
                        f"{distance:g} m",
                        icon="add",
                        on_click=lambda _e, distance=distance: context.configure_distance(
                            distance
                        ),
                    ).props(
                        f'dense outline data-reference-distance="{distance:g}" '
                        'data-reference-state="unconfigured"'
                    ).classes("distance-unconfigured").tooltip(
                        t("distance_set_reference")
                    ).bind_enabled_from(
                        context.busy_state,
                        "busy",
                        backward=lambda busy: bool(
                            context.reference_player and not busy and not context.distance_locked
                        ),
                    )
                else:
                    ui.button(
                        f"{frame['distance_m']:g} m",
                        icon="check",
                        on_click=lambda _e, frame=frame: context.preview_frame(frame),
                    ).props(
                        (
                            "dense unelevated"
                            if frame["id"] == context.selection_id
                            else "dense outline"
                        )
                        + f' aria-pressed="{str(frame["id"] == context.selection_id).lower()}"'
                        + f' data-reference-distance="{distance:g}" '
                        'data-reference-state="configured"'
                    ).tooltip(
                        t("distance_zero_no_reference")
                        if distance == 0
                        else frame_time(frame["offset_ms"])
                    ).bind_enabled_from(
                        context.busy_state,
                        "busy",
                        backward=lambda busy: not busy and not context.distance_locked,
                    )
        ui.run_javascript(
            Path(__file__)
            .with_name("distance_choice_scroll.js")
            .read_text()
            .replace("__STRIP_ID__", str(distance_strip.id))
        )
        with ui.row().classes("distance-visibility-choices gap-1 no-wrap"):
            ui.button(t("distance_add_distance"), icon="add", on_click=context.add_distance).props(
                'dense outline data-distance-action="add-distance"'
            ).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: bool(
                    context.reference
                    and context.reference_player
                    and not busy
                    and not context.distance_locked
                ),
            )
            for choice in context.skip_choices:
                ui.button(
                    t(choice["label"]),
                    color="grey-7",
                    on_click=lambda _e, choice=choice: context.preview_frame(choice),
                ).props(
                    "dense unelevated" if choice["id"] == context.selection_id else "dense outline"
                ).bind_enabled_from(
                    context.busy_state,
                    "busy",
                    backward=lambda busy: not busy and not context.distance_locked,
                )

    selected = next(
        (frame for frame in context.frames if frame["id"] == context.selection_id), None
    )
    if selected is None:
        return
    variants = sorted(
        (frame for frame in context.frames if frame["distance_m"] == selected["distance_m"]),
        key=lambda frame: frame["offset_ms"],
    )
    if len(variants) <= 1:
        return
    with (
        ui.row()
        .classes("distance-frames items-center")
        .props(f'role="group" aria-label="{t("distance_reference_timestamps")}"')
    ):
        ui.label(t("distance_reference_timestamps")).classes("text-caption shrink-0")
        for frame in variants:
            active = frame["id"] == context.selection_id
            ui.button(
                frame_time(frame["offset_ms"]),
                on_click=lambda _e, frame=frame: context.preview_frame(frame),
            ).props(
                ("dense unelevated" if active else "dense outline")
                + f' aria-pressed="{str(active).lower()}"'
            ).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: not busy and not context.distance_locked,
            )
