import asyncio
import platform
import shutil
import subprocess
from pathlib import Path

import yaml
from nicegui import run, ui

from review_app.app.config import (
    get_bundled_default_config_path,
    get_config_path,
    get_default_db_path,
    save_config,
)
from review_app.app.media import set_media_dirs
from review_app.app.translations import t
from review_app.app.utils import sync_with_progress

FFMPEG_INSTALL_MAC = "brew install ffmpeg"
FFMPEG_INSTALL_WINDOWS = "winget install ffmpeg"
FFMPEG_INSTALL_LINUX = "sudo apt install ffmpeg"


def validate_video_dir(path: str) -> str | None:
    """Return an error message if the directory is unsuitable, or None if valid."""
    from review_app.app.config import VIDEO_EXTENSIONS

    p = Path(path)
    if not p.exists():
        return t("video_dir_not_exist")
    if not p.is_dir():
        return "The selected path is a file, not a directory."
    has_files = False
    for child in p.rglob("*"):
        if child.is_file():
            has_files = True
            if child.suffix.lower() in VIDEO_EXTENSIONS:
                return None
    if not has_files:
        return "The directory is empty — no files were found."
    exts = ", ".join(sorted(VIDEO_EXTENSIONS))
    return f"No video files found in this directory. Supported formats: {exts}"


def check_ffmpeg() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def get_ffmpeg_install_cmd() -> str:
    system = platform.system()
    if system == "Darwin":
        return FFMPEG_INSTALL_MAC
    elif system == "Windows":
        return FFMPEG_INSTALL_WINDOWS
    else:
        return FFMPEG_INSTALL_LINUX


def generate_config(annotator_name: str) -> dict:
    default_path = get_bundled_default_config_path()
    if default_path.exists():
        with open(default_path) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    config["annotator_name"] = annotator_name

    # Resolve bundled CSV paths to absolute paths using __file__,
    # which works correctly both in dev and inside PyInstaller's _MEIPASS.
    data_dir = Path(__file__).parent.parent / "data"
    species_csv = data_dir / "species.csv"
    behaviors_csv = data_dir / "species_behaviors.csv"
    if species_csv.exists():
        config["species_csv_path"] = str(species_csv)
    if behaviors_csv.exists():
        config["species_behaviors_csv_path"] = str(behaviors_csv)

    return config


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
        default_annotator = defaults.get("annotator_name", "")
        default_project_name = defaults.get("_last_project_name", "")

        submit_button_holder: list = [None]

        def update_submit_button():
            btn = submit_button_holder[0]
            if btn is not None:
                can_submit = bool(self.inputs["video_dir"].value.strip()) and self.ffmpeg_ok
                btn.set_enabled(can_submit)

        def on_video_dir_change(e):
            update_submit_button()
            # Auto-fill project name from directory basename when empty
            if not self.inputs["project_name"].value.strip():
                path = e.value.strip()
                if path:
                    from pathlib import Path as _Path

                    self.inputs["project_name"].set_value(_Path(path).name)

        async def do_check_ffmpeg():
            ok = await run.io_bound(check_ffmpeg)
            self.ffmpeg_ok = ok
            if ok:
                ffmpeg_status_label.text = t("installed")
                ffmpeg_status_label.classes("text-positive", remove="text-negative text-grey-6")
            else:
                ffmpeg_status_label.text = t("not_installed")
                ffmpeg_status_label.classes("text-negative", remove="text-positive text-grey-6")
                ffmpeg_install_card.visible = True
            update_submit_button()

        async def _confirm_existing_db(db_path: str):
            result: list = [None]
            done: list = [False]

            dialog = ui.dialog().props("persistent")
            with dialog, ui.card().classes("q-pa-lg"):
                ui.label(t("database_exists")).classes("text-h6 q-mb-sm")
                ui.label(f"{db_path}").classes("text-caption text-grey-6 q-mb-md")
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

        async def submit():
            submit_button_holder[0].set_enabled(False)

            video_dir = self.inputs["video_dir"].value.strip()
            annotator_name = self.inputs["annotator_name"].value.strip() or "default"
            project_name = (
                self.inputs["project_name"].value.strip() or Path(video_dir).name or "My Project"
            )

            if not video_dir:
                ui.notify(t("enter_video_dir"), type="warning")
                update_submit_button()
                return
            dir_error = await run.io_bound(validate_video_dir, video_dir)
            if dir_error:
                ui.notify(dir_error, type="negative", timeout=6000)
                update_submit_button()
                return
            if not self.ffmpeg_ok:
                ui.notify(t("ffmpeg_required"), type="negative")
                update_submit_button()
                return

            db_path = get_default_db_path()
            adding_to_existing = bool(self.existing_config.get("active_project_id"))
            if db_path.exists() and not adding_to_existing:
                confirmed = await _confirm_existing_db(str(db_path))
                if confirmed is None:
                    update_submit_button()
                    return
                if confirmed is False:
                    db_path.unlink()

            if adding_to_existing:
                config = dict(self.existing_config)
                config["annotator_name"] = annotator_name
            else:
                config = generate_config(annotator_name)
            save_config(config)

            from review_app.app.state import (
                get_active_project_id,
                init_user_prefs,
                set_active_project,
                set_current_idx,
                set_data_provider,
                set_queue,
                set_selections,
            )
            from review_app.backend.local_data_provider import LocalDataProvider

            init_user_prefs(
                dark_mode=config.get("dark_mode", True),
                language=config.get("language", "en"),
                annotator_name=config.get("annotator_name", "default"),
            )

            # Create project in a temporary dp (active_project_id not in config yet)
            _dp_tmp = LocalDataProvider(str(self.config_path))
            project = _dp_tmp.create_project(project_name, video_dir)
            set_active_project(project.id)
            set_queue([])
            set_current_idx(0)
            set_selections([])

            # Reload dp now that active_project_id is in config so project dirs are populated
            dp = LocalDataProvider(str(self.config_path))
            set_data_provider(dp)

            set_media_dirs([Path(d.path) for d in dp.get_project_dirs(get_active_project_id())])
            has_videos = await run.io_bound(dp.has_videos_in_db, get_active_project_id())

            if not has_videos:
                submit_button_holder[0].visible = False

                dialog = ui.dialog().props("persistent")
                with dialog, ui.card().classes("q-pa-lg").style("min-width: 400px"):
                    ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                    progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
                    status = ui.label(t("starting")).classes("text-caption text-grey-6 q-mt-sm")
                    go_btn = (
                        ui.button(
                            t("go_to_overview_btn"),
                            icon="play_arrow",
                            color="primary",
                            on_click=lambda: (dialog.close(), self.on_complete_callback()),
                        )
                        .props("size=lg")
                        .classes("full-width q-mt-md")
                    )
                    go_btn.visible = False

                dialog.open()
                stats = await sync_with_progress(
                    dp, progress=progress, status=status, video_dir=video_dir,
                    active_project_id=get_active_project_id(),
                )
                status.text = t("sync_complete")
                if stats:
                    ui.label(t("sync_stat_scanned", n=stats["scanned"])).classes(
                        "text-caption text-grey-6"
                    )
                    ui.label(t("sync_stat_added", n=stats["added"])).classes(
                        "text-caption text-positive"
                    )
                    ui.label(t("sync_stat_updated", n=stats["updated"])).classes(
                        "text-caption text-grey-6"
                    )
                go_btn.visible = True
            else:
                self.on_complete_callback()

        with ui.column().classes("w-full max-w-2xl mx-auto q-pa-lg"):
            with ui.card().classes("full-width q-mb-lg"):
                ui.label(t("welcome_setup")).classes("text-h4 text-primary font-weight-bold")
                ui.label(t("welcome_setup_msg")).classes("text-body1 text-grey-7")

            with ui.card().classes("full-width q-mb-md"):
                ui.label(t("project_name_label")).classes(
                    "text-subtitle1 font-weight-medium q-mb-xs"
                )
                ui.label(t("project_name_desc")).classes("text-caption text-grey-6 q-mb-md")
                self.inputs["project_name"] = ui.input(
                    placeholder=t("project_name_placeholder"), value=default_project_name
                ).props("outlined dense class=w-full")

            with ui.card().classes("full-width q-mb-md"):
                ui.label(t("video_dir_label")).classes("text-subtitle1 font-weight-medium q-mb-xs")
                ui.label(t("video_dir_desc")).classes("text-caption text-grey-6 q-mb-md")
                self.inputs["video_dir"] = ui.input(
                    placeholder=t("video_dir_placeholder"), value=default_video
                ).props("outlined dense class=w-full")
                self.inputs["video_dir"].on_value_change(on_video_dir_change)

            with ui.card().classes("full-width q-mb-md"):
                ui.label(t("annotator_label")).classes("text-subtitle1 font-weight-medium q-mb-xs")
                ui.label(t("annotator_setup_desc")).classes("text-caption text-grey-6 q-mb-md")
                self.inputs["annotator_name"] = ui.input(
                    placeholder=t("annotator_name_placeholder"), value=default_annotator
                ).props("outlined dense class=w-full")

            with ui.card().classes("full-width q-mb-md"):
                with ui.row().classes("items-center gap-sm"):
                    ui.label(t("ffmpeg_label")).classes("text-subtitle1 font-weight-medium")
                    ffmpeg_status_label = ui.label(t("ffmpeg_checking")).classes(
                        "text-caption text-grey-6"
                    )
                ui.label(t("ffmpeg_desc")).classes("text-caption text-grey-6 q-mt-xs")

            ffmpeg_install_card = ui.card().classes("full-width q-mb-md bg-negative text-white")
            ffmpeg_install_card.visible = False
            with ffmpeg_install_card:
                ui.label(t("ffmpeg_not_found_title")).classes(
                    "text-subtitle1 font-weight-bold q-mb-xs"
                )
                ui.label(t("ffmpeg_install_instructions")).classes("text-caption q-mb-sm")
                ui.code(get_ffmpeg_install_cmd()).classes("full-width")

            submit_button_holder[0] = ui.button(
                t("sync_videos_title"), on_click=submit, icon="play_arrow", color="primary"
            ).props("size=lg")
            submit_button_holder[0].classes("full-width")
            submit_button_holder[0].set_enabled(False)

            ui.timer(0, do_check_ffmpeg, once=True)


def setup_wizard(on_complete_callback, config_path: Path | str | None = None):
    wizard = SetupWizard(on_complete_callback, config_path=config_path)
    wizard.build()
