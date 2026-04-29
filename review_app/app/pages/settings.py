import asyncio
from pathlib import Path

from nicegui import run, ui

from review_app.app.config import (
    get_bundled_behaviors_csv,
    get_bundled_species_csv,
    get_config_path,
    load_config,
    update_config_key,
)
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_data_provider,
    get_species_threshold,
    init_user_prefs,
    set_active_project,
    set_current_idx,
    set_data_provider,
    set_queue,
    set_selections,
)
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    render_uninitialized_state,
    sync_with_progress,
)
from review_app.backend.local_data_provider import LocalDataProvider

CONFIG_PATH = get_config_path()


def _build_settings_content(container: ui.column):
    config = load_config()
    current_db_dir = config.get("db_dir", "")
    current_db_file = config.get("db_filename", "review_data.db")
    from review_app.app.config import get_default_db_path

    current_db_path = (
        Path(current_db_dir) / current_db_file if current_db_dir else get_default_db_path()
    )

    bundled_species = get_bundled_species_csv()
    bundled_behaviors = get_bundled_behaviors_csv()

    species_csv_val = config.get("species_csv_path") or bundled_species or ""
    behaviors_csv_val = config.get("species_behaviors_csv_path") or bundled_behaviors or ""

    initial_annotator = get_annotator_name()
    initial_blank_threshold = get_blank_threshold()
    initial_species_threshold = get_species_threshold()

    active_project_id = get_active_project_id()
    current_project_name = ""

    stats = {"videos": 0}
    current_video_dirs: list = []
    try:
        _dp_stats = LocalDataProvider(str(CONFIG_PATH))
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
        new_dp = LocalDataProvider(str(CONFIG_PATH))
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
                        _dp = get_data_provider() or LocalDataProvider(str(CONFIG_PATH))
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
                    update_config_key("annotator_name", name)
                    init_user_prefs(
                        dark_mode=config.get("dark_mode", True),
                        language=config.get("language", "en"),
                        annotator_name=name,
                        blank_threshold=get_blank_threshold(),
                        species_threshold=get_species_threshold(),
                    )
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
                    _dp = get_data_provider() or LocalDataProvider(str(CONFIG_PATH))
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
                        ui.icon("storage", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("database_file")).classes("text-subtitle1 font-weight-medium")
                    ui.label(t("database_file_desc")).classes("text-caption text-grey-6 q-mb-md")
                    with ui.row().classes("w-full items-center gap-sm"):
                        db_path_input = (
                            ui.input(
                                placeholder=t("database_file_placeholder"),
                                value=str(current_db_path) if current_db_path else "",
                            )
                            .props("outlined dense")
                            .classes("col")
                        )

                        async def save_db_path():
                            new_db_path = db_path_input.value.strip()
                            if not new_db_path:
                                ui.notify(t("database_path_required"), type="warning")
                                return
                            db_path_changed = (
                                new_db_path != str(current_db_path) if current_db_path else True
                            )
                            if db_path_changed and Path(new_db_path).exists():
                                confirmed = await _confirm_existing_db(new_db_path)
                                if confirmed is None:
                                    return
                                if confirmed is False:
                                    old_dp = get_data_provider()
                                    if old_dp:
                                        old_dp.engine.dispose()
                                    Path(new_db_path).unlink()
                            update_config_key("db_dir", str(Path(new_db_path).parent))
                            update_config_key("db_filename", Path(new_db_path).name)
                            _reinit_dp()
                            ui.notify(t("settings_saved"), type="positive")

                        ui.button(
                            t("save"), icon="check", color="primary", on_click=save_db_path
                        ).props("dense")

                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-sm"):
                        ui.icon("table_chart", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("species_csv")).classes("text-subtitle1 font-weight-medium")
                    ui.label(t("species_csv_desc")).classes("text-caption text-grey-6 q-mb-md")

                    species_csv_input = ui.input(
                        label=t("custom_species_csv"),
                        value=species_csv_val,
                    ).props("outlined dense class=w-full")

                    with ui.row().classes("w-full items-center mt-2"):
                        ui.label(t("csv_mode_label") + ":").classes(
                            "text-caption text-grey-6 q-mr-md"
                        )
                        species_csv_mode_radio = ui.radio(
                            options={"override": t("mode_override"), "append": t("mode_append")},
                            value="override",
                        ).props("inline dense")

                    if bundled_species:
                        with ui.row().classes("w-full items-center mt-1 justify-end"):
                            ui.label(t("mode_bundled") + ":").classes(
                                "text-caption text-grey-6 q-mr-sm"
                            )
                            ui.button(
                                Path(bundled_species).name,
                                on_click=lambda: species_csv_input.set_value(bundled_species),
                            ).props("flat dense color=primary").classes(
                                "text-capitalize text-caption"
                            )

                    with ui.row().classes("w-full justify-end q-mt-sm"):

                        async def save_species_csv():
                            csv_path = species_csv_input.value.strip()
                            if not csv_path:
                                ui.notify(t("custom_species_required"), type="warning")
                                return
                            if not Path(csv_path).exists():
                                ui.notify(t("custom_species_not_exist"), type="negative")
                                return
                            update_config_key("species_csv_path", csv_path)
                            update_config_key("species_csv_mode", species_csv_mode_radio.value)
                            _reinit_dp()
                            ui.notify(t("settings_saved"), type="positive")

                        ui.button(
                            t("save"), icon="check", color="primary", on_click=save_species_csv
                        ).props("dense")

                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-sm"):
                        ui.icon("list", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("behaviors_csv")).classes("text-subtitle1 font-weight-medium")
                    ui.label(t("behaviors_csv_desc")).classes("text-caption text-grey-6 q-mb-md")

                    behaviors_csv_input = ui.input(
                        label=t("custom_behaviors_csv"),
                        value=behaviors_csv_val,
                    ).props("outlined dense class=w-full")

                    with ui.row().classes("w-full items-center mt-2"):
                        ui.label(t("csv_mode_label") + ":").classes(
                            "text-caption text-grey-6 q-mr-md"
                        )
                        behaviors_csv_mode_radio = ui.radio(
                            options={"override": t("mode_override"), "append": t("mode_append")},
                            value="override",
                        ).props("inline dense")

                    if bundled_behaviors:
                        with ui.row().classes("w-full items-center mt-1 justify-end"):
                            ui.label(t("mode_bundled") + ":").classes(
                                "text-caption text-grey-6 q-mr-sm"
                            )
                            ui.button(
                                Path(bundled_behaviors).name,
                                on_click=lambda: behaviors_csv_input.set_value(bundled_behaviors),
                            ).props("flat dense color=primary").classes(
                                "text-capitalize text-caption"
                            )

                    with ui.row().classes("w-full justify-end q-mt-sm"):

                        async def save_behaviors_csv():
                            csv_path = behaviors_csv_input.value.strip()
                            if csv_path and not Path(csv_path).exists():
                                ui.notify(t("custom_behaviors_not_exist"), type="negative")
                                return
                            update_config_key("species_behaviors_csv_path", csv_path)
                            update_config_key(
                                "species_behaviors_csv_mode", behaviors_csv_mode_radio.value
                            )
                            _reinit_dp()
                            ui.notify(t("settings_saved"), type="positive")

                        ui.button(
                            t("save"), icon="check", color="primary", on_click=save_behaviors_csv
                        ).props("dense")

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
                            update_config_key("blank_threshold", blank_threshold_slider.value)
                            update_config_key("species_threshold", species_threshold_slider.value)
                            init_user_prefs(
                                dark_mode=config.get("dark_mode", True),
                                language=config.get("language", "en"),
                                annotator_name=get_annotator_name(),
                                blank_threshold=blank_threshold_slider.value,
                                species_threshold=species_threshold_slider.value,
                            )
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
                                _dp = LocalDataProvider(str(CONFIG_PATH))
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
                            _reinit_dp()
                            ui.notify(t("database_reset"), type="positive")
                            ui.navigate.to("/")

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
