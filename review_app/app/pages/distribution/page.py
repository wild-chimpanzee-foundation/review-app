from __future__ import annotations

from datetime import date

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    render_uninitialized_state,
    user_error_message,
)


class DistributionSection:
    def __init__(self, dp, project_id: str):
        self.dp = dp
        self.project_id = project_id
        self._pending: dict[str, str | None] = {}

    @ui.refreshable_method
    def render(self) -> None:
        dp = self.dp
        pid = self.project_id
        annotators = dp.get_all_annotators()
        camera_stats = dp.get_camera_stats(pid)
        camera_assignment = dp.get_camera_assignment_map(pid)
        for cam in camera_assignment:
            if cam not in self._pending:
                self._pending[cam] = camera_assignment[cam]

        # ── Annotator registry ─────────────────────────────────────────────────
        ui.label(t("distribution_annotators_label")).classes("text-caption text-grey-6 q-mb-xs")

        name_input: list[ui.input] = []

        async def add_annotator():
            name = (name_input[0].value or "").strip()
            if not name:
                return
            await run.io_bound(dp.add_annotator, name)
            name_input[0].value = ""
            self.render.refresh()

        with ui.row().classes("items-center gap-sm q-mb-sm"):
            inp = (
                ui.input(placeholder=t("distribution_annotator_placeholder"))
                .props("outlined dense")
                .classes("flex-grow")
            )
            inp.on("keydown.enter", add_annotator)
            name_input.append(inp)
            ui.button(t("distribution_add_btn"), on_click=add_annotator).props(
                "outline color=primary size=sm"
            )

        if not annotators:
            ui.label(t("distribution_no_annotators")).classes("text-caption text-grey-5 q-mb-md")
        else:
            with ui.row().classes("gap-xs flex-wrap q-mb-md"):
                for ann in annotators:
                    with ui.chip(ann, icon="person", removable=True).props(
                        "outline color=primary"
                    ) as chip:
                        pass

                    async def _on_remove(a=ann):
                        with ui.dialog() as dlg, ui.card().classes("q-pa-md"):
                            ui.label(t("distribution_remove_annotator_confirm", name=a)).classes(
                                "text-body2 q-mb-md"
                            )
                            with ui.row().classes("gap-sm justify-end"):
                                ui.button(t("cancel"), on_click=dlg.close).props("flat")
                                ui.button(
                                    t("delete"),
                                    on_click=lambda d=dlg, name=a: _do_remove(d, name),
                                    color="negative",
                                ).props("unelevated")
                        dlg.open()

                    chip.on("remove", _on_remove)

        async def _do_remove(dlg, name: str) -> None:
            dlg.close()
            await run.io_bound(dp.remove_annotator, name)
            self._pending = {k: None if v == name else v for k, v in self._pending.items()}
            self.render.refresh()

        # ── Camera assignment ──────────────────────────────────────────────────
        ui.separator().classes("q-my-sm")
        ui.label(t("distribution_cameras_label")).classes("text-caption text-grey-6 q-mb-xs")

        if not camera_stats:
            ui.label(t("distribution_no_cameras")).classes("text-caption text-grey-5")
            return

        annotator_options = {a: a for a in annotators}

        async def auto_distribute():
            if not annotators:
                ui.notify(t("distribution_no_annotators"), type="warning")
                return
            result = await run.io_bound(dp.auto_distribute, pid, annotators)
            self._pending = {c: None for c in self._pending}
            for ann, cams in result.items():
                for cam in cams:
                    self._pending[cam] = ann
            self.render.refresh()

        async def apply_distribution():
            assignment: dict[str, list[str]] = {a: [] for a in annotators}
            for cam, ann in self._pending.items():
                if ann and ann in assignment:
                    assignment[ann].append(cam)
            n = await run.io_bound(dp.apply_distribution, pid, assignment)
            filled = sum(1 for cams in assignment.values() if cams)
            ui.notify(t("distribution_applied", n=n, annotators=filled), type="positive")
            self.render.refresh()

        with ui.row().classes("gap-sm q-mb-sm"):
            ui.button(
                t("distribution_auto_btn"), icon="auto_fix_high", on_click=auto_distribute
            ).props("outline color=secondary size=sm").tooltip(t("distribution_auto_tooltip"))
            ui.button(
                t("distribution_apply_btn"), icon="check", on_click=apply_distribution
            ).props("unelevated color=primary size=sm")

        with ui.grid(columns="1fr auto auto 200px").classes(
            "w-full gap-x-md gap-y-xs items-center"
        ):
            ui.label(t("distribution_camera_col")).classes(
                "text-caption text-grey-6 font-weight-bold"
            )
            ui.label(t("distribution_videos_col")).classes(
                "text-caption text-grey-6 font-weight-bold text-right"
            )
            ui.label(t("distribution_hours_col")).classes(
                "text-caption text-grey-6 font-weight-bold text-right"
            )
            ui.label(t("distribution_assigned_col")).classes(
                "text-caption text-grey-6 font-weight-bold"
            )

            for cam in camera_stats:
                cam_id = cam["camera_id"]
                ui.label(cam_id).classes("text-body2")
                ui.label(str(cam["video_count"])).classes("text-body2 text-right")
                ui.label(f"{cam['hours']:.1f}").classes("text-body2 text-right")
                sel = ui.select(
                    options=annotator_options,
                    value=self._pending.get(cam_id),
                    clearable=True,
                ).props("outlined dense")

                def _make_handler(c):
                    def _on_change(e):
                        self._pending[c] = e.value or None

                    return _on_change

                sel.on_value_change(_make_handler(cam_id))

        # ── Current distribution summary ───────────────────────────────────────
        summary = dp.get_assignment_summary(pid)
        if summary:
            ui.separator().classes("q-my-sm")
            ui.label(t("distribution_summary_label")).classes("text-caption text-grey-6 q-mb-xs")
            with ui.grid(columns="1fr auto auto auto").classes(
                "w-full gap-x-md gap-y-xs items-center"
            ):
                for header in [
                    t("distribution_summary_annotator"),
                    t("distribution_summary_cameras"),
                    t("distribution_summary_videos"),
                    t("distribution_summary_hours"),
                ]:
                    ui.label(header).classes("text-caption text-grey-6 font-weight-bold")
                for row in summary:
                    ui.label(row["annotator"]).classes("text-body2")
                    ui.label(str(row["cameras"])).classes("text-body2 text-right")
                    ui.label(str(row["video_count"])).classes("text-body2 text-right")
                    ui.label(str(row["hours"])).classes("text-body2 text-right")


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
            ui.label(t("bundle_annotator_filter_label")).classes(
                "text-caption text-grey-6 q-mb-xs"
            )
            annotator_select = (
                ui.select(
                    options=annotator_options,
                    value="",
                )
                .props("outlined dense")
                .classes("w-64")
            )

            def _get_include() -> list[str]:
                include: list[str] = []
                if include_species.value:
                    include.append("species")
                if include_tags.value:
                    include.append("tags")
                if include_model.value:
                    include.append("model_annotations")
                if include_metadata.value:
                    include.append("metadata")
                return include

            async def download_bundle():
                include = _get_include()
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
                    ann_suffix = (
                        f"_{chosen_annotator.replace(' ', '_')}" if chosen_annotator else ""
                    )
                    filename = f"bundle{ann_suffix}_{date.today()}.zip"
                    ui.download(zip_bytes, filename)
                except Exception as exc:
                    ui.notify(t("bundle_error", msg=user_error_message(exc)), type="negative")

            async def download_all_bundles():
                if not annotators:
                    ui.notify(t("bundle_no_annotators"), type="warning")
                    return
                include = _get_include()
                if not include:
                    ui.notify(t("bundle_include_label"), type="warning")
                    return
                try:
                    zip_bytes = await run.io_bound(dp.export_all_bundles, project_id, include)
                    filename = f"all_bundles_{date.today()}.zip"
                    ui.download(zip_bytes, filename)
                except Exception as exc:
                    ui.notify(t("bundle_error", msg=user_error_message(exc)), type="negative")

            with ui.row().classes("gap-sm q-mt-md"):
                ui.button(
                    t("bundle_download_btn"), icon="download", on_click=download_bundle
                ).props("unelevated color=primary")
                ui.button(
                    t("bundle_download_all_btn"), icon="folder_zip", on_click=download_all_bundles
                ).props("outline color=primary").tooltip(t("bundle_download_all_tooltip"))

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
                    ui.label(t("bundle_import_result_title")).classes("text-subtitle2 q-mb-sm")
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
                    zip_bytes = await e.file.read()
                    results = await run.io_bound(dp.import_project_bundle, project_id, zip_bytes)
                    bundle_result_container.visible = True
                    bundle_result_ui.refresh(results)
                    ui.notify(t("bundle_import_title"), type="positive")
                except Exception as exc:
                    ui.notify(t("bundle_error", msg=user_error_message(exc)), type="negative")

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
                    content = (await e.file.read()).decode("utf-8")
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
                    ui.notify(f"{e.name}: {user_error_message(exc)}", type="negative")

            ui.upload(
                on_upload=handle_batch_upload,
                multiple=True,
                label=t("batch_import_btn"),
                auto_upload=True,
            ).props("accept=.csv")
