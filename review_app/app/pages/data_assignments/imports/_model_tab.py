import logging
import time

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import client_id as _client_id
from review_app.app.utils import ignore_deleted_client, user_error_message
from review_app.backend.errors import DataImportError
from review_app.backend.utils import df_to_records

from ._helpers import (
    auto_suggest_mappings,
    auto_suggest_path_col,
    is_long_format,
    read_upload_file,
)
from ._species_mapping import MODEL_IMPORT, pending_species, render_species_mappings

logger = logging.getLogger(__name__)

_MAX_ERRORS_PER_TYPE = 50


def _friendly_import_error(exc: Exception) -> str:
    """Map low-level failures to an actionable message.

    A dropped websocket / deleted client mid-import (common with very large
    files that stall the event loop) surfaces as TimeoutError or a "has been
    deleted" RuntimeError. Those don't mean the import failed — the DB write
    usually finished — so tell the user to check the review page instead of
    blindly re-uploading."""
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "has been deleted" in text or "connection" in text:
        return t("import_connection_interrupted")
    return user_error_message(exc)


def _cap_errors_for_state(errors_df) -> tuple[pd.DataFrame | None, dict]:
    """Return (capped frame, full counts). Keeps at most _MAX_ERRORS_PER_TYPE rows per
    error type so the display frame stays bounded on large imports."""
    if errors_df is None or errors_df.empty:
        return None, {}
    counts = errors_df["error"].value_counts().to_dict()
    capped = errors_df.groupby("error", group_keys=False).head(_MAX_ERRORS_PER_TYPE)
    return capped, counts


async def setup_model_tab(dp, loading_dialog) -> None:
    templates = await run.io_bound(dp.get_csv_templates)
    csv_content = templates["model_annotations"]

    import_button_holder: list = [None]
    pending_warning_holder: list = [None]

    # Per-page in-memory state, kept out of app.storage.user on purpose: that store
    # re-serializes the whole user-storage dict to disk on every mutation, so routing
    # the multi-MB frames (`frames`) and the per-keystroke config (`state`) here stops
    # each species-mapping keystroke from dumping tens of MB to disk. Both dicts are
    # fresh per page build and die on navigate.
    frames: dict[str, pd.DataFrame | None] = {}
    state: dict = {}

    # ── Template download ─────────────────────────────────────────────────────
    ui.label(t("model_import_desc")).classes("text-body2 q-mb-md")

    def download_template():
        ui.download(csv_content.encode("utf-8"), "model_annotations_template.csv")

    with ui.row().classes("items-center q-mb-md"):
        ui.label(t("csv_template")).classes("text-caption")
        ui.button(t("download_template"), icon="download", on_click=download_template).props(
            "flat dense size=sm"
        )

    # ── Step 1: Upload ────────────────────────────────────────────────────────
    _step_header("1", t("step_upload"))

    with ui.card().classes("full-width q-mb-lg"):
        ui.label(t("upload_csv")).classes("text-subtitle1 font-weight-medium q-mb-md")
        upload_result = ui.label(t("upload_csv_msg")).classes("text-body2")

        # Persistent error banner — a toast vanishes, so failures during a long
        # batch of uploads went unnoticed. This stays until the next upload.
        error_banner = (
            ui.card()
            .classes("full-width q-pa-sm q-mt-sm bg-red-1 text-red-10")
            .props("flat bordered")
        )
        error_banner.visible = False
        with error_banner:
            with ui.row().classes("items-center no-wrap w-full"):
                ui.icon("error_outline").classes("q-mr-sm")
                error_banner_label = ui.label("").classes("col text-body2")
                ui.button(icon="close", on_click=lambda: error_banner.set_visibility(False)).props(
                    "flat round dense size=sm"
                )

        def report_error(exc: Exception) -> None:
            msg = _friendly_import_error(exc)
            error_banner_label.text = msg
            error_banner.set_visibility(True)

        def clear_error() -> None:
            error_banner.set_visibility(False)

        step2_header = ui.row().classes("items-center gap-sm q-mb-sm")
        step2_header.visible = False

        config_card = ui.card().classes("full-width q-mb-lg")
        config_card.visible = False

        step3_header = ui.row().classes("items-center gap-sm q-mb-sm").props("id=import-step3")
        step3_header.visible = False

        results_container = ui.card().classes("full-width q-mb-lg")
        results_container.visible = False

        # Persistent success banner — a toast vanishes (and can be missed entirely if
        # the socket dropped during a long import), so import completion is shown
        # on-page instead of via ui.notify. It sits below the results card, next to the
        # step 3 import button that triggers it, and outside results_container so a
        # refresh of that card doesn't clear it.
        success_banner = (
            ui.card()
            .classes("full-width q-pa-sm q-mb-lg bg-green-1 text-green-10")
            .props("flat bordered")
        )
        success_banner.visible = False
        with success_banner:
            with ui.row().classes("items-center no-wrap w-full"):
                ui.icon("check_circle").classes("q-mr-sm")
                success_banner_label = ui.label("").classes("col text-body2")
                ui.button(
                    icon="close", on_click=lambda: success_banner.set_visibility(False)
                ).props("flat round dense size=sm")

        def report_success(msg: str) -> None:
            success_banner_label.text = msg
            success_banner.set_visibility(True)

        def clear_success() -> None:
            success_banner.set_visibility(False)

        upload_widget_holder: list = [None]

        async def run_preview_logic():
            raw_df = frames.get("raw_df")
            if raw_df is None:
                return
            loading_dialog.open()
            try:
                _, stats = await run.io_bound(
                    dp.normalize_model_csv_with_mapping,
                    raw_df,
                    state.get("path_col") or "",
                    state.get("ann_mappings") or [],
                    get_active_project_id(),
                    state.get("filename_match") or False,
                )
                state["match_preview"] = stats
                config_ui.refresh()
            except Exception as exc:
                report_error(exc)
            finally:
                loading_dialog.close()

        async def handle_upload(e):
            loading_dialog.open()
            clear_error()
            try:
                content = await e.file.read()
                raw_df = read_upload_file(content)
                columns = list(raw_df.columns)
                sample = raw_df.head(3).to_dict(orient="records")
                logger.info(
                    "CSV uploaded: %.1f MB, %d rows, %d cols, format=%s (client=%s)",
                    len(content) / 1e6,
                    len(raw_df),
                    len(columns),
                    "long" if is_long_format(columns) else "wide",
                    _client_id(),
                )

                frames["raw_df"] = raw_df
                frames["uploaded"] = None
                frames["cleaned"] = None
                frames["errors"] = None
                # Belongs to the previous CSV; validating the new one rebuilds it.
                frames["base"] = None
                state["raw_csv_columns"] = columns
                state["path_col"] = auto_suggest_path_col(columns, sample)
                state["ann_mappings"] = auto_suggest_mappings(columns)
                state["csv_format"] = "long" if is_long_format(columns) else "wide"
                state["match_preview"] = None
                state["match_stats"] = None
                state["species_mappings"] = {}
                state["unmapped_species"] = []
                state["filename_match"] = False
                state["error_counts"] = {}

                upload_result.text = t("loaded_rows", count=len(raw_df))
                if upload_widget_holder[0]:
                    upload_widget_holder[0].visible = False
                step2_header.visible = True
                config_card.visible = True
                step3_header.visible = False
                results_container.visible = False
                config_ui.refresh()
            except Exception as exc:
                report_error(exc)
                loading_dialog.close()
                return

            loading_dialog.close()

            # Auto-run preview for wide format so user immediately sees match quality
            if state.get("csv_format") == "wide":
                await run_preview_logic()

        upload_widget_holder[0] = ui.upload(
            on_upload=handle_upload,
            multiple=False,
            label=t("choose_csv"),
            auto_upload=True,
        ).props("accept=.csv,.tsv,.txt")

    # ── Step 2: Configure & Validate ─────────────────────────────────────────
    with step2_header:
        _step_badge("2")
        ui.label(t("step_configure_validate")).classes("text-subtitle1 font-weight-bold")

    @ui.refreshable
    def config_ui():
        columns = state.get("raw_csv_columns") or []
        ann_mappings = state.get("ann_mappings") or []
        col_opts = {c: c for c in columns}
        col_opts_none = {"": t("no_columns_col"), **col_opts}

        async def do_process():
            if frames.get("raw_df") is None:
                ui.notify(t("no_data_import"), type="warning")
                return

            if state.get("csv_format") == "long":
                await _validate_long(
                    dp, loading_dialog, frames, state, refresh_results, report_error
                )
                return

            path_col = state.get("path_col") or ""
            ann_maps = state.get("ann_mappings") or []
            if not path_col:
                ui.notify(t("error_no_path_col"), type="warning")
                return
            if not any(m.get("model_name") for m in ann_maps):
                ui.notify(t("error_no_ann_mappings"), type="warning")
                return

            await _validate_wide(
                dp,
                loading_dialog,
                frames,
                state,
                path_col,
                ann_maps,
                refresh_results,
                report_error,
            )

        if state.get("csv_format") == "long":
            _render_long_format_config(do_process)
            return

        ui.label(t("configure_import")).classes("text-subtitle1 font-weight-medium q-mb-sm")

        # Path matching card
        with ui.card().classes("full-width q-mb-md"):
            ui.label(t("path_matching")).classes("text-subtitle2 q-mb-xs")
            with ui.row().classes("w-full items-end gap-md q-mb-sm"):
                path_sel = (
                    ui.select(
                        label=t("path_col_label"),
                        options=col_opts,
                        value=state.get("path_col"),
                    )
                    .props("outlined dense")
                    .classes("col")
                )

                async def on_path_col_change():
                    state["path_col"] = path_sel.value
                    state["match_preview"] = None
                    await run_preview_logic()

                path_sel.on_value_change(on_path_col_change)

            filename_match_val = state.get("filename_match") or False
            filename_toggle = ui.checkbox(
                t("filename_match_label"),
                value=filename_match_val,
            ).classes("q-mb-xs")

            async def on_filename_match_change():
                state["filename_match"] = filename_toggle.value
                state["match_preview"] = None
                await run_preview_logic()

            filename_toggle.on_value_change(on_filename_match_change)

            if filename_match_val:
                ui.label(t("filename_match_warning")).classes("text-caption text-warning q-mb-xs")

            preview = state.get("match_preview")
            if preview:
                _render_preview_result(preview)

        # Annotation columns card
        with ui.card().classes("full-width q-mb-md"):
            ui.label(t("annotation_columns")).classes("text-subtitle2 q-mb-xs")
            _render_annotation_mappings(state, ann_mappings, col_opts_none, config_ui)

        has_preview = state.get("match_preview") is not None
        validate_btn = ui.button(
            t("validate_csv"), icon="play_arrow", on_click=do_process, color="primary"
        ).classes("q-mt-sm")
        if not has_preview:
            validate_btn.props("disabled")
            ui.label(t("validate_requires_preview")).classes("text-caption text-grey q-mt-xs")

    with config_card:
        config_ui()

    # ── Step 3: Review & Import ───────────────────────────────────────────────
    with step3_header:
        _step_badge("3")
        ui.label(t("step_review_import")).classes("text-subtitle1 font-weight-bold")

    async def do_import():
        started = time.monotonic()
        logger.info("Import clicked (client=%s)", _client_id())
        loading_dialog.open()
        clear_error()
        clear_success()
        try:
            mappings = state.get("species_mappings") or {}
            base = frames.get("base")
            if base is not None:
                # Re-apply the current mappings so species mapped just before import are
                # reflected in cleaned_df, without a separate "Apply" step.
                cleaned_df, errors_df, _, unmapped_species = dp.apply_species_mappings(
                    base, mappings
                )
                frames["cleaned"] = cleaned_df
                errors_capped, error_counts = _cap_errors_for_state(errors_df)
                frames["errors"] = errors_capped
                state["error_counts"] = error_counts
                state["unmapped_species"] = unmapped_species
            else:
                cleaned_df = frames.get("cleaned")

            if cleaned_df is None or cleaned_df.empty:
                raise DataImportError("No data to import", user_message_key="no_data_import")

            # cleaned_df is already mapped: apply_species_mappings resolved every species,
            # dropped the ignored rows and rewrote the blank-mapped ones.
            result = await run.io_bound(
                dp.import_model_csv,
                cleaned_df=cleaned_df,
                active_project_id=get_active_project_id(),
            )
            logger.info(
                "Import finished in %.1fs: %s rows (client=%s)",
                time.monotonic() - started,
                result.get("imported", 0),
                _client_id(),
            )
            with ignore_deleted_client("import success banner"):
                report_success(t("imported_rows", count=result.get("imported", 0)))
        except Exception as exc:
            logger.exception("Import failed after %.1fs", time.monotonic() - started)
            with ignore_deleted_client("import error banner"):
                report_error(exc)
        finally:
            with ignore_deleted_client("import loading dialog close"):
                loading_dialog.close()

    def _pending_species() -> list[str]:
        species_mappings = state.get("species_mappings") or {}
        unmapped = state.get("unmapped_species") or []
        return pending_species(
            set(species_mappings) | {u["original"] for u in unmapped}, species_mappings
        )

    def update_import_button():
        cleaned_df = frames.get("cleaned")
        pending = _pending_species()
        can_import = cleaned_df is not None and not cleaned_df.empty and not pending
        btn = import_button_holder[0]
        if btn:
            btn.props(f"{'disabled' if not can_import else ''}")

        warning = pending_warning_holder[0]
        if warning:
            if pending:
                warning.text = t("missing_mappings", list=", ".join(pending))
                warning.visible = True
            else:
                warning.visible = False

    @ignore_deleted_client()
    def refresh_results():
        # A large import can stall the loop long enough for the browser to give up and
        # the client to be pruned; touching its elements then raises. Skip the UI update
        # in that case rather than crash the whole handler — the data is already in
        # session state.
        step3_header.visible = True
        results_container.clear()
        results_container.visible = True

        with results_container:
            cleaned_df = frames.get("cleaned")
            valid_count = len(cleaned_df) if cleaned_df is not None else 0
            can_import = cleaned_df is not None and not cleaned_df.empty and not _pending_species()

            # For wide format use matched-video count in CTA; annotation record
            # count goes in the validation section below to avoid confusion.
            match_stats = state.get("match_stats")
            if match_stats is not None:
                cta_label = t("videos_ready_to_import", count=match_stats["matched"])
            else:
                cta_label = t("rows_ready_to_import", count=valid_count)

            # Match stats
            _render_match_stats(state.get("match_stats"))

            # Valid / invalid summary
            errors_df = frames.get("errors")
            error_counts = state.get("error_counts") or {}
            total_invalid = (
                sum(error_counts.values())
                if error_counts
                else (len(errors_df) if errors_df is not None else 0)
            )
            _render_validation_counts(valid_count, errors_df, cleaned_df, total_invalid)

            # Species mappings (if any unknown species require mapping)
            all_mappings = dict(state.get("species_mappings") or {})
            unmapped = state.get("unmapped_species") or []
            unmapped_origs = {u["original"] for u in unmapped}
            all_species = set(all_mappings.keys()) | unmapped_origs

            uploaded_df_for_counts = frames.get("uploaded")
            if (
                uploaded_df_for_counts is not None
                and "value_text" in uploaded_df_for_counts.columns
            ):
                sp_mask = uploaded_df_for_counts["annotation_type"].isin(
                    {"species", "object_detection"}
                )
                species_counts = (
                    uploaded_df_for_counts.loc[sp_mask, "value_text"].value_counts().to_dict()
                )
            else:
                species_counts = {}

            if all_species:
                ui.separator().classes("q-my-md")

                async def auto_revalidate():
                    # Re-apply the current mappings so the valid/invalid counts and the
                    # CTA update live as the user maps each species. Only the mapping
                    # pass runs here; the base pass was done once at validate time, which
                    # is what keeps this off the event loop for more than a moment.
                    mappings = state.get("species_mappings") or {}
                    base = frames.get("base")
                    if base is None:
                        return
                    started = time.monotonic()
                    cleaned_df, errors_df, _, unmapped_species = dp.apply_species_mappings(
                        base, mappings
                    )
                    logger.info(
                        "Revalidate done in %.2fs: %d valid, %d invalid, %d unmapped (client=%s)",
                        time.monotonic() - started,
                        len(cleaned_df),
                        len(errors_df),
                        len(unmapped_species),
                        _client_id(),
                    )
                    frames["cleaned"] = cleaned_df
                    errors_capped, error_counts = _cap_errors_for_state(errors_df)
                    frames["errors"] = errors_capped
                    state["error_counts"] = error_counts
                    state["unmapped_species"] = unmapped_species
                    refresh_results()

                with ui.element("div").classes("w-full").props("id=species-mapping"):
                    render_species_mappings(
                        dp,
                        state,
                        all_mappings,
                        unmapped_origs,
                        all_species,
                        apply_fn=lambda: None,
                        on_change=auto_revalidate,
                        can_apply=False,
                        options=MODEL_IMPORT,
                        project_id=get_active_project_id(),
                        species_counts=species_counts,
                    )

            # Import CTA — shown after species mappings so unmapped species are resolved first
            ui.separator().classes("q-my-md")
            with ui.card().classes("full-width q-mb-md q-pa-md").props("id=import-cta"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.column().classes("gap-xs"):
                        ui.label(cta_label).classes(
                            "text-subtitle1 font-weight-bold text-positive"
                        )
                        pending_warning_holder[0] = ui.label("").classes(
                            "text-warning text-caption"
                        )
                        pending_warning_holder[0].visible = False
                    import_button_holder[0] = ui.button(
                        t("import_valid_rows"),
                        icon="file_upload",
                        on_click=do_import,
                        color="warning",
                    ).props(f"{'disabled' if not can_import else ''}")
            update_import_button()

            # Error details
            if errors_df is not None and not errors_df.empty:
                ui.separator().classes("q-my-md")
                _render_error_details(errors_df, error_counts)

            if unmapped:
                ui.label(t("unmapped_species_count", count=len(unmapped))).classes(
                    "text-warning q-mb-sm"
                )


# ── Private rendering helpers ─────────────────────────────────────────────────


def _step_header(number: str, label: str) -> None:
    with ui.row().classes("items-center gap-sm q-mb-sm"):
        _step_badge(number)
        ui.label(label).classes("text-subtitle1 font-weight-bold")


def _step_badge(number: str) -> None:
    ui.label(number).classes(
        "text-caption font-weight-bold bg-primary text-white q-px-sm q-py-xs rounded-borders"
    )


def _render_long_format_config(do_process) -> None:
    ui.label(t("configure_import")).classes("text-subtitle1 font-weight-medium q-mb-sm")
    with ui.card().classes("full-width q-mb-md"):
        ui.icon("check_circle", color="positive").classes("q-mb-xs")
        ui.label(t("long_format_detected")).classes("text-body2 text-positive q-mb-xs")
        ui.label(t("long_format_desc")).classes("text-caption")
    ui.button(t("validate_csv"), icon="play_arrow", on_click=do_process, color="primary").classes(
        "q-mt-sm"
    )


def _render_preview_result(preview: dict) -> None:
    total = preview["total_rows"]
    matched = preview["matched"]
    unmatched = preview["unmatched"]
    color = "text-positive" if unmatched == 0 else "text-warning"
    ui.label(t("match_preview_result", matched=matched, total=total)).classes(
        f"text-body2 {color}"
    )
    if unmatched and preview.get("unmatched_sample"):
        with ui.expansion(t("wide_format_unmatched", count=unmatched), icon="warning").classes(
            "q-mt-xs"
        ):
            for up in preview["unmatched_sample"]:
                ui.label(up).classes("text-caption")


def _render_annotation_mappings(
    state: dict, ann_mappings: list, col_opts_none: dict, config_ui
) -> None:
    type_opts = {
        "species": t("ann_type_species"),
        "blank_non_blank": t("ann_type_blank"),
        "behavior": t("ann_type_behavior"),
        "object_detection": t("ann_type_object_detection"),
    }

    if ann_mappings:
        with ui.row().classes("w-full gap-sm q-mb-xs"):
            for lbl in (
                t("ann_col_model_name"),
                t("ann_col_type"),
                t("ann_col_value"),
                t("ann_col_prob"),
                t("ann_col_count"),
            ):
                ui.label(lbl).classes("col text-caption")
            ui.element("div").style("min-width:32px")

    for i, m in enumerate(ann_mappings):
        with ui.row().classes("w-full items-center gap-sm q-mb-xs"):
            name_in = (
                ui.input(value=m.get("model_name", "")).props("outlined dense").classes("col")
            )
            type_sel = (
                ui.select(options=type_opts, value=m.get("annotation_type", "species"))
                .props("outlined dense")
                .classes("col")
            )
            val_sel = (
                ui.select(options=col_opts_none, value=m.get("value_col", ""))
                .props("outlined dense")
                .classes("col")
            )
            prob_sel = (
                ui.select(options=col_opts_none, value=m.get("prob_col", ""))
                .props("outlined dense")
                .classes("col")
            )
            count_sel = (
                ui.select(options=col_opts_none, value=m.get("count_col", ""))
                .props("outlined dense")
                .classes("col")
            )

            def make_updater(idx, ni, ts, vs, ps, cs):
                def _upd():
                    ms = state.get("ann_mappings") or []
                    if idx < len(ms):
                        ms[idx].update(
                            {
                                "model_name": ni.value,
                                "annotation_type": ts.value,
                                "value_col": vs.value or "",
                                "prob_col": ps.value or "",
                                "count_col": cs.value or "",
                            }
                        )
                        state["ann_mappings"] = ms

                return _upd

            upd = make_updater(i, name_in, type_sel, val_sel, prob_sel, count_sel)
            name_in.on_value_change(upd)
            type_sel.on_value_change(upd)
            val_sel.on_value_change(upd)
            prob_sel.on_value_change(upd)
            count_sel.on_value_change(upd)

            def make_remover(idx):
                def _rem():
                    ms = state.get("ann_mappings") or []
                    ms.pop(idx)
                    state["ann_mappings"] = ms
                    config_ui.refresh()

                return _rem

            ui.button(icon="close", on_click=make_remover(i)).props("flat round dense")

    def add_row():
        ms = state.get("ann_mappings") or []
        ms.append(
            {
                "model_name": "",
                "annotation_type": "species",
                "value_col": "",
                "prob_col": "",
                "count_col": "",
            }
        )
        state["ann_mappings"] = ms
        config_ui.refresh()

    ui.button(t("add_annotation_row"), icon="add", on_click=add_row).props(
        "flat dense color=primary"
    )


def _render_match_stats(match_stats: dict | None) -> None:
    if not match_stats:
        return
    with ui.card().classes("full-width q-mb-md"):
        total = match_stats["total_rows"]
        matched = match_stats["matched"]
        unmatched = match_stats["unmatched"]
        color = "text-positive" if unmatched == 0 else "text-warning"
        ui.label(t("match_preview_result", matched=matched, total=total)).classes(
            f"text-body2 {color}"
        )
        if match_stats.get("matched_by_cam_stem", 0) > 0:
            ui.label(
                t("wide_format_stem_fallback", count=match_stats["matched_by_cam_stem"])
            ).classes("text-caption text-warning")
        if match_stats.get("matched_by_filename", 0) > 0:
            ui.label(
                t("wide_format_filename_fallback", count=match_stats["matched_by_filename"])
            ).classes("text-caption text-warning")
        if unmatched and match_stats.get("unmatched_sample"):
            with ui.expansion(t("wide_format_unmatched", count=unmatched), icon="warning").classes(
                "q-mt-xs"
            ):
                for up in match_stats["unmatched_sample"]:
                    ui.label(up).classes("text-caption")


def _render_validation_counts(
    valid_count: int, errors_df, cleaned_df, total_invalid: int | None = None
) -> None:
    ui.label(t("validation_result")).classes("text-subtitle1 font-weight-medium q-mb-md")
    invalid_count = (
        total_invalid
        if total_invalid is not None
        else (len(errors_df) if errors_df is not None else 0)
    )

    with ui.row().classes("gap-lg q-mb-md"):
        with ui.card().classes("text-center q-pa-md"):
            ui.label(str(valid_count)).classes("text-h5 font-weight-bold text-positive")
            ui.label(t("valid_rows")).classes("text-caption")
        with ui.card().classes("text-center q-pa-md"):
            ui.label(str(invalid_count)).classes("text-h5 font-weight-bold text-negative")
            ui.label(t("invalid_rows")).classes("text-caption")

    if cleaned_df is not None and not cleaned_df.empty:
        display_cols = [c for c in cleaned_df.columns if c != "video_id"]
        with ui.expansion(t("show_valid_rows"), icon="table_rows").classes("full-width q-mt-sm"):
            ui.aggrid(
                {
                    "columnDefs": [{"field": c, "headerName": c} for c in display_cols],
                    "rowData": df_to_records(cleaned_df[display_cols], limit=500),
                    "columnSize": "responsive",
                    "pagination": True,
                    "paginationPageSize": 50,
                }
            ).classes("h-64")


def _render_error_details(errors_df, error_counts: dict | None = None) -> None:
    total = sum(error_counts.values()) if error_counts else len(errors_df)
    ui.label(t("validation_errors_count", count=total)).classes("text-negative q-mb-sm")

    summary = error_counts if error_counts else errors_df["error"].value_counts().to_dict()
    if summary:
        ui.label(t("error_summary")).classes("text-body2 q-mt-sm")
        for err, count in summary.items():
            ui.label(t("error_summary_item", err=t(err), count=count)).classes("text-body2")

    shown = len(errors_df)
    if shown < total:
        ui.label(t("errors_showing_sample", shown=shown, total=total)).classes(
            "text-caption text-grey q-mt-xs"
        )

    with ui.expansion(t("show_detailed_errors"), icon="table_rows").classes("full-width q-mt-sm"):
        err_display_cols = [c for c in errors_df.columns if c != "video_id"]
        ui.aggrid(
            {
                "columnDefs": [{"field": c, "headerName": c} for c in err_display_cols],
                "rowData": df_to_records(errors_df[err_display_cols], limit=500),
                "columnSize": "responsive",
                "rowSelection": "single",
                "pagination": True,
                "paginationPageSize": 50,
            }
        ).classes("h-64")


# ── Validation logic (pulled out of config_ui to keep it short) ───────────────


async def _validate_long(dp, loading_dialog, frames, state, refresh_results, report_error) -> None:
    raw_df = frames.get("raw_df")
    loading_dialog.open()
    try:
        # The expensive pass, run once here and cached in frames["base"]; every later
        # species mapping only re-runs apply_species_mappings against it.
        base = await run.io_bound(dp.validate_model_csv_base, raw_df, get_active_project_id())
        frames["base"] = base
        cleaned_df, errors_df, species_mappings, unmapped_species = dp.apply_species_mappings(
            base, None
        )
        state["match_stats"] = None
        frames["uploaded"] = raw_df
        frames["cleaned"] = cleaned_df
        errors_capped, error_counts = _cap_errors_for_state(errors_df)
        frames["errors"] = errors_capped
        state["error_counts"] = error_counts
        state["species_mappings"] = {
            m["original"]: m.get("mapped_to", "") for m in species_mappings
        }
        state["unmapped_species"] = unmapped_species
        ui.notify(t("csv_validated"), type="positive")
        refresh_results()
    except Exception as exc:
        report_error(exc)
    finally:
        loading_dialog.close()


async def _validate_wide(
    dp, loading_dialog, frames, state, path_col, ann_maps, refresh_results, report_error
) -> None:
    raw_df = frames.get("raw_df")
    loading_dialog.open()
    try:
        normalized_df, match_stats = await run.io_bound(
            dp.normalize_model_csv_with_mapping,
            raw_df,
            path_col,
            ann_maps,
            get_active_project_id(),
            state.get("filename_match") or False,
        )
        state["match_stats"] = match_stats
        frames["uploaded"] = normalized_df

        # See _validate_long: base pass once, mapping pass per change.
        base = await run.io_bound(
            dp.validate_model_csv_base, normalized_df, get_active_project_id()
        )
        frames["base"] = base
        cleaned_df, errors_df, species_mappings, unmapped_species = dp.apply_species_mappings(
            base, None
        )
        frames["cleaned"] = cleaned_df
        errors_capped, error_counts = _cap_errors_for_state(errors_df)
        frames["errors"] = errors_capped
        state["error_counts"] = error_counts
        state["species_mappings"] = {
            m["original"]: m.get("mapped_to", "") for m in species_mappings
        }
        state["unmapped_species"] = unmapped_species
        ui.notify(t("csv_validated"), type="positive")
        refresh_results()
    except Exception as exc:
        report_error(exc)
    finally:
        loading_dialog.close()
