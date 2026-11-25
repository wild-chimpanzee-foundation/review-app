"""Keep browser preferences independent of the application's launch directory."""

import os
import shutil
from pathlib import Path


def configure_user_storage(user_data_dir: Path) -> Path:
    """Configure NiceGUI before serving requests, retaining existing user files.

    NiceGUI may already be imported by UI modules, so setting its environment
    variable alone is too late: its storage path is resolved at import time.
    Explicit operator overrides remain supported.
    """
    from nicegui.storage import Storage

    override = os.environ.get("NICEGUI_STORAGE_PATH")
    target = Path(override).resolve() if override else user_data_dir / "nicegui"
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
        # One-time migration from the old working-directory-relative default.
        # Never import unrelated legacy files when an override was requested.
        if not override:
            for source in (Path.cwd() / ".nicegui").glob("storage-user-*.json"):
                shutil.copy2(source, target / source.name)
    Storage.path = target
    return target
