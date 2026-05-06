import asyncio
import uuid
from pathlib import Path

from nicegui import run, ui

from review_app.app.config import get_default_db_path, get_user_data_dir
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_data_provider,
    get_language,
    get_species_threshold,
    load_settings_from_db,
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
    user_error_message,
)
from review_app.backend.db.backup import (
    BackupError,
    create_backup,
    get_backup_dir,
    list_backups,
    restore_backup,
)
from review_app.backend.provider.local_data_provider import LocalDataProvider


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

        # Advanced Section
        with ui.expansion(t("advanced_settings"), icon="settings").classes("full-width q-mb-lg"):
            with ui.column().classes("w-full gap-lg q-pa-md"):
                # Project Species & Behaviors
                if active_project_id:
                    with ui.expansion(t("project_species_settings"), icon="pets").classes(
                        "full-width"
                    ):
                        ui.label(t("project_species_desc")).classes(
                            "text-caption text-grey-6 q-mb-md"
                        )

                        _dp = get_data_provider() or LocalDataProvider()
                        lang = get_language()
                        all_species_map = _dp.get_species_display_map(lang=lang)
                        project_species = _dp.get_project_species(active_project_id)
                        behavior_display_map = _dp.get_behavior_display_map(lang=lang)

                        with (
                            ui.column()
                            .classes("w-full gap-sm")
                            .style("max-height: 400px; overflow-y: auto") as behaviors_container
                        ):
                            pass

                        def _render_behaviors():
                            behaviors_container.clear()
                            with behaviors_container:
                                ui.label(t("configure_behaviors")).classes(
                                    "text-subtitle2 q-mt-md"
                                )
                                for spec in sorted(species_select.value or []):
                                    current_behaviors = _dp.get_project_species_behaviors(
                                        active_project_id, spec
                                    )
                                    spec_display = all_species_map.get(spec, spec)
                                    with ui.row().classes("w-full items-center gap-sm no-wrap"):
                                        ui.label(spec_display).classes("text-caption").style(
                                            "width: 150px"
                                        )

                                        def _make_behavior_save(s):
                                            def _save(ev):
                                                _dp.set_project_species_behaviors(
                                                    active_project_id, s, ev.value or []
                                                )

                                            return _save

                                        ui.select(
                                            options=behavior_display_map,
                                            value=current_behaviors,
                                            multiple=True,
                                            label=t("species_behaviors", species=spec_display),
                                        ).props("outlined dense use-chips").classes(
                                            "col"
                                        ).on_value_change(_make_behavior_save(spec))

                        def _save_and_render(ev):
                            _dp.set_project_species(active_project_id, ev.value or [])
                            _render_behaviors()

                        with ui.row().classes("w-full items-center gap-sm"):
                            species_select = (
                                ui.select(
                                    options=all_species_map,
                                    value=project_species,
                                    label=t("enable_species"),
                                    multiple=True,
                                    with_input=True,
                                )
                                .props("outlined use-chips full-width")
                                .classes("col")
                                .on_value_change(_save_and_render)
                            )
                            ui.button(
                                t("select_all"),
                                on_click=lambda: species_select.set_value(
                                    list(all_species_map.keys())
                                ),
                            ).props("flat dense")
                            ui.button(
                                t("deselect_all"),
                                on_click=lambda: species_select.set_value([]),
                            ).props("flat dense")

                        _render_behaviors()

                        async def _add_custom_species_dialog():
                            dialog = ui.dialog()
                            groups = _dp.get_existing_groups()
                            iucn_options = _dp.get_existing_iucn()
                            existing_sci_names = list(all_species_map.keys())
                            existing_sci_names_lower = {n.lower() for n in existing_sci_names}
                            existing_sp_en = list(_dp.get_species_display_map(lang="en").values())
                            existing_sp_fr = list(_dp.get_species_display_map(lang="fr").values())

                            def _fuzzy_matches(val, candidates, threshold=50):
                                from thefuzz import process

                                if not val or not candidates:
                                    return []
                                return [
                                    m
                                    for m, score in process.extract(val, candidates, limit=4)
                                    if score >= threshold
                                ]

                            with (
                                dialog,
                                ui.card()
                                .classes("q-pa-lg")
                                .style(
                                    "width: 480px; max-height: 90vh; display: flex; flex-direction: column;"
                                ),
                            ):
                                ui.label(t("add_custom_species_title")).classes("text-h6 q-mb-md")
                                with (
                                    ui.column()
                                    .classes("w-full gap-xs")
                                    .style("overflow-y: auto; flex: 1;")
                                ):
                                    sci = (
                                        ui.input(t("scientific_name"))
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    sci_hint = (
                                        ui.label("")
                                        .classes("text-caption q-mb-xs")
                                        .style(
                                            "min-height: 1em; white-space: nowrap; overflow-x: auto;"
                                        )
                                    )

                                    def _update_sci_hint(e):
                                        val = (e.value or "").strip()
                                        if not val:
                                            sci_hint.text = ""
                                            return
                                        if val.lower() in existing_sci_names_lower:
                                            sci_hint.text = t("species_exists")
                                            sci_hint.style(
                                                "color: var(--q-negative); white-space: nowrap; overflow-x: auto;"
                                            )
                                        else:
                                            matches = _fuzzy_matches(val, existing_sci_names)
                                            sci_hint.text = (
                                                ("Similar: " + " · ".join(matches))
                                                if matches
                                                else ""
                                            )
                                            sci_hint.style(
                                                "color: var(--q-on-surface, inherit); white-space: nowrap; overflow-x: auto;"
                                            )

                                    sci.on_value_change(_update_sci_hint)
                                    n_en = (
                                        ui.input(t("name_en_label"))
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    n_en_hint = (
                                        ui.label("")
                                        .classes("text-caption text-grey-6 q-mb-xs")
                                        .style(
                                            "min-height: 1em; white-space: nowrap; overflow-x: auto;"
                                        )
                                    )

                                    def _update_n_en_hint(e):
                                        matches = _fuzzy_matches(
                                            (e.value or "").strip(), existing_sp_en
                                        )
                                        n_en_hint.text = (
                                            ("Similar: " + " · ".join(matches)) if matches else ""
                                        )

                                    n_en.on_value_change(_update_n_en_hint)
                                    n_fr = (
                                        ui.input(t("name_fr_label"))
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    n_fr_hint = (
                                        ui.label("")
                                        .classes("text-caption text-grey-6 q-mb-xs")
                                        .style(
                                            "min-height: 1em; white-space: nowrap; overflow-x: auto;"
                                        )
                                    )

                                    def _update_n_fr_hint(e):
                                        matches = _fuzzy_matches(
                                            (e.value or "").strip(), existing_sp_fr
                                        )
                                        n_fr_hint.text = (
                                            ("Similar: " + " · ".join(matches)) if matches else ""
                                        )

                                    n_fr.on_value_change(_update_n_fr_hint)
                                    g_en = (
                                        ui.select(
                                            options=groups["en"],
                                            label=t("group_en_label"),
                                            with_input=True,
                                            new_value_mode="add",
                                        )
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    g_fr = (
                                        ui.select(
                                            options=groups["fr"],
                                            label=t("group_fr_label"),
                                            with_input=True,
                                            new_value_mode="add",
                                        )
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    iucn = (
                                        ui.select(
                                            options=iucn_options,
                                            label=t("iucn_label"),
                                            with_input=True,
                                            new_value_mode="add",
                                        )
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )

                                async def _do_add_species():
                                    if not all(
                                        [sci.value, n_en.value, n_fr.value, g_en.value, g_fr.value]
                                    ):
                                        ui.notify(t("all_fields_required"), type="warning")
                                        return
                                    success = await run.io_bound(
                                        _dp.add_custom_species,
                                        sci.value,
                                        n_en.value,
                                        n_fr.value,
                                        g_en.value,
                                        g_fr.value,
                                        iucn.value or None,
                                    )
                                    if not success:
                                        ui.notify(t("species_exists"), type="warning")
                                        return
                                    ui.notify(t("species_added"), type="positive")
                                    dialog.close()
                                    nonlocal all_species_map
                                    all_species_map = _dp.get_species_display_map(lang=lang)
                                    species_select.options = all_species_map
                                    species_select.update()
                                    _render_behaviors()

                                with ui.row().classes("w-full justify-end q-mt-md"):
                                    ui.button(t("cancel"), on_click=dialog.close).props("flat")
                                    ui.button(
                                        t("add_species_btn"), on_click=_do_add_species
                                    ).props("unelevated")
                            dialog.open()

                        async def _add_custom_behavior_dialog():
                            dialog = ui.dialog()
                            existing_behavior_keys = [b["key"] for b in _dp.get_all_behaviors()]
                            existing_behavior_keys_lower = {
                                k.lower() for k in existing_behavior_keys
                            }
                            existing_beh_en = list(
                                _dp.get_behavior_display_map(lang="en").values()
                            )
                            existing_beh_fr = list(
                                _dp.get_behavior_display_map(lang="fr").values()
                            )

                            def _fuzzy_matches(val, candidates, threshold=50):
                                from thefuzz import process

                                if not val or not candidates:
                                    return []
                                return [
                                    m
                                    for m, score in process.extract(val, candidates, limit=4)
                                    if score >= threshold
                                ]

                            with (
                                dialog,
                                ui.card()
                                .classes("q-pa-lg")
                                .style(
                                    "width: 480px; max-height: 90vh; display: flex; flex-direction: column;"
                                ),
                            ):
                                ui.label(t("add_custom_behavior_title")).classes("text-h6 q-mb-md")
                                with (
                                    ui.column()
                                    .classes("w-full gap-xs")
                                    .style("overflow-y: auto; flex: 1;")
                                ):
                                    key = (
                                        ui.input(t("behavior_key"))
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    key_hint = (
                                        ui.label("")
                                        .classes("text-caption q-mb-xs")
                                        .style(
                                            "min-height: 1em; white-space: nowrap; overflow-x: auto;"
                                        )
                                    )

                                    def _update_key_hint(e):
                                        val = (e.value or "").strip()
                                        if not val:
                                            key_hint.text = ""
                                            return
                                        if val.lower() in existing_behavior_keys_lower:
                                            key_hint.text = t("behavior_exists")
                                            key_hint.style(
                                                "color: var(--q-negative); white-space: nowrap; overflow-x: auto;"
                                            )
                                        else:
                                            matches = _fuzzy_matches(val, existing_behavior_keys)
                                            key_hint.text = (
                                                ("Similar: " + " · ".join(matches))
                                                if matches
                                                else ""
                                            )
                                            key_hint.style(
                                                "color: var(--q-on-surface, inherit); white-space: nowrap; overflow-x: auto;"
                                            )

                                    key.on_value_change(_update_key_hint)
                                    n_en = (
                                        ui.input(t("name_en_label"))
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    n_en_hint = (
                                        ui.label("")
                                        .classes("text-caption text-grey-6 q-mb-xs")
                                        .style(
                                            "min-height: 1em; white-space: nowrap; overflow-x: auto;"
                                        )
                                    )

                                    def _update_beh_en_hint(e):
                                        matches = _fuzzy_matches(
                                            (e.value or "").strip(), existing_beh_en
                                        )
                                        n_en_hint.text = (
                                            ("Similar: " + " · ".join(matches)) if matches else ""
                                        )

                                    n_en.on_value_change(_update_beh_en_hint)
                                    n_fr = (
                                        ui.input(t("name_fr_label"))
                                        .props("outlined dense")
                                        .classes("w-full")
                                    )
                                    n_fr_hint = (
                                        ui.label("")
                                        .classes("text-caption text-grey-6 q-mb-xs")
                                        .style(
                                            "min-height: 1em; white-space: nowrap; overflow-x: auto;"
                                        )
                                    )

                                    def _update_beh_fr_hint(e):
                                        matches = _fuzzy_matches(
                                            (e.value or "").strip(), existing_beh_fr
                                        )
                                        n_fr_hint.text = (
                                            ("Similar: " + " · ".join(matches)) if matches else ""
                                        )

                                    n_fr.on_value_change(_update_beh_fr_hint)

                                async def _do_add_behavior():
                                    if not all([key.value, n_en.value]):
                                        ui.notify(t("all_fields_required"), type="warning")
                                        return
                                    success = await run.io_bound(
                                        _dp.add_custom_behavior,
                                        key.value,
                                        n_en.value,
                                        n_fr.value or None,
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
                                    ui.button(
                                        t("add_behavior_btn"), on_click=_do_add_behavior
                                    ).props("unelevated")
                            dialog.open()

                        async def _handle_species_upload(e):
                            content = (await e.file.read()).decode("utf-8", errors="replace")
                            try:
                                count = await run.io_bound(
                                    _dp.import_project_species_from_csv, active_project_id, content
                                )
                                ui.notify(
                                    t("species_import_success", count=count), type="positive"
                                )
                                nonlocal all_species_map
                                all_species_map = _dp.get_species_display_map(lang=lang)
                                species_select.options = all_species_map
                                species_select.update()
                                species_select.set_value(
                                    _dp.get_project_species(active_project_id)
                                )
                                _render_behaviors()
                            except Exception as exc:
                                ui.notify(
                                    t("csv_import_error", error=user_error_message(exc)),
                                    type="negative",
                                )

                        async def _handle_behaviors_upload(e):
                            content = (await e.file.read()).decode("utf-8", errors="replace")
                            try:
                                count = await run.io_bound(
                                    _dp.import_project_behaviors_from_csv,
                                    active_project_id,
                                    content,
                                )
                                ui.notify(
                                    t("behaviors_import_success", count=count), type="positive"
                                )
                                nonlocal behavior_display_map
                                behavior_display_map = _dp.get_behavior_display_map(lang=lang)
                                _render_behaviors()
                            except Exception as exc:
                                ui.notify(
                                    t("csv_import_error", error=user_error_message(exc)),
                                    type="negative",
                                )

                        # Hidden uploaders — triggered programmatically by buttons below.
                        species_uploader = (
                            ui.upload(on_upload=_handle_species_upload, auto_upload=True)
                            .props("accept=.csv")
                            .style("display: none")
                        )
                        behaviors_uploader = (
                            ui.upload(on_upload=_handle_behaviors_upload, auto_upload=True)
                            .props("accept=.csv")
                            .style("display: none")
                        )

                        with ui.row().classes("items-center gap-xs"):
                            ui.label(t("add_custom_label")).classes("text-caption text-grey-6")
                            ui.button(
                                t("add_custom_species_title"),
                                icon="add",
                                on_click=_add_custom_species_dialog,
                            ).props("flat dense")
                            ui.button(
                                t("add_custom_behavior_title"),
                                icon="add",
                                on_click=_add_custom_behavior_dialog,
                            ).props("flat dense")
                        ui.separator().classes("q-mt-md")
                        with ui.row().classes("items-center gap-xs"):
                            ui.label(t("import_csv_label")).classes("text-caption text-grey-6")
                            ui.button(
                                t("upload_species_csv"),
                                icon="upload_file",
                                on_click=lambda: ui.run_javascript(
                                    f"document.getElementById('c{species_uploader.id}').querySelector('.q-uploader__input').click()"
                                ),
                            ).props("flat dense")
                            ui.button(
                                t("upload_behaviors_csv"),
                                icon="upload_file",
                                on_click=lambda: ui.run_javascript(
                                    f"document.getElementById('c{behaviors_uploader.id}').querySelector('.q-uploader__input').click()"
                                ),
                            ).props("flat dense")

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
                    with ui.row().classes("w-full items-center q-mb-md"):
                        ui.label(t("backup_download_label")).classes("text-body2")
                        ui.space()

                        async def do_backup_download():
                            _dp = get_data_provider()
                            if not _dp:
                                _dp = LocalDataProvider()
                            try:
                                backup_path = await run.io_bound(
                                    create_backup, _dp.engine, reason="manual"
                                )
                            except BackupError as exc:
                                ui.notify(
                                    t("backup_failed", error=t(exc.user_message_key)),
                                    type="negative",
                                )
                                return
                            ui.download(backup_path, filename=backup_path.name)
                            ui.notify(t("backup_created"), type="positive")

                        ui.button(
                            t("backup_download_btn"),
                            icon="download",
                            color="primary",
                            on_click=do_backup_download,
                        )

                    with ui.row().classes("w-full items-center q-mb-md"):
                        ui.label(t("restore_backup_label")).classes("text-body2")
                        ui.space()

                        restore_dialog = ui.dialog().props("persistent")

                        async def do_restore(selected_backup_path):
                            restore_dialog.close()
                            _dp = get_data_provider() or LocalDataProvider()
                            try:
                                await run.io_bound(
                                    restore_backup, selected_backup_path, _dp.engine
                                )
                            except BackupError as exc:
                                ui.notify(
                                    t("restore_failed", error=t(exc.user_message_key)),
                                    type="negative",
                                )
                                return
                            except Exception as exc:
                                ui.notify(
                                    t("restore_failed", error=user_error_message(exc)),
                                    type="negative",
                                )
                                return
                            reset_app_state()
                            new_dp = LocalDataProvider()
                            set_data_provider(new_dp)
                            load_settings_from_db(new_dp)
                            ui.notify(t("restore_success"), type="positive")
                            await asyncio.sleep(0.5)
                            ui.navigate.to("/overview")

                        async def open_restore_dialog():
                            backups = list_backups()
                            restore_dialog.clear()
                            with (
                                restore_dialog,
                                ui.card().classes("q-pa-lg").style("min-width: 420px"),
                            ):
                                if not backups:
                                    ui.label(t("no_backups")).classes("text-body2 text-grey-6")
                                else:
                                    ui.label(t("restore_confirm")).classes(
                                        "text-subtitle1 q-mb-md"
                                    )
                                    with (
                                        ui.column()
                                        .classes("w-full gap-xs q-mb-lg")
                                        .style("max-height: 300px; overflow-y: auto")
                                    ):
                                        for b in backups:
                                            ts = b["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                                            label = f"{ts}  ({b['size_mb']} MB)"

                                            def _make_restore(p):
                                                async def _do():
                                                    await do_restore(p)

                                                return _do

                                            ui.button(
                                                label,
                                                icon="restore",
                                                on_click=_make_restore(b["path"]),
                                            ).props("flat dense align-left").classes("w-full")
                                with ui.row().classes("w-full gap-sm items-center q-mt-md"):
                                    ui.separator().classes("col")
                                    ui.label(t("upload_backup")).classes(
                                        "text-caption text-grey-6"
                                    )
                                    ui.separator().classes("col")

                                async def _handle_backup_upload(e):
                                    content = await e.file.read()
                                    tmp_path = (
                                        get_backup_dir()
                                        / f"uploaded_restore_{uuid.uuid4().hex}.db"
                                    )
                                    tmp_path.write_bytes(content)
                                    try:
                                        await do_restore(tmp_path)
                                    finally:
                                        tmp_path.unlink(missing_ok=True)

                                backup_uploader = (
                                    ui.upload(on_upload=_handle_backup_upload, auto_upload=True)
                                    .props("accept=.db")
                                    .style("display: none")
                                )
                                ui.button(
                                    t("upload_backup_btn"),
                                    icon="upload_file",
                                    on_click=lambda: ui.run_javascript(
                                        f"document.getElementById('c{backup_uploader.id}').querySelector('.q-uploader__input').click()"
                                    ),
                                ).props(
                                    f"flat dense align-left {'color=primary' if not backups else ''}"
                                ).classes("w-full")

                                with ui.row().classes("w-full justify-end"):
                                    ui.button(t("cancel"), on_click=restore_dialog.close).props(
                                        "flat"
                                    )
                            restore_dialog.open()

                        ui.button(
                            t("restore_backup_btn"),
                            icon="restore",
                            on_click=open_restore_dialog,
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
                            old_dp = get_data_provider() or LocalDataProvider()
                            if current_db_path and current_db_path.exists():
                                try:
                                    create_backup(old_dp.engine, reason="reset_database")
                                except BackupError as exc:
                                    ui.notify(
                                        t("backup_failed_proceed", error=t(exc.user_message_key)),
                                        type="warning",
                                    )
                                old_dp.engine.dispose()
                                current_db_path.unlink()
                            else:
                                old_dp.engine.dispose()
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

    dp = await get_or_create_data_provider()
    if not dp or not await run.io_bound(dp.has_videos_in_db, get_active_project_id()):
        pass

    container = ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto")
    container.clear()
    _build_settings_content(container)
