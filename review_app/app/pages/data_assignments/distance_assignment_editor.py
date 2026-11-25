"""Select Distance work by target species and optional camera-local time."""

import asyncio
from collections.abc import Awaitable, Callable
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
    invalidate: Callable[[], None]
    load: Callable[[], Awaitable[None]]


def render_distance_editor(
    dp, project_id, require_project, names, distance_species, distance_species_options
):
    distance_rows = {}
    ui.label(t("handoff_distance_help")).classes("text-body2 text-grey-7")

    def time_fields(marker):
        with ui.row().classes("w-full gap-3"):
            start = ui.input(t("handoff_start"), placeholder="18:00").props("dense")
            end = ui.input(t("handoff_end"), placeholder="06:00").props("dense")
            start.mark(f"{marker}-start")
            end.mark(f"{marker}-end")
        return start, end

    with ui.expansion(t("assign_shared_recording_time"), icon="schedule").classes("w-full"):
        shared_start, shared_end = time_fields("distance-shared-time")
        ui.label(t("handoff_time_help")).classes("text-caption")
    row_container = ui.column().classes("w-full gap-3")

    def add_distance_row(target_species):
        revision = 0
        loaded = None
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
            camera_warning = ui.label("").classes("text-caption text-warning")
            camera_warning.set_visibility(False)
            with ui.column().classes("w-full gap-2") as options:
                override = ui.checkbox(t("assign_override_recording_time"))
                with ui.column().classes("w-full gap-1").bind_visibility_from(override, "value"):
                    start, end = time_fields("distance-species-time")
                    ui.label(t("handoff_time_help")).classes("text-caption")
                selection_status = ui.label("").classes("text-caption whitespace-pre-wrap")
                videos = AssignmentVideoTable(
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
                    marker="distance-videos",
                )
            options.set_visibility(False)

        def filters() -> tuple[str | None, str | None]:
            fields = (start, end) if override.value else (shared_start, shared_end)
            return (fields[0].value or "").strip() or None, (fields[1].value or "").strip() or None

        def invalidate():
            nonlocal revision, loaded
            revision += 1
            loaded = None
            videos.set_rows([])
            count.set_text(t("assign_loading"))
            selection_status.set_text("")
            camera_warning.set_visibility(False)

        async def load():
            nonlocal revision, loaded
            revision += 1
            current, chosen = revision, filters()
            try:
                require_project()
                result = await run.io_bound(
                    dp.preview_assignment_distance_selection,
                    project_id,
                    target_species,
                    start_time=chosen[0],
                    end_time=chosen[1],
                )
                if current != revision or card.is_deleted:
                    return
                require_project()
                loaded = chosen
                unresolved_camera = result["unresolved_camera_count"]
                camera_warning.set_text(t("handoff_unresolved_camera", count=unresolved_camera))
                camera_warning.set_visibility(unresolved_camera > 0)
                videos.set_rows(
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
                    ]
                )
                video_count = len(videos.rows)
                count.set_text(t("assign_video_count", count=video_count))
                message = t(
                    "handoff_selection_result",
                    count=video_count,
                    unknown=result["unknown_time_count"],
                )
                if not video_count:
                    message += "\n" + t("data_empty_scope")
                selection_status.set_text(message)
            except Exception as exc:
                if current == revision and not card.is_deleted:
                    count.set_text(t("assign_selection_error"))
                    selection_status.set_text(_error_message(exc))
                    options.set_visibility(True)

        for field in (override, start, end):
            field.on_value_change(invalidate)
            field.on_value_change(load)

        def request():
            if loaded != filters() or not videos.rows:
                raise ValueError(t("handoff_select_distance"))
            return AssignmentRequest(
                recipient.value or "",
                tuple(row["id"] for row in videos.rows),
                "distance",
                target_species,
                *loaded,
            )

        distance_rows[target_species] = DistanceRow(card, recipient, request, invalidate, load)
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

    def invalidate_shared():
        for row in distance_rows.values():
            row.invalidate()

    async def reload_shared():
        await asyncio.gather(*(row.load() for row in distance_rows.values()))

    for field in (shared_start, shared_end):
        field.on_value_change(invalidate_shared)
        field.on_value_change(reload_shared)
    distance_species.on_value_change(select_distance_species)
    return requests, refresh_annotators
