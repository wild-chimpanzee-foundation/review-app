"""Species assignment controls; serialization, scope and acceptance stay in the backend."""

import re

from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    switch_project,
    sync_with_progress,
    user_error_message,
)
from review_app.backend.annotation_package.archive import MAX_ARCHIVE_BYTES
from review_app.backend.annotation_package.scope import AssignmentRequest


def assignment_filename(direction, recipient, assignment_id):
    """Keep names recognizable without allowing paths or unsafe filename characters."""
    name = re.sub(r"[^\w.-]+", "_", recipient).strip("._")[:80] or "recipient"
    return f"{direction}-{name}-{assignment_id}.zip"


def _error_message(exc):
    # Invalid packages and stale previews are expected validation outcomes.
    return str(exc) if isinstance(exc, ValueError) else user_error_message(exc)


def assignment_upload(handler, label):
    return (
        ui.upload(
            label=t(label),
            on_upload=handler,
            auto_upload=True,
            multiple=False,
            max_file_size=MAX_ARCHIVE_BYTES,
            on_rejected=lambda: ui.notify(t("handoff_upload_limit"), type="negative"),
        )
        .props("accept=.zip")
        .classes("w-full")
    )


def render_open_assignment(on_complete, *, show_upload=True):
    """Shared fresh-project entry point for setup and the project picker."""
    data = None
    opened = None
    busy = False
    revision = 0
    root_inputs = {}
    if show_upload:
        ui.label(t("handoff_open_help")).classes("text-body2")
    details = ui.column().classes("w-full")
    name = ui.input(t("handoff_project_name")).classes("w-full")
    status = ui.label("").classes("text-body2")
    progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
    progress.set_visibility(False)

    def update_enabled():
        enabled = (
            not busy
            and data is not None
            and bool((name.value or "").strip())
            and all((field.value or "").strip() for field in root_inputs.values())
        )
        open_button.set_enabled(enabled)
        open_button.set_text(t("handoff_retry_sync" if opened else "handoff_open"))
        continue_button.set_enabled(enabled and opened is None)
        name.set_enabled(not busy and opened is None)
        for field in root_inputs.values():
            field.set_enabled(not busy and opened is None)

    def folders_changed():
        status.set_text("")
        continue_button.set_visibility(False)
        update_enabled()

    async def uploaded(event, *, raw=None):
        nonlocal data, revision, opened
        if busy:
            return
        revision += 1
        current = revision
        data = None
        opened = None
        root_inputs.clear()
        details.clear()
        continue_button.set_visibility(False)
        status.set_text("")
        update_enabled()
        try:
            if event.file.size() > MAX_ARCHIVE_BYTES:
                raise ValueError(t("handoff_upload_limit"))
            if raw is None:
                raw = await event.file.read()
            dp = await get_or_create_data_provider()
            info = await run.io_bound(dp.inspect_assignment, raw)
            if current != revision:
                return
            data = raw
            name.set_value(f"{info['project_name']} — {info['recipient']}")
            with details:
                ui.label(t("handoff_identity", **info)).classes("text-body2")
                ui.label(t("handoff_roots_help")).classes("text-caption")
                for index, (root, paths) in enumerate(info["roots"].items(), 1):
                    ui.label(t("handoff_root_files", count=len(paths), example=paths[0]))
                    root_inputs[root] = ui.input(t("handoff_root", number=index)).classes("w-full")
                    root_inputs[root].on_value_change(folders_changed)
        except Exception as exc:
            if current != revision:
                return
            data = None
            status.set_text(_error_message(exc))
        update_enabled()

    async def open_project(*, allow_missing=False):
        nonlocal data, busy, opened
        if data is None or busy:
            return
        busy = True
        update_enabled()
        try:
            dp = await get_or_create_data_provider()
            if opened is None:
                roots = {key: field.value.strip() for key, field in root_inputs.items()}
                missing = await run.io_bound(dp.check_assignment_media, data, roots)
                if missing and not allow_missing:
                    status.set_text(t("handoff_missing_folders", count=len(missing)))
                    continue_button.set_visibility(True)
                    return
                opened = await run.io_bound(dp.open_assignment, data, name.value.strip(), roots)
            continue_button.set_visibility(False)
            update_enabled()
            status.set_text(t("syncing_videos_label"))
            progress.set_value(0)
            progress.set_visibility(True)
            await sync_with_progress(
                dp, progress=progress, status=status, active_project_id=opened.project_id
            )
            switch_project(dp, opened.project_id)
            if opened.missing_video_ids:
                ui.notify(
                    t("handoff_missing", count=len(opened.missing_video_ids)), type="warning"
                )
            on_complete()
        except Exception as exc:
            status.set_text(_error_message(exc))
        else:
            data = None
        finally:
            busy = False
            progress.set_visibility(False)
            update_enabled()

    if show_upload:
        assignment_upload(uploaded, "handoff_setup_upload")
    name.on_value_change(update_enabled)
    open_button = ui.button(t("handoff_open"), on_click=open_project, icon="create_new_folder")
    open_button.disable()

    async def open_with_missing():
        await open_project(allow_missing=True)

    continue_button = ui.button(t("handoff_open_missing"), on_click=open_with_missing)
    continue_button.set_visibility(False)
    return uploaded


def render_assignment_delivery(dp, binding, require_project):
    """Each saved handout owns its output controls and result display."""
    with ui.expansion(t("handoff_copy"), icon="folder_copy").classes("w-full"):
        ui.label(t("handoff_copy_help")).classes("text-caption")
        output = ui.input(t("handoff_copy_output")).classes("w-full")
        status = ui.label("").classes("text-body2 whitespace-pre-wrap")

        async def copy():
            copy_button.disable()
            try:
                require_project()
                result = await run.io_bound(
                    dp.copy_assignment_footage, binding["id"], output.value
                )
                lines = [
                    t(
                        "handoff_copy_result",
                        copied=result.copied_count,
                        missing=len(result.missing_video_ids),
                        failed=len(result.failures),
                        path=result.path,
                    )
                ]
                lines.extend(f"{root}: {path}" for root, path in result.media_roots.items())
                lines.extend(f"{key}: {error}" for key, error in result.failures.items())
                status.set_text("\n".join(lines))
            except Exception as exc:
                status.set_text(_error_message(exc))
            finally:
                copy_button.enable()

        copy_button = ui.button(t("handoff_copy_start"), on_click=copy)


def render_assignment_handoff(dp, project_id):
    """Prepare/download, return progress, and policy-specific preview/accept."""

    def require_project():
        if get_active_project_id() != project_id:
            raise ValueError(t("handoff_project_changed"))

    with ui.card().classes("w-full q-mb-lg"):
        ui.label(t("handoff_title")).classes("text-h6")
        ui.label(t("handoff_help")).classes("text-body2")

        @ui.refreshable
        def saved_assignments():
            bindings = dp.list_assignments(project_id)
            for binding in bindings:
                ui.label(t("handoff_identity", **binding)).classes("text-body2")
                ui.label(binding["created_at"]).classes("text-caption")

                async def download(row=binding):
                    try:
                        require_project()
                        if row["role"] == "working":
                            raw = await run.io_bound(dp.export_assignment_return, project_id)
                            filename = assignment_filename(
                                "return", row["recipient"], row["assignment_id"]
                            )
                        else:
                            raw = await run.io_bound(dp.export_assignment, row["id"])
                            filename = assignment_filename(
                                "assignment", row["recipient"], row["assignment_id"]
                            )
                        ui.download(raw, filename)
                    except Exception as exc:
                        ui.notify(_error_message(exc), type="negative")

                ui.button(
                    t("handoff_return" if binding["role"] == "working" else "handoff_download"),
                    on_click=download,
                    icon="download",
                )
                if binding["role"] == "origin":
                    render_assignment_delivery(dp, binding, require_project)
            if any(row["role"] == "working" for row in bindings):
                ui.label(t("handoff_working_help")).classes("text-caption")

        recipient = ui.select(dp.get_all_annotators(), label=t("handoff_recipient")).classes(
            "w-full"
        )
        # Distribution below can register annotators without navigating away.
        recipient.on("focus", lambda: recipient.set_options(dp.get_all_annotators()))
        ui.label(t("handoff_recipient_help")).classes("text-caption")
        scope = ui.select(
            {"assigned": t("handoff_scope_assigned"), "all": t("handoff_scope_all")},
            value="assigned",
            label=t("handoff_scope"),
        ).classes("w-full")

        async def prepare():
            prepare_button.disable()
            try:
                require_project()
                name = (recipient.value or "").strip()
                if scope.value == "assigned":
                    ids = await run.io_bound(dp.get_assigned_video_ids, project_id, name)
                else:
                    ids = await run.io_bound(dp.get_assignment_video_ids, project_id)
                result = await run.io_bound(
                    dp.prepare_assignment, project_id, AssignmentRequest(name, tuple(ids))
                )
                ui.notify(t("handoff_prepared", count=result.video_count), type="positive")
                if result.missing_video_ids:
                    ui.notify(
                        t("handoff_missing", count=len(result.missing_video_ids)),
                        type="warning",
                        timeout=0,
                    )
                saved_assignments.refresh()
            except Exception as exc:
                ui.notify(_error_message(exc), type="negative")
            finally:
                prepare_button.enable()

        prepare_button = ui.button(t("handoff_prepare"), on_click=prepare, icon="inventory_2")
        saved_assignments()

    with ui.card().classes("w-full q-mb-lg"):
        ui.label(t("handoff_accept_title")).classes("text-h6")
        ui.label(t("handoff_policy_help")).classes("text-body2")
        raw = None
        preview = None
        revision = 0
        busy = False
        impact = ui.column().classes("w-full")
        status = ui.label("").classes("text-body2")

        def invalidate():
            nonlocal preview, revision
            revision += 1
            preview = None
            impact.clear()
            accept_button.disable()
            status.set_text("")

        async def uploaded(event):
            nonlocal raw
            raw = None
            invalidate()
            current = revision
            try:
                if event.file.size() > MAX_ARCHIVE_BYTES:
                    raise ValueError(t("handoff_upload_limit"))
                content = await event.file.read()
                if current == revision:
                    raw = content
                    status.set_text(t("handoff_return_loaded"))
            except Exception as exc:
                if current == revision:
                    status.set_text(_error_message(exc))

        async def policy_changed():
            invalidate()
            if raw is not None:
                await show_preview()

        assignment_upload(uploaded, "handoff_return_upload")
        policy = ui.select(
            {"keep_existing": t("handoff_keep"), "use_imported": t("handoff_use")},
            value="use_imported",
            label=t("handoff_policy"),
            on_change=policy_changed,
        ).classes("w-full")

        async def show_preview():
            nonlocal preview
            if raw is None or busy:
                return
            invalidate()
            current = revision
            try:
                require_project()
                result = await run.io_bound(
                    dp.preview_assignment_return, project_id, raw, policy.value
                )
                if current != revision:
                    return
                preview = result
                with impact:
                    ui.label(t("handoff_totals", **result.totals)).classes("text-body1")
                    if result.totals["unapplied"]:
                        ui.label(t("handoff_keep_warning")).classes("text-warning")
                    if result.totals["replaced"]:
                        ui.label(t("handoff_replace_warning")).classes("text-warning")
                    if result.totals["annotations_removed"]:
                        ui.label(t("handoff_remove_warning")).classes("text-negative")
                    rows = [
                        {
                            "video": unit["video_path"],
                            "action": t(f"handoff_action_{unit['action']}"),
                            "before": sum(unit["existing"].values()),
                            "after": sum(unit["returned"].values()),
                            "removed": sum(unit["removed"].values()),
                        }
                        for unit in result.units
                    ]
                    with ui.expansion(t("handoff_details"), icon="table_rows").classes("w-full"):
                        ui.table(
                            columns=[
                                {
                                    "name": key,
                                    "label": t(f"handoff_column_{key}"),
                                    "field": key,
                                    "align": "left",
                                }
                                for key in ("video", "action", "before", "after", "removed")
                            ],
                            rows=rows,
                            row_key="video",
                            pagination=10,
                        ).classes("w-full")
                accept_button.set_enabled(bool(result.totals["added"] + result.totals["replaced"]))
            except Exception as exc:
                if current == revision:
                    status.set_text(_error_message(exc))

        async def accept():
            nonlocal busy
            if preview is None or raw is None or busy:
                return
            busy = True
            payload, chosen, token = raw, policy.value, preview.token
            invalidate()
            try:
                require_project()
                await run.io_bound(dp.accept_assignment_return, project_id, payload, chosen, token)
                status.set_text(t("handoff_accepted"))
            except Exception as exc:
                status.set_text(_error_message(exc))
            finally:
                busy = False

        ui.button(t("handoff_preview"), on_click=show_preview, icon="preview")
        accept_button = ui.button(t("handoff_accept"), on_click=accept, icon="check")
        accept_button.disable()
