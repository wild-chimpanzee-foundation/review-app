"""Intent-based presentation of existing, format-specific import workflows."""

from nicegui import ui

from review_app.app.translations import t

from .imports._annotations_tab import setup_annotations_tab
from .imports._distance_ai_tab import setup_distance_ai_tab
from .imports._metadata_tab import setup_metadata_tab
from .imports._model_tab import setup_model_tab
from .legacy_import import render_legacy_annotations_import


async def render_add_data(dp, project_id, loading_dialog):
    ui.label(t("data_add_help")).classes("text-body2 text-grey-6 q-mb-sm")
    with ui.tabs().props("align=left").classes("w-full") as inputs:
        ui.tab("ai", label=t("data_ai_input"), icon="auto_awesome")
        ui.tab("metadata", label=t("metadata_import_title"), icon="location_on")
        ui.tab("annotations", label=t("data_existing_annotations"), icon="fact_check")
    with ui.tab_panels(inputs, value="ai").props("keep-alive").classes("w-full"):
        with ui.tab_panel("ai"):
            with ui.expansion(t("model_import_title"), icon="pets", value=True).classes("w-full"):
                await setup_model_tab(dp, loading_dialog)
            with ui.expansion(t("distance_ai_title"), icon="straighten").classes("w-full"):
                await setup_distance_ai_tab(dp, loading_dialog)
        with ui.tab_panel("metadata"):
            ui.label(t("data_metadata_help")).classes("text-caption text-grey-6 q-mb-md")
            setup_metadata_tab(dp, loading_dialog)
        with ui.tab_panel("annotations"):
            ui.label(t("data_annotation_import_help")).classes("text-body2 text-grey-6 q-mb-md")
            with ui.expansion(t("data_species_import"), icon="table_chart", value=True).classes(
                "w-full"
            ):
                setup_annotations_tab(dp, loading_dialog)
            with ui.expansion(t("batch_import_title"), icon="playlist_add").classes("w-full"):
                render_legacy_annotations_import(dp, project_id)
    ui.button(
        t("data_vocabulary_settings"),
        icon="settings",
        on_click=lambda: ui.navigate.to("/settings"),
    ).props("flat")
