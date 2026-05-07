import pandas as pd

from review_app.app.state import get_state_val


def get_df_from_state(key: str) -> pd.DataFrame | None:
    data = get_state_val(key)
    return pd.DataFrame(data) if data is not None else None


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
                }
            )

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
                    }
                )
                break

    return suggestions


def auto_suggest_path_col(columns: list[str], sample: list[dict]) -> str:
    for preferred in ("filepath", "original_filepath", "video_path", "path", "file"):
        if preferred in columns:
            return preferred
    if sample:
        first = sample[0]
        for col in columns:
            val = str(first.get(col, ""))
            if "/" in val or "\\" in val:
                return col
    return columns[0] if columns else ""


def is_long_format(columns: list[str]) -> bool:
    return {"path", "annotation_type", "model_name"}.issubset(set(columns))
