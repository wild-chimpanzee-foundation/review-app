"""Preview, download and explicitly restore manual Distance task snapshots."""

from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import ignore_deleted_client
from review_app.backend.distance_transfer import MAX_ARCHIVE_BYTES, decode_archive


async def setup_distance_manual_tab(dp, loading_dialog):
    project_id = get_active_project_id()
    state = {
        "busy": False,
        "bytes": None,
        "import_preview": None,
        "roots": [],
        "root_map": {},
        "decisions": {},
    }

    def check_project():
        if not project_id or get_active_project_id() != project_id:
            raise ValueError(t("distance_ai_project_changed"))

    def report(exc):
        ui.notify(
            t("import_failed", error=str(exc)), type="negative", timeout=0, close_button=True
        )

    def set_root(root_id, local_id):
        if local_id:
            state["root_map"][root_id] = local_id
        else:
            state["root_map"].pop(root_id, None)
        state["import_preview"] = None
        state["decisions"] = {}
        import_preview.refresh()

    @ui.refreshable
    def root_mapping():
        if not state["roots"]:
            return
        ui.label(t("distance_manual_root_help")).classes("text-caption")
        for root_id in state["roots"]:
            ui.select(
                root_options,
                label=t("distance_manual_root", root=root_id),
                value=state["root_map"].get(root_id),
                on_change=lambda event, source=root_id: set_root(source, event.value),
            ).classes("w-full")
        ui.button(t("distance_manual_preview_import"), icon="preview", on_click=preview_import)

    async def handle_upload(event):
        if state["busy"]:
            return
        state.update(
            bytes=None, import_preview=None, roots=[], root_map={}, decisions={}, busy=True
        )
        root_mapping.refresh()
        import_preview.refresh()
        loading_dialog.open()
        try:
            check_project()
            if event.file.size() > MAX_ARCHIVE_BYTES:
                raise ValueError(t("distance_manual_upload_limit"))
            content = await event.file.read()
            manifest, _ = await run.io_bound(decode_archive, content)
            roots = manifest.get("roots", [])
            if not isinstance(roots, list) or any(not isinstance(root, str) for root in roots):
                raise ValueError("Invalid media roots in Distance manifest")
            mapping = {root: root for root in roots if root in root_options}
            if len(roots) == len(root_options) == 1:
                mapping = {roots[0]: next(iter(root_options))}
            preview = await run.io_bound(
                dp.preview_distance_annotations_import, project_id, content, root_map=mapping
            )
            check_project()
            state.update(bytes=content, roots=roots, root_map=mapping, import_preview=preview)
        except Exception as exc:
            with ignore_deleted_client():
                report(exc)
        finally:
            state["busy"] = False
            with ignore_deleted_client():
                loading_dialog.close()
                root_mapping.refresh()
                import_preview.refresh()

    async def preview_import():
        if state["busy"] or not state["bytes"]:
            return
        state.update(busy=True, import_preview=None, decisions={})
        import_preview.refresh()
        loading_dialog.open()
        try:
            check_project()
            mapping = dict(state["root_map"])
            preview = await run.io_bound(
                dp.preview_distance_annotations_import,
                project_id,
                state["bytes"],
                root_map=mapping,
            )
            check_project()
            if mapping == state["root_map"]:
                state["import_preview"] = preview
        except Exception as exc:
            with ignore_deleted_client():
                report(exc)
        finally:
            state["busy"] = False
            with ignore_deleted_client():
                loading_dialog.close()
                import_preview.refresh()

    @ui.refreshable
    def import_preview():
        if state["import_preview"] is None:
            return
        ui.label(t("distance_manual_replace_warning")).classes("text-warning")
        for row in state["import_preview"]:
            with ui.row().classes("w-full items-center"):
                ui.label(row["video_path"]).classes("grow break-all")
                if row["error"]:
                    ui.label(row["error"]).classes("text-negative")
                else:
                    ui.select(
                        {
                            "skip": t("distance_manual_skip"),
                            "replace": t("distance_manual_replace"),
                        },
                        value=state["decisions"].get(row["task_id"], "skip"),
                        label=t("distance_manual_task_decision"),
                        on_change=lambda event, task=row["task_id"]: state["decisions"].update(
                            {task: event.value}
                        ),
                    )
        ui.button(t("distance_manual_apply"), icon="file_upload", on_click=do_import).set_enabled(
            not state["busy"]
        )

    async def do_import():
        if state["busy"] or not state["bytes"] or state["import_preview"] is None:
            return
        state["busy"] = True
        import_preview.refresh()
        loading_dialog.open()
        try:
            check_project()
            result = await run.io_bound(
                dp.import_distance_annotations,
                project_id,
                state["bytes"],
                dict(state["decisions"]),
                root_map=dict(state["root_map"]),
            )
            check_project()
            state.update(bytes=None, roots=[], import_preview=None, decisions={})
            ui.notify(
                t(
                    "distance_manual_imported",
                    imported=len(result["imported"]),
                    skipped=len(result["skipped"]),
                ),
                type="positive",
            )
            summary.clear()
            with summary:
                for task_id in result["imported"]:
                    ui.label(t("distance_manual_imported_task", task=task_id))
                for task_id in result["skipped"]:
                    ui.label(t("distance_manual_skipped_task", task=task_id))
        except Exception as exc:
            with ignore_deleted_client():
                report(exc)
        finally:
            state["busy"] = False
            with ignore_deleted_client():
                loading_dialog.close()
                root_mapping.refresh()
                import_preview.refresh()

    ui.label(t("distance_manual_help")).classes("text-body2")
    root_options = (
        {row.id: row.path for row in dp.get_project_dirs(project_id)} if project_id else {}
    )
    ui.label(t("distance_manual_import_title")).classes("text-h6")
    ui.upload(
        label=t("distance_manual_upload"),
        on_upload=handle_upload,
        auto_upload=True,
        multiple=False,
        max_file_size=MAX_ARCHIVE_BYTES,
        on_rejected=lambda: ui.notify(t("distance_manual_upload_limit"), type="negative"),
    ).props("accept=.zip")
    root_mapping()
    import_preview()
    summary = ui.column().classes("w-full")
