from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nicegui import ui
from sqlalchemy.exc import IntegrityError

from review_app.app.pages.distance_review.anchor_drawing import (
    FixedFrameAnchorContext,
    render_fixed_frame_anchor_preview,
)
from review_app.app.pages.distance_review.reference_media import frame_time
from review_app.app.translations import t


@dataclass(frozen=True)
class AnchorDialogContext:
    row: dict
    player_source: str
    duration_sec: float
    frame_rate: float | None
    filters: dict
    species_options: dict[str, str]
    default_species: str | None
    guard: Callable
    is_current: Callable[[ui.dialog], bool]
    create_individual: Callable[[str, int, dict[str, float]], object]
    save_anchor: Callable[[dict[str, float]], object]


def _fixed_preview(context: AnchorDialogContext, dialog, draft, on_anchor) -> None:
    render_fixed_frame_anchor_preview(
        FixedFrameAnchorContext(
            player_source=context.player_source,
            duration_sec=context.duration_sec,
            frame_rate=context.frame_rate,
            guard=context.guard,
            is_current=lambda: context.is_current(dialog),
            on_anchor=on_anchor,
        ),
        dialog,
        draft,
        context.filters,
    )


async def open_redraw_anchor_dialog(context: AnchorDialogContext) -> None:
    row = context.row
    offset_ms = row["first_seen_offset_ms"]
    draft = dict(row)
    geometry = None
    with ui.dialog() as dialog, ui.card().classes("distance-dialog distance-player"):
        ui.label(t("distance_redraw_box")).classes("text-h6")
        ui.label(t("distance_redraw_box_help", number=row["individual_number"])).classes(
            "text-caption text-grey-6"
        )
        ui.label(t("distance_first_seen_at", time=frame_time(offset_ms))).classes(
            "text-caption text-grey-6"
        )

        def on_anchor(anchor):
            nonlocal geometry
            geometry = anchor
            save_button.set_enabled(True)

        _fixed_preview(context, dialog, draft, on_anchor)

        @context.guard
        async def save():
            if not context.is_current(dialog):
                return
            if geometry is None:
                ui.notify(t("distance_creation_box_required"), type="warning")
                return
            save_button.set_enabled(False)
            try:
                await context.save_anchor(geometry)
                ui.notify(t("distance_marker_saved"), type="positive")
                dialog.close()
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
            finally:
                if dialog.value:
                    save_button.set_enabled(geometry is not None)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")
            save_button = ui.button(t("save"), on_click=save)
            save_button.set_enabled(False)
    dialog.open()


async def open_create_individual_dialog(context: AnchorDialogContext) -> None:
    row = context.row
    offset_ms = row["first_seen_offset_ms"]
    anchor = None
    with ui.dialog() as dialog, ui.card().classes("distance-dialog distance-player"):
        ui.label(t("distance_add_individual")).classes("text-h6")
        species = ui.select(
            context.species_options,
            value=context.default_species,
            label=t("species_label"),
            with_input=True,
        ).classes("w-full")
        ui.label(t("distance_first_seen_at", time=frame_time(offset_ms)))
        ui.label(t("distance_creation_box_help")).classes("text-caption text-grey-6")

        def on_anchor(geometry):
            nonlocal anchor
            anchor = geometry
            create_button.set_enabled(True)

        _fixed_preview(context, dialog, row, on_anchor)

        @context.guard
        async def create():
            if not context.is_current(dialog):
                return
            if not species.value:
                ui.notify(t("distance_choose_species"), type="warning")
                return
            if anchor is None:
                ui.notify(t("distance_creation_box_required"), type="warning")
                return
            create_button.set_enabled(False)
            try:
                if await context.create_individual(species.value, offset_ms, anchor):
                    dialog.close()
            except (ValueError, IntegrityError) as exc:
                ui.notify(
                    str(exc) if isinstance(exc, ValueError) else t("distance_duplicate"),
                    type="negative",
                )
            finally:
                if dialog.value:
                    create_button.set_enabled(anchor is not None)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")
            create_button = ui.button(t("distance_create_individual"), on_click=create)
            create_button.set_enabled(False)
    dialog.open()
