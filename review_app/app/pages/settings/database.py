import asyncio
import uuid
from pathlib import Path

from nicegui import run, ui

from review_app.app.state import (
    load_settings_from_db,
    reset_app_state,
    set_data_provider,
)
from review_app.app.translations import t
from review_app.app.utils import format_utc_timestamp, user_error_message
from review_app.backend.db.backup import (
    BackupError,
    create_backup,
    get_backup_dir,
    list_backups,
    restore_backup,
)
from review_app.backend.provider.local_data_provider import LocalDataProvider


def render_database_section(current_db_path: Path | None) -> None:
    def _get_dp():
        from review_app.app.state import get_data_provider

        return get_data_provider() or LocalDataProvider()

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(t("backup_download_label")).classes("text-body2")
        ui.space()

        async def do_backup_download():
            try:
                backup_path = await run.io_bound(create_backup, _get_dp().engine, reason="manual")
            except BackupError as exc:
                ui.notify(t("backup_failed", error=t(exc.user_message_key)), type="negative")
                return
            ui.download(backup_path.read_bytes(), filename=backup_path.name)
            ui.notify(t("backup_created"), type="positive")

        ui.button(
            t("backup_download_btn"), icon="download", color="primary", on_click=do_backup_download
        )

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(t("restore_backup_label")).classes("text-body2")
        ui.space()

        restore_dialog = ui.dialog().props("persistent")

        async def do_restore(selected_backup_path):
            restore_dialog.close()
            try:
                await run.io_bound(restore_backup, selected_backup_path, _get_dp().engine)
            except BackupError as exc:
                ui.notify(t("restore_failed", error=t(exc.user_message_key)), type="negative")
                return
            except Exception as exc:
                ui.notify(t("restore_failed", error=user_error_message(exc)), type="negative")
                return
            reset_app_state()
            new_dp = LocalDataProvider()
            set_data_provider(new_dp)
            load_settings_from_db(new_dp)
            ui.notify(t("restore_success"), type="positive")
            await asyncio.sleep(0.5)
            ui.navigate.to("/overview")

        async def open_restore_dialog():
            backups = list_backups()
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
                        for b in backups:
                            ts = format_utc_timestamp(b["timestamp"].isoformat())
                            label = f"{ts}  ({b['size_mb']} MB)"

                            def _make_restore(p):
                                async def _do():
                                    await do_restore(p)

                                return _do

                            ui.button(
                                label, icon="restore", on_click=_make_restore(b["path"])
                            ).props("flat dense align-left").classes("w-full")
                with ui.row().classes("w-full gap-sm items-center q-mt-md"):
                    ui.separator().classes("col")
                    ui.label(t("upload_backup")).classes("text-caption text-grey-6")
                    ui.separator().classes("col")

                async def _handle_backup_upload(e):
                    content = await e.file.read()
                    tmp_path = get_backup_dir() / f"uploaded_restore_{uuid.uuid4().hex}.db"
                    tmp_path.write_bytes(content)
                    try:
                        await do_restore(tmp_path)
                    finally:
                        tmp_path.unlink(missing_ok=True)

                backup_uploader = (
                    ui.upload(on_upload=_handle_backup_upload, auto_upload=True)
                    .props("accept=.db")
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
        ui.label(t("reset_database_label")).classes("text-body2")
        ui.space()

        reset_dialog = ui.dialog().props("persistent")

        async def do_reset():
            reset_dialog.close()
            old_dp = _get_dp()
            if current_db_path and current_db_path.exists():
                try:
                    create_backup(old_dp.engine, reason="reset_database")
                except BackupError as exc:
                    ui.notify(
                        t("backup_failed_proceed", error=t(exc.user_message_key)), type="warning"
                    )
                old_dp.engine.dispose()
                current_db_path.unlink()
            else:
                old_dp.engine.dispose()
            reset_app_state()
            ui.notify(t("database_reset"), type="positive")
            ui.navigate.to("/setup")

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
