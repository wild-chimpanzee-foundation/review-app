"""Assignment controls; serialization, scope and acceptance stay in the backend."""

import re

from nicegui import run, ui

from review_app.app.components.assignment_video_table import AssignmentVideoTable
from review_app.app.components.folder_picker import pick_folder
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    switch_project,
    sync_with_progress,
    user_error_message,
)
from review_app.backend.annotation_package.archive import MAX_ARCHIVE_BYTES


def assignment_filename(direction, recipient, assignment_id):
    """Keep names recognizable without allowing paths or unsafe filename characters."""
    name = re.sub(r"[^\w.-]+", "_", recipient).strip("._")[:80] or "recipient"
    return f"{direction}-{name}-{assignment_id}.zip"


def assignment_stage_label(row):
    label = t(f"handoff_stage_{row['stage']}")
    if row.get("target_species"):
        label += f" — {row['target_species']}"
    return label


def assignment_identity(row):
    return t("handoff_identity", **row, stage_label=assignment_stage_label(row))


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
                ui.label(assignment_identity(info)).classes("text-body2")
                if info["stage"] == "distance":
                    ui.label(t("handoff_media_summary", **info)).classes("text-caption")
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


def render_assignment_delivery(dp, project_id, binding, require_project):
    """Save each selected assignment in its own server-side delivery folder."""
    bindings = binding if isinstance(binding, list) else [binding]
    bulk = len(bindings) > 1
    label = t("assign_save_deliveries") if bulk else t("assign_save_delivery")
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl gap-3"):
        ui.label(label).classes("text-h6")
        ui.label(t("assign_delivery_help")).classes("text-body2 text-grey-7")

        async def browse():
            browse_button.disable()
            save_button.disable()
            try:
                require_project()
                selected = await run.io_bound(pick_folder, output.value or "")
                require_project()
                if selected:
                    output.set_value(selected)
            except Exception:
                ui.notify(t("assign_folder_picker_unavailable"), type="warning")
            finally:
                browse_button.enable()
                save_button.enable()

        with ui.row().classes("w-full items-end gap-2 flex-nowrap"):
            output = ui.input(t("handoff_copy_output")).classes("grow min-w-0")
            browse_button = (
                ui.button(t("assign_browse_folder"), icon="folder_open", on_click=browse)
                .props("outline")
                .mark("browse-delivery-folder")
            )
        include = ui.checkbox(t("assign_include_footage"), value=False)
        ui.label(t("assign_reuse_footage")).classes("text-caption text-grey-7")
        if not bulk and bindings[0]["stage"] == "distance":
            ui.label(t("handoff_media_summary", **bindings[0])).classes("text-caption")
        status = ui.label("").classes("text-body2 whitespace-pre-wrap")
        results = ui.column().classes("w-full gap-2 max-h-64 overflow-y-auto")

        def show_result(item, result, with_footage):
            warning = bool(result.missing_video_ids or result.failures)
            with results:
                with (
                    ui.expansion(
                        item["recipient"],
                        icon="warning_amber" if warning else "check_circle_outline",
                    )
                    .classes("w-full border rounded")
                    .mark("delivery-result") as detail
                ):
                    detail.props(
                        "header-class=text-warning" if warning else "header-class=text-positive"
                    )
                    ui.label(t("assign_delivery_saved", path=result.path)).classes(
                        "text-caption break-all"
                    )
                    if with_footage:
                        ui.label(
                            t(
                                "handoff_copy_result",
                                copied=result.copied_count,
                                missing=len(result.missing_video_ids),
                                failed=len(result.failures),
                                path=result.path,
                            )
                        ).classes("text-caption break-all")
                        for root, path in result.media_roots.items():
                            ui.label(f"{root}: {path}").classes("text-caption break-all")
                        for video_id in result.missing_video_ids:
                            ui.label(t("assign_delivery_missing", video=video_id)).classes(
                                "text-caption text-warning break-all"
                            )
                        for key, error in result.failures.items():
                            ui.label(f"{key}: {error}").classes(
                                "text-caption text-negative break-all"
                            )
                if with_footage:
                    ui.label(
                        t(
                            "assign_delivery_counts",
                            copied=result.copied_count,
                            missing=len(result.missing_video_ids),
                            failed=len(result.failures),
                        )
                    ).classes(
                        "text-caption text-warning" if warning else "text-caption text-grey-7"
                    )

        async def save():
            save_button.disable()
            close_button.disable()
            output.disable()
            browse_button.disable()
            include.disable()
            with_footage = include.value
            try:
                require_project()
                lines = []
                saved = 0
                results.clear()
                status.set_text("")
                for item in bindings:
                    require_project()
                    try:
                        result = await run.io_bound(
                            dp.save_assignment_delivery,
                            project_id,
                            item["id"],
                            output.value,
                            include_footage=with_footage,
                        )
                        require_project()
                        saved += 1
                        if bulk:
                            show_result(item, result, with_footage)
                        lines.append(t("assign_delivery_saved", path=result.path))
                        if with_footage:
                            lines.append(
                                t(
                                    "handoff_copy_result",
                                    copied=result.copied_count,
                                    missing=len(result.missing_video_ids),
                                    failed=len(result.failures),
                                    path=result.path,
                                )
                            )
                            lines.extend(
                                f"{root}: {path}" for root, path in result.media_roots.items()
                            )
                            lines.extend(
                                f"{key}: {error}" for key, error in result.failures.items()
                            )
                    except Exception as exc:
                        message = f"{item['recipient']}: {_error_message(exc)}"
                        lines.append(message)
                        if bulk:
                            with results:
                                ui.label(message).classes("text-negative break-all")
                    status.set_text(
                        t("assign_deliveries_saved", saved=saved, total=len(bindings))
                        if bulk
                        else "\n".join(lines)
                    )
            except Exception as exc:
                status.set_text(_error_message(exc))
            finally:
                save_button.enable()
                close_button.enable()
                output.enable()
                browse_button.enable()
                include.enable()

        with ui.row().classes("w-full justify-end gap-2"):
            close_button = ui.button(t("close"), on_click=dialog.close).props("flat")
            save_button = ui.button(label, on_click=save, icon="folder_zip").mark("save-delivery")
    ui.button(label, icon="folder_zip", on_click=dialog.open).mark("open-delivery")


def render_assignment_handoff(dp, project_id):
    """Compatibility entry point for the canonical assignment workspace."""
    from review_app.app.pages.data_assignments.assignments import render_assignments

    render_assignments(dp, project_id)


def render_return_acceptance(dp, project_id, require_project):
    """Focused return flow; preserve impact preview and atomic acceptance."""
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
                ui.label(
                    t(
                        "handoff_preview_scope",
                        stage_label=assignment_stage_label(
                            {"stage": result.stage, "target_species": result.target_species}
                        ),
                    )
                ).classes("text-body2")
                ui.label(t("handoff_totals", **result.totals)).classes("text-body1")
                if result.species_context_changed:
                    ui.label(t("handoff_species_changed")).classes("text-warning")
                if result.reference_substitutions:
                    ui.label(t("handoff_substitution_help")).classes("text-warning")
                    for reference in result.reference_substitutions:
                        ui.label(
                            t(
                                "handoff_substitution",
                                path=reference["video_path"],
                                distance=reference["distance_m"],
                                returned=reference["returned_playback_ms"]
                                if reference["returned_playback_ms"] is not None
                                else reference["returned_offset_ms"],
                                existing=reference["existing_playback_ms"]
                                if reference["existing_playback_ms"] is not None
                                else reference["existing_offset_ms"],
                            )
                        ).classes("text-body2")
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
                    table = AssignmentVideoTable(
                        [
                            (key, t(f"handoff_column_{key}"))
                            for key in ("video", "action", "before", "after", "removed")
                        ],
                        filters=[("action", t("handoff_column_action"))],
                        marker="assignment-return-videos",
                    )
                    table.set_rows([{**row, "id": row["video"]} for row in rows])
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
