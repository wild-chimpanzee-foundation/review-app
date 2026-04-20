import asyncio
from pathlib import Path

import yaml
from nicegui import ui

from review_app.app.config import get_config_path
from review_app.app.setup_wizard import (
    get_bundled_behaviors_csv,
    get_bundled_species_csv,
)
from review_app.app.state import (
    get_data_provider,
    set_current_idx,
    set_data_provider,
    set_queue,
    set_selections,
)
from review_app.app.utils import sync_with_progress
from review_app.backend.local_data_provider import LocalDataProvider

CONFIG_PATH = get_config_path()


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_config(config: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def _build_settings_content(container: ui.column):
    config = _load_config()
    current_video_dir = config.get("video_dir", "")
    current_db_dir = config.get("db_dir", "")
    current_db_file = config.get("db_filename", "review_data.db")
    current_db_path = Path(current_db_dir) / current_db_file if current_db_dir else None

    bundled_species = get_bundled_species_csv()
    bundled_behaviors = get_bundled_behaviors_csv()
    current_species_csv = config.get("species_csv_path", bundled_species)
    current_behaviors_csv = config.get("behaviors_csv_path", "")

    inputs = {}
    species_mode = "bundled"
    behaviors_mode = "none"

    if current_species_csv and current_species_csv != bundled_species:
        species_mode = "custom"
    if current_behaviors_csv:
        if current_behaviors_csv == bundled_behaviors:
            behaviors_mode = "bundled"
        else:
            behaviors_mode = "custom"

    dp = None
    stats = {"videos": 0}
    try:
        dp = LocalDataProvider(str(CONFIG_PATH))
        stats = {
            "videos": dp.get_overview_stats().get("videos", {}).get("total", 0),
        }
    except Exception:
        pass

    with container:
        with ui.row().classes("items-center q-mb-lg"):
            ui.label("Settings").classes("text-h5 font-weight-bold")

        with ui.card().classes("full-width q-mb-lg"):
            ui.label("Current Status").classes("text-subtitle1 font-weight-medium q-mb-md")
            with ui.row().classes("w-full gap-md"):
                with ui.card().classes("col text-center"):
                    ui.label(str(stats["videos"])).classes("text-h5 font-weight-bold")
                    ui.label("Videos in DB").classes("text-caption text-grey-6")
                with ui.card().classes("col text-center"):
                    ui.label(Path(current_video_dir).name if current_video_dir else "Not set").classes(
                        "text-h6"
                    )
                    ui.label("Video Directory").classes("text-caption text-grey-6")
                with ui.card().classes("col text-center"):
                    ui.label(str(current_db_path) if current_db_path else "Not set").classes(
                        "text-body2"
                    )
                    ui.label("Database").classes("text-caption text-grey-6")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("folder_open", size="sm").classes("text-primary q-mr-sm")
                ui.label("Video Directory").classes("text-subtitle1 font-weight-medium")
            ui.label("Path to folder containing your video files").classes(
                "text-caption text-grey-6 q-mb-md"
            )
            inputs["video_dir"] = ui.input(
                placeholder="/path/to/videos",
                value=current_video_dir,
            ).props("outlined dense class=w-full")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("storage", size="sm").classes("text-primary q-mr-sm")
                ui.label("Database File").classes("text-subtitle1 font-weight-medium")
            ui.label("Path to the SQLite database file").classes("text-caption text-grey-6 q-mb-md")
            inputs["db_path"] = ui.input(
                placeholder="/path/to/review_data.db",
                value=str(current_db_path) if current_db_path else "",
            ).props("outlined dense class=w-full")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("table_chart", size="sm").classes("text-primary q-mr-sm")
                ui.label("Species CSV").classes("text-subtitle1 font-weight-medium")
            ui.label("Species list for classification").classes("text-caption text-grey-6 q-mb-md")

            species_mode_holder = [species_mode]

            def update_species_visibility():
                mode = species_mode_holder[0]
                inputs["species_csv"].visible = mode == "custom"

            with ui.row().classes("w-full items-center q-mb-sm"):
                ui.radio(
                    ["bundled", "custom"],
                    value=species_mode,
                    on_change=lambda e: (
                        species_mode_holder.__setitem__(0, e.value),
                        update_species_visibility(),
                    ),
                ).props("inline")
                ui.label(
                    f"Bundled ({Path(bundled_species).name})"
                    if bundled_species
                    else "Not available"
                ).classes("text-caption text-grey-6")

            inputs["species_csv"] = ui.input(
                label="Custom Species CSV Path",
                value=current_species_csv if species_mode == "custom" else "",
            ).props("outlined dense class=w-full")
            inputs["species_csv"].visible = species_mode == "custom"

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("list", size="sm").classes("text-primary q-mr-sm")
                ui.label("Behaviors CSV").classes("text-subtitle1 font-weight-medium")
            ui.label("Species-behavior mappings (optional)").classes(
                "text-caption text-grey-6 q-mb-md"
            )

            behaviors_mode_holder = [behaviors_mode]

            def update_behaviors_visibility():
                mode = behaviors_mode_holder[0]
                inputs["behaviors_csv"].visible = mode == "custom"

            with ui.row().classes("w-full items-center q-mb-sm"):
                ui.radio(
                    ["none", "bundled", "custom"],
                    value=behaviors_mode,
                    on_change=lambda e: (
                        behaviors_mode_holder.__setitem__(0, e.value),
                        update_behaviors_visibility(),
                    ),
                ).props("inline")
                ui.label(
                    f"Bundled ({Path(bundled_behaviors).name})"
                    if bundled_behaviors
                    else "Not available"
                ).classes("text-caption text-grey-6")

            inputs["behaviors_csv"] = ui.input(
                label="Custom Behaviors CSV Path",
                value=current_behaviors_csv if behaviors_mode == "custom" else "",
            ).props("outlined dense class=w-full")
            inputs["behaviors_csv"].visible = behaviors_mode == "custom"

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("storage", size="sm").classes("text-primary q-mr-sm")
                ui.label("Database Management").classes("text-subtitle1 font-weight-medium")

            with ui.row().classes("w-full items-center q-mb-md"):
                ui.label("Sync Videos").classes("text-body2")
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
                            ui.label("Syncing videos...").classes("text-h6 q-mb-md")
                            progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
                            status = ui.label("Starting...")

                    sync_dialog.open()
                    await sync_with_progress(dp, progress=progress, status=status)
                    ui.notify("Videos synced!", type="positive")
                    await asyncio.sleep(0.5)
                    sync_dialog.close()

                ui.button(
                    "Sync Videos",
                    icon="sync",
                    color="primary",
                    on_click=open_sync_dialog,
                )

            with ui.row().classes("w-full items-center q-mb-md"):
                ui.label("Reset Database").classes("text-body2")
                ui.space()

                reset_dialog = ui.dialog().props("persistent")
                reset_card = [None]

                def build_confirm_step():
                    reset_dialog.clear()
                    with reset_dialog, ui.card().classes("q-pa-lg") as card:
                        reset_card[0] = card
                        ui.label("Reset database?").classes("text-h6 q-mb-sm")
                        ui.label(
                            "This permanently deletes all videos, annotations, and model data. "
                            "Your video files are not affected."
                        ).classes("text-body2 text-negative q-mb-lg")
                        with ui.row().classes("w-full justify-end gap-sm"):
                            ui.button("Cancel", on_click=reset_dialog.close).props("flat")
                            ui.button("Yes, reset", icon="delete_forever", color="negative",
                                      on_click=do_reset)

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
                        ui.label("Database reset").classes("text-h6 q-mb-sm")
                        ui.label(
                            "All data has been cleared. Sync your videos to get started again."
                        ).classes("text-body2 text-grey-7 q-mb-lg")

                        sync_progress_col = ui.column().classes("w-full q-mb-md")
                        sync_progress_col.visible = False
                        with sync_progress_col:
                            progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
                            status = ui.label("Starting...")

                        async def start_sync():
                            sync_progress_col.visible = True
                            sync_btn.visible = False
                            await sync_with_progress(new_dp, progress=progress, status=status)
                            ui.notify("Sync complete!", type="positive")
                            reset_dialog.close()
                            ui.navigate.to("/overview")

                        sync_btn = ui.button(
                            "Sync videos now", icon="sync", color="primary", on_click=start_sync
                        ).classes("full-width q-mb-sm")
                        ui.button("Do it later", on_click=reset_dialog.close).props(
                            "flat class=full-width"
                        )

                ui.button(
                    "Reset Database",
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
                ui.label("Database already exists").classes("text-h6 q-mb-sm")
                ui.label(db_path).classes("text-caption text-grey-6 q-mb-md")
                ui.label(
                    "A database already exists at this path. "
                    "Keep it to continue with existing data, or delete it and start fresh."
                ).classes("text-body2 q-mb-lg")
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
                    ui.button("Cancel", on_click=on_cancel).props("flat")
                    ui.button("Keep existing", icon="storage", color="primary", on_click=on_keep)
                    ui.button("Delete & start fresh", icon="delete_forever", color="negative", on_click=on_delete)
            dialog.open()
            while not done[0]:
                await asyncio.sleep(0.05)
            return result[0]

        async def apply_settings():
            video_dir = inputs["video_dir"].value.strip()
            new_db_path = inputs["db_path"].value.strip()
            species_mode = species_mode_holder[0]
            behaviors_mode = behaviors_mode_holder[0]

            if not video_dir:
                ui.notify("Video directory is required", type="warning")
                return
            if not Path(video_dir).exists():
                ui.notify("Video directory does not exist", type="negative")
                return
            if not new_db_path:
                ui.notify("Database path is required", type="warning")
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

            if species_mode == "bundled":
                new_config["species_csv_path"] = bundled_species
            else:
                species_csv = inputs["species_csv"].value.strip()
                if not species_csv:
                    ui.notify("Custom species CSV path is required", type="warning")
                    return
                if not Path(species_csv).exists():
                    ui.notify("Custom species CSV does not exist", type="negative")
                    return
                new_config["species_csv_path"] = species_csv

            if behaviors_mode == "none":
                new_config.pop("behaviors_csv_path", None)
            elif behaviors_mode == "bundled":
                new_config["behaviors_csv_path"] = bundled_behaviors
            else:
                behaviors_csv = inputs["behaviors_csv"].value.strip()
                if not behaviors_csv:
                    ui.notify("Custom behaviors CSV path is required", type="warning")
                    return
                if not Path(behaviors_csv).exists():
                    ui.notify("Custom behaviors CSV does not exist", type="negative")
                    return
                new_config["behaviors_csv_path"] = behaviors_csv

            _save_config(new_config)

            new_dp = LocalDataProvider(str(CONFIG_PATH))
            set_data_provider(new_dp)
            set_queue([])
            set_current_idx(0)
            set_selections([])

            ui.notify("Settings saved. Sync videos manually if needed.", type="positive")
            ui.navigate.to("/overview")

        with ui.row().classes("w-full justify-end gap-md"):
            ui.button("Cancel", on_click=lambda: ui.navigate.to("/overview")).props("flat")
            ui.button("Apply", icon="check", color="primary", on_click=apply_settings)


async def setup_settings():
    container = ui.column().classes("w-full q-pa-lg")
    container.clear()
    _build_settings_content(container)
