"""Project data, assignment handoff and results in one NiceGUI destination."""

import json

from nicegui import ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import get_or_create_data_provider, render_uninitialized_state

from .add_data import render_add_data
from .assignments import render_assignments
from .results import render_results


async def setup_data_assignments(area="assignments"):
    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    shared_header()
    project_id = get_active_project_id()
    if not dp or not project_id:
        render_uninitialized_state()
        return
    if area not in {"add", "assignments", "results"}:
        area = "assignments"
    project = dp.get_project(project_id)

    loading_dialog = ui.dialog().props("persistent")
    with loading_dialog, ui.card().classes("q-pa-lg items-center"):
        ui.spinner(size="lg")
        ui.label(t("processing_wait")).classes("q-mt-md")

    def area_changed(event):
        # Keep links reloadable without navigating away and discarding upload/preview state.
        ui.run_javascript(
            "const url = new URL(window.location.href);"
            f"url.searchParams.set('area', {json.dumps(event.value)});"
            "window.history.replaceState(null, '', url);"
        )

    with (
        ui.column()
        .classes("w-full q-pa-md md:q-pa-lg gap-4")
        .style("max-width: 1400px; margin: 0 auto")
    ):
        ui.label(t("nav_data_assignments")).classes("text-h5 text-primary font-bold")
        if project:
            with ui.row().classes("items-center gap-2"):
                ui.icon("folder_open", size="sm").classes("text-grey-6")
                ui.label(project.name).classes("text-caption text-grey-6")
        with ui.tabs(on_change=area_changed).props("align=left").classes("w-full") as tabs:
            ui.tab("assignments", label=t("data_assignments"), icon="people_outline")
            ui.tab("add", label=t("data_add"), icon="add_circle_outline")
            ui.tab("results", label=t("data_results"), icon="ios_share")
        with ui.tab_panels(tabs, value=area).props("keep-alive").classes("w-full"):
            with ui.tab_panel("assignments").classes("q-pa-none"):
                render_assignments(dp, project_id)
            with ui.tab_panel("add").classes("q-pa-none"):
                with ui.card().classes("w-full q-pa-md gap-4"):
                    await render_add_data(dp, project_id, loading_dialog)
            with ui.tab_panel("results").classes("q-pa-none"):
                with ui.card().classes("w-full q-pa-md md:q-pa-lg"):
                    await render_results(dp, project_id, loading_dialog)
