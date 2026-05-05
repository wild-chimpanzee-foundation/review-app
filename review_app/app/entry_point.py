import argparse
import mimetypes
import secrets
from pathlib import Path

from review_app.app.media import set_media_dirs, setup_media_route
from review_app.app.project_picker import build_project_picker
from review_app.app.setup_wizard import setup_wizard
from review_app.app.state import (
    get_active_project_id,
    get_language,
    is_dark_mode,
    load_settings_from_db,
    set_active_project,
    set_dark_mode,
    set_data_provider,
    set_language,
)
from review_app.app.theme import apply_theme
from review_app.app.translations import t
from review_app.backend.local_data_provider import LocalDataProvider

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


def shared_header(show_drawer: bool = False):
    from nicegui import ui

    apply_theme()

    # Register navigate handler per-page
    def handle_navigate(e):
        ui.navigate.to(e.args)

    ui.on("navigate", handle_navigate)

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
        ui.card().classes("q-pa-lg relative").style("min-width: 420px; max-width: 560px"),
    ):
        ui.button(icon="close", on_click=shortcuts_dialog.close).props("flat round").classes(
            "absolute-top-right q-ma-sm"
        )
        ui.label(t("shortcuts_title")).classes("text-h6 q-mb-md q-mr-lg")

        def _shortcut_row(key: str, label: str):
            with ui.row().classes("w-full items-center justify-between q-py-xs"):
                ui.label(label).classes("text-body2")
                ui.badge(key).props("color=grey-9 outline").classes("text-caption")

        ui.label(t("shortcuts_global")).classes("text-caption text-grey-5 text-uppercase q-mt-sm")
        ui.separator().classes("q-mb-xs")
        _shortcut_row("Enter", t("shortcut_submit_next"))
        _shortcut_row("N", t("shortcut_next_video"))
        _shortcut_row("P", t("shortcut_prev_video"))
        _shortcut_row("B", t("shortcut_mark_blank"))
        _shortcut_row("M", t("review_later"))

        ui.label(t("shortcuts_review")).classes("text-caption text-grey-5 text-uppercase q-mt-md")
        ui.separator().classes("q-mb-xs")
        _shortcut_row("Space", t("shortcut_play_pause"))
        _shortcut_row("← →", t("shortcut_seek"))
        _shortcut_row("S / D", t("shortcut_speed_up_down"))
        _shortcut_row("[ / ]", t("shortcut_brightness"))
        _shortcut_row("{ / }", t("shortcut_contrast"))

        ui.separator().classes("q-mt-md q-mb-sm")

        def _restart_tour():
            from review_app.app.onboarding import show_tour

            shortcuts_dialog.close()
            show_tour(t)

        ui.button(
            t("tour_restart"),
            icon="travel_explore",
            on_click=_restart_tour,
        ).props("flat color=primary").classes("w-full")

    drawer = None

    def toggle_drawer():
        if drawer is not None:
            if "mini" in drawer._props:
                drawer.props(remove="mini")
            else:
                drawer.props("mini")

    if show_drawer:
        drawer = ui.left_drawer(value=True).props("behavior=desktop").classes("q-pa-sm")

        with drawer:
            with ui.row().classes("items-center q-mb-sm"):
                ui.button(icon="menu", on_click=toggle_drawer).props(
                    "flat round color=primary"
                ).tooltip(t("filters_label"))
                ui.label(t("filters_label")).classes(
                    "q-mini-drawer-hide text-subtitle2 font-weight-medium"
                )

    with ui.header().classes("bg-primary text-white"):
        with ui.row().classes("w-full items-center q-px-md no-wrap gap-2"):
            _active_pid = get_active_project_id()
            if _active_pid:
                _header_dp = LocalDataProvider()
                _active_proj_name = (
                    p.name if (p := _header_dp.get_project(_active_pid)) else _active_pid
                )
                project_dialog, refresh_projects = build_project_picker()
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
                with ui.button(icon="menu").props("flat round color=white").classes("lt-md"):
                    with ui.menu().props("auto-close"):
                        ui.menu_item(
                            t("nav_overview"), on_click=lambda: ui.navigate.to("/overview")
                        )
                        ui.menu_item(t("nav_review"), on_click=lambda: ui.navigate.to("/review"))
                        ui.menu_item(
                            t("nav_import"), on_click=lambda: ui.navigate.to("/model-import")
                        )
                        ui.menu_item(
                            t("nav_settings"), on_click=lambda: ui.navigate.to("/settings")
                        )

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

    return drawer, toggle_drawer


class GUI:
    def __init__(self):
        self.dp = None

    def main_page(self):
        from nicegui import ui

        if get_active_project_id():
            ui.navigate.to("/overview")
        else:
            ui.navigate.to("/setup")

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

        shared_header()

        def on_setup_complete():
            ui.navigate.to("/overview")

        setup_wizard(on_complete_callback=on_setup_complete)

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

        # Initialize data provider only if the DB already exists (i.e. app was set up before).
        # On fresh install the DB doesn't exist yet — the setup wizard creates it.
        from review_app.app.config import get_default_db_path, get_user_data_dir

        transcoded_cache = get_user_data_dir() / "transcoded_cache"
        transcoded_cache.mkdir(parents=True, exist_ok=True)
        app.add_media_files("/transcoded", transcoded_cache)

        if get_default_db_path().exists():
            try:
                dp = LocalDataProvider()
                set_data_provider(dp)
                self.dp = dp
                load_settings_from_db(dp)

                from review_app.backend.backup import BackupError, create_backup
                try:
                    create_backup(dp.engine, reason="startup")
                except BackupError:
                    pass

                active_pid = get_active_project_id()
                if active_pid and dp.get_project(active_pid):
                    dp.touch_project(active_pid)
                else:
                    proj = dp.get_most_recent_project()
                    if proj:
                        set_active_project(proj.id)
                        dp.touch_project(proj.id)

                set_media_dirs(
                    [Path(d.path) for d in dp.get_project_dirs(get_active_project_id())]
                )
            except Exception as e:
                print(f"Warning: Could not initialize data provider at startup: {e}")

        setup_media_route()

        from review_app.app.config import get_user_data_dir

        _secret_path = get_user_data_dir() / ".storage_secret"
        if _secret_path.exists():
            storage_secret = _secret_path.read_text().strip()
        else:
            storage_secret = secrets.token_hex(32)
            get_user_data_dir().mkdir(parents=True, exist_ok=True)
            _secret_path.write_text(storage_secret)

        @app.on_shutdown
        def _backup_on_shutdown():
            if self.dp and self.dp.engine:
                from review_app.backend.backup import BackupError, create_backup
                try:
                    create_backup(self.dp.engine, reason="shutdown")
                except BackupError:
                    pass

        ui.run(
            title="Video Annotation",
            favicon=Path(__file__).parent.parent / "data" / "logo.png",
            host="127.0.0.1",
            port=port,
            show=True,
            reload=dev_mode,
            storage_secret=storage_secret,
        )


if __name__ in {"__main__", "__mp_main__"}:
    from multiprocessing import freeze_support

    freeze_support()

    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="Enable dev mode with auto-reload")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the application on")
    args, _ = parser.parse_known_args()

    gui = GUI()
    try:
        gui.start(dev_mode=args.dev, port=args.port)
    except KeyboardInterrupt:
        pass
