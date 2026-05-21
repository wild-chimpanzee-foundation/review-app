from nicegui import run, ui

from review_app.app.onboarding import show_info_dialog
from review_app.app.state import get_active_project_id, set_state_val
from review_app.app.translations import t
from review_app.app.utils import get_or_create_data_provider, render_uninitialized_state

from ._annotations_tab import setup_annotations_tab
from ._metadata_tab import setup_metadata_tab
from ._model_tab import setup_model_tab

_STATE_KEYS = (
    "raw_df_records",
    "raw_csv_columns",
    "path_col",
    "ann_mappings",
    "match_preview",
    "uploaded_df",
    "cleaned_df",
    "errors_df",
    "species_mappings",
    "unmapped_species",
    "match_stats",
    "csv_format",
    "manual_import_mode",
    "ann_df_records",
    "ann_columns",
    "ann_format",
    "ann_validation",
    "ann_species_mappings",
    "ann_folder_col",
    "ann_video_col",
    "ann_species_col",
    "ann_data_type_col",
    "ann_data_type_val",
    "ann_behavior_col",
    "ann_count_col",
    "ann_observer_col",
    "ann_timestamp_col",
    "ann_is_blank_col",
    "ann_tag_cols",
)


async def setup_model_import():
    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    if not dp or not await run.io_bound(dp.has_videos_in_db, get_active_project_id()):
        shared_header()
        render_uninitialized_state()
        return

    shared_header()

    for key in _STATE_KEYS:
        set_state_val(key, None)

    loading_dialog = ui.dialog().props("persistent")
    with loading_dialog, ui.card().classes("q-pa-lg items-center"):
        ui.spinner(size="lg")
        ui.label(t("processing_wait")).classes("q-mt-md")

    with ui.column().classes("w-full q-pa-md").style("max-width: 1600px; margin: 0 auto"):
        with ui.row().classes("items-center gap-sm q-mb-lg"):
            ui.label(t("nav_import")).classes("text-h5 text-primary font-weight-bold")
            ui.button(
                icon="info_outline",
                on_click=lambda: show_info_dialog(
                    t("info_model_import_title"), t("info_model_import_body")
                ),
            ).props("flat round dense color=primary")

        with ui.tabs().classes("w-full") as tabs:
            tab_model = ui.tab("model_import", label=t("model_import_title"))
            tab_annotations = ui.tab("annotations", label=t("annotation_export_import_title"))
            tab_metadata = ui.tab("metadata", label=t("metadata_import_title"))

        with ui.tab_panels(tabs, value=tab_model).classes("w-full"):
            with ui.tab_panel(tab_model):
                await setup_model_tab(dp, loading_dialog)
            with ui.tab_panel(tab_annotations):
                setup_annotations_tab(dp, loading_dialog)
            with ui.tab_panel(tab_metadata):
                setup_metadata_tab(dp, loading_dialog)
