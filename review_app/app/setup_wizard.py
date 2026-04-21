import asyncio
import platform
import shutil
import subprocess
from pathlib import Path

import yaml
from nicegui import run, ui

from review_app.app.config import get_config_path, get_default_db_path
from review_app.app.translations import t
from review_app.app.utils import sync_with_progress

FFMPEG_INSTALL_MAC = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" && brew install ffmpeg'
FFMPEG_INSTALL_WINDOWS = "winget install ffmpeg"
FFMPEG_INSTALL_LINUX = "sudo apt install ffmpeg"


def check_ffmpeg() -> tuple[bool, str | None]:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return True, None
        except Exception:
            pass
    return False, None


def get_ffmpeg_install_cmd() -> str:
    system = platform.system()
    if system == "Darwin":
        return FFMPEG_INSTALL_MAC
    elif system == "Windows":
        return FFMPEG_INSTALL_WINDOWS
    else:
        return FFMPEG_INSTALL_LINUX


def get_bundled_species_csv() -> str | None:
    bundle_dir = Path(__file__).parent.parent / "data"
    bundled = bundle_dir / "species.csv"
    if bundled.exists():
        return str(bundled)
    return None


def get_bundled_behaviors_csv() -> str | None:
    bundle_dir = Path(__file__).parent.parent / "data"
    bundled = bundle_dir / "species_behaviors.csv"
    if bundled.exists():
        return str(bundled)
    return None


def generate_config(
    video_dir: str,
    db_path: str,
    species_csv: str | None = None,
    behaviors_csv: str | None = None,
) -> dict:
    config = {
        "video_dir": video_dir,
        "db_dir": str(Path(db_path).parent),
        "db_filename": Path(db_path).name,
        "recreate_db_on_start": False,
        "species_csv_path": species_csv,
        "species_column": "Nom_commun_anglais",
        "fuzzy_match_threshold": 80,
        "behavior_defaults": ["reacts_to_camera", "does_not_react"],
    }
    if behaviors_csv:
        config["behaviors_csv_path"] = behaviors_csv
    return config


def save_config(config: dict, path: Path | str = "config.yaml") -> None:
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


class SetupWizard:
    def __init__(self, on_complete_callback, config_path: Path | str | None = None):
        if config_path is None:
            config_path = get_config_path()
        self.on_complete_callback = on_complete_callback
        self.config_path = Path(config_path)
        self.ffmpeg_ok = False
        self.inputs = {}
        self.existing_config = self._load_existing_config()

    def _load_existing_config(self) -> dict:
        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
        return {}

    def build(self):
        defaults = self.existing_config
        default_video = defaults.get("video_dir", "")
        default_db = (
            str(Path(defaults.get("db_dir", "")) / defaults.get("db_filename", ""))
            if defaults.get("db_dir")
            else str(get_default_db_path())
        )

        bundled_species = get_bundled_species_csv()
        bundled_behaviors = get_bundled_behaviors_csv()
        ffmpeg_status_holder: list = [None]

        async def do_check_ffmpeg():
            result = check_ffmpeg()
            self.ffmpeg_ok = result[0]
            ffmpeg_status_holder[0].visible = True
            if self.ffmpeg_ok:
                ffmpeg_status_holder[0].text = t("installed")
                ffmpeg_status_holder[0].classes("text-positive", remove="text-negative")
            else:
                ffmpeg_status_holder[0].text = t("not_installed")
                ffmpeg_status_holder[0].classes("text-negative", remove="text-positive")
            update_submit_button()

        submit_button_holder: list = [None]

        def update_submit_button():
            btn = submit_button_holder[0]
            if btn is not None:
                can_submit = bool(self.inputs["video_dir"].value.strip()) and self.ffmpeg_ok
                btn.visible = can_submit

        async def on_video_dir_change():
            update_submit_button()

        async def _confirm_existing_db(db_path: str):
            """Show a dialog asking what to do with an existing DB.
            Returns True to keep it, False to overwrite, None to cancel."""
            result: list = [None]
            done: list = [False]

            dialog = ui.dialog().props("persistent")
            with dialog, ui.card().classes("q-pa-lg"):
                ui.label(t("database_exists")).classes("text-h6 q-mb-sm")
                ui.label(f"{db_path}").classes("text-caption text-grey-6 q-mb-md")
                ui.label(
                    t("database_exists_msg")
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

                    ui.button(t("cancel"), on_click=on_cancel).props("flat")
                    ui.button(t("keep_existing"), icon="storage", color="primary", on_click=on_keep)
                    ui.button(t("delete_fresh"), icon="delete_forever", color="negative", on_click=on_delete)

            dialog.open()
            while not done[0]:
                await asyncio.sleep(0.05)
            return result[0]

        async def submit():
            video_dir = self.inputs["video_dir"].value.strip()
            db_path = self.inputs["db"].value.strip()

            if not video_dir:
                ui.notify(t("enter_video_dir"), type="warning")
                return
            if not Path(video_dir).exists():
                ui.notify(t("video_dir_not_exist"), type="negative")
                return
            if not self.ffmpeg_ok:
                ui.notify(t("ffmpeg_required"), type="negative")
                return

            if Path(db_path).exists():
                confirmed = await _confirm_existing_db(db_path)
                if confirmed is None:
                    return
                if confirmed is False:
                    Path(db_path).unlink()

            config = generate_config(video_dir, db_path, bundled_species, bundled_behaviors)
            save_config(config, self.config_path)
            ui.notify(t("config_saved"), type="positive")

            from review_app.app.state import set_data_provider
            from review_app.backend.local_data_provider import LocalDataProvider

            dp = LocalDataProvider(str(self.config_path))
            set_data_provider(dp)

            has_videos = await run.io_bound(dp.has_videos_in_db)

            if not has_videos:
                wizard_container = ui.column().classes("w-full max-w-2xl mx-auto q-pa-lg")
                start_button_holder: list = [None]

                with wizard_container:
                    with ui.card().classes("full-width q-mb-lg"):
                        ui.label(t("setup_complete")).classes("text-h5 text-primary font-weight-bold")
                        ui.label(t("setup_complete_msg")).classes(
                            "text-body1 text-grey-7"
                        )

                    with ui.card().classes("full-width q-mb-lg"):
                        ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                        progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
                        status = ui.label(t("starting"))

                    start_button_holder[0] = ui.button(
                        t("start_annotating"),
                        icon="play_arrow",
                        color="primary",
                        on_click=lambda: self.on_complete_callback(),
                    )
                    start_button_holder[0].visible = False

                await sync_with_progress(dp, progress=progress, status=status)
                ui.notify(t("videos_synced_notify"), type="positive")
                status.text = t("sync_complete")
                start_button_holder[0].visible = True
            else:
                self.on_complete_callback()

        with ui.column().classes("w-full max-w-2xl mx-auto q-pa-lg"):
            with ui.card().classes("full-width q-mb-lg"):
                ui.label(t("welcome_setup")).classes(
                    "text-h4 text-primary font-weight-bold"
                )
                ui.label(t("welcome_setup_msg")).classes(
                    "text-body1 text-grey-7"
                )

            with ui.card().classes("full-width q-mb-md"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.label(t("video_dir_label")).classes("text-subtitle1 font-weight-medium")
                ui.label(t("video_dir_desc")).classes(
                    "text-caption text-grey-6 q-mb-md"
                )
                self.inputs["video_dir"] = ui.input(
                    placeholder=t("video_dir_placeholder"), value=default_video
                ).props("outlined dense class=w-full")
                self.inputs["video_dir"].on_value_change(on_video_dir_change)

            with ui.card().classes("full-width q-mb-md"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.label(t("ffmpeg_label")).classes("text-subtitle1 font-weight-medium")
                ui.label(t("ffmpeg_desc")).classes(
                    "text-caption text-grey-6 q-mb-sm"
                )
                ui.button(
                    t("check_ffmpeg"), on_click=do_check_ffmpeg, color="primary"
                )
                ffmpeg_status_holder[0] = ui.label(t("not_checked")).classes(
                    "text-caption text-grey-6 q-ml-md"
                )

            with ui.card().classes("full-width q-mb-md"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.label(t("bundled_data")).classes("text-subtitle1 font-weight-medium")
                if bundled_species:
                    ui.label(t("species_list", name=Path(bundled_species).name)).classes(
                        "text-caption text-grey-6"
                    )
                if bundled_behaviors:
                    ui.label(t("behaviors_list", name=Path(bundled_behaviors).name)).classes(
                        "text-caption text-grey-6"
                    )
                if not bundled_species:
                    ui.label(t("no_bundled_data")).classes(
                        "text-caption text-warning"
                    )

            with ui.expansion(t("advanced_settings"), icon="settings").classes("full-width q-mb-md"):
                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-xs"):
                        ui.icon("storage", size="xs").classes("text-grey-6 q-mr-sm")
                        ui.label(t("database_location")).classes("text-body2")
                    ui.label(t("database_location_desc")).classes(
                        "text-caption text-grey-6 q-mb-sm"
                    )
                    self.inputs["db"] = ui.input(value=default_db).props(
                        "outlined dense class=w-full"
                    )

            submit_button_holder[0] = ui.button(
                t("start_annotating"), on_click=submit, icon="play_arrow", color="primary"
            ).props("size=lg")
            submit_button_holder[0].classes("full-width")
            submit_button_holder[0].visible = False

            def show_and_enable_button():
                btn = submit_button_holder[0]
                btn.visible = True
                btn.set_enabled(True)

            ui.timer(0.5, show_and_enable_button, once=True)
            update_submit_button()


def setup_wizard(on_complete_callback, config_path: Path | str | None = None):
    wizard = SetupWizard(on_complete_callback, config_path=config_path)
    wizard.build()
