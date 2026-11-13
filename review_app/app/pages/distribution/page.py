from __future__ import annotations

import io

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
    def __init__(self, dp, project_id: str, on_change=None):
        self.dp = dp
        self.project_id = project_id
        self.on_change = on_change
        self._pending: dict[str, str | None] = {}
        self._active_annotators: set[str] | None = None
        self.camera_stats: list[dict] = []
        self.chunks: list[dict] = []

    @ui.refreshable_method
    def render(self) -> None:
        dp = self.dp
        pid = self.project_id
        annotators = dp.get_all_annotators()
        self.camera_stats = dp.get_camera_stats(pid)
        if not self.chunks:
            self.chunks = dp.get_video_chunks(pid)
        chunk_assignment = dp.get_chunk_assignment_map(pid, self.chunks)
        for chunk_id in chunk_assignment:
            if chunk_id not in self._pending:
                self._pending[chunk_id] = chunk_assignment[chunk_id]

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
                            self._pending = {
                                k: None if v == a else v for k, v in self._pending.items()
                            }
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

        # ── Distribution controls ──────────────────────────────────────────────
        ui.separator().classes("q-my-sm")

        if not self.camera_stats:
            ui.label(t("distribution_no_cameras")).classes("text-caption text-grey-5")
            return

        async def auto_distribute():
            if not self._active_annotators:
                ui.notify(t("distribution_no_annotators"), type="warning")
                return
            result, chunks = await run.io_bound(
                dp.auto_distribute_flexible, pid, list(self._active_annotators)
            )
            self.chunks = chunks
            self._pending = {ch["chunk_id"]: None for ch in chunks}
            for ann, chunk_ids in result.items():
                for cid in chunk_ids:
                    self._pending[cid] = ann
            self.render.refresh()
            self.render_summary.refresh()

        async def apply_distribution():
            chunk_assignment: dict[str, list[str]] = {}
            clear_chunks: list[str] = []
            for chunk_id, ann in self._pending.items():
                if ann:
                    chunk_assignment.setdefault(ann, []).append(chunk_id)
                else:
                    clear_chunks.append(chunk_id)
            n = await run.io_bound(
                dp.apply_chunk_assignment, pid, chunk_assignment, self.chunks, clear_chunks
            )
            filled = len(chunk_assignment)
            ui.notify(t("distribution_applied", n=n, annotators=filled), type="positive")
            self.render.refresh()
            self.render_summary.refresh()
            if self.on_change:
                self.on_change()

        async def reset_assignments():
            with ui.dialog() as confirm_dialog, ui.card():
                ui.label(t("distribution_reset_confirm")).classes("text-body1 q-mb-md")
                with ui.row().classes("gap-sm justify-end"):
                    ui.button(t("cancel"), on_click=confirm_dialog.close).props("outline size=sm")

                    async def _do_reset():
                        confirm_dialog.close()
                        await run.io_bound(dp.clear_all_assignments, pid)
                        self._pending = {}
                        self.chunks = []
                        self.render.refresh()
                        self.render_summary.refresh()
                        if self.on_change:
                            self.on_change()
                        ui.notify(t("distribution_reset_done"), type="positive")

                    ui.button(
                        t("distribution_reset_btn"), icon="delete_sweep", on_click=_do_reset
                    ).props("unelevated color=negative size=sm")
            confirm_dialog.open()

        # Check if pending distribution differs from database (applied) distribution
        has_unsaved_changes = False
        for chunk_id, saved_ann in chunk_assignment.items():
            if self._pending.get(chunk_id) != saved_ann:
                has_unsaved_changes = True
                break

        with ui.row().classes("gap-sm q-mb-sm items-center"):
            ui.button(
                t("distribution_auto_btn"), icon="auto_fix_high", on_click=auto_distribute
            ).props("outline color=secondary size=sm").tooltip(t("distribution_auto_tooltip"))
            if has_unsaved_changes:
                ui.button(
                    t("distribution_apply_btn"), icon="check", on_click=apply_distribution
                ).props("unelevated color=primary size=sm")
                with ui.row().classes("items-center gap-xs text-warning q-ml-sm"):
                    ui.icon("warning", size="xs")
                    ui.label(t("distribution_status_unsaved")).classes(
                        "text-caption font-weight-medium"
                    )
            else:
                with ui.row().classes("items-center gap-xs text-positive q-ml-sm"):
                    ui.icon("check_circle", size="xs")
                    ui.label(t("distribution_status_saved")).classes(
                        "text-caption font-weight-medium"
                    )
            ui.button(
                t("distribution_reset_btn"), icon="delete_sweep", on_click=reset_assignments
            ).props("flat color=negative size=sm")

    @ui.refreshable_method
    def render_summary(self) -> None:
        annotator_map: dict[str, dict] = {}
        seen_cameras: dict[str, set[str]] = {}
        chunks_by_annotator: dict[str, list[str]] = {}
        for chunk in self.chunks:
            ann = self._pending.get(chunk["chunk_id"])
            if ann is None:
                continue
            if ann not in annotator_map:
                annotator_map[ann] = {
                    "annotator": ann,
                    "cameras": 0,
                    "video_count": 0,
                    "hours": 0.0,
                }
                seen_cameras[ann] = set()
                chunks_by_annotator[ann] = []
            seen_cameras[ann].add(chunk["camera_id"])
            annotator_map[ann]["cameras"] = len(seen_cameras[ann])
            annotator_map[ann]["video_count"] += chunk["video_count"]
            annotator_map[ann]["hours"] = round(annotator_map[ann]["hours"] + chunk["hours"], 2)
            chunks_by_annotator[ann].append(chunk["label"])
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

            ui.separator().classes("q-my-sm")
            for row in summary:
                ann = row["annotator"]
                labels = chunks_by_annotator.get(ann, [])
                with ui.row().classes("gap-xs items-start q-mb-xs flex-wrap"):
                    ui.label(ann).classes("text-caption text-grey-6 font-weight-bold")
                    ui.label("·").classes("text-caption text-grey-4")
                    ui.label(", ".join(labels)).classes("text-caption text-grey-7")


def render_distribution_section(dp, project_id: str) -> None:
    from review_app.app.components.assignment_handoff import render_assignment_handoff

    render_assignment_handoff(dp, project_id)
    # Distribution is a selection input; only saved handoffs deliver assignments.
    with ui.card().classes("full-width q-mb-lg"):
        with ui.row().classes("items-center q-mb-sm"):
            ui.icon("group", size="sm").classes("text-primary q-mr-sm")
            ui.label(t("distribution_title")).classes("text-subtitle1 font-weight-medium")
        ui.label(t("distribution_desc")).classes("text-caption text-grey-6 q-mb-md")
        section = DistributionSection(dp, project_id)
        section.render()
        section.render_summary()

    ui.label(t("handoff_legacy_help")).classes("text-caption text-grey-6 q-mb-md")

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
                result = await run.io_bound(dp.import_annotations_csv, df, project_id, "append")
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
        render_distribution_section(dp, project_id)
