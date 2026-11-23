"""Side-by-side footage comparison and confirmed folder assignment."""

import asyncio
from collections import Counter

from nicegui import background_tasks, run, ui

from review_app.app.components.deployment_clip_preview import (
    clip_placeholder,
    render_deployment_clip,
)
from review_app.app.components.deployment_path_comparison import render_path_comparison
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
    on_close,
    edit_deployment,
):
    if start_id:
        start = next((i for i, row in enumerate(folders) if row["id"] == start_id), 0)
        folders = folders[start:] + folders[:start]
    review = _FolderReview(dp, project_id, folders, inventory, on_close, edit_deployment)
    await review.open()


class _FolderReview:
    def __init__(self, dp, project_id, folders, inventory, on_close, edit_deployment):
        self.dp, self.project_id = dp, project_id
        self.folders = [dict(row) for row in folders]
        self.inventory = inventory
        self.on_close, self.edit_deployment = on_close, edit_deployment
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
        with (
            ui.column()
            .classes("w-full gap-4")
            .mark("deployment-review-workspace") as self.workspace
        ):
            with ui.row().classes("w-full justify-between items-center gap-3"):
                with ui.column().classes("gap-0"):
                    self.close_button = ui.button(
                        t("deployment_manage"), icon="arrow_back", on_click=self.close
                    ).props("flat dense no-caps")
                    with ui.row().classes("items-baseline gap-3"):
                        ui.label(t("deployment_review_folders")).classes("text-h5")
                        self.progress = ui.label().classes("text-subtitle2 text-grey-6")
                with ui.row().classes("items-center gap-2"):
                    self.back = (
                        ui.button(
                            t("deployment_review_back"),
                            icon="chevron_left",
                            on_click=self.previous,
                        )
                        .props("flat no-caps")
                        .tooltip(t("deployment_review_history"))
                    )
                    self.skip = ui.button(
                        t("deployment_review_skip"), on_click=self.skip_folder
                    ).props("outline no-caps")
                    self.unavailable = ui.button(on_click=self.change_availability).props(
                        "outline no-caps"
                    )
                    self.apply_button = ui.button(
                        t("deployment_review_apply_next"), icon="check", on_click=self.apply
                    ).props("unelevated no-caps")
            self.error = ui.label().classes("text-negative break-all empty:hidden")
            self.host = ui.column().classes("w-full")
        await self.show()

    def controls(self):
        finished = self.index >= len(self.folders)
        folder = self.folders[self.index] if not finished else {}
        self.unavailable.set_visibility(
            bool(
                folder.get("root_id")
                and (folder.get("needs_reference_review") or folder.get("ordinary_unavailable"))
            )
        )
        self.unavailable.set_enabled(not self.busy)
        self.unavailable.set_text(
            t("deployment_review_reopen")
            if folder.get("ordinary_unavailable")
            else t("deployment_review_mark_unavailable")
        )
        self.back.set_enabled(not self.busy and self.index > 0)
        self.skip.set_enabled(not self.busy and not finished)
        self.skip.set_text(
            t("deployment_review_next")
            if not finished and self.folders[self.index]["id"] in self.applied
            else t("deployment_review_skip")
        )
        self.close_button.set_enabled(not self.busy)
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

    def show_selection(self):
        self.show_metadata()
        count = len(self.clips.selected)
        self.selection_status.set_text(
            t("deployment_review_selected_clips", count=count) if count else ""
        )

    def show_metadata(self):
        self.metadata.clear()
        reasons = self.suggestions.get(self.picker.value, {}).get("reasons", ())
        self.target_reasons.set_text(" · ".join(t(reason) for reason in reasons))
        self.target_reasons.set_visibility(bool(reasons))
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

    async def show(self, *, target_id=None, selected_ids=()):
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
            current, videos, ordinary = await run.io_bound(
                self.dp.get_deployment_folder,
                self.project_id,
                initial["root_id"],
                initial["folder"],
            )
            folder = initial | current if current else initial
            suggestions = await run.io_bound(
                rank_folder_deployments,
                folder | {"unmatched_references": folder["needs_reference_review"]},
                self.inventory,
                ordinary_deployments=ordinary,
            )
        except Exception as exc:
            self.error.set_text(_error(exc))
            self.busy = False
            self.controls()
            return
        if revision != self.revision or self.workspace.is_deleted:
            return
        self.folders[self.index] = folder
        self.suggestions = {row["id"]: row for row in suggestions}
        inventory = {row["id"]: row for row in self.inventory}
        target_options = {}
        for identifier in dict.fromkeys([*self.suggestions, *inventory]):
            row = inventory[identifier]
            label = row["deployment_id"]
            if row.get("name") and row["name"] != label:
                label = f"{label} · {row['name']}"
            target_options[identifier] = (
                t("deployment_review_suggested_target", target=label)
                if identifier in self.suggestions
                else label
            )
        target_options[""] = t("deployment_unresolved")
        new_candidate = next(
            (item for item in target_options if item in folder["new_comparison_candidates"]), None
        )
        default_target = (
            target_id
            if target_id in target_options
            else new_candidate or next(iter(self.suggestions), None)
        )
        self.source_clips = {row["video_id"]: row for row in videos}
        default_source = next(
            (row["video_id"] for row in videos if not row["missing"]),
            next(iter(self.source_clips), None),
        )
        with self.host:
            with ui.element("div").classes(
                "w-full grid grid-cols-1 lg:grid-cols-2 gap-4 items-start"
            ):
                with ui.card().classes("w-full min-w-0 gap-2"):
                    ui.label(t("deployment_review_folder_footage")).classes("text-subtitle1")
                    self.source_picker = (
                        ui.select(
                            {row["video_id"]: row["filename"] for row in videos},
                            value=default_source,
                            label=t("deployment_file"),
                            on_change=self.load_source,
                        )
                        .props("outlined dense options-dense")
                        .classes("w-full")
                        .mark("deployment-review-source")
                    )
                    self.source_picker.set_enabled(len(videos) > 1)
                    self.source_panel = (
                        ui.column().classes("w-full gap-1").mark("deployment-review-source-player")
                    )
                    self.source_path = ui.label(folder["folder"]).classes(
                        "text-caption font-mono break-all"
                    )
                with ui.card().classes("w-full min-w-0 gap-2"):
                    ui.label(t("deployment_review_target_footage")).classes("text-subtitle1")
                    self.picker = (
                        ui.select(
                            target_options,
                            label=t("deployment_target"),
                            value=default_target,
                            with_input=True,
                        )
                        .props("outlined dense options-dense")
                        .classes("w-full")
                        .mark("deployment-review-target")
                    )
                    self.inputs.append(self.picker)
                    self.target_panel = (
                        ui.column().classes("w-full gap-1").mark("deployment-review-target-player")
                    )
                    self.target_reasons = ui.label().classes("text-caption text-grey-6")

            self.path_comparison = ui.column().classes("w-full empty:hidden")
            if folder["ordinary_unavailable"]:
                ui.label(t("deployment_review_ordinary_unavailable")).classes("text-positive")
            elif folder["new_comparison_candidates"]:
                ui.label(t("deployment_review_match_found")).classes("text-warning")
            elif folder["needs_reference_review"] and not suggestions:
                ui.label(t("deployment_review_likely_missing_ordinary")).classes("text-warning")
            elif not suggestions:
                ui.label(t("deployment_review_no_suggestions")).classes("text-caption text-grey-6")
            if folder["review_note"]:
                ui.label(folder["review_note"]).classes("text-caption break-all")
            if folder["id"] in self.applied:
                ui.label(t("deployment_review_done")).classes("text-positive")
            if not folder["root_id"]:
                ui.label(t("deployment_error_membership")).classes("text-negative")
            self.selection_status = ui.label().classes("text-caption empty:hidden")
            self.results = ui.column().classes("w-full")

            with ui.expansion(t("deployment_review_select_clips"), icon="checklist").classes(
                "w-full"
            ):
                self.clips = (
                    ui.table(
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
                    )
                    .classes("w-full")
                    .mark("deployment-review-clips")
                )
                self.clips.selected = [
                    row for row in self.clips.rows if row["video_id"] in selected_ids
                ]
            with ui.expansion(t("deployment_review_folder_details"), icon="info_outline").classes(
                "w-full"
            ):
                if folder.get("root_path"):
                    ui.label(folder["root_path"]).classes("text-caption break-all")
                ui.label(t("deployment_folder_counts", **folder)).classes("text-body2")
                assignments = ", ".join(
                    f"{row['deployment'] or t('deployment_unresolved')}: {row['count']}"
                    for row in folder["assignments"]
                )
                ui.label(f"{t('deployment_folder_assignments')}: {assignments}").classes(
                    "text-caption"
                )
                link = folder.get("link")
                ui.label(
                    t(
                        "deployment_review_current_link",
                        deployment=link["deployment"] or t("deployment_unresolved"),
                    )
                    if link
                    else t("deployment_review_no_link")
                ).classes("text-caption")
                self.metadata = ui.column().classes("w-full gap-0")
                with ui.row():
                    add = ui.button(
                        t("deployment_add"), icon="add", on_click=lambda: self.edit(False)
                    ).props("flat no-caps")
                    edit = ui.button(
                        t("deployment_edit"), icon="edit", on_click=lambda: self.edit(True)
                    ).props("flat no-caps")
                    self.inputs.extend([add, edit])
        self.picker.on_value_change(self.target_changed)
        self.clips.on_select(self.update_preview)
        self.busy = False
        self.controls()
        self.show_selection()
        background_tasks.create(self.load_source(), name="load folder footage")
        background_tasks.create(self.target_changed(), name="load comparison deployment")

    async def load_source(self, event=None):
        if (
            self.workspace.is_deleted
            or self.source_panel.is_deleted
            or (event is not None and event.sender is not self.source_picker)
        ):
            return
        self.source_panel.clear()
        with self.source_panel:
            clip = self.source_clips.get(self.source_picker.value)
            if clip is None:
                clip_placeholder(t("deployment_review_no_clips"))
                return
            request = ui.column().classes("w-full")
            with request:
                clip_placeholder(loading=True)
        folder = self.folders[self.index]
        await render_deployment_clip(
            self.dp, clip["video_id"], clip, folder.get("root_path"), request
        )

    async def load_target(self):
        if self.workspace.is_deleted or self.target_panel.is_deleted:
            return
        target = self.picker.value
        source_folder = self.folders[self.index]["folder"]
        source_path, path_comparison = self.source_path, self.path_comparison
        path_comparison.clear()
        source_path.set_visibility(True)
        self.target_panel.clear()
        with self.target_panel:
            if not target:
                clip_placeholder(t("deployment_review_choose_comparison"))
                return
            request = ui.column().classes("w-full gap-1")
            with request:
                clip_placeholder(loading=True)
        try:
            clips = await run.io_bound(
                self.dp.get_deployment_comparison_clips, self.project_id, target
            )
        except Exception as exc:
            if not request.is_deleted:
                request.clear()
                with request:
                    clip_placeholder(_error(exc))
            return
        if request.is_deleted:
            return
        request.clear()
        if not clips:
            with request:
                clip_placeholder(t("deployment_review_no_comparison_clip"))
            return

        filenames = Counter(clip["filename"] for clip in clips)
        options = {}
        for clip in clips:
            filename = clip["filename"]
            label = filename if filenames[filename] == 1 else clip["video_path"]
            if clip["missing"]:
                label = f"{label} ({t('deployment_missing_media')})"
            options[clip["video_id"]] = label

        async def play(video_id):
            if media.is_deleted:
                return
            path_comparison.clear()
            source_path.set_visibility(True)
            media.clear()
            with media, ui.column().classes("w-full") as player:
                clip_placeholder(loading=True)
            try:
                clip = await run.io_bound(
                    self.dp.get_deployment_comparison_clip, self.project_id, target, video_id
                )
            except Exception as exc:
                if not player.is_deleted:
                    player.clear()
                    with player:
                        clip_placeholder(_error(exc))
                return
            if player.is_deleted:
                return
            if clip is None:
                player.clear()
                with player:
                    clip_placeholder(t("deployment_media_offline"))
                return
            with path_comparison:
                render_path_comparison(source_folder, clip["folder"])
            source_path.set_visibility(False)
            await render_deployment_clip(self.dp, video_id, clip, clip["root_path"], player)

        async def clip_changed(event):
            await play(event.value)

        with request:
            media = ui.column().classes("w-full")
            clip_picker = (
                ui.select(
                    options,
                    value=next(iter(options)),
                    label=t("deployment_review_comparison_video"),
                    with_input=True,
                    on_change=clip_changed,
                )
                .props("outlined dense options-dense")
                .classes("w-full")
                .mark("deployment-review-target-clip")
            )
            clip_picker.set_enabled(len(options) > 1)
        await play(clip_picker.value)

    async def target_changed(self, event=None):
        if (
            not self.busy
            and not self.workspace.is_deleted
            and (event is None or event.sender is self.picker)
        ):
            await asyncio.gather(self.load_target(), self.update_preview())

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

        async def saved(identifier):
            self.dirty = True
            selected_ids = {row["video_id"] for row in self.clips.selected}
            self.inventory = await run.io_bound(self.dp.get_project_deployments, self.project_id)
            await self.show(target_id=identifier, selected_ids=selected_ids)

        await self.edit_deployment(
            selected,
            initial_deployment_id="" if existing else self.folders[self.index]["folder"],
            on_saved=saved,
        )

    async def update_preview(self, event=None, *, notice=""):
        if (
            self.busy
            or self.picker is None
            or self.workspace.is_deleted
            or (event is not None and event.sender is not self.clips)
        ):
            return
        self.revision += 1
        revision = self.revision
        self.preview = None
        self.results.clear()
        self.error.set_text(notice)
        self.controls()
        self.show_selection()
        folder = self.folders[self.index]
        target = self.picker.value
        if target is None or not folder["root_id"] or not self.clips.rows:
            return
        selected = [row["video_id"] for row in self.clips.selected]
        with self.results:
            ui.label(t("deployment_review_preparing_preview")).classes("text-caption")
        try:
            preview = await run.io_bound(
                self.dp.preview_deployment_assignment,
                self.project_id,
                selected,
                target_id=target or None,
                action="assign" if selected else "folder",
                folder=None
                if selected
                else {"root_id": folder["root_id"], "path": folder["folder"]},
            )
        except Exception as exc:
            if revision == self.revision and not self.workspace.is_deleted:
                self.results.clear()
                self.error.set_text(_error(exc))
            return
        if revision != self.revision or self.workspace.is_deleted:
            return
        already_linked = preview["action"] == "folder" and preview["link"] == (
            preview["target_id"],
            "manual",
        )
        leaves_unmatched = (
            folder["unmatched_references"]
            and preview["target_id"] is not None
            and all(row["before"] == preview["target_id"] for row in preview["rows"])
        )
        confirms_match = (
            preview["action"] == "folder"
            and preview["target_id"] in folder["new_comparison_candidates"]
        )
        no_changes = (
            not preview["changed"] and not confirms_match and (already_linked or leaves_unmatched)
        )
        if not no_changes:
            self.preview = preview
        self.results.clear()
        with self.results:
            if no_changes:
                ui.label(t("deployment_review_no_change")).classes("text-warning")
            render_assignment_preview(preview, grouped=not selected)
        self.controls()

    async def apply(self):
        preview = self.preview
        if self.busy or preview is None or preview["blocked"]:
            return
        selected = {row["video_id"] for row in self.clips.selected}
        previewed = (
            {row["video_id"] for row in preview["rows"]}
            if preview["action"] == "assign"
            else set()
        )
        if (
            self.picker.value is None
            or preview["target_id"] != (self.picker.value or None)
            or selected != previewed
        ):
            return
        self.busy = True
        self.controls()
        advance = preview["action"] == "folder"
        try:
            counts = await run.io_bound(self.dp.apply_deployment_assignment, preview)
        except Exception as exc:
            self.preview = None
            self.busy = False
            self.results.clear()
            if isinstance(exc, DeploymentInputError) and exc.key == "deployment_error_stale":
                await self.update_preview(notice=t("deployment_review_preview_changed"))
                return
            self.error.set_text(_error(exc))
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

    async def change_availability(self):
        if self.busy or self.index >= len(self.folders):
            return
        folder = self.folders[self.index]

        async def save(note, dialog=None):
            if (
                self.busy
                or self.index >= len(self.folders)
                or self.folders[self.index] is not folder
            ):
                return
            self.busy = True
            self.controls()
            self.error.set_text("")
            try:
                await run.io_bound(
                    self.dp.set_folder_ordinary_unavailable,
                    self.project_id,
                    folder["root_id"],
                    folder["folder"],
                    note,
                )
            except Exception as exc:
                self.error.set_text(_error(exc))
                if dialog is not None:
                    dialog.close()
                self.busy = False
                self.controls()
                return
            self.dirty = True
            if dialog is not None:
                dialog.close()
            if note is None:
                self.applied.discard(folder["id"])
            else:
                self.applied.add(folder["id"])
                self.skipped.discard(folder["id"])
                self.index += 1
            await self.show()

        if folder["ordinary_unavailable"]:
            await save(None)
            return
        dialog = ui.dialog().props("persistent")
        dialog.on("hide", dialog.delete)
        with dialog, ui.card():
            ui.label(t("deployment_review_mark_unavailable")).classes("text-h6")
            note = ui.textarea(
                t("deployment_field_optional", field=t("deployment_field_notes")),
                value=folder["review_note"] or "",
            ).classes("w-full")
            with ui.row():
                ui.button(t("cancel"), on_click=dialog.close).props("flat")
                ui.button(t("save"), on_click=lambda: save(note.value or "", dialog)).props(
                    "unelevated"
                )
        dialog.open()

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
        self.revision += 1
        self.busy = True
        self.controls()
        await self.on_close(self.dirty)
