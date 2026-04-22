from pathlib import Path

import pandas as pd

_MIME_BY_EXT = {
    ".mp4": "video/mp4", ".avi": "video/x-msvideo", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".wmv": "video/x-ms-wmv", ".flv": "video/x-flv",
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


def get_default_species_from_annotations(
    model_ann: pd.DataFrame, valid_species: list[str], fallback_species: str
) -> str:
    """Pick a default species from model annotations based on highest probability sum."""
    if model_ann is None or model_ann.empty:
        return fallback_species
    if "annotation_type" not in model_ann.columns or "value_text" not in model_ann.columns:
        return fallback_species

    species_rows = model_ann[
        (model_ann["annotation_type"] == "species")
        & model_ann["value_text"].notna()
        & (model_ann["value_text"].astype(str).str.strip() != "")
    ].copy()
    if species_rows.empty:
        return fallback_species

    if "probability" not in species_rows.columns:
        return fallback_species

    species_rows["probability"] = species_rows["probability"].fillna(0.0).astype(float)
    probs = species_rows.groupby("value_text", as_index=False)["probability"].sum()
    probs = probs.sort_values("probability", ascending=False)
    if probs.empty:
        return fallback_species

    candidate = str(probs.iloc[0]["value_text"]).strip()
    return candidate if candidate in valid_species else fallback_species
