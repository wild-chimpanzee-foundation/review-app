from __future__ import annotations

from datetime import date

import pandas as pd
from nicegui import run, ui

from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_state_val,
    set_state_val,
)
from review_app.app.translations import t
from review_app.app.utils import user_error_message
from review_app.backend.utils import df_to_records

from ._helpers import (
    auto_suggest_ann_cols,
    col_val,
    get_df_from_state,
    make_col_selects,
    read_upload_file,
    render_species_mappings,
)

_MAPPINGS_KEY = "ann_species_mappings"

# Required path-matching columns — default to the first column in the CSV
_REQUIRED_COLS = ("ann_folder_col", "ann_video_col", "ann_species_col")
# Optional annotation columns — default to "" (none)
_OPTIONAL_COLS = (
    "ann_data_type_col",
    "ann_data_type_val",
    "ann_behavior_col",
    "ann_count_col",
    "ann_observer_col",
    "ann_timestamp_col",
    "ann_is_blank_col",
)

_NONE_VALUE = ""


def _is_app_format(columns: list[str]) -> bool:
    col_set = set(columns)
    return ("video_path" in col_set or "video_id" in col_set) and "is_blank" in col_set


def setup_annotations_tab(dp, loading_dialog) -> None:
    # ── Export ────────────────────────────────────────────────────────────────
    with ui.card().classes("full-width q-pa-md q-mb-md"):
        ui.label(t("export_annotations")).classes("text-subtitle2 font-weight-medium q-mb-xs")
        ui.label(t("annotation_export_desc")).classes("text-caption q-mb-md")

        async def do_export() -> None:
            try:
                df = await run.io_bound(dp.export_annotations_csv, get_active_project_id())
                project_name = (
                    df["project_name"].iloc[0].replace(" ", "_")
                    if not df.empty and "project_name" in df.columns
                    else "project"
                )
                annotator = get_annotator_name().replace(" ", "_")
                filename = f"annotations_{project_name}_{annotator}_{date.today()}.csv"
                ui.download(df.to_csv(index=False).encode("utf-8"), filename)
            except Exception as exc:
                ui.notify(t("export_failed", error=user_error_message(exc)), type="negative")

        async def do_export_ai() -> None:
            try:
                project_id = get_active_project_id()
                df = await run.io_bound(dp.export_model_annotations_csv, project_id)
                project_name = project_id.replace(" ", "_") if project_id else "project"
                filename = f"ai_annotations_{project_name}_{date.today()}.csv"
                ui.download(df.to_csv(index=False).encode("utf-8"), filename)
            except Exception as exc:
                ui.notify(t("export_failed", error=user_error_message(exc)), type="negative")

        with ui.row().classes("gap-sm"):
            ui.button(t("export_annotations"), icon="download", on_click=do_export).props(
                "flat color=primary"
            )
            ui.button(t("export_ai_annotations"), icon="download", on_click=do_export_ai).props(
                "flat color=primary"
            )

    # ── Import ────────────────────────────────────────────────────────────────
    with ui.card().classes("full-width q-pa-md q-mb-md"):
        ui.label(t("upload_csv")).classes("text-subtitle2 font-weight-medium q-mb-xs")
        ui.label(t("ann_import_desc")).classes("text-caption q-mb-md")

        with ui.row().classes("items-center gap-sm q-mb-md"):
            ui.label(t("csv_mode_label")).classes("text-caption")
            mode_toggle = ui.toggle(
                {"override": t("mode_override"), "append": t("mode_append")},
                value=get_state_val("manual_import_mode") or "override",
            ).props("dense size=sm")

            async def on_mode_change() -> None:
                set_state_val("manual_import_mode", mode_toggle.value)
                if get_state_val("ann_format") == "app" and get_state_val("ann_df_records"):
                    await _run_app_validate(dp, loading_dialog, results_ui, results_container)

            mode_toggle.on_value_change(on_mode_change)

        import_status = ui.label("").classes("text-body2")
        upload_holder: list = [None]

        col_config_section = ui.element("div").classes("w-full q-mb-md")
        col_config_section.visible = False

        results_container = ui.column().classes("w-full q-mt-md")
        results_container.visible = False

        # ── External format: column config ────────────────────────────────────

        @ui.refreshable
        def col_config_ui() -> None:
            col_config_section.clear()
            columns = get_state_val("ann_columns") or []
            if not columns:
                return

            required_opts = {c: c for c in columns}
            optional_opts = {_NONE_VALUE: f"— {t('historic_col_none')} —", **required_opts}
            path_mode = get_state_val("ann_path_mode") or "split"

            with col_config_section:
                ui.label(t("historic_path_matching")).classes(
                    "text-caption text-grey q-mb-xs q-mt-sm"
                )
                path_toggle = ui.toggle(
                    {
                        "split": t("ann_path_mode_split"),
                        "single": t("ann_path_mode_single"),
                    },
                    value=path_mode,
                ).props("dense size=sm q-mb-sm")

                async def on_path_mode_change() -> None:
                    set_state_val("ann_path_mode", path_toggle.value)
                    col_config_ui.refresh()
                    await _run_validate(dp, loading_dialog, results_ui, results_container)

                path_toggle.on_value_change(on_path_mode_change)

                with ui.row().classes("w-full gap-md q-mb-sm items-end"):
                    if path_mode == "single":
                        sels = make_col_selects([("ann_path_col", required_opts)])
                    else:
                        sels = make_col_selects(
                            [
                                ("ann_folder_col", required_opts),
                                ("ann_video_col", required_opts),
                            ]
                        )

                ui.label(t("historic_annotation_cols")).classes(
                    "text-caption text-grey q-mb-xs q-mt-sm"
                )
                with ui.row().classes("w-full gap-md q-mb-sm items-end"):
                    sels += make_col_selects(
                        [
                            ("ann_species_col", required_opts),
                            ("ann_behavior_col", optional_opts),
                            ("ann_count_col", optional_opts),
                            ("ann_observer_col", optional_opts),
                            ("ann_timestamp_col", optional_opts),
                        ]
                    )

                ui.label(t("historic_extra_cols")).classes(
                    "text-caption text-grey q-mb-xs q-mt-sm"
                )
                with ui.row().classes("w-full gap-md q-mb-sm items-end"):
                    sels += make_col_selects([("ann_is_blank_col", optional_opts)])

                    tag_sel = (
                        ui.select(
                            label=t("ann_tag_cols"),
                            options=required_opts,
                            value=get_state_val("ann_tag_cols") or [],
                            multiple=True,
                            with_input=True,
                        )
                        .props("outlined dense use-chips")
                        .classes("col")
                    )
                    tag_sel._props["hint"] = t("ann_tag_cols_hint")

                with ui.row().classes("w-full gap-md q-mb-sm items-end"):
                    sels += make_col_selects([("ann_data_type_col", optional_opts)])

                    data_type_val_sel = (
                        ui.select(
                            label=t("ann_data_type_val"),
                            options={},
                            value=get_state_val("ann_data_type_val") or None,
                            with_input=True,
                        )
                        .props("outlined dense")
                        .classes("col")
                    )
                    data_type_val_sel._props["hint"] = t("ann_data_type_val_hint")

                async def on_col_change() -> None:
                    prev_data_type_col = get_state_val("ann_data_type_col")
                    for key, sel in sels:
                        set_state_val(key, sel.value)
                    set_state_val("ann_tag_cols", tag_sel.value)
                    set_state_val("ann_data_type_val", data_type_val_sel.value)
                    if get_state_val("ann_data_type_col") != prev_data_type_col:
                        new_col = get_state_val("ann_data_type_col") or ""
                        set_state_val("ann_data_type_val", None)
                        new_opts: dict[str, str] = {}
                        if new_col:
                            df = get_df_from_state("ann_df_records")
                            if df is not None and new_col in df.columns:
                                vals = sorted(
                                    df[new_col].dropna().astype(str).str.strip().unique().tolist()
                                )
                                new_opts = {v: v for v in vals if v}
                        data_type_val_sel.set_options(new_opts, value=None)
                    await _run_validate(dp, loading_dialog, results_ui, results_container)

                for _, sel in sels:
                    sel.on_value_change(on_col_change)
                tag_sel.on_value_change(on_col_change)
                data_type_val_sel.on_value_change(on_col_change)

        col_config_ui()

        # ── External format: validation results + import button ───────────────

        @ui.refreshable
        def results_ui() -> None:
            validation = get_state_val("ann_validation")
            if not validation:
                return

            # ── App format: dry-run summary + import button ───────────────────
            if get_state_val("ann_format") == "app":
                matched = validation["matched"]
                skipped = validation["skipped"]
                blanks = validation.get("blanks_to_set", 0)
                ins = validation["obs_to_insert"]
                upd = validation["obs_to_update"]
                dlt = validation["obs_to_delete"]

                with ui.card().classes("full-width q-pa-md q-mb-md"):
                    color = "text-positive" if not skipped else "text-warning"
                    ui.label(t("app_csv_matched", count=matched)).classes(
                        f"text-subtitle2 {color} q-mb-xs"
                    )
                    if skipped:
                        with ui.expansion(
                            t("app_csv_skipped", count=len(skipped)), icon="warning"
                        ).classes("q-mt-xs full-width"):
                            ui.aggrid(
                                {
                                    "columnDefs": [{"field": "path", "headerName": "Path"}],
                                    "rowData": [{"path": p} for p in skipped[:500]],
                                    "columnSize": "autoSize",
                                    "pagination": True,
                                    "paginationPageSize": 50,
                                }
                            ).classes("h-48")

                    ui.separator().classes("q-my-sm")
                    if blanks:
                        ui.label(t("app_csv_blanks", count=blanks)).classes("text-body2")
                    if ins == 0 and upd == 0 and dlt == 0:
                        ui.label(t("app_csv_no_changes")).classes("text-caption text-grey")
                    else:
                        if ins:
                            ui.label(t("app_csv_obs_insert", count=ins)).classes(
                                "text-body2 text-positive"
                            )
                        if upd:
                            ui.label(t("app_csv_obs_update", count=upd)).classes("text-body2")
                        if dlt:
                            ui.label(t("app_csv_obs_delete", count=dlt)).classes(
                                "text-body2 text-warning"
                            )

                ui.separator().classes("q-my-md")

                async def do_app_import() -> None:
                    loading_dialog.open()
                    try:
                        df = get_df_from_state("ann_df_records")
                        if df is None:
                            ui.notify(t("no_data_import"), type="warning")
                            return
                        result = await run.io_bound(
                            dp.import_annotations_csv,
                            df,
                            get_active_project_id(),
                            mode=get_state_val("manual_import_mode") or "override",
                        )
                        summary = t("imported_annotations", count=result["imported"])
                        by_ann = {k: v for k, v in result.get("by_annotator", {}).items() if k}
                        if by_ann:
                            ann_str = ", ".join(
                                f"{name}: {count}"
                                for name, count in sorted(by_ann.items(), key=lambda x: -x[1])
                            )
                            summary += " " + t("import_by_annotator", summary=ann_str)
                        if result.get("custom_tags"):
                            summary += " " + t("import_custom_tags", count=result["custom_tags"])
                        skipped_vids = result.get("skipped", [])
                        notify_type = "positive"
                        if skipped_vids:
                            skip_msg = t("skipped_annotations", count=len(skipped_vids))
                            if len(skipped_vids) <= 5:
                                skip_msg += " (" + ", ".join(skipped_vids) + ")"
                            else:
                                skip_msg += (
                                    " ("
                                    + ", ".join(skipped_vids[:5])
                                    + f", +{len(skipped_vids) - 5} {t('more')})"
                                )
                            summary += " " + skip_msg
                            notify_type = "warning"
                        import_status.set_text(summary)
                        ui.notify(summary, type=notify_type)
                    except Exception as exc:
                        ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
                    finally:
                        loading_dialog.close()

                ui.button(
                    t("historic_import_btn"),
                    icon="file_upload",
                    on_click=do_app_import,
                    color="warning",
                )
                return

            # ── External format: validation results + import button ───────────
            matched = validation["matched"]
            unmatched = validation["unmatched"]
            skipped_inst = validation["skipped_installation"]
            unknown = validation["unknown_species"]

            with ui.card().classes("full-width q-pa-md q-mb-md"):
                color = "text-positive" if unmatched == 0 else "text-warning"
                ui.label(t("historic_matched_videos", count=matched)).classes(
                    f"text-subtitle2 {color} q-mb-xs"
                )
                if unmatched:
                    ui.label(t("historic_unmatched_videos", count=unmatched)).classes(
                        "text-body2 text-warning q-mb-xs"
                    )
                    unmatched_paths = validation.get("unmatched_paths") or []
                    if unmatched_paths:
                        with ui.expansion(
                            t("wide_format_unmatched", count=unmatched), icon="warning"
                        ).classes("q-mt-xs full-width"):
                            ui.aggrid(
                                {
                                    "columnDefs": [
                                        {
                                            "field": "path",
                                            "headerName": t("ann_folder_col")
                                            + "/"
                                            + t("ann_video_col"),
                                        }
                                    ],
                                    "rowData": df_to_records(
                                        pd.DataFrame({"path": unmatched_paths}), limit=500
                                    ),
                                    "columnSize": "autoSize",
                                    "pagination": True,
                                    "paginationPageSize": 50,
                                }
                            ).classes("h-48")
                if skipped_inst:
                    ui.label(t("historic_skipped_installation", count=skipped_inst)).classes(
                        "text-caption text-grey"
                    )

            if unknown:
                with ui.card().classes("full-width q-pa-md q-mb-md"):
                    ui.label(t("historic_unknown_species")).classes(
                        "text-subtitle2 text-warning q-mb-sm"
                    )
                    ui.separator().classes("q-mb-sm")

                    all_mappings = get_state_val(_MAPPINGS_KEY) or {}
                    all_species = set(unknown) | set(all_mappings.keys())
                    unmapped_origs = set(unknown)

                    async def apply_mappings() -> None:
                        loading_dialog.open()
                        try:
                            df = get_df_from_state("ann_df_records")
                            if df is None:
                                return
                            mappings = get_state_val(_MAPPINGS_KEY) or {}
                            is_single = (get_state_val("ann_path_mode") or "split") == "single"
                            result = await run.io_bound(
                                dp.validate_historic_csv,
                                df,
                                get_active_project_id(),
                                col_val("ann_folder_col"),
                                col_val("ann_video_col"),
                                col_val("ann_species_col"),
                                col_val("ann_data_type_col"),
                                col_val("ann_data_type_val"),
                                mappings,
                                col_val("ann_is_blank_col"),
                                get_state_val("ann_tag_cols") or [],
                                col_val("ann_path_col") if is_single else "",
                            )
                            set_state_val("ann_validation", result)
                            ui.notify(t("mappings_applied"), type="positive")
                            results_ui.refresh()
                        except Exception as exc:
                            ui.notify(
                                t("mapping_failed", error=user_error_message(exc)), type="negative"
                            )
                        finally:
                            loading_dialog.close()

                    pending = [k for k, v in (get_state_val(_MAPPINGS_KEY) or {}).items() if not v]
                    can_apply = bool(get_state_val("ann_df_records")) and not pending

                    render_species_mappings(
                        dp,
                        all_mappings,
                        unmapped_origs,
                        all_species,
                        apply_fn=apply_mappings,
                        update_import_button=results_ui.refresh,
                        can_apply=can_apply,
                        mappings_state_key=_MAPPINGS_KEY,
                        show_blank_option=True,
                        project_id=get_active_project_id(),
                    )

            ui.separator().classes("q-my-md")

            async def do_external_import() -> None:
                loading_dialog.open()
                try:
                    df = get_df_from_state("ann_df_records")
                    if df is None:
                        ui.notify(t("no_data_import"), type="warning")
                        return
                    is_single = (get_state_val("ann_path_mode") or "split") == "single"
                    result = await run.io_bound(
                        dp.import_historic_csv,
                        df,
                        get_active_project_id(),
                        col_val("ann_folder_col"),
                        col_val("ann_video_col"),
                        col_val("ann_species_col"),
                        col_val("ann_data_type_col"),
                        col_val("ann_data_type_val"),
                        col_val("ann_behavior_col"),
                        col_val("ann_count_col"),
                        col_val("ann_observer_col"),
                        col_val("ann_timestamp_col"),
                        get_state_val("manual_import_mode") or "override",
                        get_state_val(_MAPPINGS_KEY) or {},
                        col_val("ann_is_blank_col"),
                        get_state_val("ann_tag_cols") or [],
                        col_val("ann_path_col") if is_single else "",
                    )
                    msg = t("imported_historic", count=result["imported"])
                    if result["skipped"]:
                        msg += t("skipped_historic", count=len(result["skipped"]))
                    if result["skipped_observations"]:
                        msg += t("skipped_obs_historic", count=len(result["skipped_observations"]))
                    import_status.set_text(msg)
                    ui.notify(msg, type="positive")
                except Exception as exc:
                    ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
                finally:
                    loading_dialog.close()

            ui.button(
                t("historic_import_btn"),
                icon="file_upload",
                on_click=do_external_import,
                color="warning",
            )

        # ── Upload ────────────────────────────────────────────────────────────

        async def handle_upload(e) -> None:
            loading_dialog.open()
            try:
                content = await e.file.read()
                df = read_upload_file(content)
                columns = list(df.columns)

                if _is_app_format(columns):
                    # App format: validate first, show dry-run, then let user import
                    set_state_val("ann_format", "app")
                    set_state_val("ann_df_records", df.to_dict(orient="records"))
                    set_state_val("ann_validation", None)
                    col_config_section.visible = False
                    col_config_ui.refresh()
                    if upload_holder[0]:
                        upload_holder[0].visible = False
                else:
                    # External format: show column config + validate
                    set_state_val("ann_format", "external")
                    set_state_val("ann_df_records", df.to_dict(orient="records"))
                    set_state_val("ann_columns", columns)
                    set_state_val(_MAPPINGS_KEY, {})
                    set_state_val("ann_validation", None)

                    for key in _REQUIRED_COLS:
                        set_state_val(key, columns[0])
                    for key in _OPTIONAL_COLS:
                        set_state_val(key, "")
                    set_state_val("ann_tag_cols", [])
                    set_state_val("ann_path_mode", "split")
                    set_state_val("ann_path_col", columns[0])

                    # Apply conservative auto-suggestions based on column names
                    suggestions = auto_suggest_ann_cols(columns)
                    for key, val in suggestions.items():
                        if key == "ann_path_mode":
                            set_state_val(key, val)
                        elif val in columns:
                            set_state_val(key, val)

                    col_config_section.visible = True
                    col_config_ui.refresh()
                    if upload_holder[0]:
                        upload_holder[0].visible = False
            except Exception as exc:
                ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
                return
            finally:
                loading_dialog.close()

            fmt = get_state_val("ann_format")
            if fmt == "external":
                await _run_validate(dp, loading_dialog, results_ui, results_container)
            elif fmt == "app":
                await _run_app_validate(dp, loading_dialog, results_ui, results_container)

        upload_holder[0] = ui.upload(
            on_upload=handle_upload,
            multiple=False,
            label=t("choose_annotations_csv"),
            auto_upload=True,
        ).props("accept=.csv,.tsv,.txt")

    with results_container:
        results_ui()


async def _run_validate(dp, loading_dialog, results_ui, results_container) -> None:
    loading_dialog.open()
    try:
        df = get_df_from_state("ann_df_records")
        if df is None:
            return
        mappings = get_state_val(_MAPPINGS_KEY) or {}
        is_single = (get_state_val("ann_path_mode") or "split") == "single"
        result = await run.io_bound(
            dp.validate_historic_csv,
            df,
            get_active_project_id(),
            col_val("ann_folder_col"),
            col_val("ann_video_col"),
            col_val("ann_species_col"),
            col_val("ann_data_type_col"),
            col_val("ann_data_type_val"),
            mappings,
            col_val("ann_is_blank_col"),
            get_state_val("ann_tag_cols") or [],
            col_val("ann_path_col") if is_single else "",
        )
        set_state_val("ann_validation", result)
        results_container.visible = True
        results_ui.refresh()
    except Exception as exc:
        ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
    finally:
        loading_dialog.close()


async def _run_app_validate(dp, loading_dialog, results_ui, results_container) -> None:
    loading_dialog.open()
    try:
        df = get_df_from_state("ann_df_records")
        if df is None:
            return
        result = await run.io_bound(
            dp.validate_annotations_csv,
            df,
            get_active_project_id(),
            get_state_val("manual_import_mode") or "override",
        )
        set_state_val("ann_validation", result)
        results_container.visible = True
        results_ui.refresh()
    except Exception as exc:
        ui.notify(t("import_failed", error=user_error_message(exc)), type="negative")
    finally:
        loading_dialog.close()
