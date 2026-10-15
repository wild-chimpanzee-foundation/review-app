from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from review_app.app.pages.distance_review.reference_media import frame_time
from review_app.app.pages.review.annotations import _shortcut_badge
from review_app.app.translations import t


@dataclass(frozen=True)
class IndividualViewsContext:
    review_active: bool
    camera_id: str | None
    has_player: bool
    busy: bool
    busy_state: object
    species_options: dict[str, str]
    individuals: list[dict]
    individual_id: str | None
    tag_options: dict[str, str]
    all_tag_options: dict[str, str]
    samples: list[dict]
    species_choices: Callable[[dict], dict[str, str]]
    label: Callable[[dict], str]
    on_clock: Callable
    on_add: Callable
    on_select: Callable[[str], object]
    on_attribute: Callable[[str, str, object], object]
    on_delete: Callable[[str], object]
    on_redraw: Callable[[str], object]
    on_first_seen: Callable[[str], object]
    render_progress: Callable


def render_individuals(context: IndividualViewsContext) -> None:
    if not context.review_active:
        ui.label(t("distance_verify_required")).classes("text-caption text-grey-6")
        ui.button(t("distance_verify_time"), icon="schedule", on_click=context.on_clock)
    else:
        with ui.row().classes("w-full justify-center q-mb-xs"):
            with (
                ui.button(on_click=context.on_add)
                .props('size=md color=primary outline data-distance-action="add"')
                .style("border-style:dashed") as add
            ):
                with ui.row().classes("items-center justify-between w-full q-px-xs"):
                    with ui.row().classes("items-center gap-xs col justify-center"):
                        ui.icon("add", size="sm")
                        ui.label(t("distance_add_individual"))
                    _shortcut_badge("A")
            add.set_enabled(
                bool(context.has_player and context.camera_id and context.species_options)
            )
    if not context.camera_id:
        ui.label(t("distance_missing_camera")).classes("text-warning")
    if not context.individuals:
        if context.review_active:
            with ui.card().classes("full-width q-pa-sm q-mb-sm"):
                ui.label(t("distance_no_individuals")).classes("text-grey-6")
        return
    for row in context.individuals:
        render_individual_card(context, row)


def render_individual_card(context: IndividualViewsContext, row: dict) -> None:
    active = row["id"] == context.individual_id
    card = (
        ui.card()
        .classes("full-width q-pa-sm q-mb-sm distance-individual-card")
        .style(f"border:2px solid var(--q-{'warning' if active else 'primary'})")
    )
    card._props["data-distance-individual"] = row["id"]
    card._props["data-distance-active"] = "true" if active else "false"
    with card:
        with ui.row().classes("w-full no-wrap gap-2"):
            select = (
                ui.button(
                    f"#{row['individual_number']}",
                    icon="radio_button_checked" if active else "radio_button_unchecked",
                    on_click=lambda: context.on_select(row["id"]),
                )
                .props("flat dense no-caps")
                .classes("self-center")
            )
            select._props["aria-pressed"] = "true" if active else "false"
            select._props["aria-label"] = t(
                "distance_review_individual", number=row["individual_number"]
            )
            select._props["data-distance-action"] = "select-individual"
            select.bind_enabled_from(context.busy_state, "busy", backward=lambda busy: not busy)
            select.tooltip(
                t("distance_active_individual")
                if active
                else t("distance_review_individual", number=row["individual_number"])
            )
            ui.select(
                context.species_choices(row),
                value=row["species_id"],
                label=t("species_label"),
                with_input=True,
                on_change=lambda e: context.on_attribute(row["id"], "species_id", e.value),
            ).classes("col min-w-0").props("dense outlined").bind_enabled_from(
                context.busy_state, "busy", backward=lambda busy: not busy
            )
            (
                ui.button(icon="delete", on_click=lambda: context.on_delete(row["id"]))
                .props("flat color=negative dense")
                .tooltip(t("distance_delete_individual"))
                .props('data-distance-action="delete-individual"')
                .bind_enabled_from(context.busy_state, "busy", backward=lambda busy: not busy)
            )
            ui.button(icon="crop_free", on_click=lambda: context.on_redraw(row["id"])).props(
                "flat round dense size=sm data-distance-action=redraw-anchor"
            ).tooltip(t("distance_redraw_box")).set_enabled(
                bool(context.has_player and not context.busy)
            )
        if not active:
            return
        context.render_progress()
        if any(
            sample["carried_by_individual_id"]
            and row["id"] in (sample["individual_id"], sample["carried_by_individual_id"])
            for sample in context.samples
        ):
            ui.label(t("distance_linked_species_change")).classes("text-caption text-grey-6")
        with ui.row().classes("w-full no-wrap gap-2"):
            ui.select(
                {
                    value: t(f"distance_{value.lower()}")
                    for value in ("Adult", "Adolescent", "Juvenile", "Infant")
                },
                value=row["age"],
                label=t("distance_age"),
                clearable=True,
                on_change=lambda e: context.on_attribute(row["id"], "age", e.value),
            ).classes("flex-1 min-w-0").props("dense outlined").bind_enabled_from(
                context.busy_state, "busy", backward=lambda busy: not busy
            )
            ui.select(
                {value: t(f"distance_{value.lower()}") for value in ("Male", "Female", "Unknown")},
                value=row["sex"],
                label=t("distance_sex"),
                clearable=True,
                on_change=lambda e: context.on_attribute(row["id"], "sex", e.value),
            ).classes("flex-1 min-w-0").props("dense outlined").bind_enabled_from(
                context.busy_state, "busy", backward=lambda busy: not busy
            )
        tag_options = {
            **context.tag_options,
            **{
                tag_id: context.all_tag_options[tag_id]
                for tag_id in row["tag_ids"]
                if tag_id in context.all_tag_options
            },
        }
        ui.select(
            tag_options,
            value=row["tag_ids"],
            label=t("distance_tags"),
            multiple=True,
            with_input=True,
            on_change=lambda e: context.on_attribute(row["id"], "tag_ids", list(e.value or [])),
        ).props("dense outlined use-chips").classes("w-full").bind_enabled_from(
            context.busy_state, "busy", backward=lambda busy: not busy
        )
        with ui.row().classes("w-full items-center gap-xs no-wrap"):
            ui.label(
                t("distance_first_seen_at", time=frame_time(row["first_seen_offset_ms"]))
            ).classes("text-caption text-grey-6 col")
            ui.button(icon="person_pin", on_click=lambda: context.on_first_seen(row["id"])).props(
                "flat round dense size=sm"
            ).tooltip(t("distance_go_first_seen")).set_enabled(bool(context.has_player))
