import asyncio
from pathlib import Path

from nicegui import run, ui

from review_app.app.config import get_default_db_path
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_data_provider,
    get_language,
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

        # Project Species & Behaviors Section
        if active_project_id:
            with ui.expansion(t("project_species_settings"), icon="pets").classes(
                "full-width q-mb-lg"
            ):
                with ui.column().classes("w-full gap-md q-pa-md"):
                    ui.label(t("project_species_desc")).classes("text-caption text-grey-6")

                    _dp = get_data_provider() or LocalDataProvider()
                    lang = get_language()
                    all_species_map = _dp.get_species_display_map(lang=lang)
                    project_species = _dp.get_project_species(active_project_id)
                    behavior_display_map = _dp.get_behavior_display_map(lang=lang)

                    # Behavior configuration container
                    with ui.column().classes("w-full gap-sm") as behaviors_container:
                        pass # Initial placeholder
                    
                    behavior_selects: dict = {}

                    def _render_behaviors():
                        behaviors_container.clear()
                        behavior_selects.clear()
                        with behaviors_container:
                            ui.label(t("configure_behaviors")).classes("text-subtitle2 q-mt-md")
                            selected_species = species_select.value or []
                            for spec in selected_species:
                                current_behaviors = _dp.get_project_species_behaviors(
                                    active_project_id, spec
                                )
                                spec_display = all_species_map.get(spec, spec)
                                with ui.row().classes("w-full items-center gap-sm no-wrap"):
                                    ui.label(spec_display).classes("text-caption").style("width: 150px")
                                    # Behavior select for project-specific configuration
                                    behavior_select = ui.select(
                                        options=behavior_display_map,
                                        value=current_behaviors,
                                        multiple=True,
                                        label=t("species_behaviors", species=spec_display),
                                    ).props("outlined dense use-chips").classes("col")
                                    behavior_selects[spec] = behavior_select

                    # Species Selection
                    with ui.row().classes("w-full items-center gap-sm"):
                        species_select = ui.select(
                            options=all_species_map,
                            value=project_species,
                            label=t("enable_species"),
                            multiple=True,
                            with_input=True,
                        ).props("outlined use-chips full-width").classes("col").on("update:model-value", _render_behaviors)

                        ui.button(
                            t("select_all"),
                            on_click=lambda: [species_select.set_value(list(all_species_map.keys())), _render_behaviors()],
                        ).props("flat dense")
                        ui.button(
                            t("deselect_all"),
                            on_click=lambda: [species_select.set_value([]), _render_behaviors()],
                        ).props("flat dense")

                    _render_behaviors()

                    async def _apply_all():
                        # Save species
                        _dp.set_project_species(active_project_id, species_select.value)
                        # Save behaviors for each selected species
                        for spec, select_el in behavior_selects.items():
                            keys = select_el.value
                            _dp.set_project_species_behaviors(active_project_id, spec, keys)
                        ui.notify(t("settings_saved"), type="positive")

                    with ui.row().classes("w-full justify-end q-mt-sm"):
                        ui.button(t("apply_changes"), on_click=_apply_all).props("unelevated color=primary")

                    # Add Buttons
                    async def _add_custom_species_dialog():
                        dialog = ui.dialog()
                        groups = _dp.get_existing_groups()
                        iucn_options = _dp.get_existing_iucn()
                        
                        with dialog, ui.card().classes("q-pa-lg").style("min-width: 400px"):
                            ui.label(t("add_custom_species_title")).classes("text-h6 q-mb-md")
                            sci = ui.input(t("scientific_name")).props("outlined dense full-width")
                            n_en = ui.input(t("name_en_label")).props("outlined dense full-width")
                            n_fr = ui.input(t("name_fr_label")).props("outlined dense full-width")
                            g_en = ui.select(options=groups["en"], label=t("group_en_label"), with_input=True, new_value_mode='add').props("outlined dense full-width")
                            g_fr = ui.select(options=groups["fr"], label=t("group_fr_label"), with_input=True, new_value_mode='add').props("outlined dense full-width")
                            iucn = ui.select(options=iucn_options, label=t("iucn_label"), with_input=True, new_value_mode='add').props("outlined dense full-width")

                            async def _do_add():
                                if not all([sci.value, n_en.value, n_fr.value, g_en.value, g_fr.value]):
                                    ui.notify(t("all_fields_required"), type="warning")
                                    return
                                success = await run.io_bound(
                                    _dp.add_custom_species,
                                    sci.value, n_en.value, n_fr.value, g_en.value, g_fr.value, iucn.value or None,
                                )
                                if not success:
                                    ui.notify(t("species_exists"), type="warning")
                                    return
                                    
                                ui.notify(t("species_added"), type="positive")
                                dialog.close()
                                
                                # Refresh data from the DP
                                nonlocal all_species_map, species_select
                                all_species_map = _dp.get_species_display_map(lang=lang)
                                
                                # Update select options and re-render
                                species_select.options = all_species_map
                                species_select.update()
                                
                                # Explicitly re-render behaviors to include new species if needed
                                _render_behaviors()

                            with ui.row().classes("w-full justify-end q-mt-md"):
                                ui.button(t("cancel"), on_click=dialog.close).props("flat")
                                ui.button(t("add_species_btn"), on_click=_do_add).props("unelevated")
                        dialog.open()

                    async def _add_custom_behavior_dialog():
                        dialog = ui.dialog()
                        with dialog, ui.card().classes("q-pa-lg").style("min-width: 400px"):
                            ui.label(t("add_custom_behavior_title")).classes("text-h6 q-mb-md")
                            key = ui.input(t("behavior_key")).props("outlined dense full-width")
                            n_en = ui.input(t("name_en_label")).props("outlined dense full-width")
                            n_fr = ui.input(t("name_fr_label")).props("outlined dense full-width")

                            async def _do_add():
                                if not all([key.value, n_en.value]):
                                    ui.notify(t("all_fields_required"), type="warning")
                                    return
                                success = await run.io_bound(
                                    _dp.add_custom_behavior,
                                    key.value, n_en.value, n_fr.value or None,
                                )
                                if not success:
                                    ui.notify(t("behavior_exists"), type="warning")
                                    return
                                ui.notify(t("behavior_added"), type="positive")
                                dialog.close()
                                nonlocal behavior_display_map
                                behavior_display_map = _dp.get_behavior_display_map(lang=lang)
                                _render_behaviors()
                            
                            with ui.row().classes("w-full justify-end q-mt-md"):
                                ui.button(t("cancel"), on_click=dialog.close).props("flat")
                                ui.button(t("add_species_btn"), on_click=_do_add).props("unelevated")
                        dialog.open()

                    with ui.row().classes("q-mt-sm"):
                        ui.button(t("add_custom_species_title"), icon="add", on_click=_add_custom_species_dialog).props("flat dense")
                        ui.button(t("add_custom_behavior_title"), icon="add", on_click=_add_custom_behavior_dialog).props("flat dense")

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
