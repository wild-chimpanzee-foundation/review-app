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
FFMPEG_DOWNLOAD = "https://ffmpeg.org/download.html"


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


def generate_config(
    video_dir: str,
    species_csv: str,
    behaviors_csv: str | None,
    db_path: str,
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
    def __init__(self, on_complete_callback, config_path: Path | str = "config.yaml"):
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
        default_species = defaults.get("species_csv_path", "")
        default_behaviors = defaults.get("behaviors_csv_path", "")
        default_db = (
            str(Path(defaults.get("db_dir", "")) / defaults.get("db_filename", ""))
            if defaults.get("db_dir")
            else str(get_default_db_path())
        )

        async def do_check_ffmpeg():
            result = check_ffmpeg()
            self.ffmpeg_ok = result[0]
            if self.ffmpeg_ok:
                ui.notify("ffmpeg is installed and working!", type="positive")
            else:
                ui.notify("ffmpeg not found. Please install it.", type="negative")

        async def submit():
            video_dir = self.inputs["video_dir"].value.strip()
            species_csv = self.inputs["species"].value.strip()
            behaviors_csv = self.inputs["behaviors"].value.strip() or None
            db_path = self.inputs["db"].value.strip()

            if not video_dir:
                ui.notify("Please enter a video directory path", type="warning")
                return
            if not Path(video_dir).exists():
                ui.notify("Video directory does not exist", type="negative")
                return
            if not species_csv:
                ui.notify("Please enter a species CSV path", type="warning")
                return
            if not Path(species_csv).exists():
                ui.notify("Species CSV file does not exist", type="negative")
                return
            if behaviors_csv and not Path(behaviors_csv).exists():
                ui.notify("Behaviors CSV file does not exist", type="negative")
                return

            if not self.ffmpeg_ok:
                ui.notify("ffmpeg is required. Please install it first.", type="negative")
                return

            config = generate_config(video_dir, species_csv, behaviors_csv, db_path)
            save_config(config)
            ui.notify("Configuration saved!", type="positive")
            self.on_complete_callback()

        with ui.column().classes("w-full max-w-2xl mx-auto gap-4 p-4"):
            ui.label("Video Annotation Setup").classes("text-2xl font-bold")

            with ui.card().classes("w-full"):
                ui.label("Video Directory").classes("text-lg font-semibold")
                ui.label("Path to folder containing video files").classes("text-sm")
                self.inputs["video_dir"] = ui.input(
                    placeholder="/path/to/videos", value=default_video
                ).props("outlined dense")

            with ui.card().classes("w-full"):
                ui.label("ffmpeg Requirement").classes("text-lg font-semibold")
                ui.button("Check ffmpeg", icon="refresh", on_click=do_check_ffmpeg)
                ui.label("Install: sudo apt install ffmpeg").classes("text-xs")

            with ui.expansion("Advanced Settings", icon="settings").classes("w-full"):
                with ui.card().classes("w-full"):
                    ui.label("Species CSV").classes("text-lg font-semibold")
                    ui.label("Must have 'Nom_commun_anglais' column").classes("text-sm")
                    self.inputs["species"] = ui.input(
                        placeholder="/path/to/species.csv", value=default_species
                    ).props("outlined dense")

                with ui.card().classes("w-full"):
                    ui.label("Behaviors CSV (Optional)").classes("text-lg font-semibold")
                    ui.label("Species-behavior mappings").classes("text-sm")
                    self.inputs["behaviors"] = ui.input(
                        placeholder="/path/to/behaviors.csv", value=default_behaviors
                    ).props("outlined dense")

                with ui.card().classes("w-full"):
                    ui.label("Database Location").classes("text-lg font-semibold")
                    self.inputs["db"] = ui.input(value=default_db).props("outlined dense")

            ui.button("Complete Setup", on_click=submit, icon="check").classes("self-center")


def setup_wizard(on_complete_callback, config_path: Path | str = "config.yaml"):
    wizard = SetupWizard(on_complete_callback, config_path=config_path)
    wizard.build()
