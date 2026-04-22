import asyncio
from pathlib import Path

from nicegui import ui

from review_app.app.config import (
    get_bundled_behaviors_csv,
    get_bundled_species_csv,
    get_config_path,
    load_config,
    save_config,
)
from review_app.app.state import (
    get_annotator_name,
    get_data_provider,
    init_user_prefs,
    set_current_idx,
    set_data_provider,
    set_queue,
    set_selections,
)
from review_app.app.translations import t
from review_app.app.utils import sync_with_progress
from review_app.backend.local_data_provider import LocalDataProvider

CONFIG_PATH = get_config_path()


def _build_settings_content(container: ui.column):
    config = load_config()
    current_video_dir = config.get("video_dir", "")
    current_db_dir = config.get("db_dir", "")
    current_db_file = config.get("db_filename", "review_data.db")
    from review_app.app.config import get_default_db_path

    current_db_path = (
        Path(current_db_dir) / current_db_file if current_db_dir else get_default_db_path()
    )

    bundled_species = get_bundled_species_csv()
    bundled_behaviors = get_bundled_behaviors_csv()

    # Defaults to bundled if not specified in config
    species_csv_val = config.get("species_csv_path") or bundled_species or ""
    behaviors_csv_val = config.get("species_behaviors_csv_path") or bundled_behaviors or ""
    initial_annotator = get_annotator_name()

    inputs = {}

    dp = None
    stats = {"videos": 0}
    try:
        dp = LocalDataProvider(str(CONFIG_PATH))
        stats = {
            "videos": dp.get_overview_stats().get("videos", {}).get("total", 0),
        }
    except Exception:
        pass

    def check_changes():
        changed = (
            inputs["video_dir"].value.strip() != current_video_dir
            or inputs["db_path"].value.strip() != str(current_db_path)
            or inputs["species_csv"].value.strip() != species_csv_val
            or inputs["behaviors_csv"].value.strip() != behaviors_csv_val
            or inputs["annotator"].value.strip() != initial_annotator
        )
        apply_btn.set_enabled(changed)

    with container:
        with ui.row().classes("items-center q-mb-lg"):
            ui.label(t("settings_title")).classes("text-h5 font-weight-bold")

        with ui.card().classes("full-width q-mb-lg"):
            ui.label(t("current_status")).classes("text-subtitle1 font-weight-medium q-mb-md")
            with ui.row().classes("w-full gap-md"):
                with ui.card().classes("col text-center"):
                    ui.label(str(stats["videos"])).classes("text-h5 font-weight-bold")
                    ui.label(t("videos_in_db")).classes("text-caption text-grey-6")
                with ui.card().classes("col text-center"):
                    ui.label(
                        Path(current_video_dir).name if current_video_dir else t("not_available")
                    ).classes("text-h6")
                    ui.label(t("video_dir_label")).classes("text-caption text-grey-6")
                with ui.card().classes("col text-center"):
                    ui.label(
                        str(current_db_path) if current_db_path else t("not_available")
                    ).classes("text-body2")
                    ui.label(t("database")).classes("text-caption text-grey-6")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("person", size="sm").classes("text-primary q-mr-sm")
                ui.label(t("annotator_label")).classes("text-subtitle1 font-weight-medium")
            inputs["annotator"] = ui.input(t("annotator_name"), value=initial_annotator).props(
                "outlined dense class=full-width"
            )
            inputs["annotator"].on_value_change(check_changes)

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("folder_open", size="sm").classes("text-primary q-mr-sm")
                ui.label(t("video_dir_label")).classes("text-subtitle1 font-weight-medium")
            ui.label(t("video_dir_desc")).classes("text-caption text-grey-6 q-mb-md")
            inputs["video_dir"] = ui.input(
                placeholder=t("video_dir_placeholder"),
                value=current_video_dir,
            ).props("outlined dense class=w-full")
            inputs["video_dir"].on_value_change(check_changes)

        # Advanced Section
        with ui.expansion(t("advanced_settings"), icon="settings").classes("full-width q-mb-lg"):
            with ui.column().classes("w-full gap-lg q-pa-md"):
                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-sm"):
                        ui.icon("storage", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("database_file")).classes("text-subtitle1 font-weight-medium")
                    ui.label(t("database_file_desc")).classes("text-caption text-grey-6 q-mb-md")
                    inputs["db_path"] = ui.input(
                        placeholder=t("database_file_placeholder"),
                        value=str(current_db_path) if current_db_path else "",
                    ).props("outlined dense class=w-full")
                    inputs["db_path"].on_value_change(check_changes)

                # Species CSV Section
                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-sm"):
                        ui.icon("table_chart", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("species_csv")).classes("text-subtitle1 font-weight-medium")
                    ui.label(t("species_csv_desc")).classes("text-caption text-grey-6 q-mb-md")

                    inputs["species_csv"] = ui.input(
                        label=t("custom_species_csv"),
                        value=species_csv_val,
                    ).props("outlined dense class=w-full")
                    inputs["species_csv"].on_value_change(check_changes)

                    if bundled_species:
                        with ui.row().classes("w-full items-center mt-1 justify-end"):
                            ui.label(t("mode_bundled") + ":").classes(
                                "text-caption text-grey-6 q-mr-sm"
                            )
                            ui.button(
                                Path(bundled_species).name,
                                on_click=lambda: inputs["species_csv"].set_value(bundled_species),
                            ).props("flat dense color=primary").classes(
                                "text-capitalize text-caption"
                            )

                # Behaviors CSV Section
                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-sm"):
                        ui.icon("list", size="sm").classes("text-primary q-mr-sm")
                        ui.label(t("behaviors_csv")).classes("text-subtitle1 font-weight-medium")
                    ui.label(t("behaviors_csv_desc")).classes("text-caption text-grey-6 q-mb-md")

                    inputs["behaviors_csv"] = ui.input(
                        label=t("custom_behaviors_csv"),
                        value=behaviors_csv_val,
                    ).props("outlined dense class=w-full")
                    inputs["behaviors_csv"].on_value_change(check_changes)

                    if bundled_behaviors:
                        with ui.row().classes("w-full items-center mt-1 justify-end"):
                            ui.label(t("mode_bundled") + ":").classes(
                                "text-caption text-grey-6 q-mr-sm"
                            )
                            ui.button(
                                Path(bundled_behaviors).name,
                                on_click=lambda: inputs["behaviors_csv"].set_value(
                                    bundled_behaviors
                                ),
                            ).props("flat dense color=primary").classes(
                                "text-capitalize text-caption"
                            )

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
                            dp = get_data_provider()
                            if not dp:
                                dp = LocalDataProvider(str(CONFIG_PATH))
                                set_data_provider(dp)

                            sync_container = ui.column().classes("w-full")
                            sync_dialog.clear()
                            with sync_dialog, ui.card().classes("q-pa-lg"):
                                with sync_container:
                                    ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                                    progress = ui.linear_progress(value=0, show_value=False).props(
                                        "color=primary"
                                    )
                                    status = ui.label(t("starting"))

                            sync_dialog.open()
                            await sync_with_progress(dp, progress=progress, status=status)
                            ui.notify(t("videos_synced_notify"), type="positive")
                            await asyncio.sleep(0.5)
                            sync_dialog.close()

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
                        reset_card = [None]

                        def build_confirm_step():
                            reset_dialog.clear()
                            with reset_dialog, ui.card().classes("q-pa-lg") as card:
                                reset_card[0] = card
                                ui.label(t("reset_confirm")).classes("text-h6 q-mb-sm")
                                ui.label(t("reset_warning")).classes(
                                    "text-body2 text-negative q-mb-lg"
                                )
                                with ui.row().classes("w-full justify-end gap-sm"):
                                    ui.button(t("cancel"), on_click=reset_dialog.close).props(
                                        "flat"
                                    )
                                    ui.button(
                                        t("yes_reset"),
                                        icon="delete_forever",
                                        color="negative",
                                        on_click=do_reset,
                                    )

                        async def do_reset():
                            old_dp = get_data_provider()
                            if old_dp:
                                old_dp.engine.dispose()
                            if current_db_path and current_db_path.exists():
                                current_db_path.unlink()
                            new_dp = LocalDataProvider(str(CONFIG_PATH))
                            set_data_provider(new_dp)
                            set_queue([])
                            set_current_idx(0)
                            set_selections([])

                            reset_dialog.clear()
                            with reset_dialog, ui.card().classes("q-pa-lg"):
                                ui.icon("check_circle", size="lg").classes("text-positive q-mb-sm")
                                ui.label(t("database_reset")).classes("text-h6 q-mb-sm")
                                ui.label(t("reset_success")).classes(
                                    "text-body2 text-grey-7 q-mb-lg"
                                )

                                sync_progress_col = ui.column().classes("w-full q-mb-md")
                                sync_progress_col.visible = False
                                with sync_progress_col:
                                    progress = ui.linear_progress(value=0, show_value=False).props(
                                        "color=primary"
                                    )
                                    status = ui.label(t("starting"))

                                async def start_sync():
                                    sync_progress_col.visible = True
                                    sync_btn.visible = False
                                    await sync_with_progress(
                                        new_dp, progress=progress, status=status
                                    )
                                    ui.notify(t("sync_complete"), type="positive")
                                    reset_dialog.close()
                                    ui.navigate.to("/overview")

                                sync_btn = ui.button(
                                    t("sync_now"),
                                    icon="sync",
                                    color="primary",
                                    on_click=start_sync,
                                ).classes("full-width q-mb-sm")
                                ui.button(t("do_later"), on_click=reset_dialog.close).props(
                                    "flat class=full-width"
                                )

                        ui.button(
                            t("reset_database_label"),
                            icon="delete_forever",
                            color="negative",
                            on_click=lambda: (build_confirm_step(), reset_dialog.open()),
                        )

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
                    ui.button(
                        t("keep_existing"), icon="storage", color="primary", on_click=on_keep
                    )
                    ui.button(
                        t("delete_fresh"),
                        icon="delete_forever",
                        color="negative",
                        on_click=on_delete,
                    )
            dialog.open()
            while not done[0]:
                await asyncio.sleep(0.05)
            return result[0]

        async def apply_settings():
            video_dir = inputs["video_dir"].value.strip()
            new_db_path = inputs["db_path"].value.strip()
            species_csv = inputs["species_csv"].value.strip()
            behaviors_csv = inputs["behaviors_csv"].value.strip()
            annotator_name = inputs["annotator"].value.strip() or "default"

            if not video_dir:
                ui.notify(t("video_dir_required"), type="warning")
                return
            if not Path(video_dir).exists():
                ui.notify(t("video_dir_not_exist"), type="negative")
                return
            if not new_db_path:
                ui.notify(t("database_path_required"), type="warning")
                return
            if not species_csv:
                ui.notify(t("custom_species_required"), type="warning")
                return
            if not Path(species_csv).exists():
                ui.notify(t("custom_species_not_exist"), type="negative")
                return
            if behaviors_csv and not Path(behaviors_csv).exists():
                ui.notify(t("custom_behaviors_not_exist"), type="negative")
                return

            db_path_changed = new_db_path != str(current_db_path) if current_db_path else True
            if db_path_changed and Path(new_db_path).exists():
                confirmed = await _confirm_existing_db(new_db_path)
                if confirmed is None:
                    return
                if confirmed is False:
                    old_dp = get_data_provider()
                    if old_dp:
                        old_dp.engine.dispose()
                    Path(new_db_path).unlink()

            new_config = dict(config)
            new_config["db_dir"] = str(Path(new_db_path).parent)
            new_config["db_filename"] = Path(new_db_path).name
            new_config["video_dir"] = video_dir
            new_config["species_csv_path"] = species_csv
            new_config["species_behaviors_csv_path"] = behaviors_csv
            new_config["annotator_name"] = annotator_name

            save_config(new_config)

            # Update memory state for annotator and other prefs
            init_user_prefs(
                dark_mode=new_config.get("dark_mode", True),
                language=new_config.get("language", "en"),
                annotator_name=new_config.get("annotator_name", "default"),
            )

            new_dp = LocalDataProvider(str(CONFIG_PATH))
            set_data_provider(new_dp)
            set_queue([])
            set_current_idx(0)
            set_selections([])

            ui.notify(t("settings_saved"), type="positive")
            ui.navigate.to("/settings")

        with ui.row().classes("w-full justify-end gap-md"):
            apply_btn = ui.button(
                t("apply"), icon="check", color="primary", on_click=apply_settings
            )
            apply_btn.set_enabled(False)


async def setup_settings():
    container = ui.column().classes("w-full q-pa-lg")
    container.clear()
    _build_settings_content(container)
