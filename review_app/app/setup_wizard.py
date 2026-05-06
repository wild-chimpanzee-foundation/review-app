import platform
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path

from nicegui import run, ui

from review_app.app.config import get_default_db_path
from review_app.app.state import get_language
from review_app.app.translations import t
from review_app.app.utils import sync_with_progress

FFMPEG_INSTALL_MAC = "brew install ffmpeg"
FFMPEG_INSTALL_WINDOWS = "winget install ffmpeg OR download from https://ffmpeg.org/download.html"
FFMPEG_INSTALL_LINUX = "sudo apt install ffmpeg"


def validate_video_dir(path: str) -> tuple[str, dict] | None:
    from review_app.app.config import VIDEO_EXTENSIONS

    p = Path(path)
    if not p.exists():
        return ("video_dir_not_exist", {})
    if not p.is_dir():
        return ("video_dir_not_a_dir", {})
    has_files = False
    for child in p.rglob("*"):
        if child.is_file():
            has_files = True
            if child.suffix.lower() in VIDEO_EXTENSIONS:
                return None
    if not has_files:
        return ("video_dir_empty", {})
    return ("video_dir_no_videos", {"exts": ", ".join(sorted(VIDEO_EXTENSIONS))})


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


def _db_has_projects(db_path: Path) -> bool:
    try:
        con = sqlite3.connect(str(db_path))
        count = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        con.close()
        return count > 0
    except Exception:
        return False


class SetupWizard:
    def __init__(self, on_complete_callback):
        self.on_complete_callback = on_complete_callback
        self.ffmpeg_ok = False
        self.inputs = {}

    def build(self):
        db_path = get_default_db_path()
        adding_to_existing = db_path.exists() and _db_has_projects(db_path)

        annotator_name_cell: list[str] = [""]
        continue_btn_holder: list = [None]
        project_btn_holder: list = [None]

        # ── Shared helpers ────────────────────────────────────────────────────

        def update_continue_button():
            btn = continue_btn_holder[0]
            if btn is not None:
                name_ok = bool(
                    self.inputs.get("annotator_name")
                    and self.inputs["annotator_name"].value.strip()
                )
                btn.set_enabled(name_ok and self.ffmpeg_ok)

        def update_project_button():
            btn = project_btn_holder[0]
            if btn is not None:
                btn.set_enabled(
                    bool(
                        self.inputs.get("video_dir")
                        and self.inputs["video_dir"].value.strip()
                        and self.inputs.get("project_name")
                        and self.inputs["project_name"].value.strip()
                    )
                )

        def on_video_dir_change(e):
            update_project_button()
            if not self.inputs["project_name"].value.strip():
                path = e.value.strip()
                if path:
                    self.inputs["project_name"].set_value(Path(path).name)

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
            update_continue_button()

        async def submit_project():
            project_btn_holder[0].set_enabled(False)

            video_dir = self.inputs["video_dir"].value.strip()
            project_name = (
                self.inputs["project_name"].value.strip() or Path(video_dir).name or "My Project"
            )

            if not video_dir:
                ui.notify(t("enter_video_dir"), type="warning")
                update_project_button()
                return
            dir_error = await run.io_bound(validate_video_dir, video_dir)
            if dir_error:
                key, kwargs = dir_error
                ui.notify(t(key, **kwargs), type="negative", timeout=6000)
                update_project_button()
                return

            from review_app.app.state import (
                get_active_project_id,
                load_settings_from_db,
                save_user_prefs_to_db,
                set_annotator_name,
                set_data_provider,
            )
            from review_app.app.utils import switch_project
            from review_app.backend.provider.local_data_provider import LocalDataProvider

            dp = LocalDataProvider()
            set_data_provider(dp)
            if adding_to_existing:
                load_settings_from_db(dp)
            else:
                save_user_prefs_to_db(dp)
                set_annotator_name(annotator_name_cell[0])

            project = dp.create_project(project_name, video_dir)
            switch_project(dp, project.id)

            has_videos = await run.io_bound(dp.has_videos_in_db, get_active_project_id())

            if not has_videos:
                project_btn_holder[0].visible = False

                dialog = ui.dialog().props("persistent")
                with dialog, ui.card().classes("q-pa-lg").style("min-width: 400px"):
                    ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                    progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
                    status = ui.label(t("starting")).classes("text-caption text-grey-6 q-mt-sm")
                    post_sync = ui.column().classes("w-full q-mt-md gap-sm")
                    post_sync.visible = False
                    with post_sync:
                        ui.separator()
                        ui.label(t("wizard_import_suggestion")).classes("text-body2 q-mt-xs")
                        ui.button(
                            t("wizard_go_to_import_btn"),
                            icon="upload_file",
                            color="primary",
                            on_click=lambda: (dialog.close(), ui.navigate.to("/model-import")),
                        ).props("size=lg").classes("full-width")
                        ui.button(
                            t("go_to_overview_btn"),
                            icon="play_arrow",
                            on_click=lambda: (dialog.close(), self.on_complete_callback()),
                        ).props("size=lg flat").classes("full-width")

                dialog.open()
                stats = await sync_with_progress(
                    dp,
                    progress=progress,
                    status=status,
                    video_dir=video_dir,
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
                post_sync.visible = True
            else:
                self.on_complete_callback()

        # ── Layout ────────────────────────────────────────────────────────────

        with ui.column().classes("w-full q-pa-lg").style("max-width: 720px; margin: 0 auto"):
            if not adding_to_existing:
                # ── Step 1: language, annotator, ffmpeg ───────────────────────
                with ui.column().classes("w-full gap-0") as step1:
                    with ui.card().classes("full-width q-mb-lg"):
                        with ui.row().classes("w-full items-start justify-between"):
                            with ui.column().classes("col"):
                                ui.label(t("welcome_setup")).classes(
                                    "text-h4 text-primary font-weight-bold"
                                )
                                ui.label(t("welcome_setup_msg")).classes("text-body1 text-grey-7")

                            def change_language(e):
                                from review_app.app.translations import set_language

                                set_language(e.value)
                                ui.run_javascript("window.location.reload()")

                            ui.select(
                                options={"en": t("lang_en"), "fr": t("lang_fr")},
                                value=get_language(),
                                on_change=change_language,
                            ).props("dense outlined").classes("q-mt-xs")

                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("annotator_label")).classes(
                            "text-subtitle1 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("annotator_setup_desc")).classes(
                            "text-caption text-grey-6 q-mb-md"
                        )
                        self.inputs["annotator_name"] = ui.input(
                            placeholder=t("annotator_name_placeholder"),
                        ).props("outlined dense class=w-full")
                        self.inputs["annotator_name"].on_value_change(
                            lambda e: update_continue_button()
                        )

                    with ui.card().classes("full-width q-mb-md"):
                        with ui.row().classes("items-center gap-sm"):
                            ui.label(t("ffmpeg_label")).classes(
                                "text-subtitle1 font-weight-medium"
                            )
                            ffmpeg_status_label = ui.label(t("ffmpeg_checking")).classes(
                                "text-caption text-grey-6"
                            )
                        ui.label(t("ffmpeg_desc")).classes("text-caption text-grey-6 q-mt-xs")

                    ffmpeg_install_card = ui.card().classes(
                        "full-width q-mb-md bg-negative text-white"
                    )
                    ffmpeg_install_card.visible = False
                    with ffmpeg_install_card:
                        ui.label(t("ffmpeg_not_found_title")).classes(
                            "text-subtitle1 font-weight-bold q-mb-xs"
                        )
                        ui.label(t("ffmpeg_install_instructions")).classes("text-caption q-mb-sm")
                        ui.code(get_ffmpeg_install_cmd()).classes("full-width")

                    continue_btn_holder[0] = (
                        ui.button(t("tour_next"), icon="arrow_forward", color="primary")
                        .props("size=lg")
                        .classes("full-width")
                    )
                    continue_btn_holder[0].set_enabled(False)

                ui.timer(0, do_check_ffmpeg, once=True)

                # ── Step 1.5: fresh start vs restore choice ───────────────────
                with ui.column().classes("w-full gap-0") as step_choice:
                    step_choice.visible = False

                    with ui.card().classes("full-width q-mb-lg"):
                        ui.label(t("wizard_start_choice")).classes(
                            "text-h5 text-primary font-weight-bold"
                        )

                    with ui.row().classes("w-full gap-md"):
                        with (
                            ui.card()
                            .classes("col cursor-pointer q-pa-md")
                            .props("bordered flat") as fresh_card
                        ):
                            with ui.column().classes("items-center text-center gap-sm"):
                                ui.icon("add_circle_outline", size="3rem").classes("text-primary")
                                ui.label(t("wizard_fresh_start")).classes(
                                    "text-subtitle1 font-weight-bold"
                                )
                                ui.label(t("wizard_fresh_start_desc")).classes(
                                    "text-caption text-grey-6"
                                )

                        with (
                            ui.card()
                            .classes("col cursor-pointer q-pa-md")
                            .props("bordered flat") as restore_card
                        ):
                            with ui.column().classes("items-center text-center gap-sm"):
                                ui.icon("settings_backup_restore", size="3rem").classes(
                                    "text-primary"
                                )
                                ui.label(t("wizard_restore_start")).classes(
                                    "text-subtitle1 font-weight-bold"
                                )
                                ui.label(t("wizard_restore_start_desc")).classes(
                                    "text-caption text-grey-6"
                                )

                # ── Step 2: first project ─────────────────────────────────────
                with ui.column().classes("w-full gap-0") as step2:
                    step2.visible = False

                    with ui.card().classes("full-width q-mb-lg"):
                        ui.label(t("new_project")).classes("text-h5 text-primary font-weight-bold")

                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("project_name_label")).classes(
                            "text-subtitle1 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("project_name_desc")).classes(
                            "text-caption text-grey-6 q-mb-md"
                        )
                        self.inputs["project_name"] = ui.input(
                            placeholder=t("project_name_placeholder"),
                        ).props("outlined dense class=w-full")
                        self.inputs["project_name"].on_value_change(
                            lambda e: update_project_button()
                        )

                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("video_dir_label")).classes(
                            "text-subtitle1 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("video_dir_desc")).classes("text-caption text-grey-6 q-mb-md")
                        self.inputs["video_dir"] = ui.input(
                            placeholder=t("video_dir_placeholder"),
                        ).props("outlined dense class=w-full")
                        self.inputs["video_dir"].on_value_change(on_video_dir_change)

                    project_btn_holder[0] = (
                        ui.button(
                            t("sync_videos_title"),
                            on_click=submit_project,
                            icon="play_arrow",
                            color="primary",
                        )
                        .props("size=lg")
                        .classes("full-width")
                    )
                    project_btn_holder[0].set_enabled(False)

                # ── Step 3: restore flow ──────────────────────────────────────
                with ui.column().classes("w-full gap-0") as step_restore:
                    step_restore.visible = False

                    with ui.card().classes("full-width q-mb-lg"):
                        ui.label(t("wizard_restore_title")).classes(
                            "text-h5 text-primary font-weight-bold"
                        )
                        ui.label(t("wizard_restore_desc")).classes(
                            "text-body2 text-grey-7 q-mt-xs"
                        )

                    restore_list_col = ui.column().classes("w-full gap-xs q-mb-md")
                    restore_status_label: list = [None]

                    async def do_wizard_restore(backup_path: Path):
                        from review_app.app.config import get_default_db_path
                        from review_app.app.media import set_media_dirs
                        from review_app.app.state import (
                            load_settings_from_db,
                            set_active_project,
                            set_data_provider,
                        )
                        from review_app.backend.db.backup import (
                            RestoreSchemaVersionError,
                            _check_restore_schema_version,
                        )
                        from review_app.backend.db.migrations import MIGRATIONS
                        from review_app.backend.provider.local_data_provider import (
                            LocalDataProvider,
                        )

                        lbl = restore_status_label[0]
                        if lbl:
                            lbl.set_text(t("starting"))
                            lbl.visible = True

                        try:
                            await run.io_bound(
                                _check_restore_schema_version, backup_path, len(MIGRATIONS)
                            )
                        except RestoreSchemaVersionError:
                            msg = t("restore_error_schema_version")
                            if lbl:
                                lbl.set_text(msg)
                            ui.notify(msg, type="negative", timeout=8000)
                            return

                        db_path = get_default_db_path()
                        db_path.parent.mkdir(parents=True, exist_ok=True)

                        try:
                            await run.io_bound(shutil.copy2, backup_path, db_path)
                        except Exception as exc:
                            if lbl:
                                lbl.set_text(t("restore_failed", error=str(exc)))
                            ui.notify(t("restore_failed", error=str(exc)), type="negative")
                            return

                        try:
                            dp = LocalDataProvider()
                            set_data_provider(dp)
                            load_settings_from_db(dp)
                            proj = dp.get_most_recent_project()
                            if proj:
                                set_active_project(proj.id)
                                dp.touch_project(proj.id)
                            set_media_dirs(
                                [
                                    Path(d.path)
                                    for d in dp.get_project_dirs(proj.id if proj else None)
                                ]
                            )
                        except Exception as exc:
                            if lbl:
                                lbl.set_text(t("restore_failed", error=str(exc)))
                            ui.notify(t("restore_failed", error=str(exc)), type="negative")
                            return

                        ui.notify(t("wizard_restore_success"), type="positive")
                        self.on_complete_callback()

                    async def _handle_backup_upload(e):
                        from review_app.backend.db.backup import get_backup_dir

                        content = await e.file.read()
                        tmp_path = get_backup_dir() / f"uploaded_restore_{uuid.uuid4().hex}.db"
                        tmp_path.parent.mkdir(parents=True, exist_ok=True)
                        tmp_path.write_bytes(content)
                        try:
                            await do_wizard_restore(tmp_path)
                        finally:
                            tmp_path.unlink(missing_ok=True)

                    async def populate_restore_list():
                        from review_app.backend.db.backup import list_backups

                        backups = await run.io_bound(list_backups)
                        restore_list_col.clear()
                        with restore_list_col:
                            if backups:
                                ui.label(t("restore_confirm")).classes("text-subtitle2 q-mb-xs")
                                with (
                                    ui.column()
                                    .classes("w-full gap-xs")
                                    .style("max-height: 260px; overflow-y: auto")
                                ):
                                    for b in backups:
                                        ts = b["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                                        label = f"{ts}  ({b['size_mb']} MB)"

                                        def _make_restore(p):
                                            async def _do():
                                                await do_wizard_restore(p)

                                            return _do

                                        ui.button(
                                            label,
                                            icon="restore",
                                            on_click=_make_restore(b["path"]),
                                        ).props("flat dense align-left").classes("w-full")

                                with ui.row().classes("w-full gap-sm items-center q-mt-md"):
                                    ui.separator().classes("col")
                                    ui.label(t("upload_backup")).classes(
                                        "text-caption text-grey-6"
                                    )
                                    ui.separator().classes("col")

                            backup_uploader = (
                                ui.upload(on_upload=_handle_backup_upload, auto_upload=True)
                                .props("accept=.db")
                                .style("display: none")
                            )
                            ui.button(
                                t("upload_backup_btn"),
                                icon="upload_file",
                                color="primary" if not backups else None,
                                on_click=lambda: ui.run_javascript(
                                    f"document.getElementById('c{backup_uploader.id}').querySelector('.q-uploader__input').click()"
                                ),
                            ).props("size=lg").classes("full-width")

                            restore_status_label[0] = ui.label("").classes(
                                "text-caption text-grey-6 q-mt-xs"
                            )
                            restore_status_label[0].visible = False

                def go_to_step_choice():
                    annotator_name_cell[0] = self.inputs["annotator_name"].value.strip()
                    step1.visible = False
                    step_choice.visible = True

                def go_to_step2():
                    step_choice.visible = False
                    step2.visible = True

                async def go_to_restore():
                    step_choice.visible = False
                    step_restore.visible = True
                    await populate_restore_list()

                fresh_card.on("click", go_to_step2)
                restore_card.on("click", go_to_restore)
                continue_btn_holder[0].on_click(go_to_step_choice)

            else:
                # ── Add-project flow: project creation only ────────────────────
                with ui.card().classes("full-width q-mb-lg"):
                    ui.label(t("new_project")).classes("text-h5 text-primary font-weight-bold")

                with ui.card().classes("full-width q-mb-md"):
                    ui.label(t("project_name_label")).classes(
                        "text-subtitle1 font-weight-medium q-mb-xs"
                    )
                    ui.label(t("project_name_desc")).classes("text-caption text-grey-6 q-mb-md")
                    self.inputs["project_name"] = ui.input(
                        placeholder=t("project_name_placeholder"),
                    ).props("outlined dense class=w-full")
                    self.inputs["project_name"].on_value_change(lambda e: update_project_button())

                with ui.card().classes("full-width q-mb-md"):
                    ui.label(t("video_dir_label")).classes(
                        "text-subtitle1 font-weight-medium q-mb-xs"
                    )
                    ui.label(t("video_dir_desc")).classes("text-caption text-grey-6 q-mb-md")
                    self.inputs["video_dir"] = ui.input(
                        placeholder=t("video_dir_placeholder"),
                    ).props("outlined dense class=w-full")
                    self.inputs["video_dir"].on_value_change(on_video_dir_change)

                project_btn_holder[0] = (
                    ui.button(
                        t("sync_videos_title"),
                        on_click=submit_project,
                        icon="play_arrow",
                        color="primary",
                    )
                    .props("size=lg")
                    .classes("full-width")
                )
                project_btn_holder[0].set_enabled(False)


def setup_wizard(on_complete_callback):
    wizard = SetupWizard(on_complete_callback)
    wizard.build()
