import os
import platform
from pathlib import Path


def get_config_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    app_dir = base / "video_review_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / "config.yaml"


def get_default_db_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    app_dir = base / "video_review_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / "review_data.db"
