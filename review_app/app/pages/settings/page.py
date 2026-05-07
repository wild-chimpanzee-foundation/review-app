import asyncio
from pathlib import Path

from nicegui import ui

from review_app.app.config import get_default_db_path, get_user_data_dir
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_data_provider,
    get_language,
    get_species_threshold,
    set_active_project,
    set_annotator_name,
    set_blank_threshold,
    set_species_threshold,
)
from review_app.app.translations import t
from review_app.app.utils import get_or_create_data_provider, sync_with_progress
from review_app.backend.provider.local_data_provider import LocalDataProvider

from .database import render_database_section
from .species import render_species_section


def _build_settings_content(container: ui.column):
    current_db_path = get_default_db_path()

    initial_annotator = get_annotator_name()
    initial_blank_threshold = get_blank_threshold()
    initial_species_threshold = get_species_threshold()

    active_project_id = get_active_project_id()
    current_project_name = ""

    stats = {"videos": 0}
    current_video_dirs: list = []
    try:
        _dp_stats = LocalDataProvider()
        if active_project_id:
            _proj = _dp_stats.get_project(active_project_id)
            current_project_name = _proj.name if _proj else ""
        stats["videos"] = (
            _dp_stats.get_overview_stats(active_project_id).get("videos", {}).get("total", 0)
        )
        current_video_dirs = (
            [Path(d.path) for d in _dp_stats.get_project_dirs(active_project_id)]
            if active_project_id
            else []
        )
    except Exception:
        pass

    with container:
        with ui.row().classes("items-center q-mb-lg"):
            ui.label(t("settings_title")).classes("text-h5 font-weight-bold")

        if active_project_id:
            with ui.card().classes("full-width q-mb-lg"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.icon("folder_special", size="sm").classes("text-primary q-mr-sm")
                    ui.label(t("project_name_label")).classes("text-subtitle1 font-weight-medium")
                with ui.row().classes("w-full items-center gap-sm"):
                    project_name_input = (
                        ui.input(value=current_project_name).props("outlined dense").classes("col")
                    )

                    async def save_project_name():
                        name = project_name_input.value.strip()
                        if not name:
                            ui.notify(t("project_name_required"), type="warning")
                            return
                        _dp = get_data_provider() or LocalDataProvider()
                        _dp.update_project_name(active_project_id, name)
                        set_active_project(active_project_id)
                        ui.notify(t("project_name_saved"), type="positive")
                        await asyncio.sleep(0.5)
                        ui.navigate.to("/settings")

                    ui.button(
                        t("save"), icon="check", color="primary", on_click=save_project_name
                    ).props("dense")

        with ui.card().classes("full-width q-mb-lg"):
            ui.label(t("current_status")).classes("text-subtitle1 font-weight-medium q-mb-md")
            with ui.row().classes("w-full gap-md"):
                with ui.card().classes("col text-center"):
                    ui.label(str(stats["videos"])).classes("text-h5 font-weight-bold")
                    ui.label(t("videos_in_db")).classes("text-caption text-grey-6")
                with ui.card().classes("col text-center"):
                    ui.label(
                        str(current_db_path) if current_db_path else t("not_available")
                    ).classes("text-body2")
                    ui.label(t("database")).classes("text-caption text-grey-6")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("person", size="sm").classes("text-primary q-mr-sm")
                ui.label(t("annotator_label")).classes("text-subtitle1 font-weight-medium")
            with ui.row().classes("w-full items-center gap-sm"):
                annotator_input = (
                    ui.input(t("annotator_name"), value=initial_annotator)
                    .props("outlined dense")
                    .classes("col")
                )

                def save_annotator():
                    name = annotator_input.value.strip() or "default"
                    set_annotator_name(name)
                    ui.notify(t("settings_saved"), type="positive")

                ui.button(t("save"), icon="check", color="primary", on_click=save_annotator).props(
                    "dense"
                )

        if active_project_id:
            with ui.card().classes("full-width q-mb-lg"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.icon("folder_open", size="sm").classes("text-primary q-mr-sm")
                    ui.label(t("video_dir_label")).classes("text-subtitle1 font-weight-medium")
                ui.label(t("video_dir_desc")).classes("text-caption  q-mb-md")

                video_dir = current_video_dirs[0] if current_video_dirs else None
                dir_label = str(video_dir) if video_dir else t("not_available")
                ui.label(dir_label).classes(
                    "text-body2  q-pa-xs rounded-borders full-width q-mb-sm"
                )

                dir_sync_dialog = ui.dialog()

                async def sync_dir():
                    _dp = get_data_provider() or LocalDataProvider()
                    dir_sync_dialog.clear()
                    with dir_sync_dialog, ui.card().classes("q-pa-lg").style("min-width: 360px"):
                        ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                        progress = ui.linear_progress(value=0, show_value=False).props(
                            "color=primary"
                        )
                        status = ui.label(t("starting"))
                    dir_sync_dialog.open()
                    sync_stats = await sync_with_progress(
                        _dp, progress=progress, status=status, active_project_id=active_project_id
                    )
                    dir_sync_dialog.clear()
                    with dir_sync_dialog, ui.card().classes("q-pa-lg").style("min-width: 360px"):
                        ui.icon("check_circle", size="lg").classes("text-positive q-mb-sm")
                        ui.label(t("sync_complete")).classes("text-h6 q-mb-md")
                        if sync_stats:
                            with ui.column().classes("w-full gap-xs q-mb-lg"):
                                ui.label(t("sync_stat_scanned", n=sync_stats["scanned"])).classes(
                                    "text-body2"
                                )
                                ui.label(t("sync_stat_added", n=sync_stats["added"])).classes(
                                    "text-body2 text-positive"
                                )
                                ui.label(t("sync_stat_updated", n=sync_stats["updated"])).classes(
                                    "text-body2 text-grey-6"
                                )
                        ui.button(
                            t("close"), on_click=dir_sync_dialog.close, color="primary"
                        ).classes("full-width")

                ui.button(
                    t("sync_videos_label"), icon="sync", color="primary", on_click=sync_dir
                ).props("dense")

        with ui.expansion(t("advanced_settings"), icon="settings").classes("full-width q-mb-lg"):
            with ui.column().classes("w-full gap-lg q-pa-md"):
                if active_project_id:
                    lang = get_language()
                    _dp = get_data_provider() or LocalDataProvider()
                    with ui.expansion(t("project_species_settings"), icon="pets").classes(
                        "full-width"
                    ):
                        render_species_section(_dp, active_project_id, lang)

                with ui.expansion(t("blank_detection"), icon="tune").classes("full-width"):
                    ui.label(t("blank_detection_desc")).classes("text-caption text-grey-6 q-mb-md")
                    ui.label(t("blank_threshold_label")).classes(
                        "text-caption text-grey-6 q-mb-xs"
                    )
                    blank_threshold_slider = ui.slider(
                        min=0.0, max=1.0, step=0.05, value=initial_blank_threshold
                    ).props("label label-always class=q-mb-md")
                    ui.label(t("species_threshold_label")).classes(
                        "text-caption text-grey-6 q-mb-xs"
                    )
                    species_threshold_slider = ui.slider(
                        min=0.0, max=1.0, step=0.05, value=initial_species_threshold
                    ).props("label label-always")

                    with ui.row().classes("w-full justify-end q-mt-sm"):

                        def save_thresholds():
                            set_blank_threshold(blank_threshold_slider.value)
                            set_species_threshold(species_threshold_slider.value)
                            ui.notify(t("settings_saved"), type="positive")

                        ui.button(
                            t("save"), icon="check", color="primary", on_click=save_thresholds
                        ).props("dense")

                with ui.expansion(t("database_management"), icon="storage").classes("full-width"):
                    render_database_section(current_db_path)

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("w-full items-center q-mb-md"):
                log_path = get_user_data_dir() / "app.log"
                ui.label(t("download_log_label")).classes("text-body2")
                ui.space()
                if log_path.exists():
                    ui.button(
                        t("download_log_btn"),
                        icon="description",
                        on_click=lambda: ui.download(log_path, filename="app.log"),
                    ).props("flat color=primary dense")
                else:
                    ui.button(
                        t("download_log_btn"),
                        icon="description",
                    ).props("flat dense").classes("disabled").tooltip(t("log_not_available"))


async def setup_settings():
    from review_app.app.entry_point import shared_header

    shared_header()

    await get_or_create_data_provider()

    container = ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto")
    container.clear()
    _build_settings_content(container)
