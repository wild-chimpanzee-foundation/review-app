from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nicegui import ui

from review_app.app.pages.distance_review.reference_media import frame_time
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
    busy: bool
    busy_state: object
    reference_player: object | None
    render_player: Callable[[dict, float, object], object]
    reference_name: Callable[[dict], str]
    prepare_reference: Callable
    refresh_references: Callable
    seek_reference: Callable
    reference_load_failed: Callable
    cycle_reference: Callable
    open_reference: Callable[[dict], object]
    preview_frame: Callable[[dict], object]
    move_distance: Callable[[int], object]
    reference_thumbnail_path: Callable[[dict], Path | None]
    load_reference_thumbnail: Callable[[dict, object], object]
    render_controls: Callable
    render_frames: Callable
    render_samples: Callable


def distance_choices(context: ReferenceViewsContext) -> list[dict]:
    choices = {frame["distance_m"]: frame for frame in reversed(context.frames)}
    selected = next(
        (frame for frame in context.frames if frame["id"] == context.selection_id), None
    )
    if selected:
        choices[selected["distance_m"]] = selected
    return [choices[meters] for meters in sorted(choices)] + list(context.skip_choices)


def render_references(context: ReferenceViewsContext) -> None:
    with ui.card().classes("distance-panel distance-player") as panel:
        with ui.row().classes("distance-panel-heading w-full items-center gap-1 no-wrap"):
            ui.label(t("distance_references")).classes(
                "text-subtitle1 ellipsis col min-w-0"
            ).tooltip(t("distance_references"))
            ui.space()
            ui.button(icon="bookmark_add", on_click=context.prepare_reference).props(
                "flat round dense"
            ).tooltip(t("distance_prepare_reference")).bind_enabled_from(
                context.busy_state,
                "busy",
                backward=lambda busy: bool(context.reference and not busy),
            )
            ui.button(icon="refresh", on_click=context.refresh_references).props(
                "flat round dense"
            ).tooltip(t("distance_refresh_references")).bind_enabled_from(
                context.busy_state, "busy", backward=lambda busy: context.has_camera and not busy
            )
        if context.reference:
            player = context.render_player(context.reference, context.reference_duration, panel)
            player.on(
                "loadedmetadata", lambda: context.seek_reference() if not context.busy else None
            )
            player.on("error", context.reference_load_failed)
            selected = next(
                (frame for frame in context.frames if frame["id"] == context.selection_id), None
            )
            if selected:
                context.seek_reference()
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
    context.render_frames()
    context.render_samples()
    with ui.row().classes("w-full items-center justify-between"):
        ui.label(t("distance_reference_clip")).classes("text-caption text-grey-6")
        ui.button(
            t("distance_cycle_references"), icon="skip_next", on_click=context.cycle_reference
        ).props('flat dense size=sm data-distance-action="next-reference"').bind_enabled_from(
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
                .bind_enabled_from(context.busy_state, "busy", backward=lambda busy: not busy)
            ):
                thumbnail = context.reference_thumbnail_path(reference)
                image = (
                    ui.image(thumbnail if thumbnail and thumbnail.is_file() else "")
                    .classes("distance-reference-thumbnail")
                    .props("no-spinner no-transition")
                )
                ui.label(name).classes("text-caption ellipsis w-full")
                if not reference["is_tagged"]:
                    ui.label(t("distance_saved_reference")).classes("text-caption text-grey-6")
                if thumbnail and not thumbnail.is_file():
                    asyncio.create_task(context.load_reference_thumbnail(reference, image))


def render_frames(context: ReferenceViewsContext) -> None:
    if context.reference_player:
        context.reference_player.classes(
            add=(
                "distance-reference-dimmed"
                if context.selection_id in {choice["id"] for choice in context.skip_choices}
                else ""
            ),
            remove=(
                ""
                if context.selection_id in {choice["id"] for choice in context.skip_choices}
                else "distance-reference-dimmed"
            ),
        )
    if not context.frames:
        ui.label(t("distance_no_prepared_frames")).classes("text-caption text-grey-6")
    choices = distance_choices(context)
    position = next(
        (i for i, frame in enumerate(choices) if frame["id"] == context.selection_id), -1
    )
    with ui.row().classes("w-full items-center gap-1"):
        ui.button(icon="keyboard_arrow_down", on_click=lambda: context.move_distance(-1)).props(
            'flat round dense data-distance-action="distance-down"'
        ).tooltip(t("distance_decrease")).bind_enabled_from(
            context.busy_state,
            "busy",
            backward=lambda busy: bool(choices and position > 0 and not busy),
        )
        ui.button(icon="keyboard_arrow_up", on_click=lambda: context.move_distance(1)).props(
            'flat round dense data-distance-action="distance-up"'
        ).tooltip(t("distance_increase")).bind_enabled_from(
            context.busy_state,
            "busy",
            backward=lambda busy: bool(choices and position < len(choices) - 1 and not busy),
        )
        ui.label(t("distance_arrow_help")).classes("text-caption text-grey-6")
    with ui.row().classes("distance-frames"):
        for frame in sorted(context.frames, key=lambda row: (row["distance_m"], row["offset_ms"])):
            ui.button(
                f"{frame['distance_m']:g} m",
                on_click=lambda _e, frame=frame: context.preview_frame(frame),
            ).props(
                "dense unelevated" if frame["id"] == context.selection_id else "dense outline"
            ).tooltip(frame_time(frame["offset_ms"]))
    with ui.row().classes("distance-visibility-choices w-full gap-1"):
        for choice in context.skip_choices:
            ui.button(
                t(choice["label"]),
                color="grey-7",
                on_click=lambda _e, choice=choice: context.preview_frame(choice),
            ).props(
                "dense unelevated" if choice["id"] == context.selection_id else "dense outline"
            ).bind_enabled_from(context.busy_state, "busy", backward=lambda busy: not busy)
