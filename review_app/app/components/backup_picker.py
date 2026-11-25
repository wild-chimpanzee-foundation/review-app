from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from nicegui import ui
from nicegui.elements.upload_files import FileUpload

from review_app.app.translations import t
from review_app.backend.db.migrations import MIGRATIONS

BACKUP_UPLOAD_ACCEPT = "accept=.db,.db.gz"


def is_backup_compatible(backup: dict[str, Any]) -> bool:
    # A filename without a version is checked when its contents are staged.
    version = backup["schema_version"]
    return version is None or version <= len(MIGRATIONS)


def _backup_option(backup: dict[str, Any], *, on_click=None) -> ui.button:
    timestamp = backup["timestamp"].astimezone().strftime("%Y-%m-%d %H:%M:%S")
    compressed = backup["path"].suffix == ".gz"
    details = t(
        "backup_compressed_size" if compressed else "backup_uncompressed_size",
        size=backup["size_mb"],
    )
    button = (
        ui.button(icon="restore", on_click=on_click)
        .props("flat no-caps align=left")
        .classes("w-full px-3 py-2")
    )
    with button, ui.column().classes("col min-w-0 items-start gap-0 pl-3 text-left"):
        ui.label(timestamp).classes("text-body2 text-weight-medium")
        ui.label(details).classes("text-caption")
        if on_click is None:
            ui.label(t("backup_requires_schema", version=backup["schema_version"])).classes(
                "text-caption"
            )
    button.tooltip(backup["name"])
    if on_click is None:
        button.disable()
    return button


def render_backup_options(backups, *, on_select) -> list[ui.button]:
    buttons, incompatible = [], []
    for backup in backups:
        if is_backup_compatible(backup):

            async def select(_event, backup=backup):
                await on_select(backup)

            buttons.append(_backup_option(backup, on_click=select))
        else:
            incompatible.append(backup)
    if incompatible:
        with ui.expansion(
            t("backup_newer_schema_group", count=len(incompatible)), icon="info_outline"
        ).classes("w-full"):
            for backup in incompatible:
                _backup_option(backup)
    return buttons


async def restore_uploaded_backup(upload: FileUpload, restore) -> None:
    with TemporaryDirectory(prefix="review-backup-") as directory:
        path = Path(directory) / "upload"
        try:
            await upload.save(path)
        except OSError:
            ui.notify(t("restore_failed", error=t("restore_error_copy_failed")), type="negative")
            return
        await restore(path)
