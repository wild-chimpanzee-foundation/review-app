from __future__ import annotations

from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import ignore_deleted_client, user_error_message

from ._helpers import col_val, make_col_selects, read_upload_file

_TEMPLATE_CSV = "path,created_at,latitude,longitude\nparent_dir/video.mp4,2024-06-01T08:30:00Z,46.9481,7.4474\n"
_NONE_VALUE = ""

_REQUIRED_COLS = ("meta_folder_col", "meta_file_col")
_OPTIONAL_COLS = ("meta_datetime_col", "meta_lat_col", "meta_lon_col")
_SOURCE_EPSG_KEY = "meta_source_epsg"


def _source_epsg(state: dict) -> int | None:
    val = state.get(_SOURCE_EPSG_KEY)
    try:
        return int(val) if val else None
    except (ValueError, TypeError):
        return None


def setup_metadata_tab(dp, loading_dialog) -> None:
    # Per-page in-memory state. Kept out of app.storage.user on purpose: that
    # store re-serializes to disk on every mutation, so routing per-keystroke
    # config here (and the uploaded frame) avoids dumping it repeatedly. Both
    # dicts are fresh per page build and die on navigate.
    frames: dict = {}
    state: dict = {}

    with ui.card().classes("q-pa-md q-mt-md"):
        ui.label(t("metadata_import_title")).classes("text-subtitle2 font-weight-medium q-mb-xs")
        ui.label(t("metadata_import_desc")).classes("text-caption q-mb-md")

        status_label = ui.label("").classes("text-body2")
        upload_holder: list = [None]

        col_config_section = ui.element("div").classes("w-full q-mb-md")
        col_config_section.visible = False

        results_container = ui.column().classes("w-full q-mt-md")
        results_container.visible = False

        # ── Column config (external format) ──────────────────────────────────

        @ui.refreshable
        def col_config_ui() -> None:
            col_config_section.clear()
            columns = state.get("meta_columns") or []
            if not columns:
                return

            required_opts = {c: c for c in columns}
            optional_opts = {_NONE_VALUE: f"— {t('historic_col_none')} —", **required_opts}

            with col_config_section:
                ui.label(t("meta_path_matching")).classes("text-caption text-grey q-mb-xs q-mt-sm")
                with ui.row().classes("w-full gap-md q-mb-sm items-end"):
                    sels = make_col_selects(
                        state,
                        [
                            ("meta_folder_col", required_opts),
                            ("meta_file_col", required_opts),
                        ],
                    )

                ui.label(t("meta_data_cols")).classes("text-caption text-grey q-mb-xs q-mt-sm")
                with ui.row().classes("w-full gap-md q-mb-sm items-end"):
                    sels += make_col_selects(
                        state,
                        [
                            ("meta_datetime_col", optional_opts),
                            ("meta_lat_col", optional_opts),
                            ("meta_lon_col", optional_opts),
                        ],
                    )

                ui.label(t("meta_source_crs")).classes("text-caption text-grey q-mb-xs q-mt-sm")
                epsg_input = (
                    ui.input(
                        label=t("meta_source_epsg"),
                        placeholder="e.g. 32632",
                        value=state.get(_SOURCE_EPSG_KEY) or "",
                    )
                    .props("clearable")
                    .classes("w-48")
                )
                ui.label(t("meta_source_epsg_hint")).classes("text-caption text-grey-6 q-mb-sm")

                async def on_col_change() -> None:
                    for key, sel in sels:
                        state[key] = sel.value
                    state[_SOURCE_EPSG_KEY] = epsg_input.value
                    await _run_validate(
                        dp, loading_dialog, frames, state, results_ui, results_container
                    )

                async def on_epsg_change() -> None:
                    state[_SOURCE_EPSG_KEY] = epsg_input.value

                epsg_input.on_value_change(on_epsg_change)

                for _, sel in sels:
                    sel.on_value_change(on_col_change)

        col_config_ui()

        # ── Validation results + import button ────────────────────────────────

        @ui.refreshable
        def results_ui() -> None:
            validation = state.get("meta_validation")
            if not validation:
                return

            matched = validation["matched"]
            unmatched = validation["unmatched"]

            with ui.card().classes("full-width q-pa-md q-mb-md"):
                color = "text-positive" if unmatched == 0 else "text-warning"
                ui.label(t("metadata_matched_videos", count=matched)).classes(
                    f"text-subtitle2 {color} q-mb-xs"
                )
                if unmatched:
                    ui.label(t("metadata_unmatched_videos", count=unmatched)).classes(
                        "text-body2 text-warning"
                    )

            ui.separator().classes("q-my-md")

            async def do_import() -> None:
                loading_dialog.open()
                try:
                    df = frames.get("meta_df")
                    if df is None:
                        ui.notify(t("no_data_import"), type="warning")
                        return
                    result = await run.io_bound(
                        dp.import_video_metadata_csv,
                        df,
                        get_active_project_id(),
                        col_val(state, "meta_folder_col"),
                        col_val(state, "meta_file_col"),
                        col_val(state, "meta_datetime_col"),
                        col_val(state, "meta_lat_col"),
                        col_val(state, "meta_lon_col"),
                        _source_epsg(state),
                    )
                    msg = t("metadata_imported", count=result["updated"])
                    if result["skipped"]:
                        msg += t("metadata_skipped", count=len(result["skipped"]))
                    with ignore_deleted_client():
                        status_label.set_text(msg)
                        ui.notify(msg, type="positive")
                except Exception as exc:
                    with ignore_deleted_client():
                        ui.notify(
                            t("import_failed", error=user_error_message(exc)), type="negative"
                        )
                finally:
                    with ignore_deleted_client():
                        loading_dialog.close()

            ui.button(
                t("metadata_import_btn"),
                icon="file_upload",
                on_click=do_import,
                color="primary",
            )

        # ── Upload ────────────────────────────────────────────────────────────

        async def handle_upload(e) -> None:
            loading_dialog.open()
            try:
                content = await e.file.read()
                df = read_upload_file(content)
                columns = list(df.columns)

                if "path" in columns:
                    # Standard format: import directly
                    frames["meta_df"] = None
                    state["meta_validation"] = None
                    col_config_section.visible = False
                    results_container.visible = False
                    col_config_ui.refresh()
                    results_ui.refresh()

                    result = await run.io_bound(
                        dp.import_video_metadata_csv, df, get_active_project_id()
                    )
                    msg = t("metadata_imported", count=result["updated"])
                    if result["skipped"]:
                        msg += t("metadata_skipped", count=len(result["skipped"]))
                    with ignore_deleted_client():
                        status_label.set_text(msg)
                        ui.notify(msg, type="positive")
                else:
                    # External format: show column config
                    frames["meta_df"] = df
                    state["meta_columns"] = columns
                    state["meta_validation"] = None

                    for key in _REQUIRED_COLS:
                        state[key] = columns[0]
                    for key in _OPTIONAL_COLS:
                        state[key] = ""

                    col_config_section.visible = True
                    col_config_ui.refresh()
                    if upload_holder[0]:
                        upload_holder[0].visible = False
            except Exception as exc:
                with ignore_deleted_client():
                    ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
                return
            finally:
                with ignore_deleted_client():
                    loading_dialog.close()

            if frames.get("meta_df") is not None:
                await _run_validate(
                    dp, loading_dialog, frames, state, results_ui, results_container
                )

        upload_holder[0] = ui.upload(
            on_upload=handle_upload,
            multiple=False,
            label=t("metadata_choose_csv"),
            auto_upload=True,
        ).props("accept=.csv,.tsv,.txt")

        ui.button(
            "Download template",
            icon="download",
            on_click=lambda: ui.download(_TEMPLATE_CSV.encode(), "metadata_template.csv"),
        ).props("flat color=primary size=sm").classes("q-mt-sm")

    with results_container:
        results_ui()


async def _run_validate(dp, loading_dialog, frames, state, results_ui, results_container) -> None:
    loading_dialog.open()
    try:
        df = frames.get("meta_df")
        if df is None:
            return
        result = await run.io_bound(
            dp.validate_metadata_csv,
            df,
            get_active_project_id(),
            col_val(state, "meta_folder_col"),
            col_val(state, "meta_file_col"),
        )
        state["meta_validation"] = result
        with ignore_deleted_client():
            results_container.visible = True
            results_ui.refresh()
    except Exception as exc:
        with ignore_deleted_client():
            ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
    finally:
        with ignore_deleted_client():
            loading_dialog.close()
