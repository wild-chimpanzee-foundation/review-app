"""Scientific outputs and specialist transfers, using existing export operations."""

from datetime import datetime

from nicegui import run, ui

from review_app.app.state import get_active_project_id, get_annotator_name, get_language
from review_app.app.translations import t
from review_app.app.utils import ignore_deleted_client, user_error_message

from .distance_export import render_distance_export
from .footage_export import render_species_video_export


def render_csv_export(dp, project_id, *, ai=False):
    """Keep CSV serialization unchanged and bind the operation to this project."""
    status = ui.label("").classes("text-caption text-grey-6")

    async def download():
        button.disable()
        try:
            if get_active_project_id() != project_id:
                raise ValueError(t("handoff_project_changed"))
            method = dp.export_model_annotations_csv if ai else dp.export_annotations_csv
            df = await run.io_bound(method, project_id)
            if get_active_project_id() != project_id:
                raise ValueError(t("handoff_project_changed"))
            project = dp.get_project(project_id)
            project_name = project.name.replace(" ", "_") if project else "project"
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            if ai:
                filename = f"ai_annotations_{project_name}_{timestamp}.csv"
            else:
                annotator = get_annotator_name().replace(" ", "_")
                filename = f"annotations_{project_name}_{annotator}_{timestamp}.csv"
            with ignore_deleted_client():
                ui.download(df.to_csv(index=False).encode("utf-8"), filename)
                status.set_text(t("data_exported_rows", count=len(df)))
        except Exception as exc:
            with ignore_deleted_client():
                ui.notify(t("export_failed", error=user_error_message(exc)), type="negative")
        finally:
            with ignore_deleted_client():
                button.enable()

    button = ui.button(
        t("export_ai_annotations" if ai else "export_annotations"),
        icon="download",
        on_click=download,
    ).props("unelevated")


async def render_results(dp, project_id, loading_dialog):
    datasets = [
        ("species", "data_species_results", "CSV", "data_species_results_help"),
        ("distance", "data_distance_results", "TSV", "data_distance_results_help"),
        ("ai", "data_ai_results", "CSV", "data_ai_results_help"),
        ("footage", "species_video_export_title", "", "data_footage_results_help"),
        ("snapshot", "data_snapshot_results", "ZIP", "data_snapshot_results_help"),
    ]
    with ui.row().classes("w-full items-start gap-6"):
        with (
            ui.tabs()
            .props("vertical align=left")
            .classes("w-full md:w-64 shrink-0") as datasets_menu
        ):
            for name, title, _, _ in datasets:
                if name == "snapshot":
                    ui.separator().classes("q-my-sm")
                    ui.label(t("data_specialist_transfer")).classes("text-caption text-grey-6")
                ui.tab(name, label=t(title)).classes("justify-start")
        with (
            ui.tab_panels(datasets_menu, value="species")
            .props("keep-alive")
            .classes("grow min-w-0 w-full md:w-0")
        ):
            for name, title, fmt, description in datasets:
                with ui.tab_panel(name).classes("q-pa-none"):
                    with ui.column().classes("w-full gap-4"):
                        with ui.row().classes("items-center gap-3"):
                            ui.label(t(title)).classes("text-h6")
                            if fmt:
                                ui.badge(fmt).props("outline")
                        ui.label(t(description)).classes("text-body2 text-grey-6")
                        ui.label(t("data_current_scope")).classes("text-caption text-grey-6")
                        if name in {"species", "ai"}:
                            render_csv_export(dp, project_id, ai=name == "ai")
                        elif name in {"distance", "snapshot"}:
                            ui.label(t("distance_manual_help")).classes("text-caption text-grey-6")
                            await render_distance_export(
                                dp, project_id, loading_dialog, analysis=name == "distance"
                            )
                        else:
                            render_species_video_export(dp, project_id, get_language())
