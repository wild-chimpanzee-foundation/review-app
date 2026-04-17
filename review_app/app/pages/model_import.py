import io
from pathlib import Path

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_data_provider, get_state_val, set_data_provider, set_state_val
from review_app.backend.local_data_provider import LocalDataProvider


def _make_serializable(val):
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    records = []
    for _, row in df.head(500).iterrows():
        records.append({k: _make_serializable(v) for k, v in row.items()})
    return records


async def setup_model_import():
    dp = get_data_provider()
    if not dp:
        config_path = Path("config.yaml")
        if config_path.exists():
            dp = LocalDataProvider(str(config_path))
            set_data_provider(dp)
        else:
            with ui.card().classes("q-pa-xl"):
                ui.label("Error: Data provider not initialized").classes("text-h6 text-negative")
                ui.button("Set up", on_click=lambda: ui.navigate.to("/setup"), icon="settings")
            return

    set_state_val("uploaded_df", None)
    set_state_val("cleaned_df", None)
    set_state_val("errors_df", None)
    set_state_val("species_mappings", {})
    set_state_val("unmapped_species", [])

    import_button_holder: list = [None]
    pending_warning_holder: list = [None]

    with ui.column().classes("w-full q-pa-md"):
        with ui.row().classes("items-center q-mb-md"):
            ui.label("Model Output Import").classes("text-h5 text-primary font-weight-bold")

        ui.label("Upload model outputs as CSV and import them into model_annotations.").classes(
            "text-body2 q-mb-lg"
        )

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center"):
                ui.label("CSV Template").classes("text-subtitle1 font-weight-medium")

            templates = await run.io_bound(dp.get_csv_templates)
            csv_content = templates["model_annotations"]

            def download_template():
                ui.download(csv_content.encode("utf-8"), "model_annotations_template.csv")

            ui.button(
                "Download Unified Annotation Template", icon="download", on_click=download_template
            )

        with ui.card().classes("full-width q-mb-lg"):
            ui.label("Upload CSV").classes("text-subtitle1 font-weight-medium q-mb-md")

            upload_result = ui.label("Upload a CSV file to validate and import.").classes(
                "text-body2 text-grey-6"
            )

            async def handle_upload(e):
                try:
                    content = await e.file.read()
                    df = pd.read_csv(io.BytesIO(content))
                    set_state_val("uploaded_df", df)
                    upload_result.text = f"Loaded {len(df)} rows"

                    cleaned_df, errors_df, species_mappings, unmapped_species = await run.io_bound(
                        dp.validate_model_csv, df
                    )

                    # Debug: show what we got
                    ui.notify(
                        f"Debug: {len(species_mappings)} mappings, {len(unmapped_species)} unmapped"
                    )

                    set_state_val("cleaned_df", cleaned_df)
                    set_state_val("errors_df", errors_df)
                    set_state_val(
                        "species_mappings",
                        {m["original"]: m.get("mapped_to", "") for m in species_mappings},
                    )
                    set_state_val("unmapped_species", unmapped_species)

                    ui.notify("CSV validated!", type="positive")
                    refresh_results()
                except Exception as exc:
                    ui.notify(f"Error: {exc}", type="negative")

            ui.upload(on_upload=handle_upload, multiple=False, label="Choose CSV file")

        results_container = ui.card().classes("full-width q-mb-lg")
        mappings_container = ui.card().classes("full-width q-mb-lg")

        def update_import_button():
            cleaned_df = get_state_val("cleaned_df")
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
                    warning.text = f"Please map all species. Missing: {', '.join(pending)}"
                    warning.visible = True
                else:
                    warning.visible = False

        def refresh_results():
            results_container.clear()
            mappings_container.clear()

            with results_container:
                ui.label("Validation Result").classes("text-subtitle1 font-weight-medium q-mb-md")

                cleaned_df = get_state_val("cleaned_df")
                valid_count = len(cleaned_df) if cleaned_df is not None else 0
                errors_df = get_state_val("errors_df")
                unmapped = get_state_val("unmapped_species", [])
                invalid_count = (
                    len(errors_df) if errors_df is not None and not errors_df.empty else 0
                ) + len(unmapped)

                with ui.row().classes("gap-lg q-mb-md"):
                    with ui.card().classes("text-center q-pa-md"):
                        ui.label(str(valid_count)).classes(
                            "text-h5 font-weight-bold text-positive"
                        )
                        ui.label("Valid Rows").classes("text-caption text-grey-6")
                    with ui.card().classes("text-center q-pa-md"):
                        ui.label(str(invalid_count)).classes(
                            "text-h5 font-weight-bold text-negative"
                        )
                        ui.label("Invalid Rows").classes("text-caption text-grey-6")

            with mappings_container:
                all_mappings = dict(get_state_val("species_mappings", {}))
                unmapped = get_state_val("unmapped_species", [])
                unmapped_origs = {u["original"] for u in unmapped}
                all_species = set(all_mappings.keys()) | unmapped_origs

                # Debug output
                debug_text = f"Debug: {len(all_mappings)} mappings, {len(unmapped_origs)} unmapped, {len(all_species)} total"
                ui.label(debug_text).classes("text-caption text-grey")

                if all_species:
                    ui.label("Species Mappings").classes(
                        "text-subtitle1 font-weight-medium q-mb-sm"
                    )
                    ui.label("Edit species mappings. Click 'Apply Mappings' when done.").classes(
                        "text-caption text-grey-6 q-mb-md"
                    )

                    valid_species = dp.get_valid_species()
                    for orig in sorted(all_species):
                        current_mapping = all_mappings.get(orig, "")
                        is_unmapped = orig in unmapped_origs
                        with ui.row().classes("w-full items-center q-mb-sm"):
                            ui.label(orig).classes(f"col {'text-negative' if is_unmapped else ''}")
                            select = ui.select(
                                label="Mapped To",
                                options=[""] + valid_species,
                                value=current_mapping,
                            ).props("outlined dense class=col-4")

                            def make_update_fn(o, sel):
                                def update_mapping():
                                    mappings = get_state_val("species_mappings", {})
                                    mappings[o] = sel.value
                                    set_state_val("species_mappings", mappings)
                                    update_import_button()

                                return update_mapping

                            select.on_value_change(make_update_fn(orig, select))

                    async def apply_and_import():
                        try:
                            cleaned_df = get_state_val("cleaned_df")
                            df_to_import = cleaned_df.copy()
                            mappings = get_state_val("species_mappings", {})

                            if mappings:
                                species_mask = df_to_import["annotation_type"] == "species"
                                df_to_import.loc[species_mask, "value_text"] = df_to_import.loc[
                                    species_mask, "value_text"
                                ].replace(mappings)

                            result = await run.io_bound(
                                dp.import_model_csv, cleaned_df=df_to_import
                            )
                            ui.notify(
                                f"Imported {result.get('inserted_rows', 0)} rows!", type="positive"
                            )
                            set_state_val("uploaded_df", None)
                            set_state_val("cleaned_df", None)
                            set_state_val("errors_df", None)
                            set_state_val("species_mappings", {})
                            set_state_val("unmapped_species", [])
                            results_container.clear()
                            mappings_container.clear()
                            with results_container:
                                ui.label("Upload a CSV file to validate and import.").classes(
                                    "text-body2 text-grey-6"
                                )
                        except Exception as exc:
                            ui.notify(f"Import failed: {exc}", type="negative")

                    species_mappings = get_state_val("species_mappings", {})
                    pending_unmapped = [k for k, v in species_mappings.items() if not v]
                    cleaned_df = get_state_val("cleaned_df")
                    can_apply_and_import = (
                        cleaned_df is not None and not cleaned_df.empty and not pending_unmapped
                    )

                    ui.button(
                        "Apply Mappings & Import",
                        icon="upload",
                        on_click=apply_and_import,
                        color="primary",
                    ).props(f"wide {'disabled' if not can_apply_and_import else ''}")

                    if pending_unmapped:
                        ui.label(
                            f"Map all species to import: {', '.join(pending_unmapped)}"
                        ).classes("text-warning text-caption q-mt-sm")

            errors_df = get_state_val("errors_df")
            if errors_df is not None and not errors_df.empty:
                with results_container:
                    ui.label(f"{len(errors_df)} rows have validation errors.").classes(
                        "text-negative q-mb-sm"
                    )

                    error_summary = errors_df["error"].value_counts().to_dict()
                    if error_summary:
                        ui.label("Error summary:").classes("text-body2 q-mt-sm")
                        for err, count in error_summary.items():
                            ui.label(f"  • {err}: {count} rows").classes("text-body2")

                    with ui.expansion("Show detailed errors", icon="table_rows").classes(
                        "full-width q-mt-sm"
                    ):
                        error_cols = [{"field": c, "headerName": c} for c in errors_df.columns]
                        ui.aggrid(
                            {
                                "columnDefs": error_cols,
                                "rowData": _df_to_records(errors_df),
                                "columnSize": "responsive",
                                "rowSelection": "single",
                                "pagination": True,
                                "paginationPageSize": 50,
                            }
                        ).classes("h-64")

            unmapped = get_state_val("unmapped_species", [])
            if unmapped:
                with results_container:
                    ui.label(f"{len(unmapped)} species without fuzzy match.").classes(
                        "text-warning q-mb-sm"
                    )

            cleaned_df = get_state_val("cleaned_df")
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

            async def do_import():
                try:
                    cleaned_df = get_state_val("cleaned_df")
                    df_to_import = cleaned_df.copy()
                    mappings = get_state_val("species_mappings", {})

                    if mappings:
                        species_mask = df_to_import["annotation_type"] == "species"
                        df_to_import.loc[species_mask, "value_text"] = df_to_import.loc[
                            species_mask, "value_text"
                        ].replace(mappings)

                    result = await run.io_bound(dp.import_model_csv, cleaned_df=df_to_import)
                    ui.notify(f"Imported {result.get('inserted_rows', 0)} rows!", type="positive")
                    set_state_val("uploaded_df", None)
                    set_state_val("cleaned_df", None)
                    set_state_val("errors_df", None)
                    set_state_val("species_mappings", {})
                    set_state_val("unmapped_species", [])
                    results_container.clear()
                    mappings_container.clear()
                    with results_container:
                        ui.label("Upload a CSV file to validate and import.").classes(
                            "text-body2 text-grey-6"
                        )
                except Exception as exc:
                    ui.notify(f"Import failed: {exc}", type="negative")

            with results_container:
                import_button_holder[0] = ui.button(
                    "Import Valid Rows",
                    on_click=do_import,
                    color="primary",
                ).props(f"wide {'disabled' if not can_import else ''}")
