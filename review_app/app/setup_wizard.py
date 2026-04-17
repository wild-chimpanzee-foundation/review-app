import os
import platform
import shutil
import subprocess
from pathlib import Path

import yaml
from nicegui import ui

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


def get_default_db_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    app_dir = base / "video_review_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / "review_data.db"


def get_config_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    app_dir = base / "video_review_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / "config.yaml"


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
                ffmpeg_status_holder[0].text = "Installed"
                ffmpeg_status_holder[0].classes("text-positive", remove="text-negative")
            else:
                ffmpeg_status_holder[0].text = "Not found - Please install"
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

        async def submit():
            video_dir = self.inputs["video_dir"].value.strip()
            db_path = self.inputs["db"].value.strip()

            if not video_dir:
                ui.notify("Please enter a video directory path", type="warning")
                return
            if not Path(video_dir).exists():
                ui.notify("Video directory does not exist", type="negative")
                return
            if not self.ffmpeg_ok:
                ui.notify("ffmpeg is required. Please install it first.", type="negative")
                return

            config = generate_config(video_dir, db_path, bundled_species, bundled_behaviors)
            save_config(config)
            ui.notify("Configuration saved!", type="positive")
            self.on_complete_callback()

        with ui.column().classes("w-full max-w-2xl mx-auto q-pa-lg"):
            with ui.card().classes("full-width q-mb-lg"):
                ui.label("Welcome to Video Annotation").classes(
                    "text-h4 text-primary font-weight-bold"
                )
                ui.label("Configure your workspace to start annotating videos").classes(
                    "text-body1 text-grey-7"
                )

            with ui.card().classes("full-width q-mb-md"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.icon("folder_open", size="sm").classes("text-primary q-mr-sm")
                    ui.label("Video Directory").classes("text-subtitle1 font-weight-medium")
                ui.label("Path to folder containing your video files").classes(
                    "text-caption text-grey-6 q-mb-md"
                )
                self.inputs["video_dir"] = ui.input(
                    placeholder="/path/to/videos", value=default_video
                ).props("outlined dense class=w-full")
                self.inputs["video_dir"].on_value_change(on_video_dir_change)

            with ui.card().classes("full-width q-mb-md"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.icon("movie", size="sm").classes("text-primary q-mr-sm")
                    ui.label("ffmpeg").classes("text-subtitle1 font-weight-medium")
                ui.label("Required for video processing").classes(
                    "text-caption text-grey-6 q-mb-sm"
                )
                ui.button(
                    "Check ffmpeg", icon="refresh", on_click=do_check_ffmpeg, color="primary"
                )
                ffmpeg_status_holder[0] = ui.label("Not checked").classes(
                    "text-caption text-grey-6 q-ml-md"
                )

            with ui.card().classes("full-width q-mb-md"):
                with ui.row().classes("items-center q-mb-sm"):
                    ui.icon("check_circle", size="sm").classes("text-positive q-mr-sm")
                    ui.label("Bundled Data").classes("text-subtitle1 font-weight-medium")
                if bundled_species:
                    ui.label(f"Species list: {Path(bundled_species).name} (bundled)").classes(
                        "text-caption text-grey-6"
                    )
                if bundled_behaviors:
                    ui.label(f"Behaviors: {Path(bundled_behaviors).name} (bundled)").classes(
                        "text-caption text-grey-6"
                    )
                if not bundled_species:
                    ui.label("No bundled data - configure manually in advanced settings").classes(
                        "text-caption text-warning"
                    )

            with ui.expansion("Advanced Settings", icon="settings").classes("full-width q-mb-md"):
                with ui.card().classes("full-width"):
                    with ui.row().classes("items-center q-mb-xs"):
                        ui.icon("storage", size="xs").classes("text-grey-6 q-mr-sm")
                        ui.label("Database Location").classes("text-body2")
                    ui.label("Where to store review data").classes(
                        "text-caption text-grey-6 q-mb-sm"
                    )
                    self.inputs["db"] = ui.input(value=default_db).props(
                        "outlined dense class=w-full"
                    )

            submit_button_holder[0] = ui.button(
                "Start Annotating", on_click=submit, icon="play_arrow", color="primary"
            ).props("size=lg")
            submit_button_holder[0].classes("full-width")
            submit_button_holder[0].visible = False

            def show_and_enable_button():
                btn = submit_button_holder[0]
                btn.visible = True
                btn.set_enabled(True)

            ui.timer(0.5, show_and_enable_button, once=True)
            update_submit_button()


def setup_wizard(on_complete_callback, config_path: Path | str = "config.yaml"):
    wizard = SetupWizard(on_complete_callback, config_path=config_path)
    wizard.build()
