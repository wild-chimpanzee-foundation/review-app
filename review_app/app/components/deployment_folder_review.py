"""Folder-by-folder correction dialog; assignments use the existing preview/apply seam."""

from nicegui import run, ui

from review_app.app.translations import t
from review_app.app.utils import user_error_message
from review_app.backend.deployment_review import rank_folder_deployments, summarize_folder_preview
from review_app.backend.deployments import DeploymentInputError


def _error(exc):
    return (
        t(exc.key, **exc.params)
        if isinstance(exc, DeploymentInputError)
        else user_error_message(exc)
    )


def render_assignment_preview(preview, *, grouped=False):
    ui.label(t("deployment_preview_counts", **preview)).classes("text-subtitle2")
    rows = (
        summarize_folder_preview(preview)
        if grouped
        else [
            {
                **row,
                "before": row["before_label"],
                "after": row["before_label"] if row["preserved"] else row["after_label"],
            }
            for row in preview["rows"]
        ]
    )
    columns = [
        ("count", "deployment_folder_total") if grouped else ("path", "deployment_file"),
        ("before", "deployment_current"),
        ("after", "deployment_target"),
        ("preserved", "deployment_status"),
        ("blocked", "deployment_blockers"),
    ]
    ui.table(
        columns=[
            {"name": field, "field": field, "label": t(label), "align": "left"}
            for field, label in columns
        ],
        rows=[
            row
            | {
                "id": index,
                "before": row["before"] or t("deployment_unresolved"),
                "after": row["after"] or t("deployment_unresolved"),
                "preserved": t("deployment_exception_kept") if row["preserved"] else "",
                "blocked": t(row["blocked"]) if row["blocked"] else "",
            }
            for index, row in enumerate(rows)
        ],
        row_key="id",
        pagination=5 if grouped else 10,
    ).classes("w-full")


async def review_deployment_folders(
    dp,
    project_id,
    folders,
    inventory,
    *,
    start_id=None,
    on_changed,
    edit_deployment,
    inspect_video,
):
    if start_id:
        start = next((i for i, row in enumerate(folders) if row["id"] == start_id), 0)
        folders = folders[start:] + folders[:start]
    review = _FolderReview(
        dp, project_id, folders, inventory, on_changed, edit_deployment, inspect_video
    )
    await review.open()


class _FolderReview:
    def __init__(
        self, dp, project_id, folders, inventory, on_changed, edit_deployment, inspect_video
    ):
        self.dp, self.project_id = dp, project_id
        self.folders = [dict(row) for row in folders]
        self.inventory = inventory
        self.on_changed, self.edit_deployment, self.inspect_video = (
            on_changed,
            edit_deployment,
            inspect_video,
        )
        self.index = 0
        self.revision = 0
        self.preview = None
        self.picker = None
        self.clips = None
        self.busy = False
        self.dirty = False
        self.applied, self.skipped = set(), set()
        self.inputs = []

    async def open(self):
        # Persistent prevents Escape/outside-click from hiding an in-flight write.
        self.dialog = ui.dialog().props("persistent")
        with (
            self.dialog,
            ui.card()
            .classes("q-pa-lg")
            .style(
                "width:1150px; max-width:96vw; height:94vh; overflow:hidden; display:flex; flex-direction:column"
            ),
        ):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label(t("deployment_review_folders")).classes("text-h5")
                self.progress = ui.label().classes("text-subtitle2")
            self.host = (
                ui.column().classes("w-full").style("flex:1; min-height:0; overflow-y:auto")
            )
            self.error = ui.label().classes("text-negative break-all")
            with ui.row().classes("w-full items-center gap-2"):
                self.back = ui.button(
                    t("deployment_review_back"), icon="arrow_back", on_click=self.previous
                ).props("flat")
                self.skip = ui.button(
                    t("deployment_review_skip"), on_click=self.skip_folder
                ).props("outline")
                ui.space()
                self.preview_button = ui.button(
                    t("deployment_preview"), on_click=self.make_preview
                ).props("outline")
                self.apply_button = ui.button(
                    t("deployment_review_apply_next"), icon="check", on_click=self.apply
                )
                self.close_button = ui.button(t("close"), on_click=self.close).props("flat")
            ui.label(t("deployment_review_history")).classes("text-caption text-grey-6")
        self.dialog.open()
        await self.show()

    def controls(self):
        finished = self.index >= len(self.folders)
        self.back.set_enabled(not self.busy and self.index > 0)
        self.skip.set_enabled(not self.busy and not finished)
        self.skip.set_text(
            t("deployment_review_next")
            if not finished and self.folders[self.index]["id"] in self.applied
            else t("deployment_review_skip")
        )
        self.close_button.set_enabled(not self.busy)
        selected = not finished and self.picker is not None and self.picker.value is not None
        valid_root = not finished and bool(self.folders[self.index]["root_id"])
        self.preview_button.set_enabled(
            not self.busy
            and selected
            and valid_root
            and self.clips is not None
            and bool(self.clips.rows)
        )
        self.apply_button.set_text(
            t("deployment_review_apply_clips")
            if self.clips is not None and self.clips.selected
            else t("deployment_review_apply_next")
        )
        self.apply_button.set_enabled(
            not self.busy and self.preview is not None and not self.preview["blocked"]
        )
        for widget in self.inputs:
            widget.set_enabled(not self.busy)

    def invalidate(self, _=None):
        self.revision += 1
        self.preview = None
        self.results.clear()
        self.error.set_text("")
        self.controls()
        self.show_metadata()
        count = len(self.clips.selected)
        self.selection_status.set_text(
            t("deployment_review_selected_clips", count=count)
            if count
            else t("deployment_review_preview_help")
        )

    def show_metadata(self):
        self.metadata.clear()
        selected = next((row for row in self.inventory if row["id"] == self.picker.value), None)
        if selected:
            with self.metadata:
                ui.label(t("deployment_review_target_metadata")).classes("text-subtitle2")
                for field in (
                    "name",
                    "camera_id",
                    "transect_id",
                    "installation_date",
                    "retrieval_date",
                    "habitat",
                    "notes",
                ):
                    if selected.get(field):
                        ui.label(f"{field}: {selected[field]}").classes("text-caption break-all")

    async def show(self):
        self.revision += 1
        revision = self.revision
        self.preview = None
        self.picker = None
        self.clips = None
        self.inputs = []
        self.busy = True
        self.controls()
        self.error.set_text("")
        self.host.clear()
        if self.index >= len(self.folders):
            with self.host:
                ui.icon("task_alt", size="3rem").classes("text-positive")
                ui.label(
                    t(
                        "deployment_review_complete",
                        applied=len(self.applied),
                        skipped=len(self.skipped),
                    )
                ).classes("text-h6")
            self.progress.set_text("")
            self.busy = False
            self.controls()
            return
        initial = self.folders[self.index]
        self.progress.set_text(
            t("deployment_review_position", current=self.index + 1, total=len(self.folders))
        )
        try:
            current, videos = await run.io_bound(
                self.dp.get_deployment_folder,
                self.project_id,
                initial["root_id"],
                initial["folder"],
            )
            folder = initial | current if current else initial
            suggestions = await run.io_bound(rank_folder_deployments, folder, self.inventory)
        except Exception as exc:
            self.error.set_text(_error(exc))
            self.busy = False
            self.controls()
            return
        if revision != self.revision or self.dialog.is_deleted:
            return
        with self.host:
            ui.label(folder["folder"]).classes("text-subtitle1 font-mono break-all")
            if folder.get("root_path"):
                ui.label(folder["root_path"]).classes("text-caption text-grey-6 break-all")
            if current:
                ui.label(t("deployment_folder_counts", **folder)).classes("text-body2")
            else:
                ui.label(t("deployment_review_no_clips")).classes("text-warning")
            if folder["unmatched_references"]:
                ui.label(
                    t("deployment_review_unmatched", count=folder["unmatched_references"])
                ).classes("text-warning")
            if folder["id"] in self.applied:
                ui.label(t("deployment_review_done")).classes("text-positive")
            current = ", ".join(
                f"{row['deployment'] or t('deployment_unresolved')}: {row['count']}"
                for row in folder["assignments"]
            )
            ui.label(f"{t('deployment_folder_assignments')}: {current}").classes("text-caption")
            link = folder.get("link")
            ui.label(
                t(
                    "deployment_review_current_link",
                    deployment=(link["deployment"] or t("deployment_unresolved"))
                    if link
                    else t("deployment_review_no_link"),
                )
            ).classes("text-caption")
            if not folder["root_id"]:
                ui.label(t("deployment_error_membership")).classes("text-negative")
            with ui.expansion(
                t("deployment_footage"), icon="movie", value=bool(videos and len(videos) <= 20)
            ).classes("w-full"):
                self.clips = ui.table(
                    columns=[
                        {"name": field, "field": field, "label": t(label), "align": "left"}
                        for field, label in (
                            ("filename", "deployment_file"),
                            ("status", "deployment_current"),
                            ("kind", "deployment_reference"),
                            ("availability", "deployment_status"),
                        )
                    ],
                    rows=[
                        row
                        | {
                            "status": row["deployment"] or t("deployment_unresolved"),
                            "kind": t("deployment_reference") if row["reference"] else "",
                            "availability": t("deployment_missing_media")
                            if row["missing"]
                            else "",
                        }
                        for row in videos
                    ],
                    row_key="video_id",
                    selection="multiple",
                    pagination=10,
                ).classes("w-full")
                inspect = ui.button(
                    t("deployment_inspect"),
                    icon="play_arrow",
                    on_click=self.inspect_selected,
                ).props("outline")
                self.inputs.append(inspect)
            ui.label(t("deployment_review_advisory")).classes("text-caption text-warning")
            options = {"": t("deployment_unresolved")} | {
                row["id"]: row["deployment_id"] + (f" — {row['name']}" if row.get("name") else "")
                for row in self.inventory
            }
            self.picker = ui.select(
                options, label=t("deployment_target"), with_input=True
            ).classes("w-full")
            self.inputs.append(self.picker)
            if not suggestions:
                ui.label(t("deployment_review_no_suggestions")).classes("text-grey-6")
            for suggestion in suggestions:
                with ui.card().classes("w-full q-pa-sm"):
                    with ui.row().classes("w-full items-center justify-between"):
                        button = ui.button(
                            suggestion["deployment_id"],
                            on_click=lambda tid=suggestion["id"]: self.picker.set_value(tid),
                        ).props("flat no-caps")
                        self.inputs.append(button)
                    ui.label(" · ".join(t(reason) for reason in suggestion["reasons"])).classes(
                        "text-caption"
                    )
            with ui.row():
                add = ui.button(
                    t("deployment_add"), icon="add", on_click=lambda: self.edit(False)
                ).props("outline")
                edit = ui.button(
                    t("deployment_edit"), icon="edit", on_click=lambda: self.edit(True)
                ).props("outline")
                self.inputs.extend([add, edit])
            self.metadata = ui.column().classes("w-full gap-0")
            self.selection_status = ui.label(t("deployment_review_preview_help")).classes(
                "text-caption"
            )
            self.results = ui.column().classes("w-full")
        self.picker.on_value_change(self.invalidate)
        self.clips.on_select(self.invalidate)
        self.busy = False
        self.controls()

    async def inspect_selected(self):
        if len(self.clips.selected) != 1:
            ui.notify(t("deployment_inspect_one"), type="warning")
            return
        await self.inspect_video(self.clips.selected[0])

    async def edit(self, existing):
        if self.busy:
            return
        selected = (
            next((row for row in self.inventory if row["id"] == self.picker.value), None)
            if existing
            else None
        )
        if existing and selected is None:
            self.error.set_text(t("deployment_error_target"))
            return
        self.invalidate()

        async def saved(identifier):
            self.dirty = True
            self.inventory = await run.io_bound(self.dp.get_project_deployments, self.project_id)
            await self.show()
            self.picker.set_value(identifier)

        await self.edit_deployment(selected, on_saved=saved)

    async def make_preview(self):
        if self.busy or self.picker is None or self.picker.value is None:
            return
        self.invalidate()
        revision = self.revision
        folder = self.folders[self.index]
        selected = [row["video_id"] for row in self.clips.selected]
        self.busy = True
        self.controls()
        try:
            preview = await run.io_bound(
                self.dp.preview_deployment_assignment,
                self.project_id,
                selected,
                target_id=self.picker.value or None,
                action="assign" if selected else "folder",
                folder=None
                if selected
                else {"root_id": folder["root_id"], "path": folder["folder"]},
            )
            if revision != self.revision or self.dialog.is_deleted:
                return
            self.preview = preview
            with self.results:
                render_assignment_preview(preview, grouped=not selected)
        except Exception as exc:
            self.error.set_text(_error(exc))
        finally:
            self.busy = False
            self.controls()

    async def apply(self):
        if self.busy or self.preview is None or self.preview["blocked"]:
            return
        self.busy = True
        self.controls()
        advance = self.preview["action"] == "folder"
        try:
            counts = await run.io_bound(self.dp.apply_deployment_assignment, self.preview)
        except Exception as exc:
            self.preview = None
            self.error.set_text(_error(exc))
            self.busy = False
            self.controls()
            return
        folder = self.folders[self.index]
        self.applied.add(folder["id"])
        self.skipped.discard(folder["id"])
        self.dirty = True
        ui.notify(t("deployment_applied", count=counts["changed"]), type="positive")
        if advance:
            self.index += 1
        await self.show()

    async def previous(self):
        if self.busy or self.index == 0:
            return
        self.index -= 1
        await self.show()

    async def skip_folder(self):
        if self.busy or self.index >= len(self.folders):
            return
        folder = self.folders[self.index]
        if folder["id"] not in self.applied:
            self.skipped.add(folder["id"])
        self.index += 1
        await self.show()

    async def close(self):
        if self.busy:
            return
        self.busy = True
        self.controls()
        self.dialog.close()
        if self.dirty:
            await self.on_changed()
