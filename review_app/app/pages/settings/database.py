import asyncio
import logging
from pathlib import Path

from nicegui import run, ui

from review_app.app.components.backup_picker import (
    BACKUP_UPLOAD_ACCEPT,
    backup_option,
    restore_uploaded_backup,
)
from review_app.app.media import set_media_dirs
from review_app.app.state import (
    load_settings_from_db,
    reset_app_state,
    set_data_provider,
)
from review_app.app.translations import t
from review_app.app.utils import (
    ignore_deleted_client,
    run_backup_with_progress,
    user_error_message,
)
from review_app.backend.db.backup import (
    BackupError,
    create_backup,
    list_backups,
    remove_db_sidecars,
)
from review_app.backend.path_matching import normalize_path_str

logger = logging.getLogger(__name__)

_db_op_lock = asyncio.Lock()


def render_database_section(
    current_db_path: Path | None, active_project_id: str | None = None
) -> None:
    def _get_dp():
        from review_app.app.state import get_data_provider

        return get_data_provider()

    def _busy() -> bool:
        if _db_op_lock.locked():
            ui.notify(t("db_op_in_progress"), type="warning")
            return True
        return False

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(t("backup_download_label")).classes("text-body2")
        ui.space()

        async def do_backup_download():
            if _busy():
                return
            async with _db_op_lock:
                try:
                    backup_path = await run_backup_with_progress(create_backup, reason="manual")
                except BackupError as exc:
                    with ignore_deleted_client():
                        ui.notify(
                            t("backup_failed", error=t(exc.user_message_key)), type="negative"
                        )
                    return
            with ignore_deleted_client():
                ui.download(backup_path)
                ui.notify(t("backup_created"), type="positive")

        ui.button(
            t("backup_download_btn"), icon="download", color="primary", on_click=do_backup_download
        )

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(t("restore_backup_label")).classes("text-body2")
        ui.space()

        restore_dialog = ui.dialog().props("persistent")

        async def do_restore(selected_backup_path: Path):
            restore_dialog.close()
            if _busy():
                return
            loading_dialog = ui.dialog().props("persistent")
            with loading_dialog, ui.card().classes("q-pa-lg row items-center gap-md no-wrap"):
                ui.spinner(size="md")
                ui.label(t("restore_in_progress"))
            loading_dialog.open()
            try:
                async with _db_op_lock:
                    dp = _get_dp()
                    try:
                        new_dp = await run.io_bound(dp.restore_from_backup, selected_backup_path)
                    except Exception as exc:
                        logger.exception("Database restore failed")
                        message = (
                            t(exc.user_message_key)
                            if isinstance(exc, BackupError)
                            else user_error_message(exc)
                        )
                        with ignore_deleted_client():
                            ui.notify(t("restore_failed", error=message), type="negative")
                        return

                    reset_app_state()
                    set_data_provider(new_dp)
                    load_settings_from_db(new_dp)

                    all_dirs = [
                        Path(normalize_path_str(d.path))
                        for _p in new_dp.list_projects()
                        for d in new_dp.get_project_dirs(_p.id)
                    ]
                    set_media_dirs(all_dirs)

                    with ignore_deleted_client():
                        ui.notify(t("restore_success"), type="positive")
                        await asyncio.sleep(0.5)
                        ui.navigate.to("/")
            finally:
                with ignore_deleted_client():
                    loading_dialog.close()

        async def open_restore_dialog():
            backups = await run.io_bound(list_backups)
            restore_dialog.clear()
            with restore_dialog, ui.card().classes("q-pa-lg").style("min-width: 420px"):
                if not backups:
                    ui.label(t("no_backups")).classes("text-body2 text-grey-6")
                else:
                    ui.label(t("restore_confirm")).classes("text-subtitle1 q-mb-md")
                    with (
                        ui.column()
                        .classes("w-full gap-xs q-mb-lg")
                        .style("max-height: 300px; overflow-y: auto")
                    ):
                        for backup in backups:

                            async def restore_selected(_event, path=backup["path"]):
                                await do_restore(path)

                            backup_option(backup, on_click=restore_selected)
                with ui.row().classes("w-full gap-sm items-center q-mt-md"):
                    ui.separator().classes("col")
                    ui.label(t("upload_backup")).classes("text-caption text-grey-6")
                    ui.separator().classes("col")

                async def _handle_backup_upload(e):
                    await restore_uploaded_backup(e.file, do_restore)

                backup_uploader = (
                    ui.upload(on_upload=_handle_backup_upload, auto_upload=True)
                    .props(BACKUP_UPLOAD_ACCEPT)
                    .style("display: none")
                )
                ui.button(
                    t("upload_backup_btn"),
                    icon="upload_file",
                    on_click=lambda: ui.run_javascript(
                        f"document.getElementById('c{backup_uploader.id}').querySelector('.q-uploader__input').click()"
                    ),
                ).props(f"flat dense align-left {'color=primary' if not backups else ''}").classes(
                    "w-full"
                )

                with ui.row().classes("w-full justify-end"):
                    ui.button(t("cancel"), on_click=restore_dialog.close).props("flat")
            restore_dialog.open()

        ui.button(t("restore_backup_btn"), icon="restore", on_click=open_restore_dialog)

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(t("delete_model_ann_label")).classes("text-body2")
        ui.space()

        delete_ann_dialog = ui.dialog().props("persistent")

        async def do_delete_annotations():
            delete_ann_dialog.close()
            if _busy():
                return
            async with _db_op_lock:
                dp = _get_dp()
                await run.io_bound(dp.delete_model_annotations, active_project_id)
                with ignore_deleted_client():
                    ui.notify(t("delete_model_ann_success"), type="positive")

        with delete_ann_dialog, ui.card().classes("q-pa-lg"):
            ui.label(t("delete_model_ann_confirm")).classes("text-h6 q-mb-sm")
            ui.label(t("delete_model_ann_warning")).classes("text-body2 text-negative q-mb-lg")
            with ui.row().classes("w-full justify-end gap-sm"):
                ui.button(t("cancel"), on_click=delete_ann_dialog.close).props("flat")
                ui.button(
                    t("yes_delete"),
                    icon="delete_forever",
                    color="negative",
                    on_click=do_delete_annotations,
                )

        ui.button(
            t("delete_model_ann_label"),
            icon="delete_sweep",
            color="negative",
            on_click=delete_ann_dialog.open,
        )

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(t("reset_database_label")).classes("text-body2")
        ui.space()

        reset_dialog = ui.dialog().props("persistent")

        async def do_reset():
            reset_dialog.close()
            if _busy():
                return
            async with _db_op_lock:
                old_dp = _get_dp()
                if current_db_path and current_db_path.exists():
                    try:
                        await run_backup_with_progress(create_backup, reason="reset_database")
                    except BackupError as exc:
                        with ignore_deleted_client():
                            ui.notify(
                                t("backup_failed", error=t(exc.user_message_key)), type="negative"
                            )
                        return
                    old_dp.engine.dispose()
                    current_db_path.unlink()
                    remove_db_sidecars(current_db_path)
                else:
                    old_dp.engine.dispose()
                reset_app_state(keep_prefs=True)
                with ignore_deleted_client():
                    ui.notify(t("database_reset"), type="positive")
                    ui.navigate.to("/login")

        with reset_dialog, ui.card().classes("q-pa-lg"):
            ui.label(t("reset_confirm")).classes("text-h6 q-mb-sm")
            ui.label(t("reset_warning")).classes("text-body2 text-negative q-mb-lg")
            with ui.row().classes("w-full justify-end gap-sm"):
                ui.button(t("cancel"), on_click=reset_dialog.close).props("flat")
                ui.button(
                    t("yes_reset"), icon="delete_forever", color="negative", on_click=do_reset
                )

        ui.button(
            t("reset_database_label"),
            icon="delete_forever",
            color="negative",
            on_click=reset_dialog.open,
        )
