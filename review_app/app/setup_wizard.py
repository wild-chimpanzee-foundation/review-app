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


def setup_wizard(on_complete_callback):
    video_dir_input = ui.input(
        "Video Directory",
        placeholder="/path/to/videos",
        value="",
    ).props("outlined dense")
    species_input = ui.input(
        "Species CSV Path",
        placeholder="/path/to/species.csv",
        value="",
    ).props("outlined dense")
    behaviors_input = ui.input(
        "Behaviors CSV Path (optional)",
        placeholder="/path/to/behaviors.csv",
        value="",
    ).props("outlined dense")
    db_path_input = ui.input(
        "Database Path",
        value=str(get_default_db_path()),
    ).props("outlined dense")

    ffmpeg_ok = False
    install_cmd = get_ffmpeg_install_cmd()

    async def check_ffmpeg():
        nonlocal ffmpeg_ok
        ffmpeg_ok, _ = check_ffmpeg()
        if ffmpeg_ok:
            ui.notify("ffmpeg is installed and working!", type="positive")
        else:
            ui.notify("ffmpeg not found. Please install it.", type="negative")

    async def submit():
        nonlocal ffmpeg_ok
        video_dir = video_dir_input.value.strip()
        species_csv = species_input.value.strip()
        behaviors_csv = behaviors_input.value.strip() or None
        db_path = db_path_input.value.strip()

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

        if not ffmpeg_ok:
            ui.notify("ffmpeg is required. Please install it first.", type="negative")
            return

        config = generate_config(video_dir, species_csv, behaviors_csv, db_path)
        save_config(config)
        ui.notify("Configuration saved!", type="positive")
        on_complete_callback()

    with ui.column().classes("w-full max-w-2xl mx-auto gap-6 p-4"):
        ui.label("Video Annotation Setup").classes("text-2xl font-bold")

        with ui.card().classes("w-full"):
            ui.label("Video Directory").classes("text-lg font-semibold")
            ui.label("Path to folder containing video files").classes("text-sm text-gray-600")
            video_dir_input

        with ui.card().classes("w-full"):
            ui.label("Species CSV").classes("text-lg font-semibold")
            ui.label("Path to CSV with species names (must have 'Nom_commun_anglais' column)").classes(
                "text-sm text-gray-600"
            )
            species_input

        with ui.card().classes("w-full"):
            ui.label("Behaviors CSV (Optional)").classes("text-lg font-semibold")
            ui.label("Path to CSV with species-behavior mappings").classes("text-sm text-gray-600")
            behaviors_input

        with ui.card().classes("w-full"):
            ui.label("Database Location").classes("text-lg font-semibold")
            ui.label("Where to store the review database").classes("text-sm text-gray-600")
            db_path_input

        with ui.card().classes("w-full bg-yellow-50") as card:
            ui.label("ffmpeg Requirement").classes("text-lg font-semibold")
            with ui.column().classes("gap-2"):
                ui.button("Check ffmpeg", on_click=check_ffmpeg, icon="refresh")
                ui.label("Install instructions:").classes("text-sm font-semibold")
                if platform.system() == "Windows":
                    ui.code(FFMPEG_INSTALL_WINDOWS).classes("text-xs")
                elif platform.system() == "Darwin":
                    ui.label("Run Homebrew install command, then: brew install ffmpeg").classes("text-xs")
                else:
                    ui.code(FFMPEG_INSTALL_LINUX).classes("text-xs")
                ui.label(f"Or download: {FFMPEG_DOWNLOAD}").classes("text-xs text-blue-600")

        ui.button(
            "Complete Setup",
            on_click=submit,
            icon="check",
        ).classes("self-center")
