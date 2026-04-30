import asyncio
from pathlib import Path

from nicegui import run, ui

from review_app.app.config import get_default_db_path
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_data_provider,
    get_species_threshold,
    reset_app_state,
    set_active_project,
    set_annotator_name,
    set_blank_threshold,
    set_current_idx,
    set_data_provider,
    set_queue,
    set_selections,
    set_species_threshold,
)
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    sync_with_progress,
)
from review_app.backend.local_data_provider import LocalDataProvider


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

    async def _confirm_existing_db(db_path: str):
        """Returns True to keep, False to delete, None to cancel."""
        result: list = [None]
        done: list = [False]
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().classes("q-pa-lg"):
            ui.label(t("database_exists")).classes("text-h6 q-mb-sm")
            ui.label(db_path).classes("text-caption text-grey-6 q-mb-md")
            ui.label(t("database_exists_msg")).classes("text-body2 q-mb-lg")
            with ui.row().classes("w-full justify-end gap-sm"):

                def on_cancel():
                    result[0] = None
                    done[0] = True
                    dialog.close()

                def on_keep():
                    result[0] = True
                    done[0] = True
                    dialog.close()

                def on_delete():
                    result[0] = False
                    done[0] = True
                    dialog.close()

                ui.button(t("cancel"), on_click=on_cancel).props("flat")
                ui.button(t("keep_existing"), icon="storage", color="primary", on_click=on_keep)
                ui.button(
                    t("delete_fresh"), icon="delete_forever", color="negative", on_click=on_delete
                )
        dialog.open()
        while not done[0]:
            await asyncio.sleep(0.05)
        return result[0]

    def _reinit_dp():
        new_dp = LocalDataProvider()
        set_data_provider(new_dp)
        set_queue([])
        set_current_idx(0)
        set_selections([])
        return new_dp

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
                ui.label(t("video_dir_desc")).classes("text-caption text-grey-6 q-mb-md")

                video_dir = current_video_dirs[0] if current_video_dirs else None
                dir_label = str(video_dir) if video_dir else t("not_available")
                ui.label(dir_label).classes(
                    "text-body2 text-grey-5 q-pa-xs bg-grey-9 rounded-borders full-width q-mb-sm"
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

        # Advanced Section
        with ui.expansion(t("advanced_settings"), icon="settings").classes("full-width q-mb-lg"):
            with ui.column().classes("w-full gap-lg q-pa-md"):
                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-sm"):
                        ui.icon("tune", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("blank_detection")).classes("text-subtitle1 font-weight-medium")
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

                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-sm"):
                        ui.icon("storage", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("database_management")).classes(
                            "text-subtitle1 font-weight-medium"
                        )

                    with ui.row().classes("w-full items-center q-mb-md"):
                        ui.label(t("sync_videos_label")).classes("text-body2")
                        ui.space()

                        sync_dialog = ui.dialog()

                        async def open_sync_dialog():
                            _dp = get_data_provider()
                            if not _dp:
                                _dp = LocalDataProvider()
                                set_data_provider(_dp)
                            sync_dialog.clear()
                            with (
                                sync_dialog,
                                ui.card().classes("q-pa-lg").style("min-width: 360px"),
                            ):
                                ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                                progress = ui.linear_progress(value=0, show_value=False).props(
                                    "color=primary"
                                )
                                status = ui.label(t("starting"))
                            sync_dialog.open()
                            stats = await sync_with_progress(
                                _dp,
                                progress=progress,
                                status=status,
                                active_project_id=active_project_id,
                            )
                            sync_dialog.clear()
                            with (
                                sync_dialog,
                                ui.card().classes("q-pa-lg").style("min-width: 360px"),
                            ):
                                ui.icon("check_circle", size="lg").classes("text-positive q-mb-sm")
                                ui.label(t("sync_complete")).classes("text-h6 q-mb-md")
                                if stats:
                                    with ui.column().classes("w-full gap-xs q-mb-lg"):
                                        ui.label(
                                            t("sync_stat_scanned", n=stats["scanned"])
                                        ).classes("text-body2")
                                        ui.label(t("sync_stat_added", n=stats["added"])).classes(
                                            "text-body2 text-positive"
                                        )
                                        ui.label(
                                            t("sync_stat_updated", n=stats["updated"])
                                        ).classes("text-body2 text-grey-6")
                                ui.button(
                                    t("close"), on_click=sync_dialog.close, color="primary"
                                ).classes("full-width")

                        ui.button(
                            t("sync_videos_label"),
                            icon="sync",
                            color="primary",
                            on_click=open_sync_dialog,
                        )

                    with ui.row().classes("w-full items-center q-mb-md"):
                        ui.label(t("reset_database_label")).classes("text-body2")
                        ui.space()

                        reset_dialog = ui.dialog().props("persistent")

                        async def do_reset():
                            reset_dialog.close()
                            old_dp = get_data_provider()
                            if old_dp:
                                old_dp.engine.dispose()
                            if current_db_path and current_db_path.exists():
                                current_db_path.unlink()
                            reset_app_state()
                            ui.notify(t("database_reset"), type="positive")
                            ui.navigate.to("/setup")

                        with reset_dialog, ui.card().classes("q-pa-lg"):
                            ui.label(t("reset_confirm")).classes("text-h6 q-mb-sm")
                            ui.label(t("reset_warning")).classes(
                                "text-body2 text-negative q-mb-lg"
                            )
                            with ui.row().classes("w-full justify-end gap-sm"):
                                ui.button(t("cancel"), on_click=reset_dialog.close).props("flat")
                                ui.button(
                                    t("yes_reset"),
                                    icon="delete_forever",
                                    color="negative",
                                    on_click=do_reset,
                                )

                        ui.button(
                            t("reset_database_label"),
                            icon="delete_forever",
                            color="negative",
                            on_click=reset_dialog.open,
                        )


async def setup_settings():
    from review_app.app.entry_point import shared_header

    shared_header()

    dp = await get_or_create_data_provider()
    if not dp or not await run.io_bound(dp.has_videos_in_db, get_active_project_id()):
        pass

    container = ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto")
    container.clear()
    _build_settings_content(container)
