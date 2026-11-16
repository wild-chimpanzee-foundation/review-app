"""Upload, preview and import pending Distance annotations."""

from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import ignore_deleted_client
from review_app.backend.distance_ai_package import MAX_ZIP_BYTES


async def setup_distance_ai_tab(dp, loading_dialog):
    project_id = get_active_project_id()
    state = {"bytes": None, "preview": None, "busy": False, "replace_confirmed": False}

    def check_project():
        if not project_id or get_active_project_id() != project_id:
            raise ValueError(t("distance_ai_project_changed"))

    def prediction_table(rows):
        fields = [
            "prediction_id",
            "individual_id",
            "time_s",
            "predicted_distance_m",
            "suggested_reference_id",
            "mask_path",
        ]
        ui.table(
            columns=[{"name": key, "label": key, "field": key} for key in fields],
            rows=[{key: row.get(key) for key in fields} for row in rows],
            row_key="prediction_id",
            pagination=10,
        ).classes("w-full")

    @ui.refreshable
    def preview_ui():
        preview = state["preview"]
        if preview is None:
            return
        ui.label(t("distance_ai_counts", **preview)).classes("text-subtitle2")
        ui.label(t("distance_ai_reference_counts", **preview))
        if not preview["already_imported"]:
            ui.label(t("distance_ai_draft_counts", **preview))
            for schedule in preview["sampling"]:
                ui.label(t("distance_ai_inferred_cadence", **schedule)).classes(
                    "text-caption break-all"
                )
        if preview["replace_video_ids"]:
            ui.label(t("distance_ai_replace_warning")).classes("text-warning")
            ui.checkbox(
                t("distance_ai_replace_confirm"),
                value=state["replace_confirmed"],
                on_change=lambda event: confirm_replace(event.value),
            )
        for error in preview["errors"][:20]:
            ui.label(error).classes("text-negative break-all")
        if preview["already_imported"]:
            ui.label(t("distance_ai_already_imported")).classes("text-positive")
        prediction_table(preview["preview"])
        ui.button(t("distance_ai_import"), icon="file_upload", on_click=do_import).set_enabled(
            not state["busy"]
            and not preview["errors"]
            and not preview["already_imported"]
            and (not preview["replace_video_ids"] or state["replace_confirmed"])
        )

    def confirm_replace(value):
        state["replace_confirmed"] = value
        preview_ui.refresh()

    async def handle_upload(event):
        if state["busy"]:
            return
        state.update(bytes=None, preview=None, busy=True, replace_confirmed=False)
        preview_ui.refresh()
        loading_dialog.open()
        try:
            check_project()
            if event.file.size() > MAX_ZIP_BYTES:
                raise ValueError(t("distance_ai_upload_limit"))
            content = await event.file.read()
            preview = await run.io_bound(dp.preview_distance_ai_package, project_id, content)
            check_project()
            state.update(bytes=content, preview=preview)
        except Exception as exc:
            with ignore_deleted_client():
                ui.notify(
                    t("import_failed", error=str(exc)),
                    type="negative",
                    timeout=0,
                    close_button=True,
                )
        finally:
            state["busy"] = False
            with ignore_deleted_client():
                preview_ui.refresh()
                loading_dialog.close()

    async def do_import():
        if state["busy"] or not state["bytes"]:
            return
        preview = state["preview"]
        if preview["errors"] or (preview["replace_video_ids"] and not state["replace_confirmed"]):
            return
        state["busy"] = True
        preview_ui.refresh()
        loading_dialog.open()
        try:
            check_project()
            result = await run.io_bound(
                dp.import_distance_ai_package,
                project_id,
                state["bytes"],
                replace_video_ids=preview["replace_video_ids"],
            )
            state["replace_confirmed"] = False
            state["preview"] = await run.io_bound(
                dp.preview_distance_ai_package, project_id, state["bytes"]
            )
            imports = await run.io_bound(dp.list_distance_imports, project_id)
            with ignore_deleted_client():
                saved_select.set_options(import_options(imports), value=result["import_id"])
                ui.notify(t("distance_ai_imported"), type="positive")
        except Exception as exc:
            with ignore_deleted_client():
                ui.notify(
                    t("import_failed", error=str(exc)),
                    type="negative",
                    timeout=0,
                    close_button=True,
                )
        finally:
            state["busy"] = False
            with ignore_deleted_client():
                preview_ui.refresh()
                loading_dialog.close()

    ui.label(t("distance_ai_help")).classes("text-body2")
    ui.label(t("distance_ai_timing_warning")).classes("text-warning text-body2")
    ui.upload(
        label=t("distance_ai_choose_zip"),
        on_upload=handle_upload,
        auto_upload=True,
        multiple=False,
        max_file_size=MAX_ZIP_BYTES,
        on_rejected=lambda: ui.notify(t("distance_ai_upload_limit"), type="negative"),
    ).props("accept=.zip")
    preview_ui()
    ui.separator()

    def import_options(imports):
        return {
            row["id"]: f"{row['manifest']['source_id']} · {row['manifest']['run_id']}"
            for row in imports
        }

    imports = await run.io_bound(dp.list_distance_imports, project_id)
    saved_select = ui.select(import_options(imports), label=t("distance_ai_saved_runs")).classes(
        "w-full"
    )
    saved_content = ui.column().classes("w-full")

    async def show_saved():
        if not saved_select.value or state["busy"]:
            return
        state["busy"] = True
        loading_dialog.open()
        try:
            check_project()
            saved = await run.io_bound(dp.get_distance_import, project_id, saved_select.value)
            with ignore_deleted_client():
                saved_content.clear()
                with saved_content:
                    ui.label(
                        t(
                            "distance_ai_counts",
                            references=len(saved["references"]),
                            individuals=len(saved["individuals"]),
                            predictions=len(saved["predictions"]),
                        )
                    )
                    ui.label(t("distance_ai_saved_preview"))
                    prediction_table(saved["predictions"][:100])
        except Exception as exc:
            with ignore_deleted_client():
                ui.notify(t("import_failed", error=str(exc)), type="negative")
        finally:
            state["busy"] = False
            with ignore_deleted_client():
                loading_dialog.close()

    ui.button(t("distance_ai_view_run"), icon="visibility", on_click=show_saved)
