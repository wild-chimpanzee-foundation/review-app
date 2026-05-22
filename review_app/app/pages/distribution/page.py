from __future__ import annotations

from datetime import date

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_active_project_id, get_annotator_name
from review_app.app.translations import t
from review_app.app.utils import get_or_create_data_provider, render_uninitialized_state, user_error_message
from review_app.app.pages.settings.distribution import DistributionSection


async def setup_distribution():
    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    shared_header()

    project_id = get_active_project_id()

    if not dp or not project_id:
        render_uninitialized_state()
        return

    with ui.column().classes("w-full q-pa-lg").style("max-width: 1400px; margin: 0 auto"):
        ui.label(t("nav_distribution")).classes("text-h5 font-weight-bold q-mb-lg")

        # ── Work Distribution ─────────────────────────────────────────────────
        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("group", size="sm").classes("text-primary q-mr-sm")
                ui.label(t("distribution_title")).classes("text-subtitle1 font-weight-medium")
            ui.label(t("distribution_desc")).classes("text-caption text-grey-6 q-mb-md")
            DistributionSection(dp, project_id).render()

        # ── Bundle Export ─────────────────────────────────────────────────────
        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("folder_zip", size="sm").classes("text-primary q-mr-sm")
                ui.label(t("bundle_export_title")).classes("text-subtitle1 font-weight-medium")
            ui.label(t("bundle_export_desc")).classes("text-caption text-grey-6 q-mb-md")

            annotators = await run.io_bound(dp.get_all_annotators)
            annotator_options = {"": t("bundle_annotator_none")} | {a: a for a in annotators}

            include_species = ui.checkbox(t("bundle_include_species"), value=True)
            include_tags = ui.checkbox(t("bundle_include_tags"), value=True)
            include_model = ui.checkbox(t("bundle_include_model_annotations"), value=True)
            include_metadata = ui.checkbox(t("bundle_include_metadata"), value=True)

            ui.separator().classes("q-my-sm")
            ui.label(t("bundle_annotator_filter_label")).classes("text-caption text-grey-6 q-mb-xs")
            annotator_select = ui.select(
                options=annotator_options,
                value="",
            ).props("outlined dense").classes("w-64")

            async def download_bundle():
                include: list[str] = []
                if include_species.value:
                    include.append("species")
                if include_tags.value:
                    include.append("tags")
                if include_model.value:
                    include.append("model_annotations")
                if include_metadata.value:
                    include.append("metadata")
                if not include:
                    ui.notify(t("bundle_include_label"), type="warning")
                    return

                chosen_annotator = annotator_select.value or None
                camera_ids: list[str] | None = None
                if chosen_annotator:
                    camera_map = await run.io_bound(dp.get_camera_assignment_map, project_id)
                    camera_ids = [c for c, a in camera_map.items() if a == chosen_annotator]

                try:
                    zip_bytes = await run.io_bound(
                        dp.export_project_bundle, project_id, include, camera_ids
                    )
                    ann_suffix = f"_{chosen_annotator.replace(' ', '_')}" if chosen_annotator else ""
                    filename = f"bundle{ann_suffix}_{date.today()}.zip"
                    ui.download(zip_bytes, filename)
                except Exception as exc:
                    ui.notify(
                        t("bundle_error", msg=user_error_message(exc)), type="negative"
                    )

            ui.button(t("bundle_download_btn"), icon="download", on_click=download_bundle).props(
                "unelevated color=primary q-mt-md"
            )

        # ── Bundle Import ─────────────────────────────────────────────────────
        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("unarchive", size="sm").classes("text-primary q-mr-sm")
                ui.label(t("bundle_import_title")).classes("text-subtitle1 font-weight-medium")
            ui.label(t("bundle_import_desc")).classes("text-caption text-grey-6 q-mb-md")

            bundle_result_container = ui.column().classes("w-full q-mt-md")
            bundle_result_container.visible = False

            @ui.refreshable
            def bundle_result_ui(results: dict | None = None):
                bundle_result_container.clear()
                if not results:
                    return
                with bundle_result_container:
                    ui.label(t("bundle_import_result_title")).classes(
                        "text-subtitle2 q-mb-sm"
                    )
                    component_labels = {
                        "species": t("bundle_component_species"),
                        "tags": t("bundle_component_tags"),
                        "model_annotations": t("bundle_component_model_annotations"),
                        "metadata": t("bundle_component_metadata"),
                    }
                    for key, label in component_labels.items():
                        if key in results:
                            r = results[key]
                            if "error" in r:
                                status = t("bundle_error", msg=r["error"])
                                color = "negative"
                            else:
                                n = r.get("imported") or r.get("updated") or 0
                                status = t("bundle_imported_n", n=n)
                                color = "positive"
                            with ui.row().classes("items-center gap-sm"):
                                ui.badge(label, color="grey").props("outline")
                                ui.label(status).classes(f"text-{color}")

            async def handle_bundle_upload(e):
                bundle_result_container.visible = False
                bundle_result_ui.refresh(None)
                try:
                    zip_bytes = e.content.read()
                    results = await run.io_bound(
                        dp.import_project_bundle, project_id, zip_bytes
                    )
                    bundle_result_container.visible = True
                    bundle_result_ui.refresh(results)
                    ui.notify(t("bundle_import_title"), type="positive")
                except Exception as exc:
                    ui.notify(
                        t("bundle_error", msg=user_error_message(exc)), type="negative"
                    )

            ui.upload(
                on_upload=handle_bundle_upload,
                multiple=False,
                label=t("bundle_import_btn"),
                auto_upload=True,
            ).props("accept=.zip")

            with bundle_result_container:
                bundle_result_ui()

        # ── Batch Annotation Import ───────────────────────────────────────────
        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-sm"):
                ui.icon("playlist_add", size="sm").classes("text-primary q-mr-sm")
                ui.label(t("batch_import_title")).classes("text-subtitle1 font-weight-medium")
            ui.label(t("batch_import_desc")).classes("text-caption text-grey-6 q-mb-md")

            batch_status = ui.label("").classes("text-body2")
            _batch_state: dict = {"total_imported": 0, "total_skipped": 0, "file_count": 0}

            async def handle_batch_upload(e):
                try:
                    content = e.content.read().decode("utf-8")
                    import io as _io
                    df = pd.read_csv(_io.StringIO(content))
                    result = await run.io_bound(
                        dp.import_annotations_csv, df, project_id, "append"
                    )
                    _batch_state["total_imported"] += result.get("imported", 0)
                    _batch_state["total_skipped"] += len(result.get("skipped", []))
                    _batch_state["file_count"] += 1
                    batch_status.set_text(
                        t(
                            "batch_import_summary",
                            files=_batch_state["file_count"],
                            imported=_batch_state["total_imported"],
                            skipped=_batch_state["total_skipped"],
                        )
                    )
                except Exception as exc:
                    ui.notify(
                        f"{e.name}: {user_error_message(exc)}", type="negative"
                    )

            ui.upload(
                on_upload=handle_batch_upload,
                multiple=True,
                label=t("batch_import_btn"),
                auto_upload=True,
            ).props("accept=.csv")
