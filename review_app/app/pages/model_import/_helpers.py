from typing import Callable

import pandas as pd
from nicegui import ui

from review_app.app.state import get_language, get_state_val, set_state_val
from review_app.app.translations import t


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


BLANK_SENTINEL = "__blank__"


def render_species_mappings(
    dp,
    all_mappings: dict,
    unmapped_origs: set,
    all_species: set,
    apply_fn: Callable,
    update_import_button: Callable,
    can_apply: bool,
    mappings_state_key: str = "species_mappings",
    show_blank_option: bool = False,
) -> None:
    """Shared species-mapping editor used by both the model-import and historic-import tabs."""
    ui.label(t("species_mappings")).classes("text-subtitle1 font-weight-medium q-mb-sm")
    ui.label(t("edit_mappings_desc")).classes("text-caption q-mb-md")

    species_map = dp.get_species_display_map(get_language())
    blank_opt = {BLANK_SENTINEL: f"— {t('map_to_blank_video')} —"} if show_blank_option else {}
    select_options = {"": "", **blank_opt, **species_map}

    for orig in sorted(all_species):
        current_mapping = all_mappings.get(orig, "")
        is_unmapped = orig in unmapped_origs
        with ui.row().classes("w-full items-center q-mb-sm"):
            ui.label(orig).classes(f"col {'text-negative' if is_unmapped else ''}")
            select = ui.select(
                label=t("mapped_to"),
                options=select_options,
                value=current_mapping,
                with_input=True,
            ).props("outlined dense class=col-4")

            def make_update_fn(o: str, sel) -> Callable:
                def _update():
                    mappings = get_state_val(mappings_state_key) or {}
                    mappings[o] = sel.value
                    set_state_val(mappings_state_key, mappings)
                    update_import_button()

                return _update

            select.on_value_change(make_update_fn(orig, select))

    pending_unmapped = [k for k, v in (get_state_val(mappings_state_key) or {}).items() if not v]
    ui.button(t("apply"), icon="refresh", on_click=apply_fn, color="primary").props(
        f"wide {'disabled' if not can_apply else ''}"
    )
    if pending_unmapped:
        ui.label(t("map_all_to_import", list=", ".join(pending_unmapped))).classes(
            "text-warning text-caption q-mt-sm"
        )
