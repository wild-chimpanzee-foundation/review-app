"""Edit live Species work groups before reviewing an assignment plan."""

import json
from html import escape

from nicegui import run, ui

from review_app.app.components.assignment_handoff import _error_message
from review_app.app.translations import t

from .assignment_plan import WorkGroup, species_requests
from .workload import workload_labels


def render_species_editor(
    dp, project_id, require_project, names, groups, status, set_review_enabled
):
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
            summary.refresh()
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
    def summary():
        totals = species_requests(groups)
        if totals:
            with ui.row().classes("w-full gap-3"):
                for request in totals:
                    workload = dp.get_assignment_workload(request.video_ids)
                    videos, cameras, hours, *unknown = workload_labels(workload)
                    with ui.card().props("flat bordered").classes("grow min-w-48 gap-1"):
                        ui.label(request.recipient).classes("font-medium break-all")
                        ui.label(hours).classes("text-h6")
                        ui.label(f"{videos} · {cameras}").classes("text-caption")
                        for label in unknown:
                            ui.label(label).classes("text-caption")
        else:
            ui.label(t("assign_no_allocation")).classes("text-grey-7")
        unassigned = sum(len(group.video_ids) for group in groups if not group.recipient)
        if unassigned:
            ui.label(t("assign_unallocated", count=unassigned)).classes("text-caption")

    @ui.refreshable
    def allocation():
        if not groups:
            return
        rows = []
        for index, group in enumerate(groups):
            workload = dp.get_assignment_workload(group.video_ids)
            rows.append(
                {
                    "id": index,
                    "camera": group.label or t("assign_other_videos"),
                    "videos": workload["video_count"],
                    "hours": round(workload["hours"], 2),
                    "unknown": workload["video_count"] - workload["duration_count"],
                    "recipient": group.recipient,
                }
            )
        columns = [
            ("camera", "distribution_camera_col", "left"),
            ("videos", "distribution_videos_col", "right"),
            ("hours", "distribution_hours_col", "right"),
            ("unknown", "assign_unknown_duration_col", "right"),
            ("recipient", "distribution_assigned_col", "left"),
        ]
        search = (
            ui.input(t("assign_search_allocation"))
            .props("dense outlined clearable debounce=200")
            .classes("w-full sm:w-80")
        )
        table = (
            ui.table(
                columns=[
                    {
                        "name": key,
                        "field": key,
                        "label": t(label),
                        "align": align,
                        "sortable": True,
                    }
                    for key, label, align in columns
                ],
                rows=rows,
                row_key="id",
                pagination={"rowsPerPage": 15, "sortBy": "camera"},
            )
            .props("flat bordered wrap-cells separator=horizontal")
            .classes("w-full")
            .mark("species-allocation")
        )
        search.bind_value_to(table, "filter")
        options = escape(json.dumps(names), quote=True)
        unassigned = escape(json.dumps(t("distribution_unassigned")), quote=True)
        recipient_label = escape(t("handoff_recipient"), quote=True)
        table.add_slot(
            "body-cell-recipient",
            f"""
            <q-td :props="props" style="min-width: 12rem">
                <q-select
                    dense outlined clearable
                    :options="{options}"
                    :model-value="props.row.recipient"
                    :display-value="props.row.recipient ?? {unassigned}"
                    aria-label="{recipient_label}"
                    @update:model-value="value => $parent.$emit('assign-recipient', props.row.id, value)"
                />
            </q-td>
            """,
        )
        table.add_slot(
            "body-cell-camera",
            '<q-td :props="props" style="overflow-wrap: anywhere">{{ props.value }}</q-td>',
        )
        table.add_slot(
            "body-cell-hours",
            '<q-td :props="props">{{ props.value.toFixed(2) }}</q-td>',
        )

        def change(event):
            index, recipient = event.args
            if type(index) is not int or not 0 <= index < len(rows):
                return
            if recipient is not None and recipient not in names:
                return
            groups[index].recipient = recipient
            table.rows[index]["recipient"] = recipient
            table.update()
            summary.refresh()

        table.on("assign-recipient", change)

    summary()
    with ui.expansion(t("assign_adjust"), icon="tune").classes("w-full"):
        allocation()

    def refresh_annotators(name):
        annotators.set_options(names)
        annotators.set_value(list(dict.fromkeys([*annotators.value, name])))
        allocation.refresh()

    return refresh_annotators
