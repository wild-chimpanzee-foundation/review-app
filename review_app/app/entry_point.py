import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("GDK_BACKEND", "x11")
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

APP_DIR = (
    Path(sys._MEIPASS).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).parent.parent.parent
)
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

from review_app.app.setup_wizard import setup_wizard
from review_app.app.state import set_data_provider
from review_app.backend.local_data_provider import LocalDataProvider

CONFIG_PATH = APP_DIR / "config.yaml"


def shared_header():
    from nicegui import ui

    dark = ui.dark_mode(value=False)

    def toggle_dark():
        dark.value = not dark.value

    with ui.header().classes("bg-primary items-center").style("padding: 0 16px;"):
        ui.label("Video Annotation").classes("text-xl font-bold text-white")
        ui.space()
        ui.button(icon="dark_mode", on_click=toggle_dark).props("flat round color=white")
        ui.button("Overview", on_click=lambda: ui.navigate.to("/overview")).props(
            "flat color=white"
        )
        ui.button("Review", on_click=lambda: ui.navigate.to("/review")).props("flat color=white")


class GUI:
    def __init__(self):
        self.dp = None

    def main_page(self):
        from nicegui import ui

        shared_header()

        if not CONFIG_PATH.exists():
            with ui.column().classes("w-full items-center justify-center min-h-screen gap-4"):
                ui.label("No configuration found").classes("text-xl")
                ui.label("Please set up the application").classes("text-lg")
                ui.button("Set up now", on_click=lambda: ui.navigate.to("/setup"), icon="settings")
        else:
            try:
                dp = LocalDataProvider(str(CONFIG_PATH))
                set_data_provider(dp)
                self.dp = dp

                with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):
                    ui.label("Welcome to Video Annotation").classes("text-2xl")
                    ui.label(f"Database: {dp.db_path}").classes("text-sm")
                    ui.label(f"Video directory: {dp.video_dir}").classes("text-sm")
                    ui.label(f"Videos in DB: {'Yes' if dp.has_videos_in_db() else 'No'}").classes(
                        "text-sm"
                    )

                    if not dp.has_videos_in_db():
                        ui.button(
                            "Sync Videos", icon="sync", on_click=lambda: self._sync_videos(dp)
                        )
                    else:
                        ui.button(
                            "Go to Overview",
                            icon="dashboard",
                            on_click=lambda: ui.navigate.to("/overview"),
                        )

            except Exception as e:
                ui.label(f"Error loading configuration: {e}")
                ui.button(
                    "Reconfigure",
                    on_click=lambda: CONFIG_PATH.unlink() if CONFIG_PATH.exists() else None,
                )

    def overview_page(self):
        from review_app.app.pages.overview import setup_overview

        shared_header()
        setup_overview()

    def review_page(self):
        from review_app.app.pages.review import setup_review

        shared_header()
        setup_review()

    def setup_page(self):
        from nicegui import ui

        shared_header()
        setup_wizard(on_complete_callback=lambda: ui.navigate.to("/"), config_path=CONFIG_PATH)

    def _sync_videos(self, data_provider):
        from nicegui import ui

        progress = ui.linear_progress(value=0, show_value=False)
        status = ui.label("Starting sync...")

        def update_progress(current, total, filename):
            if total > 0:
                progress.value = current / total
                status.text = f"Processing {current}/{total}: {filename}"
            else:
                status.text = "Scanning..."

        data_provider.sync_videos(progress_callback=update_progress)
        progress.value = 1.0
        status.text = "Sync complete!"
        ui.notify("Video sync complete!", type="positive")

    def start(self, dev_mode=False):
        from nicegui import ui

        ui.page("/")(self.main_page)
        ui.page("/overview")(self.overview_page)
        ui.page("/review")(self.review_page)
        ui.page("/setup")(self.setup_page)

        ui.run(
            title="Video Annotation",
            host="127.0.0.1",
            port=8000,
            show=True,
            reload=dev_mode,
            storage_secret="video_annotation_secret_key",
        )


if __name__ in ("__main__", "__mp_main__"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="Enable dev mode with auto-reload")
    args = parser.parse_args()

    gui = GUI()
    gui.start(dev_mode=args.dev)
