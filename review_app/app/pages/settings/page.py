import asyncio
from pathlib import Path

from nicegui import run, ui

from review_app.app.config import get_default_db_path, get_user_data_dir
from review_app.app.pages.distribution import render_distribution_section
from review_app.app.state import (
    get_active_project_id,
    get_blank_threshold,
    get_data_provider,
    get_language,
    get_obj_detection_threshold,
    get_species_threshold,
    set_active_project,
    set_blank_threshold,
    set_obj_detection_threshold,
    set_species_threshold,
)
from review_app.app.translations import t
from review_app.app.utils import get_or_create_data_provider, sync_with_progress

from .database import render_database_section
from .species import render_species_section
from .tags import TagsSection


def _build_settings_content(container: ui.column):
    current_db_path = get_default_db_path()

    initial_blank_threshold = get_blank_threshold()
    initial_species_threshold = get_species_threshold()
    initial_obj_detection_threshold = get_obj_detection_threshold()

    active_project_id = get_active_project_id()
    current_project_name = ""

    current_video_dirs: list = []
    try:
        _dp_stats = get_data_provider()
        if active_project_id:
            _proj = _dp_stats.get_project(active_project_id)
            current_project_name = _proj.name if _proj else ""
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
                        _dp = get_data_provider()
                        _dp.update_project_name(active_project_id, name)
                        set_active_project(active_project_id)
                        ui.notify(t("project_name_saved"), type="positive")
                        await asyncio.sleep(0.5)
                        ui.navigate.to("/settings")

                    ui.button(
                        t("save"), icon="check", color="primary", on_click=save_project_name
                    ).props("dense")

                ui.separator().classes("q-my-sm")
                ui.label(t("project_collection_label")).classes("text-subtitle2 q-mb-xs")
                ui.label(t("project_collection_desc")).classes("text-caption text-grey-6 q-mb-sm")
                _dp_coll = get_data_provider()
                _collections = _dp_coll.list_collections()
                _coll_options = {"": t("no_collection")} | {
                    c["id"]: c["name"] for c in _collections
                }
                _current_coll = _dp_coll.get_project_collection(active_project_id) or ""
                with ui.row().classes("w-full items-center gap-sm"):
                    coll_select = (
                        ui.select(options=_coll_options, value=_current_coll)
                        .props("outlined dense")
                        .classes("col")
                    )

                    async def save_collection():
                        cid = coll_select.value or None
                        _dp2 = get_data_provider()
                        await run.io_bound(_dp2.set_project_collection, active_project_id, cid)
                        if cid:
                            ui.notify(t("collection_applied"), type="positive")
                        else:
                            ui.notify(t("settings_saved"), type="positive")
                        ui.navigate.to("/settings")

                    ui.button(
                        t("save"), icon="check", color="primary", on_click=save_collection
                    ).props("dense")

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
                    _dp = get_data_provider()
                    dir_sync_dialog.clear()
                    with dir_sync_dialog, ui.card().classes("q-pa-lg").style("min-width: 360px"):
                        ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                        progress = ui.linear_progress(show_value=False).props(
                            "indeterminate color=primary"
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
                                if sync_stats.get("added", 0) > 0:
                                    ui.label(t("sync_stat_added", n=sync_stats["added"])).classes(
                                        "text-body2 text-positive"
                                    )
                                ui.label(
                                    t("sync_stat_removed", n=sync_stats.get("removed", 0))
                                ).classes(
                                    "text-body2 text-warning"
                                    if sync_stats.get("removed", 0) > 0
                                    else "text-body2 text-grey-6"
                                )
                        ui.button(
                            t("close"), on_click=dir_sync_dialog.close, color="primary"
                        ).classes("full-width")

                ui.button(
                    t("sync_videos_label"), icon="sync", color="primary", on_click=sync_dir
                ).props("dense")

                missing_count = 0
                try:
                    missing_count = _dp_stats.count_missing_videos(active_project_id)
                except Exception:
                    pass

                if missing_count:
                    ui.separator().classes("q-my-sm")

                    delete_missing_dialog = ui.dialog().props("persistent")

                    async def do_delete_missing():
                        delete_missing_dialog.close()
                        _dp = get_data_provider()
                        n = await run.io_bound(_dp.delete_missing_videos, active_project_id)
                        ui.notify(t("delete_missing_videos_success", n=n), type="positive")
                        await asyncio.sleep(0.3)
                        ui.navigate.to("/settings")

                    with delete_missing_dialog, ui.card().classes("q-pa-lg"):
                        ui.label(t("delete_missing_videos_confirm")).classes("text-h6 q-mb-sm")
                        ui.label(t("delete_missing_videos_warning")).classes(
                            "text-body2 text-negative q-mb-lg"
                        )
                        with ui.row().classes("w-full justify-end gap-sm"):
                            ui.button(t("cancel"), on_click=delete_missing_dialog.close).props(
                                "flat"
                            )
                            ui.button(
                                t("yes_delete"),
                                icon="delete_forever",
                                color="negative",
                                on_click=do_delete_missing,
                            )

                    with ui.row().classes("items-center gap-sm q-mt-xs"):
                        ui.label(t("delete_missing_videos_label") + f" ({missing_count})").classes(
                            "text-body2 text-grey-7"
                        )
                        ui.space()
                        ui.button(
                            t("delete_missing_videos_label"),
                            icon="video_file",
                            color="negative",
                            on_click=delete_missing_dialog.open,
                        ).props("dense outline")

        with ui.expansion(t("advanced_settings"), icon="settings").classes("full-width q-mb-lg"):
            with ui.column().classes("w-full gap-lg q-pa-md"):
                _dp = get_data_provider()
                if active_project_id:
                    lang = get_language()
                    with ui.expansion(t("project_species_settings"), icon="pets").classes(
                        "full-width"
                    ):
                        render_species_section(_dp, active_project_id, lang)

                with ui.expansion(t("settings_tags_section"), icon="label").classes("full-width"):
                    TagsSection(_dp).render()

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
                    ).props("label label-always class=q-mb-md")
                    ui.label(t("obj_detection_threshold_label")).classes(
                        "text-caption text-grey-6 q-mb-xs"
                    )
                    obj_detection_threshold_slider = ui.slider(
                        min=0.0, max=1.0, step=0.05, value=initial_obj_detection_threshold
                    ).props("label label-always")

                    with ui.row().classes("w-full justify-end q-mt-sm"):

                        def save_thresholds():
                            set_blank_threshold(blank_threshold_slider.value)
                            set_species_threshold(species_threshold_slider.value)
                            set_obj_detection_threshold(obj_detection_threshold_slider.value)
                            ui.notify(t("settings_saved"), type="positive")

                        ui.button(
                            t("save"), icon="check", color="primary", on_click=save_thresholds
                        ).props("dense")

                if active_project_id:
                    with ui.expansion(t("nav_distribution"), icon="group").classes("full-width"):
                        render_distribution_section(_dp, active_project_id)

                with ui.expansion(t("database_management"), icon="storage").classes("full-width"):
                    render_database_section(current_db_path, active_project_id)

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

            ui.separator().classes("q-my-xs")

            with ui.row().classes("w-full items-center"):
                ui.label(t("check_for_updates_label")).classes("text-body2")
                ui.space()
                check_btn = ui.button(t("check_for_updates_btn"), icon="system_update_alt").props(
                    "flat color=primary dense"
                )

                async def _do_check_update():
                    from review_app.app.update_checker import force_check_for_update

                    check_btn.props("loading")
                    try:
                        result = await run.io_bound(force_check_for_update)
                        if result:
                            tag, url = result
                            ui.notify(
                                t("update_available_notify", version=tag.lstrip("v")),
                                type="positive",
                            )
                            await ui.run_javascript(f"window.open('{url}', '_blank')")
                        else:
                            ui.notify(t("update_up_to_date"), type="positive")
                    except Exception:
                        ui.notify(t("update_check_failed"), type="warning")
                    finally:
                        check_btn.props(remove="loading")

                check_btn.on("click", _do_check_update)


async def setup_settings():
    from review_app.app.entry_point import shared_header

    shared_header()

    await get_or_create_data_provider()

    container = ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto")
    container.clear()
    _build_settings_content(container)
