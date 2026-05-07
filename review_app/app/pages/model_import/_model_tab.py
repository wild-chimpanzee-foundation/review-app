import io

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_active_project_id, get_language, get_state_val, set_state_val
from review_app.app.translations import t
from review_app.app.utils import user_error_message
from review_app.backend.errors import DataImportError
from review_app.backend.utils import df_to_records

from ._helpers import (
    auto_suggest_mappings,
    auto_suggest_path_col,
    get_df_from_state,
    is_long_format,
)

_SCROLL_TO_CTA = 'setTimeout(()=>document.getElementById("import-cta")?.scrollIntoView({behavior:"smooth",block:"start"}),100)'


async def setup_model_tab(dp, loading_dialog) -> None:
    templates = await run.io_bound(dp.get_csv_templates)
    csv_content = templates["model_annotations"]

    import_button_holder: list = [None]
    pending_warning_holder: list = [None]

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

        step2_header = ui.row().classes("items-center gap-sm q-mb-sm")
        step2_header.visible = False

        config_card = ui.card().classes("full-width q-mb-lg")
        config_card.visible = False

        step3_header = ui.row().classes("items-center gap-sm q-mb-sm").props("id=import-step3")
        step3_header.visible = False

        results_container = ui.card().classes("full-width q-mb-lg")
        results_container.visible = False

        upload_widget_holder: list = [None]

        async def run_preview_logic():
            recs = get_state_val("raw_df_records")
            if not recs:
                return
            loading_dialog.open()
            try:
                _, stats = await run.io_bound(
                    dp.normalize_model_csv_with_mapping,
                    pd.DataFrame(recs),
                    get_state_val("path_col") or "",
                    get_state_val("ann_mappings") or [],
                    get_active_project_id(),
                )
                set_state_val("match_preview", stats)
                config_ui.refresh()
            except Exception as exc:
                ui.notify(f"{t('error')}: {exc}", type="negative")
            finally:
                loading_dialog.close()

        async def handle_upload(e):
            loading_dialog.open()
            try:
                content = await e.file.read()
                raw_df = pd.read_csv(io.BytesIO(content))
                columns = list(raw_df.columns)
                sample = raw_df.head(3).to_dict(orient="records")

                set_state_val("raw_df_records", raw_df.to_dict(orient="records"))
                set_state_val("raw_csv_columns", columns)
                set_state_val("path_col", auto_suggest_path_col(columns, sample))
                set_state_val("ann_mappings", auto_suggest_mappings(columns))
                set_state_val("csv_format", "long" if is_long_format(columns) else "wide")
                set_state_val("match_preview", None)
                set_state_val("match_stats", None)
                set_state_val("uploaded_df", None)
                set_state_val("cleaned_df", None)
                set_state_val("errors_df", None)
                set_state_val("species_mappings", {})
                set_state_val("unmapped_species", [])

                upload_result.text = t("loaded_rows", count=len(raw_df))
                if upload_widget_holder[0]:
                    upload_widget_holder[0].visible = False
                step2_header.visible = True
                config_card.visible = True
                step3_header.visible = False
                results_container.visible = False
                config_ui.refresh()
            except Exception as exc:
                ui.notify(f"{t('error')}: {user_error_message(exc)}", type="negative")
                loading_dialog.close()
                return

            loading_dialog.close()

            # Auto-run preview for wide format so user immediately sees match quality
            if get_state_val("csv_format") == "wide":
                await run_preview_logic()

        upload_widget_holder[0] = ui.upload(
            on_upload=handle_upload,
            multiple=False,
            label=t("choose_csv"),
            auto_upload=True,
        )

    # ── Step 2: Configure & Validate ─────────────────────────────────────────
    with step2_header:
        _step_badge("2")
        ui.label(t("step_configure_validate")).classes("text-subtitle1 font-weight-bold")

    @ui.refreshable
    def config_ui():
        columns = get_state_val("raw_csv_columns") or []
        ann_mappings = get_state_val("ann_mappings") or []
        col_opts = {c: c for c in columns}
        col_opts_none = {"": t("no_columns_col"), **col_opts}

        async def do_process():
            recs = get_state_val("raw_df_records")
            if not recs:
                ui.notify(t("no_data_import"), type="warning")
                return

            if get_state_val("csv_format") == "long":
                await _validate_long(dp, loading_dialog, refresh_results)
                return

            path_col = get_state_val("path_col") or ""
            ann_maps = get_state_val("ann_mappings") or []
            if not path_col:
                ui.notify(t("error_no_path_col"), type="warning")
                return
            if not any(m.get("model_name") for m in ann_maps):
                ui.notify(t("error_no_ann_mappings"), type="warning")
                return

            await _validate_wide(dp, loading_dialog, path_col, ann_maps, refresh_results)

        if get_state_val("csv_format") == "long":
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
                        value=get_state_val("path_col"),
                    )
                    .props("outlined dense")
                    .classes("col")
                )

                async def on_path_col_change():
                    set_state_val("path_col", path_sel.value)
                    set_state_val("match_preview", None)
                    await run_preview_logic()

                path_sel.on_value_change(on_path_col_change)

            preview = get_state_val("match_preview")
            if preview:
                _render_preview_result(preview)

        # Annotation columns card
        with ui.card().classes("full-width q-mb-md"):
            ui.label(t("annotation_columns")).classes("text-subtitle2 q-mb-xs")
            _render_annotation_mappings(ann_mappings, col_opts_none, config_ui)

        has_preview = get_state_val("match_preview") is not None
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
        loading_dialog.open()
        try:
            cleaned_df = get_df_from_state("cleaned_df")
            if cleaned_df is None or cleaned_df.empty:
                raise DataImportError("No data to import", user_message_key="no_data_import")

            df_to_import = cleaned_df.copy()
            mappings = get_state_val("species_mappings", {})
            if mappings:
                mask = df_to_import["annotation_type"] == "species"
                df_to_import.loc[mask, "value_text"] = df_to_import.loc[
                    mask, "value_text"
                ].replace(mappings)

            result = await run.io_bound(
                dp.import_model_csv,
                cleaned_df=df_to_import,
                active_project_id=get_active_project_id(),
            )
            ui.notify(t("imported_rows", count=result.get("inserted_rows", 0)), type="positive")
            ui.navigate.to("/review")
        except Exception as exc:
            ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
            loading_dialog.close()

    def update_import_button():
        cleaned_df = get_df_from_state("cleaned_df")
        species_mappings = get_state_val("species_mappings", {})
        can_import = (
            cleaned_df is not None
            and not cleaned_df.empty
            and all(v for v in species_mappings.values())
        )
        btn = import_button_holder[0]
        if btn:
            btn.props(f"{'disabled' if not can_import else ''}")

        pending = [k for k, v in species_mappings.items() if not v]
        warning = pending_warning_holder[0]
        if warning:
            if pending:
                warning.text = t("missing_mappings", list=", ".join(pending))
                warning.visible = True
            else:
                warning.visible = False

    def refresh_results():
        step3_header.visible = True
        results_container.clear()
        results_container.visible = True

        with results_container:
            cleaned_df = get_df_from_state("cleaned_df")
            valid_count = len(cleaned_df) if cleaned_df is not None else 0
            species_mappings_now = get_state_val("species_mappings", {})
            can_import = (
                cleaned_df is not None
                and not cleaned_df.empty
                and all(v for v in species_mappings_now.values())
            )

            # For wide format use matched-video count in CTA; annotation record
            # count goes in the validation section below to avoid confusion.
            match_stats = get_state_val("match_stats")
            if match_stats is not None:
                cta_label = t("videos_ready_to_import", count=match_stats["matched"])
            else:
                cta_label = t("rows_ready_to_import", count=valid_count)

            # Import CTA — first thing user sees after scrolling to step 3
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

            # Match stats
            _render_match_stats(get_state_val("match_stats"))

            # Valid / invalid summary
            errors_df = get_df_from_state("errors_df")
            _render_validation_counts(valid_count, errors_df, cleaned_df)

            # Species mappings (if any unknown species require mapping)
            all_mappings = dict(get_state_val("species_mappings", {}))
            unmapped = get_state_val("unmapped_species", [])
            unmapped_origs = {u["original"] for u in unmapped}
            all_species = set(all_mappings.keys()) | unmapped_origs

            if all_species:
                ui.separator().classes("q-my-md")
                _render_species_mappings(
                    dp,
                    loading_dialog,
                    all_mappings,
                    unmapped_origs,
                    all_species,
                    refresh_results,
                    update_import_button,
                )

            # Error details
            if errors_df is not None and not errors_df.empty:
                ui.separator().classes("q-my-md")
                _render_error_details(errors_df)

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


def _render_annotation_mappings(ann_mappings: list, col_opts_none: dict, config_ui) -> None:
    type_opts = {
        "species": t("ann_type_species"),
        "blank_non_blank": t("ann_type_blank"),
        "behavior": t("ann_type_behavior"),
    }

    if ann_mappings:
        with ui.row().classes("w-full gap-sm q-mb-xs"):
            for lbl in (
                t("ann_col_model_name"),
                t("ann_col_type"),
                t("ann_col_value"),
                t("ann_col_prob"),
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

            def make_updater(idx, ni, ts, vs, ps):
                def _upd():
                    ms = get_state_val("ann_mappings") or []
                    if idx < len(ms):
                        ms[idx].update(
                            {
                                "model_name": ni.value,
                                "annotation_type": ts.value,
                                "value_col": vs.value or "",
                                "prob_col": ps.value or "",
                            }
                        )
                        set_state_val("ann_mappings", ms)

                return _upd

            upd = make_updater(i, name_in, type_sel, val_sel, prob_sel)
            name_in.on_value_change(upd)
            type_sel.on_value_change(upd)
            val_sel.on_value_change(upd)
            prob_sel.on_value_change(upd)

            def make_remover(idx):
                def _rem():
                    ms = get_state_val("ann_mappings") or []
                    ms.pop(idx)
                    set_state_val("ann_mappings", ms)
                    config_ui.refresh()

                return _rem

            ui.button(icon="close", on_click=make_remover(i)).props("flat round dense")

    def add_row():
        ms = get_state_val("ann_mappings") or []
        ms.append(
            {"model_name": "", "annotation_type": "species", "value_col": "", "prob_col": ""}
        )
        set_state_val("ann_mappings", ms)
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
        if match_stats.get("matched_by_stem", 0) > 0:
            ui.label(t("wide_format_stem_fallback", count=match_stats["matched_by_stem"])).classes(
                "text-caption text-warning"
            )
        if unmatched and match_stats.get("unmatched_sample"):
            with ui.expansion(t("wide_format_unmatched", count=unmatched), icon="warning").classes(
                "q-mt-xs"
            ):
                for up in match_stats["unmatched_sample"]:
                    ui.label(up).classes("text-caption")


def _render_validation_counts(valid_count: int, errors_df, cleaned_df) -> None:
    ui.label(t("validation_result")).classes("text-subtitle1 font-weight-medium q-mb-md")
    invalid_count = len(errors_df) if errors_df is not None else 0

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


def _render_species_mappings(
    dp,
    loading_dialog,
    all_mappings,
    unmapped_origs,
    all_species,
    refresh_results,
    update_import_button,
) -> None:
    ui.label(t("species_mappings")).classes("text-subtitle1 font-weight-medium q-mb-sm")
    ui.label(t("edit_mappings_desc")).classes("text-caption q-mb-md")

    species_map = dp.get_species_display_map(get_language())
    select_options = {"": "", **species_map}

    for orig in sorted(all_species):
        current_mapping = all_mappings.get(orig, "")
        is_unmapped = orig in unmapped_origs
        with ui.row().classes("w-full items-center q-mb-sm"):
            ui.label(orig).classes(f"col {'text-negative' if is_unmapped else ''}")
            select = ui.select(
                label=t("mapped_to"),
                options=select_options,
                value=current_mapping,
                with_input=True,
            ).props("outlined dense class=col-4")

            def make_update_fn(o, sel):
                def update_mapping():
                    mappings = get_state_val("species_mappings", {})
                    mappings[o] = sel.value
                    set_state_val("species_mappings", mappings)
                    update_import_button()

                return update_mapping

            select.on_value_change(make_update_fn(orig, select))

    async def apply_mappings():
        loading_dialog.open()
        try:
            uploaded_df = get_df_from_state("uploaded_df")
            mappings = get_state_val("species_mappings", {})

            cleaned_df, errors_df, _, unmapped_species = await run.io_bound(
                dp.validate_model_csv,
                uploaded_df,
                mappings,
                get_active_project_id(),
            )

            set_state_val(
                "cleaned_df",
                cleaned_df.to_dict(orient="records") if cleaned_df is not None else None,
            )
            set_state_val(
                "errors_df",
                errors_df.to_dict(orient="records") if errors_df is not None else None,
            )
            set_state_val("unmapped_species", unmapped_species)

            ui.notify(t("mappings_applied"), type="positive")
            refresh_results()
        except Exception as exc:
            ui.notify(t("mapping_failed", error=user_error_message(exc)), type="negative")
        finally:
            loading_dialog.close()

    species_mappings = get_state_val("species_mappings", {})
    pending_unmapped = [k for k, v in species_mappings.items() if not v]
    cleaned_df_now = get_df_from_state("cleaned_df")
    can_apply = cleaned_df_now is not None and not cleaned_df_now.empty and not pending_unmapped

    ui.button(t("apply"), icon="refresh", on_click=apply_mappings, color="primary").props(
        f"wide {'disabled' if not can_apply else ''}"
    )
    if pending_unmapped:
        ui.label(t("map_all_to_import", list=", ".join(pending_unmapped))).classes(
            "text-warning text-caption q-mt-sm"
        )


def _render_error_details(errors_df) -> None:
    ui.label(t("validation_errors_count", count=len(errors_df))).classes("text-negative q-mb-sm")

    error_summary = errors_df["error"].value_counts().to_dict()
    if error_summary:
        ui.label(t("error_summary")).classes("text-body2 q-mt-sm")
        for err, count in error_summary.items():
            ui.label(t("error_summary_item", err=t(err), count=count)).classes("text-body2")

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


async def _validate_long(dp, loading_dialog, refresh_results) -> None:
    recs = get_state_val("raw_df_records")
    loading_dialog.open()
    try:
        raw_df = pd.DataFrame(recs)
        cleaned_df, errors_df, species_mappings, unmapped_species = await run.io_bound(
            dp.validate_model_csv, raw_df, None, get_active_project_id()
        )
        set_state_val("match_stats", None)
        set_state_val("uploaded_df", raw_df.to_dict(orient="records"))
        set_state_val(
            "cleaned_df",
            cleaned_df.to_dict(orient="records") if cleaned_df is not None else None,
        )
        set_state_val(
            "errors_df",
            errors_df.to_dict(orient="records") if errors_df is not None else None,
        )
        set_state_val(
            "species_mappings",
            {m["original"]: m.get("mapped_to", "") for m in species_mappings},
        )
        set_state_val("unmapped_species", unmapped_species)
        ui.notify(t("csv_validated"), type="positive")
        refresh_results()
        await ui.run_javascript(_SCROLL_TO_CTA)
    except Exception as exc:
        ui.notify(f"{t('error')}: {user_error_message(exc)}", type="negative")
    finally:
        loading_dialog.close()


async def _validate_wide(dp, loading_dialog, path_col, ann_maps, refresh_results) -> None:
    recs = get_state_val("raw_df_records")
    loading_dialog.open()
    try:
        raw_df = pd.DataFrame(recs)
        normalized_df, match_stats = await run.io_bound(
            dp.normalize_model_csv_with_mapping,
            raw_df,
            path_col,
            ann_maps,
            get_active_project_id(),
        )
        set_state_val("match_stats", match_stats)
        set_state_val("uploaded_df", normalized_df.to_dict(orient="records"))

        cleaned_df, errors_df, species_mappings, unmapped_species = await run.io_bound(
            dp.validate_model_csv, normalized_df, None, get_active_project_id()
        )
        set_state_val(
            "cleaned_df",
            cleaned_df.to_dict(orient="records") if cleaned_df is not None else None,
        )
        set_state_val(
            "errors_df",
            errors_df.to_dict(orient="records") if errors_df is not None else None,
        )
        set_state_val(
            "species_mappings",
            {m["original"]: m.get("mapped_to", "") for m in species_mappings},
        )
        set_state_val("unmapped_species", unmapped_species)
        ui.notify(t("csv_validated"), type="positive")
        refresh_results()
        await ui.run_javascript(_SCROLL_TO_CTA)
    except Exception as exc:
        ui.notify(f"{t('error')}: {exc}", type="negative")
    finally:
        loading_dialog.close()
