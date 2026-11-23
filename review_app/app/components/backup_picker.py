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


def backup_option(backup: dict[str, Any], *, on_click) -> ui.button:
    timestamp = backup["timestamp"].astimezone().strftime("%Y-%m-%d %H:%M:%S")
    compressed = backup["path"].suffix == ".gz"
    details = t(
        "backup_compressed_size" if compressed else "backup_uncompressed_size",
        size=backup["size_mb"],
    )
    compatible = is_backup_compatible(backup)
    if not compatible:
        details += "  ·  " + t("backup_requires_schema", version=backup["schema_version"])
    button = (
        ui.button(f"{timestamp}  ·  {details}", icon="restore", on_click=on_click)
        .props("flat dense align-left")
        .classes("w-full")
    )
    button.tooltip(backup["name"] if compatible else t("restore_error_schema_version"))
    if not compatible:
        button.disable()
    return button


async def restore_uploaded_backup(upload: FileUpload, restore) -> None:
    with TemporaryDirectory(prefix="review-backup-") as directory:
        path = Path(directory) / "upload"
        try:
            await upload.save(path)
        except OSError:
            ui.notify(t("restore_failed", error=t("restore_error_copy_failed")), type="negative")
            return
        await restore(path)
