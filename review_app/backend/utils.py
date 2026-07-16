import json
import subprocess
from pathlib import Path

import pandas as pd

# Default confidence cutoff for model-prediction review aids (blank / species /
# object detection). Used as the fallback wherever a stored threshold is absent.
DEFAULT_REVIEW_THRESHOLD = 0.75

_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv",
}

# Extensions browsers can never play natively — transcode even when is_web_safe is NULL
_BROWSER_UNSAFE_EXTS = {".avi", ".mkv", ".wmv", ".flv", ".m4v"}


def bind_id_list(params: dict, key: str, values: list) -> str:
    """Bind a list of ids as a single JSON parameter and return a subquery usable in `IN`.

    SQLite caps bound variables at 32766; one `?` per id breaks on large projects
    ("too many SQL variables"), so the whole list is passed as one JSON string.
    """
    params[key] = json.dumps(values)
    return f"(SELECT value FROM json_each(:{key}))"


def get_video_mime(url: str) -> str:
    """Return the MIME type for a given video URL/path."""
    return _MIME_BY_EXT.get(Path(url).suffix.lower(), "video/mp4")


def make_serializable(val):
    """Make a value JSON serializable (e.g., convert datetime to ISO string)."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def df_to_records(df: pd.DataFrame, limit: int = 10) -> list[dict]:
    """Convert a DataFrame to a list of serializable dictionaries."""
    records = []
    if df is not None and not df.empty:
        for _, row in df.head(limit).iterrows():
            records.append({k: make_serializable(v) for k, v in row.items()})
    return records


def generate_thumbnail(video_path: Path, output_path: Path) -> bool:
    """Extract a single frame from the middle of video_path and save as JPEG. Returns True on success."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        duration = float(probe.stdout.strip() or 0)
        seek = max(duration / 2, 0)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(seek),
                "-i",
                str(video_path),
                "-vframes",
                "1",
                "-vf",
                "scale=320:-1",
                "-q:v",
                "5",
                str(output_path),
            ],
            capture_output=True,
            timeout=30,
        )
        return output_path.exists()
    except Exception:
        return False


def needs_browser_transcode(video_row: dict) -> bool:
    """Check if a video needs to be transcoded for browser playback."""
    ws = video_row.get("is_web_safe")
    if ws is True:
        return False
    transcoded = video_row.get("transcoded_path")
    if transcoded and Path(transcoded).exists():
        return False
    if ws is False:
        return True
    # ws is None (not yet probed): use extension as heuristic
    ext = Path(video_row.get("video_path", "")).suffix.lower()
    return ext in _BROWSER_UNSAFE_EXTS
