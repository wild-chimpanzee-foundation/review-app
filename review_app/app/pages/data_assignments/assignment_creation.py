"""One focused workflow from editable allocation to frozen assignment handoffs."""

from nicegui import run, ui

from review_app.app.components.assignment_handoff import _error_message
from review_app.app.translations import get_language, t

from .assignment_plan import species_requests, species_work_groups
from .distance_assignment_editor import render_distance_editor
from .species_assignment_editor import render_species_editor
from .workload import workload_labels


def render_assignment_creation(dp, project_id, require_project, on_cancel, on_prepared):
    groups = species_work_groups(dp, project_id)
    names = dp.get_all_annotators()
    requests = ()
    unassigned_ids = ()
    prepared = {}
    busy = False

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
                    refresh_species_annotators(name)
                    refresh_distance_annotators()
                    annotator_name.set_value("")
                except Exception as exc:
                    status.set_text(_error_message(exc))
                finally:
                    add_button.enable()

            add_button = ui.button(
                t("distribution_add_annotator"), on_click=add_annotator, icon="person_add"
            ).props("flat")

        def set_review_enabled(enabled):
            review_button.set_enabled(enabled)

        with ui.column().classes("w-full gap-3") as species_editor:
            refresh_species_annotators = render_species_editor(
                dp, project_id, require_project, names, groups, status, set_review_enabled
            )
        with ui.column().classes("w-full gap-3") as distance_editor:
            distance_requests, refresh_distance_annotators = render_distance_editor(
                dp, project_id, require_project, names, distance_species, distance_species_options
            )
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
                    requests = distance_requests()
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
                        workload = dp.get_assignment_workload(request.video_ids)
                        chunk_labels = (
                            list(
                                dict.fromkeys(
                                    group.label
                                    for group in groups
                                    if group.label
                                    and set(group.video_ids).intersection(request.video_ids)
                                )
                            )
                            if request.stage == "species"
                            else []
                        )
                        group_labels = list(
                            dict.fromkeys([*chunk_labels, *workload["camera_ids"]])
                        )
                        ui.label(
                            t(f"handoff_stage_{request.stage}")
                            + (f" · {request.target_species}" if request.target_species else "")
                            + (" · " + ", ".join(group_labels) if group_labels else "")
                        ).classes("grow")
                        for label in workload_labels(workload):
                            ui.label(label)
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
                        result = await run.io_bound(
                            dp.prepare_species_assignment,
                            project_id,
                            request,
                            unassigned_ids if index == 0 else (),
                        )
                    else:
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
