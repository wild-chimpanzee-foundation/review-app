import csv
import io

import pandas as pd
from nicegui import ui

from review_app.app.translations import t


def col_val(state: dict, key: str) -> str:
    return state.get(key) or ""


def read_upload_file(content: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "latin-1"):
        for quoting in (csv.QUOTE_MINIMAL, csv.QUOTE_NONE):
            try:
                return pd.read_csv(
                    io.BytesIO(content),
                    sep=None,
                    engine="python",
                    encoding=encoding,
                    quoting=quoting,
                )
            except UnicodeDecodeError:
                break  # wrong encoding, try next
            except Exception:
                continue  # parse error, try next quoting
    raise ValueError("Could not parse file — try saving as UTF-8 CSV")


def make_col_selects(state: dict, specs: list[tuple[str, dict]]) -> list[tuple[str, object]]:
    result = []
    for key, opts in specs:
        sel = (
            ui.select(
                label=t(key),
                options=opts,
                value=state.get(key) or "",
            )
            .props("outlined dense")
            .classes("col")
        )
        sel._props["hint"] = t(key + "_hint")
        result.append((key, sel))
    return result


def auto_suggest_mappings(columns: list[str]) -> list[dict]:
    col_set = set(columns)
    suggestions: list[dict] = []
    detected_models: list[str] = []

    for col in columns:
        if col.startswith("top_1_"):
            model = col[6:]
            detected_models.append(model)
            suggestions.append(
                {
                    "model_name": model,
                    "annotation_type": "species",
                    "value_col": col,
                    "prob_col": f"prob_{model}" if f"prob_{model}" in col_set else "",
                    "count_col": "",
                }
            )

    for model_sug in [s for s in suggestions if s["annotation_type"] == "species"]:
        model = model_sug["model_name"]
        for pattern in (f"count_{model}", f"{model}_count", "num_objects"):
            if pattern in col_set:
                model_sug["count_col"] = pattern
                break

    blank_found = False
    for model in detected_models:
        for pattern in (
            f"blank_{model}",
            f"{model}_blank",
            f"p_blank_{model}",
            f"prob_blank_{model}",
        ):
            if pattern in col_set:
                suggestions.append(
                    {
                        "model_name": model,
                        "annotation_type": "blank_non_blank",
                        "value_col": "",
                        "prob_col": pattern,
                        "count_col": "",
                    }
                )
                blank_found = True

    if not blank_found:
        for blank_col in ("blank", "blank_prob", "p_blank", "prob_blank"):
            if blank_col in col_set:
                suggestions.append(
                    {
                        "model_name": blank_col,
                        "annotation_type": "blank_non_blank",
                        "value_col": "",
                        "prob_col": blank_col,
                        "count_col": "",
                    }
                )
                break

    return suggestions


def auto_suggest_ann_cols(columns: list[str]) -> dict[str, str]:
    """Return suggested state-key → column-name mappings for the external annotation import.

    Conservative: only exact case-insensitive matches. Never returns a column that is
    not present in `columns`. Prefers single-path mode when a combined path column is found.
    """
    col_lower = {c.lower(): c for c in columns}

    _EXACT: dict[str, list[str]] = {
        "ann_folder_col": ["folder_name_standard", "folder_name", "folder"],
        "ann_video_col": ["video_name", "filename", "file_name"],
        "ann_species_col": ["species"],
        "ann_behavior_col": ["behaviour", "behavior"],
        "ann_count_col": ["number"],
        "ann_observer_col": ["observer"],
        "ann_timestamp_col": ["timestamp"],
        "ann_is_blank_col": ["is_blank"],
        "ann_path_col": ["filepath", "file_path", "video_path", "path"],
    }

    result: dict[str, str] = {}
    for key, candidates in _EXACT.items():
        for c in candidates:
            if c in col_lower:
                result[key] = col_lower[c]
                break

    # When a single combined path column is found, suggest single-path mode
    if "ann_path_col" in result:
        result["ann_path_mode"] = "single"

    return result


def auto_suggest_path_col(columns: list[str], sample: list[dict]) -> str:
    for preferred in (
        "review_filename",
        "filepath",
        "original_filepath",
        "video_path",
        "path",
        "file",
    ):
        if preferred in columns:
            return preferred
    if sample:
        first = sample[0]
        for col in columns:
            val = str(first.get(col, ""))
            if "/" in val or "\\" in val:
                return col
    return columns[0] if columns else ""


_PATH_COL_ALIASES = {"video_path", "filepath", "review_filename", "original_filepath"}


def is_long_format(columns: list[str]) -> bool:
    col_set = set(columns)
    has_path = bool(col_set & _PATH_COL_ALIASES)
    return has_path and {"annotation_type", "model_name"}.issubset(col_set)
