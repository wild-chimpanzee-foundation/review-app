"""Manual Distance measurements for analysis."""

from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import ignore_deleted_client


async def render_distance_export(dp, project_id, loading_dialog):
    selection = None
    busy = False
    task_table = None

    def check_project():
        if get_active_project_id() != project_id:
            raise ValueError(t("distance_ai_project_changed"))

    def invalidate_selection():
        nonlocal selection
        selection = None
        export_preview.refresh()

    def filters():
        return species.value, start.value or None, end.value or None

    def report(exc):
        ui.notify(
            t("export_failed", error=str(exc)), type="negative", timeout=0, close_button=True
        )

    async def preview_export():
        nonlocal busy, selection
        if busy:
            return
        busy = True
        selection = None
        export_preview.refresh()
        selected_filters = filters()
        loading_dialog.open()
        try:
            check_project()
            if not selected_filters[0]:
                raise ValueError(t("distance_manual_choose_species"))
            result = await run.io_bound(
                dp.preview_distance_export,
                project_id,
                selected_filters[0],
                start_time=selected_filters[1],
                end_time=selected_filters[2],
            )
            check_project()
            if selected_filters == filters():
                selection = result
        except Exception as exc:
            with ignore_deleted_client():
                report(exc)
        finally:
            busy = False
            with ignore_deleted_client():
                loading_dialog.close()
                export_preview.refresh()

    async def download():
        nonlocal busy
        if busy or selection is None:
            return
        ids = [row["task_id"] for row in task_table.selected if not row["error"]]
        if not ids:
            ui.notify(t("distance_manual_no_selection"), type="warning")
            return
        busy = True
        loading_dialog.open()
        try:
            check_project()
            content = await run.io_bound(dp.export_distance_measurements, project_id, ids)
            check_project()
            ui.download(content, "distance-measurements.tsv")
        except Exception as exc:
            with ignore_deleted_client():
                report(exc)
        finally:
            busy = False
            with ignore_deleted_client():
                loading_dialog.close()

    @ui.refreshable
    def export_preview():
        nonlocal task_table
        if selection is None:
            return
        rows = selection["tasks"]
        ui.label(t("distance_manual_preview_count", count=len(rows)))
        if not rows:
            ui.label(t("data_empty_scope")).classes("text-grey-6")
        if selection["unknown_time"]:
            ui.label(
                t("distance_manual_unknown_time", count=len(selection["unknown_time"]))
            ).classes("text-warning")
            with ui.expansion(t("distance_manual_unknown_videos")).classes("w-full"):
                for row in selection["unknown_time"]:
                    ui.label(row["video_path"]).classes("break-all")
        blocked = sum(bool(row["error"]) for row in rows)
        if blocked:
            ui.label(t("data_distance_excluded", count=blocked)).classes("text-warning")
        # The complete supported set is selected; inspection/narrowing is optional.
        with ui.expansion(t("data_inspect_scope"), icon="list").classes("w-full"):
            fields = ["video_path", "recorded_start", "completion_outcome", "is_missing", "error"]
            task_table = ui.table(
                columns=[{"name": key, "label": key, "field": key} for key in fields],
                rows=rows,
                row_key="task_id",
                selection="multiple",
                pagination=10,
            ).classes("w-full")
            task_table.selected = [row for row in rows if not row["error"]]
        ui.button(
            t("distance_manual_analysis"),
            icon="table_view",
            on_click=download,
        ).props("unelevated").set_enabled(any(not row["error"] for row in rows))

    options = await run.io_bound(dp.get_distance_target_species_options, project_id)
    species = ui.select(
        options, label=t("distance_manual_species"), on_change=invalidate_selection
    ).classes("w-full")
    with ui.expansion(t("data_advanced"), icon="tune").classes("w-full"):
        with ui.row().classes("w-full"):
            start = ui.input(t("distance_manual_start"), on_change=invalidate_selection).props(
                "type=time"
            )
            end = ui.input(t("distance_manual_end"), on_change=invalidate_selection).props(
                "type=time"
            )
        ui.label(t("distance_manual_time_help")).classes("text-caption text-grey-6")
    ui.button(t("distance_manual_preview"), icon="preview", on_click=preview_export).props(
        "outline"
    )
    export_preview()
