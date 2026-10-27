"""Backup recovery UI that works without an initialized data provider."""

import logging

from nicegui import run, ui

from review_app.app.translations import t
from review_app.backend.db.backup import BackupError, list_backups, restore_backup
from review_app.backend.db.migrations import MIGRATIONS

logger = logging.getLogger(__name__)


def is_backup_compatible(backup: dict) -> bool:
    # Legacy filenames omit the version; restore_backup checks the actual database.
    version = backup["schema_version"]
    return version is None or version <= len(MIGRATIONS)


def build_backup_recovery() -> None:
    with ui.column().classes("w-full gap-sm").style("max-width: 700px"):
        ui.label("Restore a backup").classes("text-subtitle1")
        ui.label(
            "Restoring replaces the current database. A safety copy is kept before replacement. "
            "Restart the application after restoring."
        ).classes("text-body2 text-grey-7")
        status = ui.label("").classes("text-body2")
        buttons = []
        busy = False
        restored = False

        async def restore(backup):
            nonlocal busy, restored
            if busy or restored:
                return
            busy = True
            for button in buttons:
                button.disable()
            status.set_text("Restoring backup…")
            try:
                await run.io_bound(restore_backup, backup["path"], len(MIGRATIONS))
            except BackupError as exc:
                status.set_text(f"Restore failed: {t(exc.user_message_key)}")
            except Exception:
                logger.exception("Database recovery failed")
                status.set_text("Restore failed. See the application log for details.")
            else:
                restored = True
                status.set_text(
                    "Backup restored successfully. Restart the application to continue."
                )
            finally:
                busy = False
                if not restored:
                    for button in buttons:
                        button.enable()

        def confirm_restore(backup):
            with ui.dialog() as dialog, ui.card():
                ui.label(f"Restore {backup['name']}?").classes("text-subtitle1")
                ui.label("This replaces the current database with the selected backup.")

                async def confirm():
                    dialog.close()
                    await restore(backup)

                with ui.row():
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button("Restore backup", on_click=confirm).props("color=primary")
            dialog.open()

        async def populate():
            try:
                backups = await run.io_bound(list_backups)
            except Exception:
                logger.exception("Could not list recovery backups")
                status.set_text("Could not load backups. See the application log for details.")
                return
            with backup_list:
                if not backups:
                    ui.label("No backups found.").classes("text-grey-7")
                for backup in backups:
                    compatible = is_backup_compatible(backup)
                    version = backup["schema_version"]
                    label = f"{backup['name']} ({backup['size_mb']} MB)"
                    if not compatible:
                        label += (
                            f" — requires schema v{version}; this app supports v{len(MIGRATIONS)}"
                        )
                    button = (
                        ui.button(
                            label,
                            icon="restore",
                            on_click=lambda _event, b=backup: confirm_restore(b),
                        )
                        .props("flat align-left")
                        .classes("w-full")
                    )
                    if compatible:
                        buttons.append(button)
                    else:
                        button.disable()

        backup_list = (
            ui.column().classes("w-full gap-xs").style("max-height: 300px; overflow-y: auto")
        )
        ui.timer(0.1, populate, once=True)
