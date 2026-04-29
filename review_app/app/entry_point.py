import argparse
import mimetypes
import os
import secrets
import sys
from pathlib import Path

from review_app.app.config import (
    get_config_path,
    get_default_db_path,
    load_config,
)  # noqa: E402
from review_app.app.media import set_media_dirs, setup_media_route  # noqa: E402
from review_app.app.project_picker import build_project_picker  # noqa: E402
from review_app.app.setup_wizard import setup_wizard  # noqa: E402
from review_app.app.state import (  # noqa: E402
    get_active_project_id,
    init_user_prefs,
    is_dark_mode,
    set_active_project,
    set_dark_mode,
    set_data_provider,
)
from review_app.app.theme import apply_theme  # noqa: E402
from review_app.app.translations import get_language, set_language, t  # noqa: E402
from review_app.app.utils import (  # noqa: E402
    get_or_create_data_provider,
    render_uninitialized_state,
    switch_project,
)
from review_app.backend.local_data_provider import LocalDataProvider  # noqa: E402

# Configure display backends for Wayland/Hyprland/X11 compatibility (Linux only)
if sys.platform.startswith("linux"):
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        os.environ.setdefault("GDK_BACKEND", "wayland,x11")
    else:
        os.environ.setdefault("GDK_BACKEND", "x11")
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb;wayland")

# Register video mimetypes for both lower and uppercase extensions (camera traps often use .MP4)
for _ext, _mime in [
    (".mp4", "video/mp4"),
    (".avi", "video/x-msvideo"),
    (".mov", "video/quicktime"),
    (".mkv", "video/x-matroska"),
    (".webm", "video/webm"),
    (".wmv", "video/x-ms-wmv"),
    (".flv", "video/x-flv"),
]:
    mimetypes.add_type(_mime, _ext)
    mimetypes.add_type(_mime, _ext.upper())


CONFIG_PATH = get_config_path()


def shared_header(show_drawer: bool = False):
    from nicegui import ui

    apply_theme()
    ui.query(".q-layout").props('view="hHh Lpr lFf"')

    # Register navigate handler per-page
    def handle_navigate(e):
        ui.navigate.to(e.args)

    ui.on("navigate", handle_navigate)

    # Dark mode is now session-safe via state functions (app.storage.user)
    dark = ui.dark_mode(value=is_dark_mode())

    def toggle_dark():
        new_val = not dark.value
        set_dark_mode(new_val)
        dark.value = new_val

    def change_language(e):
        set_language(e.value)
        ui.run_javascript("window.location.reload()")

    # Define shortcuts dialog once per page
    with (
        ui.dialog() as shortcuts_dialog,
        ui.card().classes("q-pa-lg relative").style("min-width: 400px"),
    ):
        ui.button(icon="close", on_click=shortcuts_dialog.close).props("flat round").classes(
            "absolute-top-right q-ma-sm"
        )

    drawer = None

    def toggle_drawer():
        if drawer is not None:
            if "mini" in drawer._props:
                drawer.props(remove="mini")
            else:
                drawer.props("mini")

    if show_drawer:
        drawer = ui.left_drawer(value=True).classes("q-pa-sm")

        with drawer:
            with ui.row().classes("items-center q-mb-sm"):
                ui.button(icon="tune", on_click=toggle_drawer).props(
                    "flat round color=primary"
                ).tooltip(t("filters_label"))
                ui.label(t("filters_label")).classes(
                    "q-mini-drawer-hide text-subtitle2 font-weight-medium"
                )

    with ui.header().classes("bg-primary text-white"):
        with ui.row().classes("w-full items-center q-px-md no-wrap gap-2"):
            _active_pid = get_active_project_id()
            if _active_pid:
                _header_dp = LocalDataProvider(str(CONFIG_PATH))
                _active_proj_name = (
                    p.name if (p := _header_dp.get_project(_active_pid)) else _active_pid
                )
                project_dialog, refresh_projects = build_project_picker(CONFIG_PATH)
                ui.button(
                    _active_proj_name,
                    icon="folder_special",
                    on_click=lambda: (refresh_projects(), project_dialog.open()),
                ).props("outline color=white dense icon-right=arrow_drop_down").classes(
                    "q-ml-sm text-caption q-px-sm"
                ).style("border-color: rgba(255, 255, 255, 0.5)")
            ui.space()
            with ui.row().classes("gap-2 items-center"):
                ui.button(t("nav_overview"), on_click=lambda: ui.navigate.to("/overview")).props(
                    "flat color=white"
                ).classes("gt-sm")
                ui.button(t("nav_review"), on_click=lambda: ui.navigate.to("/review")).props(
                    "flat color=white"
                ).classes("gt-sm")
                ui.button(t("nav_import"), on_click=lambda: ui.navigate.to("/model-import")).props(
                    "flat color=white"
                ).classes("gt-sm")
                ui.button(t("nav_settings"), on_click=lambda: ui.navigate.to("/settings")).props(
                    "flat color=white"
                ).classes("gt-sm")

            ui.space()

            with ui.row().classes("gap-2 items-center q-ml-md"):
                ui.select(
                    options={"en": t("lang_en"), "fr": t("lang_fr")},
                    value=get_language(),
                    on_change=change_language,
                ).props("dense outlined dark popup-content-class=header-dropdown").classes("w-32")
                ui.button(icon="help_outline", on_click=shortcuts_dialog.open).props(
                    "flat round color=white"
                )
                ui.button(icon="dark_mode", on_click=toggle_dark).props("flat round color=white")

    return drawer


class GUI:
    def __init__(self):
        self.dp = None

    async def main_page(self):
        from nicegui import run, ui

        shared_header()

        if not CONFIG_PATH.exists():
            with ui.column().classes("w-full items-center justify-center q-pa-xl"):
                with ui.card().classes("text-center q-pa-xl"):
                    ui.label(t("no_config_title")).classes("text-h5 text-primary q-mb-md")
                    ui.label(t("no_config_subtitle")).classes("text-body1 q-mb-lg")
                    ui.button(
                        t("setup_now_btn"),
                        on_click=lambda: ui.navigate.to("/setup"),
                        icon="settings",
                        color="primary",
                    ).props("size=lg")
            return

        try:
            cfg = load_config()

            raw_db_dir = cfg.get("db_dir", "")
            db_filename = cfg.get("db_filename", "review_data.db")
            if raw_db_dir and raw_db_dir != ".":
                expected_db = Path(raw_db_dir) / db_filename
            else:
                expected_db = get_default_db_path()

            if not expected_db.exists():
                with ui.column().classes("w-full items-center justify-center q-pa-xl"):
                    with ui.card().classes("q-pa-xl").style("max-width: 560px"):
                        with ui.row().classes("items-center gap-md q-mb-md"):
                            ui.icon("warning", size="lg").classes("text-warning")
                            ui.label("Database not found").classes("text-h5 font-weight-bold")
                        ui.label("The database was not found at:").classes(
                            "text-body2 text-grey-7"
                        )
                        ui.label(str(expected_db)).classes(
                            "text-caption text-grey-5 q-mb-md q-pa-sm bg-grey-9 rounded-borders"
                        )
                        ui.label(
                            "This may mean the database was deleted or moved. "
                            "Your existing annotations may be lost."
                        ).classes("text-body2 q-mb-lg")
                        with ui.row().classes("w-full gap-sm"):
                            ui.button(
                                "Start fresh",
                                icon="refresh",
                                color="primary",
                                on_click=lambda: ui.navigate.to("/setup"),
                            )
                            ui.button(
                                "Settings",
                                icon="settings",
                                on_click=lambda: ui.navigate.to("/settings"),
                            ).props("flat")
                return

            dp = await get_or_create_data_provider()
            if not dp:
                render_uninitialized_state()
                return

            _project_dirs = [Path(d.path) for d in dp.get_project_dirs(get_active_project_id())]
            missing_dirs = [d for d in _project_dirs if not d.exists()]
            if _project_dirs and len(missing_dirs) == len(_project_dirs):
                video_dir_str = str(_project_dirs[0])
                with ui.column().classes("w-full items-center justify-center q-pa-xl"):
                    with ui.card().classes("q-pa-xl").style("max-width: 560px"):
                        with ui.row().classes("items-center gap-md q-mb-md"):
                            ui.icon("folder_off", size="lg").classes("text-warning")
                            ui.label("Video directory not accessible").classes(
                                "text-h5 font-weight-bold"
                            )
                        ui.label("Your configured video directory cannot be found:").classes(
                            "text-body2 text-grey-7"
                        )
                        ui.label(video_dir_str).classes(
                            "text-caption text-grey-5 q-mb-md q-pa-sm bg-grey-9 rounded-borders"
                        )
                        ui.label(
                            "This can happen when an external drive is disconnected. "
                            "You can still access your annotations, but videos will not play."
                        ).classes("text-body2 q-mb-lg")
                        with ui.row().classes("w-full gap-sm"):
                            ui.button(
                                "Continue anyway",
                                icon="play_arrow",
                                color="primary",
                                on_click=lambda: ui.navigate.to("/overview"),
                            )
                            ui.button(
                                "Update path in Settings",
                                icon="settings",
                                on_click=lambda: ui.navigate.to("/settings"),
                            ).props("flat")
                return

            if not await run.io_bound(dp.has_videos_in_db, get_active_project_id()):
                render_uninitialized_state()
                return

            with ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto"):
                with ui.card().classes("full-width q-mb-lg"):
                    ui.label(t("welcome_title")).classes(
                        "text-h5 text-primary font-weight-bold q-mb-md"
                    )
                    ui.label(t("welcome_subtitle")).classes("text-body1 text-grey-7")

                has_videos = await run.io_bound(dp.has_videos_in_db, get_active_project_id())

                with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
                    with ui.card().classes("col"):
                        ui.label(t("db_label")).classes("text-caption text-grey-6")
                        ui.label(str(dp.db_path)).classes("text-body2")
                    with ui.card().classes("col"):
                        ui.label(t("video_dir_label")).classes("text-caption text-grey-6")
                        for _d in [
                            Path(d.path) for d in dp.get_project_dirs(get_active_project_id())
                        ]:
                            ui.label(str(_d)).classes("text-body2")
                    with ui.card().classes("col"):
                        ui.label(t("videos_in_db_label")).classes("text-caption text-grey-6")
                        ui.label(t("yes") if has_videos else t("no")).classes("text-body2")

                if not has_videos:
                    with ui.card().classes("full-width"):
                        ui.label(t("sync_videos_title")).classes("text-body1 q-mb-md")
                        ui.button(
                            t("sync_videos_btn"),
                            icon="sync",
                            on_click=lambda: self._sync_videos(dp),
                            color="primary",
                        )
                else:
                    with ui.row().classes("w-full gap-4"):
                        ui.button(
                            t("go_to_overview_btn"),
                            icon="dashboard",
                            on_click=lambda: ui.navigate.to("/overview"),
                            color="primary",
                        )
                        ui.button(
                            t("start_reviewing_btn"),
                            icon="rate_review",
                            on_click=lambda: ui.navigate.to("/review"),
                            color="secondary",
                        )

        except Exception as e:
            with ui.card().classes("text-center q-pa-xl"):
                ui.label(t("error_loading_config_title")).classes("text-h6 text-negative q-mb-md")
                ui.label(str(e)).classes("text-body2 q-mb-lg")
                ui.button(
                    t("reconfigure_btn"),
                    icon="settings",
                    on_click=lambda: ui.navigate.to("/setup"),
                    color="negative",
                )

    async def overview_page(self):
        from review_app.app.pages.overview import setup_overview

        await setup_overview()

    async def review_page(self):
        from review_app.app.pages.review import setup_review

        await setup_review()

    async def model_import_page(self):
        from review_app.app.pages.model_import import setup_model_import

        await setup_model_import()

    async def settings_page(self):
        from review_app.app.pages.settings import setup_settings

        await setup_settings()

    def setup_page(self):
        from nicegui import ui

        from review_app.app.setup_wizard import setup_wizard

        shared_header()

        def on_setup_complete():
            ui.navigate.to("/overview")

        setup_wizard(on_complete_callback=on_setup_complete, config_path=CONFIG_PATH)

    def _sync_videos(self, data_provider):
        from nicegui import run, ui

        progress = ui.linear_progress(value=0, show_value=False)
        status = ui.label(t("sync_starting"))

        def update_progress(current, total, filename):
            if total > 0:
                progress.value = current / total
                status.text = t("sync_processing", current=current, total=total, filename=filename)
            else:
                status.text = t("sync_scanning")

        async def do_sync():
            await run.io_bound(
                data_provider.sync_videos,
                progress_callback=update_progress,
                active_project_id=get_active_project_id(),
            )
            progress.value = 1.0
            status.text = t("sync_complete")
            ui.notify(t("sync_notify"), type="positive")

        ui.timer(0.1, do_sync, once=True)

    def start(self, dev_mode=False, port=8000):
        from nicegui import app, ui

        @app.on_page_exception
        def custom_error_page(exception: Exception):
            from traceback import format_exc

            with ui.column().classes("w-full h-screen items-center justify-center"):
                ui.icon("error_outline", size="xl").classes("text-negative q-mb-md")
                ui.label(t("something_wrong")).classes("text-h5 text-negative q-mb-sm")
                ui.label(str(exception)).classes("text-body1 q-mb-lg")
                if dev_mode:
                    ui.code(format_exc(chain=False)).classes("q-pa-md")
                ui.button(t("go_home_btn"), on_click=lambda: ui.navigate.to("/"), icon="home")

        ui.page("/")(self.main_page)
        ui.page("/overview")(self.overview_page)
        ui.page("/review")(self.review_page)
        ui.page("/setup")(self.setup_page)
        ui.page("/model-import")(self.model_import_page)
        ui.page("/settings")(self.settings_page)

        # Load config and register media files at startup (before ui.run)
        if CONFIG_PATH.exists():
            try:
                cfg = load_config()
                init_user_prefs(
                    dark_mode=cfg.get("dark_mode", True),
                    language=cfg.get("language", "en"),
                    annotator_name=cfg.get("annotator_name", "default"),
                    blank_threshold=cfg.get("blank_threshold", 0.75),
                    species_threshold=cfg.get("species_threshold", 0.75),
                )

                dp = LocalDataProvider(str(CONFIG_PATH))
                set_data_provider(dp)
                self.dp = dp
                proj = dp.get_most_recent_project()
                if proj:
                    set_active_project(proj.id)
                    dp.touch_project(proj.id)

                # Register all video directories for the active project
                import tempfile

                transcoded_tmp = Path(tempfile.gettempdir()) / "video_review_transcoded"
                transcoded_tmp.mkdir(parents=True, exist_ok=True)
                app.add_media_files("/transcoded", transcoded_tmp)

                set_media_dirs(
                    [Path(d.path) for d in dp.get_project_dirs(get_active_project_id())]
                )
            except Exception as e:
                print(f"Warning: Could not load config at startup: {e}")

        setup_media_route()

        storage_secret = os.environ.get("VIDEO_REVIEW_SECRET")
        if not storage_secret:
            storage_secret = secrets.token_hex(32)

        use_native = not dev_mode
        if use_native:
            try:
                from webview import guilib

                guilib.initialize()
            except Exception as e:
                print(f"Warning: native window unavailable ({e}), falling back to browser mode.")
                use_native = False

        ui.run(
            native=use_native,
            title="Video Annotation",
            host="127.0.0.1",
            port=port,
            show=dev_mode or not use_native,
            reload=dev_mode,
            storage_secret=storage_secret,
        )


if __name__ in ("__main__", "__mp_main__"):
    import multiprocessing

    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="Enable dev mode with auto-reload")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the application on")
    args, _ = parser.parse_known_args()

    gui = GUI()
    gui.start(dev_mode=args.dev, port=args.port)
