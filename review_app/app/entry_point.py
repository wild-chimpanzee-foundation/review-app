import argparse
import os
import platform
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


def _get_config_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    app_dir = base / "video_review_app"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir / "config.yaml"


from review_app.app.setup_wizard import setup_wizard  # noqa: E402
from review_app.app.state import is_dark_mode, set_dark_mode, set_data_provider  # noqa: E402
from review_app.app.theme import apply_theme  # noqa: E402
from review_app.backend.local_data_provider import LocalDataProvider  # noqa: E402

CONFIG_PATH = _get_config_path()


def shared_header():
    from nicegui import ui

    apply_theme()

    # Dark mode is now session-safe via state functions (app.storage.user)
    dark = ui.dark_mode(value=is_dark_mode())

    def toggle_dark():
        new_val = not dark.value
        set_dark_mode(new_val)
        dark.value = new_val

    with ui.header().classes("bg-primary"):
        with ui.row().classes("w-full items-center q-px-md"):
            ui.label("Video Annotation").classes("text-h6 text-white font-weight-bold")
            ui.space()
            with ui.row().classes("gap-2"):
                ui.button(icon="dark_mode", on_click=toggle_dark).props("flat round color=white")
                ui.button("Overview", on_click=lambda: ui.navigate.to("/overview")).props(
                    "flat color=white"
                )
                ui.button("Review", on_click=lambda: ui.navigate.to("/review")).props(
                    "flat color=white"
                )
                ui.button("Import", on_click=lambda: ui.navigate.to("/model-import")).props(
                    "flat color=white"
                )
                ui.button("Settings", on_click=lambda: ui.navigate.to("/settings")).props(
                    "flat color=white"
                )


class GUI:
    def __init__(self):
        self.dp = None

    async def main_page(self):
        from nicegui import run, ui

        shared_header()

        if not CONFIG_PATH.exists():
            with ui.column().classes("w-full items-center justify-center q-pa-xl"):
                with ui.card().classes("text-center q-pa-xl"):
                    ui.label("No configuration found").classes("text-h5 text-primary q-mb-md")
                    ui.label("Please set up the application to get started").classes(
                        "text-body1 q-mb-lg"
                    )
                    ui.button(
                        "Set up now",
                        on_click=lambda: ui.navigate.to("/setup"),
                        icon="settings",
                        color="primary",
                    ).props("size=lg")
        else:
            try:
                dp = LocalDataProvider(str(CONFIG_PATH))
                set_data_provider(dp)
                self.dp = dp

                with ui.column().classes("w-full q-pa-lg"):
                    with ui.card().classes("full-width q-mb-lg"):
                        ui.label("Welcome to Video Annotation").classes(
                            "text-h5 text-primary font-weight-bold q-mb-md"
                        )
                        ui.label("Your video annotation dashboard").classes(
                            "text-body1 text-grey-7"
                        )

                    has_videos = await run.io_bound(dp.has_videos_in_db)

                    with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
                        with ui.card().classes("col"):
                            ui.label("Database").classes("text-caption text-grey-6")
                            ui.label(str(dp.db_path)).classes("text-body2")
                        with ui.card().classes("col"):
                            ui.label("Video Directory").classes("text-caption text-grey-6")
                            ui.label(str(dp.video_dir)).classes("text-body2")
                        with ui.card().classes("col"):
                            ui.label("Videos in DB").classes("text-caption text-grey-6")
                            ui.label("Yes" if has_videos else "No").classes("text-body2")

                    if not has_videos:
                        with ui.card().classes("full-width"):
                            ui.label("Sync your videos to get started").classes(
                                "text-body1 q-mb-md"
                            )
                            ui.button(
                                "Sync Videos",
                                icon="sync",
                                on_click=lambda: self._sync_videos(dp),
                                color="primary",
                            )
                    else:
                        with ui.row().classes("w-full gap-4"):
                            ui.button(
                                "Go to Overview",
                                icon="dashboard",
                                on_click=lambda: ui.navigate.to("/overview"),
                                color="primary",
                            )
                            ui.button(
                                "Start Reviewing",
                                icon="rate_review",
                                on_click=lambda: ui.navigate.to("/review"),
                                color="secondary",
                            )

            except Exception as e:
                with ui.card().classes("text-center q-pa-xl"):
                    ui.label("Error loading configuration").classes(
                        "text-h6 text-negative q-mb-md"
                    )
                    ui.label(str(e)).classes("text-body2 q-mb-lg")
                    ui.button(
                        "Reconfigure",
                        icon="settings",
                        on_click=lambda: (
                            (CONFIG_PATH.unlink() if CONFIG_PATH.exists() else None) or None
                        ),
                        color="negative",
                    )

    async def overview_page(self):
        from review_app.app.pages.overview import setup_overview

        shared_header()
        await setup_overview()

    async def review_page(self):
        from review_app.app.pages.review import setup_review

        shared_header()
        await setup_review()

    async def model_import_page(self):
        from review_app.app.pages.model_import import setup_model_import

        shared_header()
        await setup_model_import()

    def setup_page(self):
        from nicegui import ui

        shared_header()

        def on_setup_complete():
            ui.navigate.to("/overview")
            ui.notify("Setup complete! Syncing videos...", type="positive")

        setup_wizard(on_complete_callback=on_setup_complete, config_path=CONFIG_PATH)

    async def settings_page(self):
        from review_app.app.pages.settings import setup_settings

        shared_header()
        await setup_settings()

    def _sync_videos(self, data_provider):
        from nicegui import run, ui

        progress = ui.linear_progress(value=0, show_value=False)
        status = ui.label("Starting sync...")

        def update_progress(current, total, filename):
            if total > 0:
                progress.value = current / total
                status.text = f"Processing {current}/{total}: {filename}"
            else:
                status.text = "Scanning..."

        async def do_sync():
            await run.io_bound(data_provider.sync_videos, progress_callback=update_progress)
            progress.value = 1.0
            status.text = "Sync complete!"
            ui.notify("Video sync complete!", type="positive")

        ui.timer(0.1, do_sync, once=True)

    def start(self, dev_mode=False):
        from nicegui import app, ui

        @app.on_page_exception
        def custom_error_page(exception: Exception):
            from traceback import format_exc

            with ui.column().classes("w-full h-screen items-center justify-center"):
                ui.icon("error_outline", size="xl").classes("text-negative q-mb-md")
                ui.label("Something went wrong").classes("text-h5 text-negative q-mb-sm")
                ui.label(str(exception)).classes("text-body1 q-mb-lg")
                if dev_mode:
                    ui.code(format_exc(chain=False)).classes("q-pa-md")
                ui.button("Go Home", on_click=lambda: ui.navigate.to("/"), icon="home")

        ui.page("/")(self.main_page)
        ui.page("/overview")(self.overview_page)
        ui.page("/review")(self.review_page)
        ui.page("/setup")(self.setup_page)
        ui.page("/settings")(self.settings_page)
        ui.page("/model-import")(self.model_import_page)

        ui.add_body_html(
            """
            <script>
                document.addEventListener('keydown', function(e) {
                    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
                    if (e.ctrlKey || e.metaKey || e.altKey) return;
                    
                    if (e.key === 'o' || e.key === 'O') {
                        window.location.href = '/overview';
                    } else if (e.key === 'r' || e.key === 'R') {
                        window.location.href = '/review';
                    } else if (e.key === 'm' || e.key === 'M') {
                        window.location.href = '/model-import';
                    } else if (e.key === 's' || e.key === 'S') {
                        window.location.href = '/settings';
                    }
                });
            </script>
        """,
            shared=True,
        )

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


if __name__ in ("__main__", "__mp_main__"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="Enable dev mode with auto-reload")
    args = parser.parse_args()

    gui = GUI()
    gui.start(dev_mode=args.dev)
