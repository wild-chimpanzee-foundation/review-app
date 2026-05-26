import io
import json
import platform
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path

from nicegui import app, run, ui

from review_app.app.config import get_default_db_path
from review_app.app.state import get_language, set_language
from review_app.app.translations import t
from review_app.app.utils import format_utc_timestamp, sync_with_progress

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


_HOMEBREW_PATHS = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")


def find_ffmpeg() -> str | None:
    """Return the ffmpeg executable path, checking Homebrew locations as fallback."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    # PyInstaller strips PATH on macOS; check known Homebrew install locations
    for candidate in _HOMEBREW_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def check_ffmpeg() -> bool:
    path = find_ffmpeg()
    if not path:
        return False
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=5)
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

        continue_btn_holder: list = [None]
        project_btn_holder: list = [None]
        collection_select_holder: list = [None]
        bundle_bytes: list = [None]
        bundle_btn_holder: list = [None]
        bundle_preview_label: list = [None]

        # ── Shared helpers ────────────────────────────────────────────────────

        def update_continue_button():
            btn = continue_btn_holder[0]
            if btn is not None:
                btn.set_enabled(self.ffmpeg_ok)

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

        def update_bundle_button():
            btn = bundle_btn_holder[0]
            if btn is not None:
                has_bundle = bundle_bytes[0] is not None
                has_dir = bool(
                    self.inputs.get("bundle_video_dir")
                    and self.inputs["bundle_video_dir"].value.strip()
                )
                btn.set_enabled(has_bundle and has_dir)

        def on_video_dir_change(e):
            update_project_button()
            if not self.inputs["project_name"].value.strip():
                path = e.value.strip()
                if path:
                    self.inputs["project_name"].set_value(Path(path).name)

        def on_bundle_video_dir_change(e):
            update_bundle_button()
            if not self.inputs["bundle_project_name"].value.strip():
                path = e.value.strip()
                if path:
                    self.inputs["bundle_project_name"].set_value(Path(path).name)

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

        async def handle_bundle_upload(e):
            raw = await e.file.read()
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    manifest = json.loads(z.read("bundle.json"))
                    contents = manifest.get("contents", [])
                comp_names = {
                    "species": t("bundle_component_species"),
                    "tags": t("bundle_component_tags"),
                    "model_annotations": t("bundle_component_model_annotations"),
                    "metadata": t("bundle_component_metadata"),
                }
                labels = [comp_names.get(c, c) for c in contents]
                preview_text = (
                    t("wizard_bundle_preview", contents=", ".join(labels)) if labels else ""
                )
                bundle_bytes[0] = raw
                if bundle_preview_label[0]:
                    bundle_preview_label[0].set_text(preview_text)
                    bundle_preview_label[0].classes("text-positive", remove="text-negative")
            except Exception:
                bundle_bytes[0] = None
                if bundle_preview_label[0]:
                    bundle_preview_label[0].set_text(t("wizard_bundle_invalid"))
                    bundle_preview_label[0].classes("text-negative", remove="text-positive")
            update_bundle_button()

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
                load_session_defaults,
                load_settings_from_db,
                set_data_provider,
            )
            from review_app.app.utils import switch_project
            from review_app.backend.provider.local_data_provider import LocalDataProvider

            dp = LocalDataProvider()
            set_data_provider(dp)
            if adding_to_existing:
                load_settings_from_db(dp)
            else:
                load_session_defaults(dp)

            project = dp.create_project(project_name, video_dir)
            collection_id = (
                collection_select_holder[0].value if collection_select_holder[0] else None
            ) or None
            if collection_id:
                await run.io_bound(dp.set_project_collection, project.id, collection_id)
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

        async def submit_bundle_project():
            if not bundle_bytes[0]:
                ui.notify(t("wizard_bundle_no_file"), type="warning")
                return

            video_dir = self.inputs["bundle_video_dir"].value.strip()
            if not video_dir:
                ui.notify(t("enter_video_dir"), type="warning")
                return

            dir_error = await run.io_bound(validate_video_dir, video_dir)
            if dir_error:
                key, kwargs = dir_error
                ui.notify(t(key, **kwargs), type="negative", timeout=6000)
                return

            project_name = (
                self.inputs["bundle_project_name"].value.strip()
                or Path(video_dir).name
                or "My Project"
            )

            bundle_btn_holder[0].set_enabled(False)

            from review_app.app.state import (
                get_active_project_id,
                load_session_defaults,
                load_settings_from_db,
                set_data_provider,
            )
            from review_app.app.utils import switch_project
            from review_app.backend.provider.local_data_provider import LocalDataProvider

            dp = LocalDataProvider()
            set_data_provider(dp)
            if adding_to_existing:
                load_settings_from_db(dp)
            else:
                load_session_defaults(dp)

            # ── Resolve annotators before creating the project ────────────────
            annotator_map = None
            bundle_annotators = await run.io_bound(dp.get_bundle_annotators, bundle_bytes[0])
            if bundle_annotators:
                existing = await run.io_bound(dp.get_all_annotators)
                logged_in = app.storage.user.get("annotator_name", "")
                select_opts = list(existing) + (
                    [logged_in] if logged_in and logged_in not in existing else []
                )
                resolve_dlg = ui.dialog().props("persistent")
                state = {"confirmed": False}
                rows = []

                _NEW = "__new__"
                with resolve_dlg, ui.card().classes("q-pa-lg").style("min-width: 580px"):
                    ui.label(t("bundle_annotator_check_title")).classes("text-h6 q-mb-sm")
                    ui.label(t("bundle_annotator_check_desc_all")).classes(
                        "text-caption text-grey-6 q-mb-md"
                    )

                    for name in bundle_annotators:
                        if name in existing:
                            default = name
                        elif select_opts:
                            default = logged_in if logged_in in select_opts else select_opts[0]
                        else:
                            default = _NEW
                        opts = {_NEW: t("bundle_annotator_keep_as", name=name)} | {
                            a: a for a in select_opts
                        }
                        with ui.row().classes("items-center gap-md w-full q-mb-sm"):
                            ui.label(name).classes("text-body2 text-bold").style(
                                "min-width: 140px"
                            )
                            ui.icon("arrow_forward").classes("text-grey-5")
                            sel = (
                                ui.select(opts, value=default)
                                .props("outlined dense")
                                .classes("flex-1")
                            )
                            rows.append({"name": name, "select": sel})

                    def _confirm():
                        state["confirmed"] = True
                        resolve_dlg.close()

                    with ui.row().classes("q-mt-md gap-sm justify-end"):
                        ui.button(t("cancel"), on_click=resolve_dlg.close).props("flat")
                        ui.button(t("confirm"), on_click=_confirm, color="primary")

                resolve_dlg.open()
                await resolve_dlg

                if not state["confirmed"]:
                    bundle_btn_holder[0].set_enabled(True)
                    return

                annotator_map = {}
                for row in rows:
                    val = row["select"].value
                    annotator_map[row["name"]] = row["name"] if val == _NEW else val

            # ── Create project and sync videos ────────────────────────────────
            project = dp.create_project(project_name, video_dir)
            switch_project(dp, project.id)

            dialog = ui.dialog().props("persistent")
            with dialog, ui.card().classes("q-pa-lg").style("min-width: 400px"):
                ui.label(t("syncing_videos_label")).classes("text-h6 q-mb-md")
                progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
                status = ui.label(t("starting")).classes("text-caption text-grey-6 q-mt-sm")
                result_col = ui.column().classes("w-full q-mt-md gap-sm")
                result_col.visible = False

            dialog.open()
            stats = await sync_with_progress(
                dp,
                progress=progress,
                status=status,
                video_dir=video_dir,
                active_project_id=get_active_project_id(),
            )
            status.text = t("sync_complete")

            bundle_summary = None
            try:
                results = await run.io_bound(
                    dp.import_project_bundle, project.id, bundle_bytes[0], annotator_map
                )
                parts = []
                comp_names = {
                    "species": t("bundle_component_species"),
                    "tags": t("bundle_component_tags"),
                    "model_annotations": t("bundle_component_model_annotations"),
                    "metadata": t("bundle_component_metadata"),
                }
                for comp_key, label in comp_names.items():
                    if comp_key in results and "error" not in results[comp_key]:
                        n = (
                            results[comp_key].get("imported")
                            or results[comp_key].get("updated")
                            or 0
                        )
                        errors = results[comp_key].get("errors", 0)
                        entry = f"{label}: {n}"
                        if errors:
                            entry += f" ({errors} unmatched)"
                        parts.append(entry)
                bundle_summary = ", ".join(parts) if parts else "—"
            except Exception as exc:
                from review_app.app.utils import user_error_message

                ui.notify(t("bundle_error", msg=user_error_message(exc)), type="negative")

            with result_col:
                ui.separator()
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
                if bundle_summary:
                    ui.label(t("wizard_bundle_imported", summary=bundle_summary)).classes(
                        "text-caption text-positive q-mt-xs"
                    )
                ui.separator()
                ui.button(
                    t("go_to_overview_btn"),
                    icon="play_arrow",
                    on_click=lambda: (dialog.close(), self.on_complete_callback()),
                ).props("size=lg").classes("full-width")

            result_col.visible = True

        def render_project_tabs():
            with ui.tabs().classes("w-full q-mb-md") as tabs:
                ui.tab(name="manual", label=t("wizard_setup_tab_manual"), icon="edit")
                ui.tab(name="bundle", label=t("wizard_setup_tab_bundle"), icon="inventory_2")

            with ui.tab_panels(tabs, value="manual").classes("w-full"):
                with ui.tab_panel("manual"):
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
                            lambda _: update_project_button()
                        )

                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("project_collection_label")).classes(
                            "text-subtitle1 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("project_collection_desc")).classes(
                            "text-caption text-grey-6 q-mb-md"
                        )
                        from review_app.backend.provider.local_data_provider import (
                            LocalDataProvider as _LDP,
                        )

                        _colls = _LDP().list_collections()
                        _coll_opts = {"": t("no_collection")} | {
                            c["id"]: c["name"] for c in _colls
                        }
                        collection_select_holder[0] = ui.select(
                            options=_coll_opts,
                            value="",
                        ).props("outlined dense class=w-full")

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

                with ui.tab_panel("bundle"):
                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("wizard_bundle_import_label")).classes(
                            "text-subtitle1 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("wizard_bundle_import_desc")).classes(
                            "text-caption text-grey-6 q-mb-sm"
                        )
                        ui.upload(
                            on_upload=handle_bundle_upload,
                            multiple=False,
                            label=t("wizard_bundle_import_btn"),
                            auto_upload=True,
                        ).props("accept=.zip flat color=primary").classes("full-width")
                        bundle_preview_label[0] = ui.label("").classes("text-caption q-mt-xs")

                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("video_dir_label")).classes(
                            "text-subtitle1 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("video_dir_desc")).classes("text-caption text-grey-6 q-mb-md")
                        self.inputs["bundle_video_dir"] = ui.input(
                            placeholder=t("video_dir_placeholder"),
                        ).props("outlined dense class=w-full")
                        self.inputs["bundle_video_dir"].on_value_change(on_bundle_video_dir_change)

                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("project_name_label")).classes(
                            "text-subtitle1 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("project_name_desc")).classes(
                            "text-caption text-grey-6 q-mb-md"
                        )
                        self.inputs["bundle_project_name"] = ui.input(
                            placeholder=t("project_name_placeholder"),
                        ).props("outlined dense class=w-full")

                    bundle_btn_holder[0] = (
                        ui.button(
                            t("wizard_bundle_submit_btn"),
                            on_click=submit_bundle_project,
                            icon="inventory_2",
                            color="primary",
                        )
                        .props("size=lg")
                        .classes("full-width")
                    )
                    bundle_btn_holder[0].set_enabled(False)

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
                                set_language(e.value)
                                ui.run_javascript("window.location.reload()")

                            ui.select(
                                options={"en": t("lang_en"), "fr": t("lang_fr")},
                                value=get_language(),
                                on_change=change_language,
                            ).props("dense outlined").classes("q-mt-xs")

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

                    render_project_tabs()

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
                        from review_app.app.media import set_media_dirs
                        from review_app.app.state import (
                            load_session_defaults,
                            load_settings_from_db,
                            set_active_project,
                            set_data_provider,
                        )
                        from review_app.backend.db.backup import BackupError, restore_backup
                        from review_app.backend.db.migrations import MIGRATIONS
                        from review_app.backend.provider.local_data_provider import (
                            LocalDataProvider,
                        )

                        lbl = restore_status_label[0]
                        if lbl:
                            lbl.set_text(t("starting"))
                            lbl.visible = True

                        try:
                            await run.io_bound(restore_backup, backup_path, len(MIGRATIONS))
                        except BackupError as exc:
                            msg = t("restore_failed", error=t(exc.user_message_key))
                            if lbl:
                                lbl.set_text(msg)
                            ui.notify(msg, type="negative", timeout=8000)
                            return
                        except Exception as exc:
                            if lbl:
                                lbl.set_text(t("restore_failed", error=str(exc)))
                            ui.notify(t("restore_failed", error=str(exc)), type="negative")
                            return

                        try:
                            dp = LocalDataProvider()
                            set_data_provider(dp)
                            load_settings_from_db(dp)
                            load_session_defaults(dp)
                            proj = dp.get_most_recent_project()
                            if proj:
                                set_active_project(proj.id)
                                dp.touch_project(proj.id)
                            all_dirs = [
                                Path(d.path)
                                for _p in dp.list_projects()
                                for d in dp.get_project_dirs(_p.id)
                            ]
                            set_media_dirs(all_dirs)
                        except Exception as exc:
                            if lbl:
                                lbl.set_text(t("restore_failed", error=str(exc)))
                            ui.notify(t("restore_failed", error=str(exc)), type="negative")
                            ui.navigate.to("/db-error")
                            return

                        ui.notify(t("wizard_restore_success"), type="positive")
                        self.on_complete_callback()

                    async def _handle_backup_upload(e):
                        import tempfile

                        content = await e.file.read()
                        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                            f.write(content)
                            tmp_path = Path(f.name)
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
                                        ts = format_utc_timestamp(b["timestamp"].isoformat())
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

                render_project_tabs()


def setup_wizard(on_complete_callback):
    wizard = SetupWizard(on_complete_callback)
    wizard.build()
