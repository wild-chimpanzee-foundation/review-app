"""Assignment controls; serialization, scope and acceptance stay in the backend."""

import re

from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import get_language, t
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


def render_assignment_delivery(dp, binding, require_project):
    """Each saved handout owns its output controls and result display."""
    with ui.expansion(t("handoff_copy"), icon="folder_copy").classes("w-full"):
        if binding["stage"] == "distance":
            ui.label(t("handoff_media_summary", **binding)).classes("text-body2")
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

    is_working = any(row["role"] == "working" for row in dp.list_assignments(project_id))

    with ui.card().classes("w-full q-mb-lg"):
        ui.label(t("data_saved_assignments")).classes("text-h6")
        ui.label(
            t("handoff_working_help" if is_working else "data_saved_assignments_help")
        ).classes("text-body2 text-grey-6")

        @ui.refreshable
        def saved_assignments():
            bindings = dp.list_assignments(project_id)
            if not bindings:
                ui.label(t("data_no_assignments")).classes("text-grey-6")
            for binding in bindings:
                ui.separator().classes("q-my-sm")
                ui.label(assignment_identity(binding)).classes("text-body1 font-medium")
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
                        require_project()
                        ui.download(raw, filename)
                        if row["role"] == "working":
                            ui.notify(t("data_return_downloaded"), type="positive")
                    except Exception as exc:
                        ui.notify(_error_message(exc), type="negative")

                ui.button(
                    t("handoff_return" if binding["role"] == "working" else "handoff_download"),
                    on_click=download,
                    icon="download",
                )
                if binding["role"] == "origin":
                    render_assignment_delivery(dp, binding, require_project)
                else:
                    ui.button(
                        t("data_continue_review"),
                        icon="play_arrow",
                        on_click=lambda stage=binding["stage"]: ui.navigate.to(
                            "/distance-review" if stage == "distance" else "/review"
                        ),
                    ).props("outline")

        saved_assignments()

    with ui.expansion(t("handoff_prepare"), icon="inventory_2").classes(
        "w-full q-mb-md"
    ) as preparation:
        preparation.set_visibility(not is_working)
        ui.label(t("handoff_help")).classes("text-body2 text-grey-6")
        stage = ui.select(
            {"species": t("handoff_stage_species"), "distance": t("handoff_stage_distance")},
            value="species",
            label=t("handoff_stage"),
        ).classes("w-full")
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

        selection_revision = 0
        loaded_filters = None
        with ui.column().classes("w-full") as distance_fields:
            ui.label(t("handoff_distance_help")).classes("text-caption")
            target = ui.select(
                dp.get_assignment_distance_species_options(project_id, get_language()),
                label=t("handoff_target"),
            ).classes("w-full")
            with ui.expansion(t("data_advanced"), icon="tune").classes("w-full"):
                with ui.row():
                    start = ui.input(t("handoff_start"), placeholder="18:00")
                    end = ui.input(t("handoff_end"), placeholder="06:00")
                ui.label(t("handoff_time_help")).classes("text-caption")
            selection_status = ui.label("").classes("text-body2")
            with ui.expansion(t("handoff_distance_videos"), icon="list").classes("w-full"):
                selected_videos = (
                    ui.select({}, multiple=True, value=[], label=t("handoff_distance_videos"))
                    .props("use-chips")
                    .classes("w-full")
                )

            def filters():
                return (
                    target.value,
                    (start.value or "").strip() or None,
                    (end.value or "").strip() or None,
                )

            def clear_selection():
                nonlocal loaded_filters, selection_revision
                selection_revision += 1
                loaded_filters = None
                selected_videos.set_value([])
                selected_videos.set_options({})
                selection_status.set_text("")

            async def load_selection():
                nonlocal loaded_filters
                clear_selection()
                current, chosen = selection_revision, filters()
                try:
                    require_project()
                    if not chosen[0]:
                        raise ValueError(t("handoff_choose_target"))
                    result = await run.io_bound(
                        dp.preview_assignment_distance_selection,
                        project_id,
                        chosen[0],
                        start_time=chosen[1],
                        end_time=chosen[2],
                    )
                    if current != selection_revision:
                        return
                    options = {
                        row["video_id"]: t(
                            "handoff_distance_video",
                            path=row["video_path"],
                            time=row["recorded_start"] or t("handoff_unknown_time"),
                            missing=t("handoff_missing_marker") if row["is_missing"] else "",
                        )
                        for row in result["videos"]
                    }
                    selected_videos.set_options(options)
                    selected_videos.set_value(list(options))
                    loaded_filters = chosen
                    selection_status.set_text(
                        t(
                            "handoff_selection_result",
                            count=len(options),
                            unknown=result["unknown_time_count"],
                        )
                    )
                except Exception as exc:
                    if current == selection_revision:
                        selection_status.set_text(_error_message(exc))

            target.on(
                "focus",
                lambda: target.set_options(
                    dp.get_assignment_distance_species_options(project_id, get_language())
                ),
            )
            for field in (target, start, end):
                field.on_value_change(clear_selection)
            ui.button(t("handoff_load_videos"), on_click=load_selection, icon="search")
        distance_fields.set_visibility(False)

        async def prepare():
            prepare_button.disable()
            try:
                require_project()
                name = (recipient.value or "").strip()
                chosen_stage, chosen_scope = stage.value, scope.value
                chosen_filters = filters()
                if chosen_stage == "distance":
                    if loaded_filters != chosen_filters or not selected_videos.value:
                        raise ValueError(t("handoff_select_distance"))
                    request = AssignmentRequest(
                        name, tuple(selected_videos.value), chosen_stage, *chosen_filters
                    )
                else:
                    if chosen_scope == "assigned":
                        ids = await run.io_bound(dp.get_assigned_video_ids, project_id, name)
                    else:
                        ids = await run.io_bound(dp.get_assignment_video_ids, project_id)
                    request = AssignmentRequest(name, tuple(ids))
                result = await run.io_bound(dp.prepare_assignment, project_id, request)
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

        def stage_changed():
            scope.set_visibility(stage.value == "species")
            distance_fields.set_visibility(stage.value == "distance")
            clear_selection()

        stage.on_value_change(stage_changed)

    with ui.expansion(t("handoff_accept_title"), icon="move_to_inbox").classes(
        "w-full q-mb-md"
    ) as acceptance:
        acceptance.set_visibility(not is_working)
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
