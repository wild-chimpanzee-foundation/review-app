import sys
import threading
import webbrowser
from pathlib import Path

import webview
from nicegui import core as nicegui_core
from nicegui import run, ui

sys.path.insert(0, str(Path(__file__).parent.parent))

from review_app.app.setup_wizard import setup_wizard
from review_app.app.state import set_data_provider
from review_app.app.theme import apply_theme
from review_app.backend.local_data_provider import LocalDataProvider

CONFIG_PATH = Path("config.yaml")


def create_window():
    return webview.create_window(
        "Video Annotation",
        url="http://localhost:8080",
        width=1400,
        height=900,
        resizable=True,
        js_api=None,
    )


def start_nicegui():
    ui.run(host="localhost", port=8080, show=False, reload=False, storage_secret="video_annotation_secret_key")


async def main_page_content():
    apply_theme()
    if not CONFIG_PATH.exists():
        with ui.column().classes("w-full items-center justify-center min-h-screen"):
            ui.label("No configuration found").classes("text-xl")
            ui.label("Please set up the application").classes("text-lg")
    else:
        try:
            dp = LocalDataProvider(str(CONFIG_PATH))
            set_data_provider(dp)

            with ui.header().classes("bg-primary text-white items-center"):
                ui.label("Video Annotation").classes("text-xl font-bold")
                ui.space()
                ui.button("Overview", on_click=lambda: ui.navigate.to("/overview"))
                ui.button("Review", on_click=lambda: ui.navigate.to("/review"))

            with ui.row().classes("w-full"):
                with ui.column().classes("w-full max-w-6xl mx-auto p-4"):
                    ui.label("Welcome to Video Annotation").classes("text-2xl")
                    ui.label(f"Database: {dp.db_path}").classes("text-sm text-gray-600")
                    ui.label(f"Video directory: {dp.video_dir}").classes("text-sm text-gray-600")
                    
                    has_videos = await run.io_bound(dp.has_videos_in_db)
                    ui.label(f"Videos in DB: {'Yes' if has_videos else 'No'}").classes(
                        "text-sm text-gray-600"
                    )

                    if not has_videos:
                        ui.button(
                            "Sync Videos",
                            icon="sync",
                            on_click=lambda: sync_videos(dp),
                        )
                    else:
                        ui.button(
                            "Go to Overview",
                            icon="dashboard",
                            on_click=lambda: ui.navigate.to("/overview"),
                        )

            async def sync_videos(data_provider):
                progress = ui.linear_progress(value=0, show_value=False)
                status = ui.label("Starting sync...")

                def update_progress(current, total, filename):
                    if total > 0:
                        progress.value = current / total
                        status.text = f"Processing {current}/{total}: {filename}"
                    else:
                        status.text = "Scanning..."

                await run.io_bound(data_provider.sync_videos, progress_callback=update_progress)
                progress.value = 1.0
                status.text = "Sync complete!"
                ui.notify("Video sync complete!", type="positive")

        except Exception as e:
            ui.label(f"Error loading configuration: {e}")
            ui.button(
                "Reconfigure",
                on_click=lambda: CONFIG_PATH.unlink() if CONFIG_PATH.exists() else None,
            )


async def overview_page_content():
    apply_theme()
    from review_app.app.pages.overview import setup_overview
    await setup_overview()


async def review_page_content():
    apply_theme()
    from review_app.app.pages.review import setup_review
    await setup_review()


async def setup_page_content():
    apply_theme()
    setup_wizard(on_complete_callback=lambda: ui.navigate.to("/"))


def setup_main_app():
    ui.page("/")(main_page_content)
    ui.page("/overview")(overview_page_content)
    ui.page("/review")(review_page_content)
    ui.page("/setup")(setup_page_content)
    if nicegui_core.script_client:
        nicegui_core.script_client.delete()
        nicegui_core.script_client = None
    nicegui_core.script_mode = False


def run_app(browser_only=False):
    setup_main_app()

    if not CONFIG_PATH.exists():
        ui.navigate.to("/setup")

    if browser_only:
        webbrowser.open("http://localhost:8080")
        ui.run(host="localhost", port=8080, show=True, reload=False, storage_secret="video_annotation_secret_key")
    else:
        threading.Thread(target=start_nicegui, daemon=True).start()
        create_window()
        webview.start()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true", help="Run in browser mode")
    args = parser.parse_args()
    run_app(browser_only=args.browser)
