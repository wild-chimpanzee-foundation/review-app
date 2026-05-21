import argparse
import logging
import mimetypes
import secrets
import socket
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from review_app.app.media import set_media_dirs, setup_media_route
from review_app.app.project_picker import build_project_picker
from review_app.app.setup_wizard import setup_wizard
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_data_provider,
    get_language,
    is_dark_mode,
    load_settings_from_db,
    set_dark_mode,
    set_data_provider,
    set_language,
)
from review_app.app.theme import apply_theme
from review_app.app.translations import t
from review_app.backend.provider.local_data_provider import LocalDataProvider

logger = logging.getLogger(__name__)


def _setup_logging(user_data_dir: Path, dev_mode: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    fh = RotatingFileHandler(
        user_data_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if dev_mode:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    # Suppress noisy third-party loggers
    logging.getLogger("nicegui").setLevel(logging.ERROR)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))

    def _thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        logger.critical(
            "Unhandled exception in thread %s",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


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
                ui.badge(key).props("color=grey-9").classes("text-caption")

        ui.label(t("shortcuts_global")).classes("text-caption text-grey-5 text-uppercase q-mt-sm")
        ui.separator().classes("q-mb-xs")
        _shortcut_row("Enter", t("shortcut_submit_next"))
        _shortcut_row("N", t("shortcut_next_video"))
        _shortcut_row("P", t("shortcut_prev_video"))
        _shortcut_row("B", t("shortcut_mark_blank"))
        _shortcut_row("M", t("review_later"))
        _shortcut_row("A", t("shortcut_add_species"))
        _shortcut_row("C", t("shortcut_clear_annotations"))
        _shortcut_row("1 – 9", t("shortcut_add_ai"))
        _shortcut_row("J / K", t("shortcut_next_annotation"))
        _shortcut_row("Tab", t("shortcut_enter_annotation"))
        _shortcut_row("X", t("shortcut_delete_annotation"))
        _shortcut_row("T", t("shortcut_focus_tags"))

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
                _header_dp = get_data_provider()
                _active_proj_name = (
                    p.name
                    if (_header_dp and (p := _header_dp.get_project(_active_pid)))
                    else _active_pid
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

                annotator = get_annotator_name()
                if annotator:
                    with (
                        ui.button(icon="person").props("flat round color=white").tooltip(annotator)
                    ):
                        with ui.menu().props("auto-close"):
                            ui.menu_item(annotator).props("disabled")
                            ui.separator()

                            def _do_logout():
                                from review_app.app.state import clear_session

                                clear_session(keep_prefs=True)
                                ui.navigate.to("/login")

                            ui.menu_item(t("login_logout"), on_click=_do_logout)

    return drawer, toggle_drawer


def _check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise OSError(
                f"Port {port} is already in use. Another instance may still be running. "
                f"Close it or pass --port to use a different port."
            )


class GUI:
    def __init__(self):
        self.dp = None
        self._startup_error: Exception | None = None

    def main_page(self):
        from nicegui import app, ui

        if self._startup_error:
            ui.navigate.to("/db-error")
        elif not app.storage.user.get("annotator_name"):
            ui.navigate.to("/login")
        elif get_active_project_id():
            ui.navigate.to("/overview")
        else:
            ui.navigate.to("/setup")

    def login_page(self):
        from review_app.app.pages.login import setup_login

        setup_login()

    def db_error_page(self):
        from nicegui import ui

        from review_app.app.config import get_default_db_path, get_user_data_dir

        shared_header()

        err = self._startup_error

        with (
            ui.column()
            .classes("w-full items-center justify-center q-pa-xl gap-4")
            .style("min-height: 80vh")
        ):
            ui.icon("error_outline", size="xl").classes("text-negative")
            ui.label("Could not open database").classes("text-h5 text-negative")
            ui.label(
                "The database failed to load, possibly due to a failed migration or corruption."
            ).classes("text-body1 text-grey-7 text-center")
            if err:
                ui.code(str(err)).classes("q-pa-md text-caption").style(
                    "max-width: 700px; white-space: pre-wrap; word-break: break-all"
                )

            ui.separator().style("max-width: 500px; width: 100%")
            ui.label(
                "You can delete the database and start fresh. "
                "A backup of the current database will be kept in the data folder."
            ).classes("text-body2 text-grey-6 text-center").style("max-width: 500px")

            def delete_and_restart():
                from review_app.backend.db.backup import quarantine_broken_db

                try:
                    quarantined = quarantine_broken_db()
                    if quarantined:
                        logger.info("Quarantined broken DB to %s", quarantined)
                except Exception:
                    logger.exception("quarantine_broken_db failed; deleting live DB anyway")
                    db_path = get_default_db_path()
                    if db_path.exists():
                        db_path.unlink()
                    from review_app.backend.db.backup import remove_db_sidecars

                    remove_db_sidecars(db_path)
                self._startup_error = None
                ui.navigate.to("/setup")

            with ui.row().classes("gap-4 q-mt-sm"):
                ui.button(
                    "Open data folder",
                    icon="folder_open",
                    on_click=lambda: ui.run_javascript(
                        f"window.open('file://{get_user_data_dir()}', '_blank')"
                    ),
                ).props("flat")
                ui.button(
                    "Delete database and start fresh",
                    icon="delete_forever",
                    on_click=delete_and_restart,
                ).props("color=negative")

    async def overview_page(self):
        from review_app.app.utils import require_login

        if not require_login():
            return
        from review_app.app.pages.overview import setup_overview

        await setup_overview()

    async def review_page(self):
        from review_app.app.utils import require_login

        if not require_login():
            return
        from review_app.app.pages.review import setup_review

        await setup_review()

    async def model_import_page(self):
        from review_app.app.utils import require_login

        if not require_login():
            return
        from review_app.app.pages.model_import import setup_model_import

        await setup_model_import()

    async def settings_page(self):
        from review_app.app.utils import require_login

        if not require_login():
            return
        from review_app.app.pages.settings import setup_settings

        await setup_settings()

    def setup_page(self):
        from nicegui import ui

        from review_app.app.utils import require_login

        if not require_login():
            return
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

    def start(self, dev_mode=False, port=8000, host="127.0.0.1"):
        from nicegui import app, ui

        from review_app.app.config import get_user_data_dir as _get_udd

        _udd = _get_udd()
        _udd.mkdir(parents=True, exist_ok=True)
        _setup_logging(_udd, dev_mode)

        logger.info("Starting (dev=%s, port=%d, host=%s, data_dir=%s)", dev_mode, port, host, _udd)

        @app.on_page_exception
        def custom_error_page(exception: Exception):
            from traceback import format_exc

            from review_app.app.utils import user_error_message

            logger.exception("Unhandled page exception: %s", exception)

            with ui.column().classes("w-full h-screen items-center justify-center"):
                ui.icon("error_outline", size="xl").classes("text-negative q-mb-md")
                ui.label(t("something_wrong")).classes("text-h5 text-negative q-mb-sm")
                ui.label(user_error_message(exception)).classes("text-body1 q-mb-lg")
                if dev_mode:
                    ui.code(format_exc(chain=False)).classes("q-pa-md")
                ui.button(t("go_home_btn"), on_click=lambda: ui.navigate.to("/"), icon="home")

        ui.page("/")(self.main_page)
        ui.page("/login")(self.login_page)
        ui.page("/db-error")(self.db_error_page)
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

                from review_app.backend.db.backup import BackupError, create_backup

                try:
                    create_backup(reason="startup")
                except BackupError:
                    pass

                # Determine and persist the global default project in the DB.
                # Per-user active project is loaded at login via load_session_defaults().
                _default_pid = dp.get_setting("active_project_id")
                if not (_default_pid and dp.get_project(_default_pid)):
                    proj = dp.get_most_recent_project()
                    if proj:
                        dp.set_setting("active_project_id", proj.id)
                        dp.touch_project(proj.id)
                        _default_pid = proj.id

                logger.info("Default project: %s", _default_pid or "none")

                # Load media dirs from all projects so any user can serve their videos.
                all_dirs = [
                    Path(d.path)
                    for proj in dp.list_projects()
                    for d in dp.get_project_dirs(proj.id)
                ]
                set_media_dirs(all_dirs)
            except Exception as e:
                logger.exception("Could not initialize data provider at startup")
                self._startup_error = e
        else:
            logger.info("No existing database found — fresh install, launching setup wizard")

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
            logger.info("Shutting down")
            if self.dp and self.dp.engine:
                from review_app.backend.db.backup import BackupError, create_backup

                try:
                    create_backup(reason="shutdown")
                except BackupError:
                    pass

        ui.run(
            title="Video Annotation",
            host=host,
            port=port,
            show=True,
            reload=dev_mode,
            storage_secret=storage_secret,
            session_middleware_kwargs={"max_age": 90 * 24 * 60 * 60},
        )


if __name__ in {"__main__", "__mp_main__"}:
    from multiprocessing import freeze_support

    freeze_support()

    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="Enable dev mode with auto-reload")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the application on")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind to (use 0.0.0.0 for network access)"
    )
    args, _ = parser.parse_known_args()

    gui = GUI()
    try:
        if __name__ == "__main__":
            _check_port(args.port)
        gui.start(dev_mode=args.dev, port=args.port, host=args.host)
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(1) from None
