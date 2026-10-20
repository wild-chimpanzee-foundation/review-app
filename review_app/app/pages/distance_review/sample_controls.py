from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from nicegui import ui

from review_app.app.pages.distance_review.reference_media import frame_time
from review_app.app.pages.distance_review.session import SampleSelection
from review_app.app.pages.review.annotations import _shortcut_badge
from review_app.app.translations import t


@dataclass(frozen=True)
class SampleControlsContext:
    review_active: bool
    individual: dict | None
    individual_id: str | None
    sample_offset: int | None
    sample: dict | None
    carrier: dict | None
    values: dict
    behavior_options: dict[str, str]
    individuals: list[dict]
    individual_label: Callable[[dict], str]
    frames: list[dict]
    selection: SampleSelection | None
    skip_choices: tuple[dict, ...]
    animal_player: object | None
    busy: bool
    video_completed: bool
    remaining_offsets: list[int]
    all_individuals_completed: bool
    on_confirm: Callable
    on_select_carrier: Callable[[str, int], object]
    on_apply_to_end: Callable
    on_finish_video: Callable


def _selected_frame(context: SampleControlsContext) -> dict | None:
    if not context.selection or context.selection.kind != "frame":
        return None
    return next((row for row in context.frames if row["id"] == context.selection.value), None)


def _selected_skip(context: SampleControlsContext) -> str | None:
    if not context.selection or context.selection.kind != "visibility":
        return None
    return next(
        (
            choice["skip_reason"]
            for choice in context.skip_choices
            if choice["id"] == context.selection.value
        ),
        None,
    )


def render_sample_controls(context: SampleControlsContext) -> None:
    if not context.review_active:
        return
    with ui.column().classes("distance-sample-controls w-full"):
        if not context.individual or context.sample_offset is None:
            return
        if context.carrier:
            ui.button(
                t("distance_carried_by", number=context.carrier["individual_number"]),
                icon="link",
                on_click=lambda: context.on_select_carrier(
                    context.carrier["id"], context.sample_offset
                ),
            ).props(
                'flat dense no-caps icon-right=arrow_forward data-distance-action="select-carrier"'
            ).tooltip(t("distance_carried_distance_help")).set_enabled(not context.busy)
        values = context.values
        skipping = bool(
            _selected_skip(context) or (context.carrier and context.sample["skip_reason"])
        )
        with ui.element("div").classes("distance-sample-fields"):
            behaviors = (
                ui.select(
                    context.behavior_options,
                    value=values["behavior_ids"],
                    multiple=True,
                    label=t("distance_behaviors"),
                    on_change=lambda e: values.update(behavior_ids=list(e.value or [])),
                )
                .classes("distance-behavior-field")
                .props("dense outlined use-chips")
                .tooltip(t("distance_behaviors_help"))
            )
            behaviors.set_enabled(not skipping)
            carries = (
                ui.checkbox(
                    t("distance_carries_infant"),
                    value=values["carries"],
                    on_change=lambda e: values.update(carries=e.value),
                )
                .props("dense")
                .classes("distance-carry-toggle")
            )
            infant_options = {
                row["id"]: context.individual_label(row)
                for row in context.individuals
                if row["id"] != context.individual_id
                and row["age"] == "Infant"
                and row["species_id"] == context.individual["species_id"]
            }
            infant_options["_new"] = t("distance_new_infant")
            infant = (
                ui.select(
                    infant_options,
                    value=values["infant_id"],
                    label=t("distance_infant_identity"),
                    on_change=lambda e: values.update(infant_id=e.value),
                )
                .classes("distance-infant-field")
                .props("dense outlined")
            )
            infant.bind_visibility_from(carries, "value")
            if context.individual["age"] == "Infant":
                carries.set_enabled(False)
        with ui.row().classes("distance-sample-summary"):
            sample = context.sample
            if sample:
                if sample["status"] == "skipped":
                    ui.label(
                        t(
                            "distance_saved_skip",
                            reason=t(
                                "distance_absent"
                                if sample["skip_reason"] == "individual absent"
                                else "distance_midpoint_hidden"
                            ),
                        )
                    ).classes("text-warning")
                else:
                    ui.label(t("distance_saved_annotation", meters=sample["distance_m"])).classes(
                        "text-positive"
                    )
            if context.carrier:
                return
            frame = _selected_frame(context)
            skip_reason = _selected_skip(context)
            if skip_reason:
                choice = next(
                    item for item in context.skip_choices if item["skip_reason"] == skip_reason
                )
                ui.label(t("distance_selected_choice", choice=t(choice["label"]))).classes(
                    "text-primary"
                )
            elif frame:
                ui.label(t("distance_selected_reference", meters=frame["distance_m"])).classes(
                    "text-primary"
                )
            else:
                ui.label(t("distance_select_reference")).classes("text-grey-6")


def render_sample_actions(context: SampleControlsContext) -> None:
    if not context.review_active or not context.individual or context.sample_offset is None:
        return
    frame = _selected_frame(context)
    valid_choice = bool(context.carrier or frame or _selected_skip(context))
    with ui.row().classes("distance-actions"):
        with (
            ui.button(on_click=context.on_confirm, color="warning")
            .classes("distance-confirm")
            .props('data-distance-action="confirm"')
        ) as confirm:
            with ui.row().classes("items-center justify-between w-full q-px-xs no-wrap"):
                ui.label(t("distance_confirm_sample")).classes("text-center col")
                _shortcut_badge("↵ Enter")
        confirm.set_enabled(bool(valid_choice and context.animal_player and not context.busy))
        with (
            ui.button(
                t("distance_apply_to_end"),
                icon="playlist_add_check",
                on_click=context.on_apply_to_end,
            )
            .props('outline dense data-distance-action="apply-to-end"')
            .classes("distance-apply-to-end")
        ) as apply_to_end:
            _shortcut_badge("Ctrl+Enter")
        apply_to_end.set_enabled(
            bool(
                context.remaining_offsets
                and valid_choice
                and not context.busy
                and not context.carrier
            )
        )


def render_video_actions(context: SampleControlsContext, *, compact: bool = False) -> None:
    if compact:
        ui.button(icon="done_all", color="positive", on_click=context.on_finish_video).props(
            'unelevated dense data-distance-action="rail-finish-video"'
        ).classes("distance-rail-finish").tooltip(
            t("distance_video_completed" if context.video_completed else "distance_finish_video")
        ).set_enabled(
            bool(
                context.review_active
                and context.individual
                and context.sample_offset is not None
                and context.all_individuals_completed
                and not context.video_completed
                and not context.busy
            )
        )
        return
    if not context.review_active or not context.individual or context.sample_offset is None:
        return
    with ui.row().classes("w-full items-center justify-between gap-xs"):
        if context.individual["review_status"] == "completed":
            ui.label(t("distance_individual_completed")).classes("text-caption text-positive")
        if context.video_completed:
            ui.label(t("distance_video_completed")).classes("text-caption text-positive")
        else:
            ui.button(
                t("distance_finish_video"), icon="done_all", on_click=context.on_finish_video
            ).props(
                'color=positive unelevated size=md data-distance-action="finish-video"'
            ).set_enabled(bool(context.all_individuals_completed and not context.busy))
        ui.icon("info_outline", size="xs").classes("text-grey-6").tooltip(
            t("distance_correction_help")
        )


@dataclass(frozen=True)
class ApplyToEndDialogContext:
    assignment: dict
    video_id: str
    individual_id: str
    sample_offset: int
    remaining_offsets: list[int]
    individual_number: int
    choice_label: str
    guard: Callable
    is_current: Callable[[], bool]
    apply: Callable[[], object]


async def open_apply_to_end_dialog(context: ApplyToEndDialogContext) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width:480px"):
        ui.label(t("distance_apply_to_end")).classes("text-h6")
        ui.label(
            t(
                "distance_apply_to_end_confirm",
                choice=context.choice_label,
                count=len(context.remaining_offsets),
                number=context.individual_number,
                time=frame_time(context.sample_offset),
            )
        )
        ui.label(t("distance_apply_to_end_help")).classes("text-caption text-grey-6")

        @context.guard
        async def confirm():
            if not dialog.value or not context.is_current():
                return
            confirm_button.set_enabled(False)
            try:
                saved = await context.apply()
                if saved is not False:
                    dialog.close()
            finally:
                if dialog.value:
                    confirm_button.set_enabled(True)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")
            confirm_button = ui.button(t("distance_apply_and_overwrite"), on_click=confirm)
    dialog.open()
