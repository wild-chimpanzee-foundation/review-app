"""One focused workflow from editable allocation to frozen assignment handoffs."""

import asyncio

from nicegui import run, ui

from review_app.app.components.assignment_handoff import _error_message
from review_app.app.components.assignment_video_table import AssignmentVideoTable
from review_app.app.translations import get_language, t
from review_app.backend.annotation_package.scope import AssignmentRequest

from .assignment_plan import (
    WorkGroup,
    apply_species_request,
    species_requests,
    species_work_groups,
)


def render_assignment_creation(dp, project_id, require_project, on_cancel, on_prepared):
    groups = species_work_groups(dp, project_id)
    names = dp.get_all_annotators()
    distance_rows = []
    requests = ()
    unassigned_ids = ()
    prepared = {}
    busy = False
    adjustment_open = False

    ui.label(t("assign_title")).classes("text-h6")
    ui.label(t("assign_help")).classes("text-body2 text-grey-7")
    editor = ui.column().classes("w-full gap-4")
    review = ui.column().classes("w-full gap-4")
    review.set_visibility(False)
    status = ui.label("").classes("text-negative whitespace-pre-wrap")

    with editor:
        stage = ui.select(
            {"species": t("handoff_stage_species"), "distance": t("handoff_stage_distance")},
            value="species",
            label=t("handoff_stage"),
        ).classes("w-full md:w-80")
        distance_species_options = dp.get_assignment_distance_species_options(
            project_id, get_language()
        )
        distance_species = (
            ui.select(
                distance_species_options,
                multiple=True,
                value=[],
                label=t("assign_distance_species"),
            )
            .props("use-chips outlined")
            .classes("w-full")
            .mark("distance-species")
        )
        distance_species.set_visibility(False)
        with ui.row().classes("w-full items-end gap-3"):
            annotator_name = ui.input(t("distribution_annotator_placeholder")).classes("grow")

            async def add_annotator():
                name = (annotator_name.value or "").strip()
                if not name:
                    return
                add_button.disable()
                try:
                    require_project()
                    await run.io_bound(dp.add_annotator, name)
                    require_project()
                    names[:] = dp.get_all_annotators()
                    annotators.set_options(names)
                    annotators.set_value(list(dict.fromkeys([*annotators.value, name])))
                    for row in distance_rows:
                        row["recipient_input"].set_options(names)
                    annotator_name.set_value("")
                    allocation.refresh()
                except Exception as exc:
                    status.set_text(_error_message(exc))
                finally:
                    add_button.enable()

            add_button = ui.button(
                t("distribution_add_annotator"), on_click=add_annotator, icon="person_add"
            ).props("flat")

        with ui.column().classes("w-full gap-3") as species_editor:
            annotators = (
                ui.select(
                    names,
                    multiple=True,
                    value=names.copy(),
                    label=t("distribution_annotators_label"),
                )
                .props("use-chips")
                .classes("w-full")
            )

            async def distribute():
                if not annotators.value:
                    status.set_text(t("assign_choose_recipients"))
                    return
                auto_button.disable()
                review_button.disable()
                try:
                    require_project()
                    mapping, chunks = await run.io_bound(
                        dp.auto_distribute_flexible, project_id, sorted(annotators.value)
                    )
                    if editor.is_deleted:
                        return
                    require_project()
                    assigned = {key: name for name, keys in mapping.items() for key in keys}
                    groups[:] = [
                        WorkGroup(
                            chunk["chunk_id"],
                            chunk["label"],
                            tuple(chunk["video_ids"]),
                            assigned.get(chunk["chunk_id"]),
                        )
                        for chunk in chunks
                    ]
                    status.set_text("")
                    allocation.refresh()
                except Exception as exc:
                    status.set_text(_error_message(exc))
                finally:
                    auto_button.enable()
                    review_button.enable()

            auto_button = ui.button(
                t("distribution_auto_btn"), on_click=distribute, icon="auto_fix_high"
            ).props("outline")
            ui.label(t("assign_auto_help")).classes("text-caption text-grey-7")

            @ui.refreshable
            def allocation():
                totals = species_requests(groups)
                if totals:
                    with ui.row().classes("gap-3 flex-wrap"):
                        for request in totals:
                            ui.chip(
                                t(
                                    "assign_recipient_count",
                                    name=request.recipient,
                                    count=len(request.video_ids),
                                ),
                                icon="person_outline",
                            ).props("outline")
                else:
                    ui.label(t("assign_no_allocation")).classes("text-grey-7")
                unassigned = sum(len(group.video_ids) for group in groups if not group.recipient)
                if unassigned:
                    ui.label(t("assign_unallocated", count=unassigned)).classes("text-caption")

                def remember_expansion(event):
                    nonlocal adjustment_open
                    adjustment_open = event.value

                with ui.expansion(
                    t("assign_adjust"),
                    icon="tune",
                    value=adjustment_open,
                    on_value_change=remember_expansion,
                ).classes("w-full"):
                    for group in groups:

                        def change(event, chosen=group):
                            chosen.recipient = event.value
                            allocation.refresh()

                        with ui.row().classes("w-full items-center gap-3"):
                            ui.label(group.label or t("assign_other_videos")).classes("grow")
                            ui.label(t("assign_video_count", count=len(group.video_ids)))
                            ui.select(
                                {
                                    None: t("distribution_unassigned"),
                                    **{name: name for name in names},
                                },
                                value=group.recipient,
                                label=t("handoff_recipient"),
                                on_change=change,
                            ).classes("w-56")

            allocation()

        with ui.column().classes("w-full gap-3") as distance_editor:
            ui.label(t("handoff_distance_help")).classes("text-body2 text-grey-7")
            row_container = ui.column().classes("w-full gap-3")

            def add_distance_row(target_species):
                row = {"revision": 0, "loaded": None, "unknown": 0, "species": target_species}
                distance_rows.append(row)
                with row_container, ui.column().classes("w-full gap-1 border-b pb-2") as card:
                    with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                        ui.label(distance_species_options[target_species]).classes("grow min-w-48")
                        recipient = (
                            ui.select(names, label=t("handoff_recipient"))
                            .props("dense outlined")
                            .classes("grow min-w-40")
                        )
                        row["recipient_input"] = recipient
                        count = ui.label("").classes("text-body2")
                        ui.button(
                            icon="tune",
                            on_click=lambda: options.set_visibility(not options.visible),
                        ).props("flat round dense").tooltip(t("assign_row_options")).mark(
                            "distance-row-options"
                        )

                        def remove():
                            distance_species.set_value(
                                [
                                    value
                                    for value in distance_species.value
                                    if value != target_species
                                ]
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
                                start = ui.input(t("handoff_start"), placeholder="18:00").props(
                                    "dense"
                                )
                                end = ui.input(t("handoff_end"), placeholder="06:00").props(
                                    "dense"
                                )
                            ui.label(t("handoff_time_help")).classes("text-caption")
                    options.set_visibility(False)
                row["card"] = card

                def filters():
                    return (
                        target_species,
                        (start.value or "").strip() or None,
                        (end.value or "").strip() or None,
                    )

                def invalidate():
                    row["revision"] += 1
                    row["loaded"] = None
                    selection.set_rows([])
                    count.set_text(t("assign_loading"))
                    selection_status.set_text("")

                def update_count():
                    if row["loaded"] is not None:
                        count.set_text(t("assign_video_count", count=len(selection.selection)))
                        message = t(
                            "handoff_selection_result",
                            count=len(selection.selection),
                            unknown=row["unknown"],
                        )
                        if not selection.rows:
                            message += "\n" + t("data_empty_scope")
                        selection_status.set_text(message)

                async def load():
                    row["revision"] += 1
                    current, chosen = row["revision"], filters()
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
                        if current != row["revision"] or card.is_deleted:
                            return
                        require_project()
                        row["loaded"] = chosen
                        row["unknown"] = result["unknown_time_count"]
                        selection.set_rows(
                            [
                                {
                                    "id": video["video_id"],
                                    "path": video["video_path"],
                                    "camera": video["camera_id"] or "",
                                    "time": video["recorded_start"] or t("handoff_unknown_time"),
                                    "availability": t(
                                        "assign_missing"
                                        if video["is_missing"]
                                        else "assign_available"
                                    ),
                                }
                                for video in result["videos"]
                            ],
                            select_all=True,
                        )
                    except Exception as exc:
                        if current == row["revision"] and not card.is_deleted:
                            count.set_text(t("assign_selection_error"))
                            selection_status.set_text(_error_message(exc))
                            options.set_visibility(True)

                for field in (start, end):
                    field.on_value_change(invalidate)
                    field.on_value_change(load)
                row["request"] = lambda: AssignmentRequest(
                    recipient.value or "", selection.selection, "distance", *filters()
                )
                row["ready"] = lambda: row["loaded"] == filters() and bool(selection.selection)
                invalidate()
                return load

            async def select_distance_species():
                selected = distance_species.value
                for row in distance_rows.copy():
                    if row["species"] not in selected:
                        row["revision"] += 1
                        distance_rows.remove(row)
                        row["card"].delete()
                existing = {row["species"] for row in distance_rows}
                loaders = [add_distance_row(value) for value in selected if value not in existing]
                await asyncio.gather(*(load() for load in loaders))

            distance_species.on_value_change(select_distance_species)
        distance_editor.set_visibility(False)

        def stage_changed():
            species_editor.set_visibility(stage.value == "species")
            distance_editor.set_visibility(stage.value == "distance")
            distance_species.set_visibility(stage.value == "distance")
            status.set_text("")

        stage.on_value_change(stage_changed)

        def show_review():
            nonlocal requests, unassigned_ids
            try:
                require_project()
                if stage.value == "species":
                    requests = species_requests(groups)
                else:
                    if not distance_rows or not all(row["ready"]() for row in distance_rows):
                        raise ValueError(t("handoff_select_distance"))
                    requests = tuple(row["request"]() for row in distance_rows)
                    targets = [request.target_species for request in requests]
                    if len(set(targets)) != len(targets):
                        raise ValueError(t("assign_duplicate_species"))
                if not requests or any(not request.video_ids for request in requests):
                    raise ValueError(t("assign_no_work"))
                if any(request.recipient not in names for request in requests):
                    raise ValueError(t("assign_choose_recipients"))
                unassigned_ids = (
                    tuple(
                        vid for group in groups if not group.recipient for vid in group.video_ids
                    )
                    if stage.value == "species"
                    else ()
                )
                status.set_text("")
                outcomes.clear()
                editor.set_visibility(False)
                review.set_visibility(True)
                render_review.refresh()
            except Exception as exc:
                status.set_text(_error_message(exc))

        with ui.row().classes("w-full justify-between"):
            ui.button(t("cancel"), on_click=on_cancel).props("flat")
            review_button = ui.button(
                t("assign_review"), on_click=show_review, icon="arrow_forward"
            )

    outcomes = {}

    @ui.refreshable
    def render_review():
        if not requests:
            return
        ui.label(t("assign_review")).classes("text-h6")
        ui.label(t("assign_freeze_help")).classes("text-body2 text-grey-7")
        recipients = {}
        for index, request in enumerate(requests):
            recipients.setdefault(request.recipient, []).append((index, request))
        for name, entries in recipients.items():
            with (
                ui.card()
                .props("flat bordered")
                .classes("w-full gap-2")
                .mark("assignment-review-recipient")
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(name).classes("text-subtitle1 font-medium")
                    ui.label(t("assign_group_count", count=len(entries))).classes(
                        "text-caption text-grey-7"
                    )
                for index, request in entries:
                    with ui.row().classes("w-full items-center gap-3"):
                        ui.label(
                            t(f"handoff_stage_{request.stage}")
                            + (f" · {request.target_species}" if request.target_species else "")
                        ).classes("grow")
                        ui.label(t("assign_video_count", count=len(request.video_ids)))
                        if index in prepared and not busy:
                            ui.button(
                                t("assign_view"),
                                on_click=lambda saved=prepared[index]: on_prepared(saved.id),
                            ).props("flat dense")
                    if index in outcomes:
                        ui.label(outcomes[index]).classes(
                            "text-positive" if index in prepared else "text-negative"
                        )
        with ui.row().classes("w-full justify-between"):

            def back():
                review.set_visibility(False)
                editor.set_visibility(True)
                status.set_text("")

            ui.button(
                t("assign_back") if not prepared else t("close"),
                on_click=back if not prepared else on_cancel,
            ).props("flat").set_enabled(not busy)
            ui.button(t("assign_prepare"), on_click=prepare, icon="inventory_2").set_enabled(
                not busy and len(prepared) < len(requests)
            )

    async def prepare():
        nonlocal busy
        if busy:
            return
        busy = True
        render_review.refresh()
        try:
            for index, request in enumerate(requests):
                if index in prepared:
                    continue
                try:
                    require_project()
                    if request.stage == "species":
                        await run.io_bound(
                            apply_species_request,
                            dp,
                            project_id,
                            request,
                            unassigned_ids if index == 0 else (),
                        )
                        require_project()
                    result = await run.io_bound(dp.prepare_assignment, project_id, request)
                    prepared[index] = result
                    outcomes[index] = t("handoff_prepared", count=result.video_count)
                    if result.missing_video_ids:
                        outcomes[index] += "\n" + t(
                            "handoff_missing", count=len(result.missing_video_ids)
                        )
                except Exception as exc:
                    outcomes[index] = _error_message(exc)
                render_review.refresh()
        finally:
            busy = False
            render_review.refresh()
        if len(requests) == 1 and prepared:
            ui.notify(outcomes[0], type="warning" if prepared[0].missing_video_ids else "positive")
            on_prepared(prepared[0].id)

    with review:
        render_review()
