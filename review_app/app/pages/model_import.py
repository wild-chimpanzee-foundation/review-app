import io

import pandas as pd
from nicegui import run, ui

from review_app.app.onboarding import show_info_dialog
from review_app.app.state import get_active_project_id, get_state_val, set_state_val
from review_app.app.translations import get_language, t
from review_app.app.utils import get_or_create_data_provider, render_uninitialized_state
from review_app.backend.utils import df_to_records


def _get_df_from_state(key: str) -> pd.DataFrame | None:
    data = get_state_val(key)
    return pd.DataFrame(data) if data is not None else None


def _auto_suggest_mappings(columns: list[str]) -> list[dict]:
    col_set = set(columns)
    suggestions: list[dict] = []
    detected_models: list[str] = []

    for col in columns:
        if col.startswith("top_1_"):
            model = col[6:]
            detected_models.append(model)
            suggestions.append(
                {
                    "model_name": model,
                    "annotation_type": "species",
                    "value_col": col,
                    "prob_col": f"prob_{model}" if f"prob_{model}" in col_set else "",
                }
            )

    # Try per-model blank columns first (blank_{model} or {model}_blank)
    blank_found = False
    for model in detected_models:
        for pattern in (f"blank_{model}", f"{model}_blank", f"p_blank_{model}", f"prob_blank_{model}"):
            if pattern in col_set:
                suggestions.append(
                    {
                        "model_name": model,
                        "annotation_type": "blank_non_blank",
                        "value_col": "",
                        "prob_col": pattern,
                    }
                )
                blank_found = True

    # Fall back to a generic blank column, using the column name as model name
    if not blank_found:
        for blank_col in ("blank", "blank_prob", "p_blank", "prob_blank"):
            if blank_col in col_set:
                suggestions.append(
                    {
                        "model_name": blank_col,
                        "annotation_type": "blank_non_blank",
                        "value_col": "",
                        "prob_col": blank_col,
                    }
                )
                break

    return suggestions


def _auto_suggest_path_col(columns: list[str], sample: list[dict]) -> str:
    for preferred in ("filepath", "original_filepath", "video_path", "path", "file"):
        if preferred in columns:
            return preferred
    if sample:
        first = sample[0]
        for col in columns:
            val = str(first.get(col, ""))
            if "/" in val or "\\" in val:
                return col
    return columns[0] if columns else ""


def _is_long_format(columns: list[str]) -> bool:
    return {"video_uid", "annotation_type", "model_name"}.issubset(set(columns))


async def setup_model_import():
    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    if not dp or not await run.io_bound(dp.has_videos_in_db, get_active_project_id()):
        shared_header()
        render_uninitialized_state()
        return

    shared_header()

    for key in (
        "raw_df_records",
        "raw_csv_columns",
        "path_col",
        "match_strategy",
        "ann_mappings",
        "match_preview",
        "uploaded_df",
        "cleaned_df",
        "errors_df",
        "species_mappings",
        "unmapped_species",
        "match_stats",
        "csv_format",
    ):
        set_state_val(key, None)

    loading_dialog = ui.dialog().props("persistent")
    with loading_dialog, ui.card().classes("q-pa-lg items-center"):
        ui.spinner(size="lg")
        ui.label(t("processing_wait")).classes("q-mt-md")

    import_button_holder: list = [None]
    pending_warning_holder: list = [None]

    with ui.column().classes("w-full q-pa-md").style("max-width: 1600px; margin: 0 auto"):
        with ui.row().classes("items-center gap-sm q-mb-lg"):
            ui.label(t("nav_import")).classes("text-h5 text-primary font-weight-bold")
            ui.button(
                icon="info_outline",
                on_click=lambda: show_info_dialog(
                    t("info_model_import_title"), t("info_model_import_body")
                ),
            ).props("flat round dense color=primary")

        with ui.tabs().classes("w-full") as tabs:
            tab_model = ui.tab("model_import", label=t("model_import_title"))
            tab_annotations = ui.tab("annotations", label=t("annotation_export_import_title"))

        with ui.tab_panels(tabs, value=tab_model).classes("w-full"):
            with ui.tab_panel(tab_model):
                ui.label(t("model_import_desc")).classes("text-body2  q-mb-md")

                templates = await run.io_bound(dp.get_csv_templates)
                csv_content = templates["model_annotations"]

                def download_template():
                    ui.download(csv_content.encode("utf-8"), "model_annotations_template.csv")

                with ui.row().classes("items-center q-mb-md"):
                    ui.label(t("csv_template")).classes("text-caption")
                    ui.button(
                        t("download_template"),
                        icon="download",
                        on_click=download_template,
                    ).props("flat dense size=sm")

                # ── Upload ────────────────────────────────────────────────────
                with ui.card().classes("full-width q-mb-lg"):
                    ui.label(t("upload_csv")).classes("text-subtitle1 font-weight-medium q-mb-md")
                    upload_result = ui.label(t("upload_csv_msg")).classes("text-body2")

                    async def handle_upload(e):
                        loading_dialog.open()
                        try:
                            content = await e.file.read()
                            raw_df = pd.read_csv(io.BytesIO(content))
                            columns = list(raw_df.columns)
                            sample = raw_df.head(3).to_dict(orient="records")

                            set_state_val("raw_df_records", raw_df.to_dict(orient="records"))
                            set_state_val("raw_csv_columns", columns)
                            set_state_val("path_col", _auto_suggest_path_col(columns, sample))
                            set_state_val("match_strategy", "suffix")
                            set_state_val("ann_mappings", _auto_suggest_mappings(columns))
                            set_state_val(
                                "csv_format", "long" if _is_long_format(columns) else "wide"
                            )
                            set_state_val("match_preview", None)
                            set_state_val("match_stats", None)
                            set_state_val("uploaded_df", None)
                            set_state_val("cleaned_df", None)
                            set_state_val("errors_df", None)
                            set_state_val("species_mappings", {})
                            set_state_val("unmapped_species", [])

                            upload_result.text = t("loaded_rows", count=len(raw_df))
                            config_card.visible = True
                            results_container.visible = False
                            mappings_container.visible = False
                            config_ui.refresh()
                        except Exception as exc:
                            ui.notify(f"{t('error')}: {exc}", type="negative")
                        finally:
                            loading_dialog.close()

                    ui.upload(
                        on_upload=handle_upload,
                        multiple=False,
                        label=t("choose_csv"),
                        auto_upload=True,
                    )

                # ── Column mapping config ─────────────────────────────────────
                config_card = ui.card().classes("full-width q-mb-lg")
                config_card.visible = False

                @ui.refreshable
                def config_ui():
                    columns = get_state_val("raw_csv_columns") or []
                    ann_mappings = get_state_val("ann_mappings") or []
                    col_opts = {c: c for c in columns}
                    col_opts_none = {"": t("no_columns_col"), **col_opts}

                    # Process ─────────────────────────────────────────────────
                    async def do_process():
                        recs = get_state_val("raw_df_records")
                        if not recs:
                            ui.notify(t("no_data_import"), type="warning")
                            return

                        if get_state_val("csv_format") == "long":
                            loading_dialog.open()
                            try:
                                raw_df = pd.DataFrame(recs)
                                (
                                    cleaned_df,
                                    errors_df,
                                    species_mappings,
                                    unmapped_species,
                                ) = await run.io_bound(
                                    dp.validate_model_csv,
                                    raw_df,
                                    None,
                                    get_active_project_id(),
                                )
                                set_state_val("match_stats", None)
                                set_state_val("uploaded_df", raw_df.to_dict(orient="records"))
                                set_state_val(
                                    "cleaned_df",
                                    cleaned_df.to_dict(orient="records")
                                    if cleaned_df is not None
                                    else None,
                                )
                                set_state_val(
                                    "errors_df",
                                    errors_df.to_dict(orient="records")
                                    if errors_df is not None
                                    else None,
                                )
                                set_state_val(
                                    "species_mappings",
                                    {
                                        m["original"]: m.get("mapped_to", "")
                                        for m in species_mappings
                                    },
                                )
                                set_state_val("unmapped_species", unmapped_species)
                                ui.notify(t("csv_validated"), type="positive")
                                refresh_results()
                            except Exception as exc:
                                ui.notify(f"{t('error')}: {exc}", type="negative")
                            finally:
                                loading_dialog.close()
                            return

                        path_col = get_state_val("path_col") or ""
                        match_strategy = get_state_val("match_strategy") or "suffix"
                        ann_maps = get_state_val("ann_mappings") or []
                        if not path_col:
                            ui.notify(t("error_no_path_col"), type="warning")
                            return
                        if not any(m.get("model_name") for m in ann_maps):
                            ui.notify(t("error_no_ann_mappings"), type="warning")
                            return

                        loading_dialog.open()
                        try:
                            raw_df = pd.DataFrame(recs)
                            normalized_df, match_stats = await run.io_bound(
                                dp.normalize_model_csv_with_mapping,
                                raw_df,
                                path_col,
                                match_strategy,
                                ann_maps,
                                get_active_project_id(),
                            )
                            set_state_val("match_stats", match_stats)
                            set_state_val("uploaded_df", normalized_df.to_dict(orient="records"))

                            (
                                cleaned_df,
                                errors_df,
                                species_mappings,
                                unmapped_species,
                            ) = await run.io_bound(
                                dp.validate_model_csv,
                                normalized_df,
                                None,
                                get_active_project_id(),
                            )

                            set_state_val(
                                "cleaned_df",
                                cleaned_df.to_dict(orient="records")
                                if cleaned_df is not None
                                else None,
                            )
                            set_state_val(
                                "errors_df",
                                errors_df.to_dict(orient="records")
                                if errors_df is not None
                                else None,
                            )
                            set_state_val(
                                "species_mappings",
                                {m["original"]: m.get("mapped_to", "") for m in species_mappings},
                            )
                            set_state_val("unmapped_species", unmapped_species)

                            ui.notify(t("csv_validated"), type="positive")
                            refresh_results()
                        except Exception as exc:
                            ui.notify(f"{t('error')}: {exc}", type="negative")
                        finally:
                            loading_dialog.close()

                    if get_state_val("csv_format") == "long":
                        ui.label(t("configure_import")).classes(
                            "text-subtitle1 font-weight-medium q-mb-sm"
                        )
                        with ui.card().classes("full-width q-mb-md "):
                            ui.icon("check_circle", color="positive").classes("q-mb-xs")
                            ui.label(t("long_format_detected")).classes(
                                "text-body2 text-positive q-mb-xs"
                            )
                            ui.label(t("long_format_desc")).classes("text-caption")
                        ui.button(
                            t("process_csv"),
                            icon="play_arrow",
                            on_click=do_process,
                            color="primary",
                        ).classes("q-mt-sm")
                        return

                    ui.label(t("configure_import")).classes(
                        "text-subtitle1 font-weight-medium q-mb-sm"
                    )

                    # Path matching ───────────────────────────────────────────
                    with ui.card().classes("full-width q-mb-md "):
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
                            path_sel.on_value_change(
                                lambda: set_state_val("path_col", path_sel.value)
                            )

                            strat_sel = (
                                ui.select(
                                    label=t("match_strategy_label"),
                                    options={
                                        "suffix": t("match_suffix"),
                                        "stem": t("match_stem"),
                                    },
                                    value=get_state_val("match_strategy") or "suffix",
                                )
                                .props("outlined dense")
                                .classes("col-4")
                            )
                            strat_sel.on_value_change(
                                lambda: set_state_val("match_strategy", strat_sel.value)
                            )

                        with ui.row().classes("items-center gap-md"):

                            async def do_preview():
                                recs = get_state_val("raw_df_records")
                                if not recs:
                                    return
                                loading_dialog.open()
                                try:
                                    stats = await run.io_bound(
                                        dp.preview_path_match,
                                        pd.DataFrame(recs),
                                        get_state_val("path_col") or "",
                                        get_state_val("match_strategy") or "suffix",
                                        get_active_project_id(),
                                    )
                                    set_state_val("match_preview", stats)
                                    config_ui.refresh()
                                except Exception as exc:
                                    ui.notify(f"{t('error')}: {exc}", type="negative")
                                finally:
                                    loading_dialog.close()

                            ui.button(
                                t("preview_match"),
                                icon="search",
                                on_click=do_preview,
                            ).props("flat color=primary dense")

                            preview = get_state_val("match_preview")
                            if preview:
                                total = preview["total_rows"]
                                matched = preview["matched"]
                                unmatched = preview["unmatched"]
                                color = "text-positive" if unmatched == 0 else "text-warning"
                                ui.label(
                                    t("match_preview_result", matched=matched, total=total)
                                ).classes(f"text-body2 {color}")
                                if unmatched and preview.get("unmatched_sample"):
                                    with ui.expansion(
                                        t("wide_format_unmatched", count=unmatched),
                                        icon="warning",
                                    ).classes("q-mt-xs"):
                                        for up in preview["unmatched_sample"]:
                                            ui.label(up).classes("text-caption")

                    # Annotation columns ──────────────────────────────────────
                    with ui.card().classes("full-width q-mb-md"):
                        ui.label(t("annotation_columns")).classes("text-subtitle2 q-mb-xs")
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
                                    ui.input(value=m.get("model_name", ""))
                                    .props("outlined dense")
                                    .classes("col")
                                )
                                type_sel = (
                                    ui.select(
                                        options=type_opts,
                                        value=m.get("annotation_type", "species"),
                                    )
                                    .props("outlined dense")
                                    .classes("col")
                                )
                                val_sel = (
                                    ui.select(
                                        options=col_opts_none,
                                        value=m.get("value_col", ""),
                                    )
                                    .props("outlined dense")
                                    .classes("col")
                                )
                                prob_sel = (
                                    ui.select(
                                        options=col_opts_none,
                                        value=m.get("prob_col", ""),
                                    )
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

                                ui.button(icon="close", on_click=make_remover(i)).props(
                                    "flat round dense"
                                )

                        def add_row():
                            ms = get_state_val("ann_mappings") or []
                            ms.append(
                                {
                                    "model_name": "",
                                    "annotation_type": "species",
                                    "value_col": "",
                                    "prob_col": "",
                                }
                            )
                            set_state_val("ann_mappings", ms)
                            config_ui.refresh()

                        ui.button(
                            t("add_annotation_row"),
                            icon="add",
                            on_click=add_row,
                        ).props("flat dense color=primary")

                    ui.button(
                        t("process_csv"),
                        icon="play_arrow",
                        on_click=do_process,
                        color="primary",
                    ).classes("q-mt-sm")

                with config_card:
                    config_ui()

                # ── Results ───────────────────────────────────────────────────
                results_container = ui.card().classes("full-width q-mb-lg")
                results_container.visible = False
                mappings_container = ui.card().classes("full-width q-mb-lg")
                mappings_container.visible = False

                async def do_import():
                    loading_dialog.open()
                    try:
                        cleaned_df = _get_df_from_state("cleaned_df")
                        if cleaned_df is None or cleaned_df.empty:
                            raise ValueError(t("no_data_import"))

                        df_to_import = cleaned_df.copy()
                        mappings = get_state_val("species_mappings", {})

                        if mappings:
                            species_mask = df_to_import["annotation_type"] == "species"
                            df_to_import.loc[species_mask, "value_text"] = df_to_import.loc[
                                species_mask, "value_text"
                            ].replace(mappings)

                        result = await run.io_bound(
                            dp.import_model_csv,
                            cleaned_df=df_to_import,
                            active_project_id=get_active_project_id(),
                        )
                        ui.notify(
                            t("imported_rows", count=result.get("inserted_rows", 0)),
                            type="positive",
                        )
                        for key in ("uploaded_df", "cleaned_df", "errors_df", "match_stats"):
                            set_state_val(key, None)
                        set_state_val("species_mappings", {})
                        set_state_val("unmapped_species", [])
                        results_container.clear()
                        results_container.visible = False
                        mappings_container.clear()
                        mappings_container.visible = False
                    except Exception as exc:
                        ui.notify(t("import_failed", error=str(exc)), type="negative")
                    finally:
                        loading_dialog.close()

                def update_import_button():
                    cleaned_df = _get_df_from_state("cleaned_df")
                    species_mappings = get_state_val("species_mappings", {})
                    can_import = (
                        cleaned_df is not None
                        and not cleaned_df.empty
                        and all(v for v in species_mappings.values())
                    )
                    btn = import_button_holder[0]
                    if btn:
                        btn.props(f"wide {'disabled' if not can_import else ''}")

                    pending = [k for k, v in species_mappings.items() if not v]
                    warning = pending_warning_holder[0]
                    if warning:
                        if pending:
                            warning.text = t("missing_mappings", list=", ".join(pending))
                            warning.visible = True
                        else:
                            warning.visible = False

                def refresh_results():
                    results_container.clear()
                    results_container.visible = True
                    mappings_container.clear()
                    mappings_container.visible = True

                    with results_container:
                        match_stats = get_state_val("match_stats")
                        if match_stats:
                            with ui.card().classes("full-width q-mb-md "):
                                total = match_stats["total_rows"]
                                matched = match_stats["matched"]
                                unmatched = match_stats["unmatched"]
                                color = "text-positive" if unmatched == 0 else "text-warning"
                                ui.label(
                                    t("match_preview_result", matched=matched, total=total)
                                ).classes(f"text-body2 {color}")
                                if match_stats.get("matched_by_stem", 0) > 0:
                                    ui.label(
                                        t(
                                            "wide_format_stem_fallback",
                                            count=match_stats["matched_by_stem"],
                                        )
                                    ).classes("text-caption text-warning")
                                if unmatched and match_stats.get("unmatched_sample"):
                                    with ui.expansion(
                                        t("wide_format_unmatched", count=unmatched),
                                        icon="warning",
                                    ).classes("q-mt-xs"):
                                        for up in match_stats["unmatched_sample"]:
                                            ui.label(up).classes("text-caption ")

                        ui.label(t("validation_result")).classes(
                            "text-subtitle1 font-weight-medium q-mb-md"
                        )

                        cleaned_df = _get_df_from_state("cleaned_df")
                        valid_count = len(cleaned_df) if cleaned_df is not None else 0
                        errors_df = _get_df_from_state("errors_df")
                        invalid_count = len(errors_df) if errors_df is not None else 0

                        with ui.row().classes("gap-lg q-mb-md"):
                            with ui.card().classes("text-center q-pa-md"):
                                ui.label(str(valid_count)).classes(
                                    "text-h5 font-weight-bold text-positive"
                                )
                                ui.label(t("valid_rows")).classes("text-caption ")
                            with ui.card().classes("text-center q-pa-md"):
                                ui.label(str(invalid_count)).classes(
                                    "text-h5 font-weight-bold text-negative"
                                )
                                ui.label(t("invalid_rows")).classes("text-caption ")

                    with mappings_container:
                        all_mappings = dict(get_state_val("species_mappings", {}))
                        unmapped = get_state_val("unmapped_species", [])
                        unmapped_origs = {u["original"] for u in unmapped}
                        all_species = set(all_mappings.keys()) | unmapped_origs

                        if all_species:
                            ui.label(t("species_mappings")).classes(
                                "text-subtitle1 font-weight-medium q-mb-sm"
                            )
                            ui.label(t("edit_mappings_desc")).classes("text-caption  q-mb-md")

                            species_map = dp.get_species_display_map(get_language())
                            select_options = {"": ""}
                            select_options.update(species_map)

                            for orig in sorted(all_species):
                                current_mapping = all_mappings.get(orig, "")
                                is_unmapped = orig in unmapped_origs
                                with ui.row().classes("w-full items-center q-mb-sm"):
                                    ui.label(orig).classes(
                                        f"col {'text-negative' if is_unmapped else ''}"
                                    )
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
                                    uploaded_df = _get_df_from_state("uploaded_df")
                                    mappings = get_state_val("species_mappings", {})

                                    (
                                        cleaned_df,
                                        errors_df,
                                        _,
                                        unmapped_species,
                                    ) = await run.io_bound(
                                        dp.validate_model_csv,
                                        uploaded_df,
                                        mappings,
                                        get_active_project_id(),
                                    )

                                    set_state_val(
                                        "cleaned_df",
                                        cleaned_df.to_dict(orient="records")
                                        if cleaned_df is not None
                                        else None,
                                    )
                                    set_state_val(
                                        "errors_df",
                                        errors_df.to_dict(orient="records")
                                        if errors_df is not None
                                        else None,
                                    )
                                    set_state_val("unmapped_species", unmapped_species)

                                    ui.notify(t("mappings_applied"), type="positive")
                                    refresh_results()
                                except Exception as exc:
                                    ui.notify(t("mapping_failed", error=str(exc)), type="negative")
                                finally:
                                    loading_dialog.close()

                            species_mappings = get_state_val("species_mappings", {})
                            pending_unmapped = [k for k, v in species_mappings.items() if not v]
                            cleaned_df = _get_df_from_state("cleaned_df")
                            can_apply = (
                                cleaned_df is not None
                                and not cleaned_df.empty
                                and not pending_unmapped
                            )

                            ui.button(
                                t("apply"),
                                icon="refresh",
                                on_click=apply_mappings,
                                color="primary",
                            ).props(f"wide {'disabled' if not can_apply else ''}")

                            if pending_unmapped:
                                ui.label(
                                    t("map_all_to_import", list=", ".join(pending_unmapped))
                                ).classes("text-warning text-caption q-mt-sm")

                    errors_df = _get_df_from_state("errors_df")
                    if errors_df is not None and not errors_df.empty:
                        with results_container:
                            ui.label(t("validation_errors_count", count=len(errors_df))).classes(
                                "text-negative q-mb-sm"
                            )

                            error_summary = errors_df["error"].value_counts().to_dict()
                            if error_summary:
                                ui.label(t("error_summary")).classes("text-body2 q-mt-sm")
                                for err, count in error_summary.items():
                                    ui.label(f"  • {err}: {count} rows").classes("text-body2")

                            with ui.expansion(
                                t("show_detailed_errors"), icon="table_rows"
                            ).classes("full-width q-mt-sm"):
                                error_cols = [
                                    {"field": c, "headerName": c} for c in errors_df.columns
                                ]
                                ui.aggrid(
                                    {
                                        "columnDefs": error_cols,
                                        "rowData": df_to_records(errors_df, limit=500),
                                        "columnSize": "responsive",
                                        "rowSelection": "single",
                                        "pagination": True,
                                        "paginationPageSize": 50,
                                    }
                                ).classes("h-64")

                    unmapped = get_state_val("unmapped_species", [])
                    if unmapped:
                        with results_container:
                            ui.label(t("unmapped_species_count", count=len(unmapped))).classes(
                                "text-warning q-mb-sm"
                            )

                    cleaned_df = _get_df_from_state("cleaned_df")
                    species_mappings = get_state_val("species_mappings", {})
                    can_import = (
                        cleaned_df is not None
                        and not cleaned_df.empty
                        and all(v for v in species_mappings.values())
                    )

                    with results_container:
                        pending_warning_holder[0] = ui.label("").classes("text-warning q-mb-sm")
                        pending_warning_holder[0].visible = False
                        update_import_button()

                        import_button_holder[0] = ui.button(
                            t("import_valid_rows"),
                            on_click=do_import,
                            color="primary",
                        ).props(f"wide {'disabled' if not can_import else ''}")

            with ui.tab_panel(tab_annotations):
                with ui.row().classes("w-full gap-md q-mt-md items-start"):
                    with ui.card().classes("col q-pa-md"):
                        ui.label(t("upload_csv")).classes(
                            "text-subtitle2 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("annotation_import_desc")).classes("text-caption  q-mb-md")

                        annotation_import_status = ui.label("").classes("text-body2 ")

                        async def handle_annotation_upload(e):
                            try:
                                content = await e.file.read()
                                df = pd.read_csv(io.BytesIO(content))
                                result = await run.io_bound(
                                    dp.import_annotations_csv, df, get_active_project_id()
                                )
                                msg = f"Imported {result['imported']} videos."
                                if result["skipped"]:
                                    msg += f" Skipped {len(result['skipped'])} unknown video IDs."
                                annotation_import_status.set_text(msg)
                                ui.notify(msg, type="positive")
                            except Exception as exc:
                                ui.notify(f"Import failed: {exc}", type="negative")

                        ui.upload(
                            on_upload=handle_annotation_upload,
                            multiple=False,
                            label=t("choose_annotations_csv"),
                            auto_upload=True,
                        ).props("accept=.csv")

                    with ui.card().classes("col q-pa-md"):
                        ui.label(t("export_annotations")).classes(
                            "text-subtitle2 font-weight-medium q-mb-xs"
                        )
                        ui.label(t("annotation_export_desc")).classes("text-caption q-mb-md")

                        async def do_export():
                            try:
                                df = await run.io_bound(
                                    dp.export_annotations_csv,
                                    get_active_project_id(),
                                    get_language(),
                                )
                                csv_bytes = df.to_csv(index=False).encode("utf-8")
                                ui.download(csv_bytes, "annotations.csv")
                            except Exception as exc:
                                ui.notify(f"Export failed: {exc}", type="negative")

                        ui.button(
                            t("export_annotations"), icon="download", on_click=do_export
                        ).props("flat color=primary")
