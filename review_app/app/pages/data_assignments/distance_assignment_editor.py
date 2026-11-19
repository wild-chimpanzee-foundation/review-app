"""Select Distance work by target species and optional camera-local time."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from nicegui import run, ui

from review_app.app.components.assignment_handoff import _error_message
from review_app.app.components.assignment_video_table import AssignmentVideoTable
from review_app.app.translations import t
from review_app.backend.annotation_package.scope import AssignmentRequest


@dataclass
class DistanceRow:
    card: ui.card
    recipient: ui.select
    request: Callable[[], AssignmentRequest]


def render_distance_editor(
    dp, project_id, require_project, names, distance_species, distance_species_options
):
    distance_rows = {}
    ui.label(t("handoff_distance_help")).classes("text-body2 text-grey-7")
    row_container = ui.column().classes("w-full gap-3")

    def add_distance_row(target_species):
        revision = 0
        loaded = None
        unknown = 0
        with row_container, ui.column().classes("w-full gap-1 border-b pb-2") as card:
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.label(distance_species_options[target_species]).classes("grow min-w-48")
                recipient = (
                    ui.select(names, label=t("handoff_recipient"))
                    .props("dense outlined")
                    .classes("grow min-w-40")
                )
                count = ui.label("").classes("text-body2")
                ui.button(
                    icon="tune",
                    on_click=lambda: options.set_visibility(not options.visible),
                ).props("flat round dense").tooltip(t("assign_row_options")).mark(
                    "distance-row-options"
                )

                def remove():
                    distance_species.set_value(
                        [value for value in distance_species.value if value != target_species]
                    )

                ui.button(icon="close", on_click=remove).props("flat round dense").tooltip(
                    t("assign_remove_row")
                )
            with ui.column().classes("w-full gap-2") as options:
                selection_status = ui.label("").classes("text-caption whitespace-pre-wrap")
                selection = AssignmentVideoTable(
                    [
                        ("path", t("handoff_column_video")),
                        ("camera", t("distribution_camera_col")),
                        ("time", t("assign_camera_time")),
                        ("availability", t("assign_availability")),
                    ],
                    filters=[
                        ("camera", t("distribution_camera_col")),
                        ("availability", t("assign_availability")),
                    ],
                    selectable=True,
                    on_selection=lambda _: update_count(),
                    marker="distance-videos",
                )
                with ui.expansion(t("data_advanced"), icon="schedule").classes("w-full"):
                    with ui.row().classes("w-full gap-3"):
                        start = ui.input(t("handoff_start"), placeholder="18:00").props("dense")
                        end = ui.input(t("handoff_end"), placeholder="06:00").props("dense")
                    ui.label(t("handoff_time_help")).classes("text-caption")
            options.set_visibility(False)

        def filters():
            return (
                target_species,
                (start.value or "").strip() or None,
                (end.value or "").strip() or None,
            )

        def invalidate():
            nonlocal revision, loaded
            revision += 1
            loaded = None
            selection.set_rows([])
            count.set_text(t("assign_loading"))
            selection_status.set_text("")

        def update_count():
            if loaded is not None:
                count.set_text(t("assign_video_count", count=len(selection.selection)))
                message = t(
                    "handoff_selection_result",
                    count=len(selection.selection),
                    unknown=unknown,
                )
                if not selection.rows:
                    message += "\n" + t("data_empty_scope")
                selection_status.set_text(message)

        async def load():
            nonlocal revision, loaded, unknown
            revision += 1
            current, chosen = revision, filters()
            if not chosen[0]:
                return
            try:
                require_project()
                result = await run.io_bound(
                    dp.preview_assignment_distance_selection,
                    project_id,
                    chosen[0],
                    start_time=chosen[1],
                    end_time=chosen[2],
                )
                if current != revision or card.is_deleted:
                    return
                require_project()
                loaded = chosen
                unknown = result["unknown_time_count"]
                selection.set_rows(
                    [
                        {
                            "id": video["video_id"],
                            "path": video["video_path"],
                            "camera": video["camera_id"] or "",
                            "time": video["recorded_start"] or t("handoff_unknown_time"),
                            "availability": t(
                                "assign_missing" if video["is_missing"] else "assign_available"
                            ),
                        }
                        for video in result["videos"]
                    ],
                    select_all=True,
                )
            except Exception as exc:
                if current == revision and not card.is_deleted:
                    count.set_text(t("assign_selection_error"))
                    selection_status.set_text(_error_message(exc))
                    options.set_visibility(True)

        for field in (start, end):
            field.on_value_change(invalidate)
            field.on_value_change(load)

        def request():
            if loaded != filters() or not selection.selection:
                raise ValueError(t("handoff_select_distance"))
            return AssignmentRequest(
                recipient.value or "", selection.selection, "distance", *filters()
            )

        distance_rows[target_species] = DistanceRow(card, recipient, request)
        invalidate()
        return load

    async def select_distance_species():
        selected = distance_species.value
        for target in distance_rows.keys() - set(selected):
            distance_rows.pop(target).card.delete()
        loaders = [add_distance_row(target) for target in selected if target not in distance_rows]
        await asyncio.gather(*(load() for load in loaders))

    def requests():
        if not distance_rows:
            raise ValueError(t("handoff_select_distance"))
        return tuple(row.request() for row in distance_rows.values())

    def refresh_annotators():
        for row in distance_rows.values():
            row.recipient.set_options(names)

    distance_species.on_value_change(select_distance_species)
    return requests, refresh_annotators
