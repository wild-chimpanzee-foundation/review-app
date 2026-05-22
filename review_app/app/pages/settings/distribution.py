from __future__ import annotations

from nicegui import run, ui

from review_app.app.translations import t


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
            inp = ui.input(placeholder=t("distribution_annotator_placeholder")).props(
                "outlined dense"
            ).classes("flex-grow")
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
                            ui.label(
                                t("distribution_remove_annotator_confirm", name=a)
                            ).classes("text-body2 q-mb-md")
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

        with ui.grid(columns="1fr auto auto 200px").classes("w-full gap-x-md gap-y-xs items-center"):
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
            ui.label(t("distribution_summary_label")).classes(
                "text-caption text-grey-6 q-mb-xs"
            )
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
