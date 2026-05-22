from __future__ import annotations

import io
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
        self._active_annotators: set[str] | None = None
        self.camera_stats: list[dict] = []

    @ui.refreshable_method
    def render(self) -> None:
        dp = self.dp
        pid = self.project_id
        annotators = dp.get_all_annotators()
        self.camera_stats = dp.get_camera_stats(pid)
        camera_assignment = dp.get_camera_assignment_map(pid)
        for cam in camera_assignment:
            if cam not in self._pending:
                self._pending[cam] = camera_assignment[cam]

        if self._active_annotators is None:
            self._active_annotators = {v for v in self._pending.values() if v is not None}

        # ── Annotator registry ─────────────────────────────────────────────────
        ui.label(t("distribution_annotators_label")).classes("text-caption text-grey-6 q-mb-xs")

        name_input: list[ui.input] = []

        async def add_annotator():
            name = (name_input[0].value or "").strip()
            if not name:
                return
            await run.io_bound(dp.add_annotator, name)
            self._active_annotators.add(name)
            name_input[0].value = ""
            self.render.refresh()
            self.render_summary.refresh()

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
                    active = ann in self._active_annotators

                    def _toggle(a=ann):
                        if a in self._active_annotators:
                            self._active_annotators.discard(a)
                            self._pending = {k: None if v == a else v for k, v in self._pending.items()}
                        else:
                            self._active_annotators.add(a)
                        self.render.refresh()
                        self.render_summary.refresh()

                    chip = ui.chip(ann, icon="person", on_click=_toggle)
                    chip.props("clickable")
                    if active:
                        chip.props("color=primary")
                    else:
                        chip.props("outline color=grey-6")

        # ── Camera assignment ──────────────────────────────────────────────────
        ui.separator().classes("q-my-sm")
        ui.label(t("distribution_cameras_label")).classes("text-caption text-grey-6 q-mb-xs")

        if not self.camera_stats:
            ui.label(t("distribution_no_cameras")).classes("text-caption text-grey-5")
            return

        annotator_options = {a: a for a in annotators}

        async def auto_distribute():
            if not self._active_annotators:
                ui.notify(t("distribution_no_annotators"), type="warning")
                return
            result = await run.io_bound(dp.auto_distribute, pid, list(self._active_annotators))
            self._pending = {c: None for c in self._pending}
            for ann, cams in result.items():
                for cam in cams:
                    self._pending[cam] = ann
            self.render.refresh()
            self.render_summary.refresh()

        async def apply_distribution():
            assignment: dict[str, list[str]] = {}
            for cam, ann in self._pending.items():
                if ann:
                    assignment.setdefault(ann, []).append(cam)
            n = await run.io_bound(dp.apply_distribution, pid, assignment)
            filled = len(assignment)
            ui.notify(t("distribution_applied", n=n, annotators=filled), type="positive")
            self.render.refresh()
            self.render_summary.refresh()

        with ui.row().classes("gap-sm q-mb-sm"):
            ui.button(
                t("distribution_auto_btn"), icon="auto_fix_high", on_click=auto_distribute
            ).props("outline color=secondary size=sm").tooltip(t("distribution_auto_tooltip"))

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

            for cam in self.camera_stats:
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
                        self.render_summary.refresh()

                    return _on_change

                sel.on_value_change(_make_handler(cam_id))

        ui.button(
            t("distribution_apply_btn"), icon="check", on_click=apply_distribution
        ).props("unelevated color=primary size=sm").classes("q-mt-sm")

    @ui.refreshable_method
    def render_summary(self) -> None:
        annotator_map: dict[str, dict] = {}
        for cam in self.camera_stats:
            ann = self._pending.get(cam["camera_id"])
            if ann is None:
                continue
            if ann not in annotator_map:
                annotator_map[ann] = {"annotator": ann, "cameras": 0, "video_count": 0, "hours": 0.0}
            annotator_map[ann]["cameras"] += 1
            annotator_map[ann]["video_count"] += cam["video_count"]
            annotator_map[ann]["hours"] = round(annotator_map[ann]["hours"] + cam["hours"], 2)
        summary = sorted(annotator_map.values(), key=lambda r: r["annotator"])
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
            section = DistributionSection(dp, project_id)
            section.render()
            section.render_summary()

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
                    ui.notify(t("bundle_include_required"), type="warning")
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
                    ui.notify(t("bundle_include_required"), type="warning")
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
                    df = pd.read_csv(io.StringIO(content))
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
