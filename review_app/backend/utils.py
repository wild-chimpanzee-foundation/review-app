from pathlib import Path

import pandas as pd

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
