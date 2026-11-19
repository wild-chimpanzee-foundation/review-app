"""Edit live Species work groups before reviewing an assignment plan."""

from nicegui import run, ui

from review_app.app.components.assignment_handoff import _error_message
from review_app.app.translations import t

from .assignment_plan import WorkGroup, species_requests
from .workload import format_camera_labels, workload_labels


def render_species_editor(
    dp, project_id, require_project, names, groups, status, set_review_enabled
):
    adjustment_open = False
    container = ui.context.slot.parent
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
        set_review_enabled(False)
        try:
            require_project()
            mapping, chunks = await run.io_bound(
                dp.auto_distribute_flexible, project_id, sorted(annotators.value)
            )
            if container.is_deleted:
                return
            require_project()
            assigned = {key: name for name, keys in mapping.items() for key in keys}
            groups[:] = [
                WorkGroup(
                    chunk["label"],
                    tuple(chunk["video_ids"]),
                    assigned.get(chunk["chunk_id"]),
                )
                for chunk in chunks
            ]
            status.set_text("")
            allocation.refresh()
        except Exception as exc:
            if not container.is_deleted:
                status.set_text(_error_message(exc))
        finally:
            if not container.is_deleted:
                auto_button.enable()
                set_review_enabled(True)

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
                    workload = dp.get_assignment_workload(request.video_ids)
                    labels = workload_labels(
                        workload,
                        video_label=t(
                            "assign_recipient_count",
                            name=request.recipient,
                            count=workload["video_count"],
                        ),
                        camera_names=", ".join(workload["camera_ids"]),
                    )
                    ui.chip(" · ".join(labels), icon="person_outline").props("outline")
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
                    workload = dp.get_assignment_workload(group.video_ids)
                    ui.label(
                        format_camera_labels(
                            workload["camera_ids"], group.label or t("assign_other_videos")
                        )
                    ).classes("grow")
                    for label in workload_labels(workload):
                        ui.label(label)
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

    def refresh_annotators(name):
        annotators.set_options(names)
        annotators.set_value(list(dict.fromkeys([*annotators.value, name])))
        allocation.refresh()

    return refresh_annotators
