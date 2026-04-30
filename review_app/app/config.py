import sys
from pathlib import Path

import platformdirs

APP_NAME = "VideoAnnotation"

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}
)

CSV_TEMPLATES: dict[str, str] = {
    "model_annotations": (
        "video_uid,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec\n"
        "CAM01/VIDEO_001.mp4,species,species_model_a,deer,,0.92,0,12.0\n"
        "CAM01/VIDEO_001.mp4,behavior,behavior_model_a,reacts_to_camera,,0.83,0,12.0\n"
        "CAM01/VIDEO_002.mp4,blank_non_blank,blank_model,blank,,0.98,0,\n"
    )
}

if getattr(sys, "frozen", False):
    REPO_ROOT = Path(sys.executable).parent
else:
    REPO_ROOT = Path(__file__).parents[2]

DEFAULT_DB_FILENAME = "review_data.db"


def get_user_data_dir() -> Path:
    """Platform-correct writable directory for config and DB on all OSes."""
    return Path(platformdirs.user_data_dir(APP_NAME))


def get_app_dir() -> Path:
    """Location of bundled read-only resources (CSV files, default config).

    NOT for writing — use get_user_data_dir() for config and DB.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parents[2]


def get_default_db_path() -> Path:
    return get_user_data_dir() / "review_data.db"


def get_bundled_species_csv() -> str | None:
    bundle_dir = Path(__file__).parent.parent / "data"
    bundled = bundle_dir / "species.csv"
    if bundled.exists():
        return str(bundled)
    return None


def get_bundled_behaviors_csv() -> str | None:
    bundle_dir = Path(__file__).parent.parent / "data"
    bundled = bundle_dir / "species_behaviors.csv"
    if bundled.exists():
        return str(bundled)
    return None


