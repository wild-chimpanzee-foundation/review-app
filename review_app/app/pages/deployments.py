"""Project deployments and footage assignment tools."""

from nicegui import run, ui

from review_app.app.components.deployment_catalog import (
    render_deployment_catalog,
    render_deployment_details,
)
from review_app.app.components.deployment_clip_preview import show_deployment_clip
from review_app.app.components.deployment_folder_review import (
    render_assignment_preview,
    review_deployment_folders,
)
from review_app.app.components.deployment_inventory import (
    deployment_error_message,
    open_deployment_editor,
    render_inventory,
)
from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    render_uninitialized_state,
)


async def setup_deployments(
    *, deployment: str = "", tools: bool = False, review_references: bool = False
):
    from review_app.app.entry_point import shared_header

    shared_header()
    dp = await get_or_create_data_provider()
    pid = get_active_project_id()
    if not dp or not pid:
        render_uninitialized_state()
        return

    with ui.column().classes("w-full q-pa-lg").style("max-width:1600px; margin:0 auto"):
        with ui.column().classes("w-full") as management_panel:
            ui.button(
                t("deployment_catalog_title") if deployment or tools else t("nav_overview"),
                icon="arrow_back",
                on_click=lambda: ui.navigate.to(
                    "/deployments" if deployment or tools else "/overview"
                ),
            ).props("flat")
            if tools:
                ui.label(t("deployment_tools")).classes("text-h5 text-primary")
                ui.label(t("deployment_management_help")).classes("text-caption text-grey-6")
            elif not deployment:
                ui.label(t("deployment_catalog_title")).classes("text-h5 text-primary")
            host = ui.column().classes("w-full")
        review_host = ui.column().classes("w-full")
        review_host.set_visibility(False)

    async def reload(*, open_reference_review: bool = False):
        try:
            management = await run.io_bound(dp.get_deployment_management, pid)
            inventory = await run.io_bound(dp.get_project_deployments, pid)
        except Exception as exc:
            ui.notify(deployment_error_message(exc), type="negative")
            return
        host.clear()
        options = {"": t("deployment_unresolved")} | {
            r["id"]: r["deployment_id"] for r in inventory
        }
        by_id = {r["id"]: r for r in inventory}
        videos = management["videos"]
        roots = {row["id"]: row["path"] for row in management["roots"]}
        folders = [
            row
            | {
                "review_status": (
                    t("deployment_review_ordinary_unavailable")
                    if row["ordinary_unavailable"]
                    else t("deployment_review_match_found")
                    if row["new_comparison_candidates"]
                    else ""
                ),
                "root_path": roots.get(row["root_id"], ""),
                "current": ", ".join(
                    f"{assignment['deployment'] or t('deployment_unresolved')}: {assignment['count']}"
                    for assignment in row["assignments"]
                ),
            }
            for row in management["folders"]
        ]

        async def refresh_inventory(_identifier):
            await reload()

        async def edit_inventory(existing=None, *, initial_deployment_id="", on_saved=None):
            open_deployment_editor(
                dp,
                pid,
                existing,
                initial_deployment_id=initial_deployment_id,
                on_saved=on_saved or refresh_inventory,
            )

        async def assignment_dialog(action="assign", folder=None):
            selected = [row["video_id"] for row in footage.selected]
            if action != "folder" and not selected:
                ui.notify(t("deployment_error_selection"), type="warning")
                return
            dialog = ui.dialog()
            preview = None
            revision = 0
            with dialog, ui.card().classes("q-pa-lg").style("width:1000px; max-width:95vw"):
                ui.label(
                    t("deployment_folder_link")
                    if action == "folder"
                    else t("deployment_reset")
                    if action == "reset"
                    else t("deployment_assign")
                ).classes("text-h6")
                if folder:
                    ui.label(folder["path"])
                    ui.label(t("deployment_folder_help")).classes("text-caption")
                picker = ui.select(
                    options, label=t("deployment_target"), value="", with_input=True
                ).classes("w-full")
                picker.set_visibility(action != "reset")
                result = ui.column().classes("w-full")
                error = ui.label("").classes("text-negative")

                def invalidate(_=None):
                    nonlocal preview, revision
                    revision += 1
                    preview = None
                    result.clear()
                    apply_button.disable()

                picker.on_value_change(invalidate)

                async def make_preview():
                    nonlocal preview
                    preview_button.disable()
                    invalidate()
                    error.set_text("")
                    requested = revision
                    try:
                        loaded = await run.io_bound(
                            dp.preview_deployment_assignment,
                            pid,
                            selected,
                            picker.value or None,
                            action=action,
                            folder=folder,
                        )
                    except Exception as exc:
                        error.set_text(deployment_error_message(exc))
                    else:
                        if requested != revision:
                            return
                        preview = loaded
                        with result:
                            render_assignment_preview(preview, grouped=action == "folder")
                        apply_button.set_enabled(not preview["blocked"])
                    finally:
                        preview_button.enable()

                async def apply():
                    if preview is None:
                        return
                    apply_button.disable()
                    try:
                        counts = await run.io_bound(dp.apply_deployment_assignment, preview)
                    except Exception as exc:
                        error.set_text(deployment_error_message(exc))
                        # A new preview is required after an error, never retry a stale one.
                        invalidate()
                        return
                    dialog.close()
                    ui.notify(t("deployment_applied", count=counts["changed"]), type="positive")
                    await reload()

                with ui.row():
                    preview_button = ui.button(t("deployment_preview"), on_click=make_preview)
                    apply_button = ui.button(t("deployment_apply"), on_click=apply)
                    apply_button.disable()
                    ui.button(t("cancel"), on_click=dialog.close).props("flat")
            dialog.open()

        async def inspect_video(row):
            await show_deployment_clip(dp, row["video_id"], roots.get(row["root_id"]))

        async def inspect_selected():
            if len(footage.selected) != 1:
                ui.notify(t("deployment_inspect_one"), type="warning")
                return
            await inspect_video(footage.selected[0])

        async def show_review(queue, start_id=None):
            if not queue:
                ui.notify(t("deployment_review_empty"), type="info")
                return
            management_panel.set_visibility(False)
            review_host.clear()
            review_host.set_visibility(True)

            async def closed(changed):
                review_host.clear()
                review_host.set_visibility(False)
                if open_reference_review:
                    ui.navigate.to("/deployments")
                else:
                    if changed:
                        await reload()
                    management_panel.set_visibility(True)

            with review_host:
                await review_deployment_folders(
                    dp,
                    pid,
                    queue,
                    inventory,
                    start_id=start_id,
                    on_close=closed,
                    edit_deployment=edit_inventory,
                )

        if open_reference_review:
            flagged = [row for row in folders if row["needs_reference_review"]]
            if flagged:
                await show_review(flagged)
                return
            ui.notify(t("deployment_review_empty"), type="info")

        if not tools:
            with host:
                if existing := by_id.get(deployment):
                    render_deployment_details(
                        existing,
                        videos,
                        folders,
                        management["links"],
                        roots,
                        edit=lambda: edit_inventory(existing),
                        review_folder=show_review,
                    )
                else:
                    render_deployment_catalog(
                        inventory,
                        videos,
                        folders,
                        add=edit_inventory,
                        review_pending=show_review,
                    )
            return

        with host:
            with ui.row().classes("w-full items-center"):
                ui.label(t("deployment_count", count=len(inventory))).classes("text-subtitle1")
                ui.button(t("deployment_add"), icon="add", on_click=lambda: edit_inventory())
                edit_picker = ui.select(
                    {r["id"]: r["deployment_id"] for r in inventory},
                    label=t("deployment_target"),
                    with_input=True,
                ).classes("grow")
                ui.button(
                    t("deployment_edit"),
                    icon="edit",
                    on_click=lambda: (
                        edit_inventory(by_id[edit_picker.value])
                        if edit_picker.value in by_id
                        else ui.notify(t("deployment_error_target"), type="warning")
                    ),
                )
            if inventory:
                with ui.expansion(t("deployment_title"), icon="list_alt").classes("w-full"):
                    render_inventory(inventory)
            with ui.row().classes("w-full items-center justify-between q-mt-md"):
                ui.label(t("deployment_review_folders")).classes("text-h6")
                ui.label(
                    t(
                        "deployment_review_count",
                        folders=len(folders),
                        attention=sum(row["needs_attention"] for row in folders),
                    )
                ).classes("text-caption")
            ui.label(t("deployment_review_help")).classes("text-caption text-grey-6")
            with ui.row().classes("w-full items-center"):
                folder_search = ui.input(t("deployment_search_folders")).classes("grow")
                folder_attention = ui.checkbox(t("deployment_needs_attention"), value=True).mark(
                    "deployment-folder-attention"
                )
            folder_columns = [
                {"name": key, "field": key, "label": label, "align": "left", "sortable": True}
                for key, label in (
                    ("folder", t("deployment_folder_name")),
                    ("total", t("deployment_folder_total")),
                    ("references", t("deployment_reference")),
                    ("unresolved", t("deployment_unresolved")),
                    ("unmatched_references", t("deployment_folder_unmatched")),
                    ("exceptions", t("deployment_folder_exceptions")),
                    ("current", t("deployment_folder_assignments")),
                    ("review_status", t("deployment_status")),
                )
            ]
            if len(management["roots"]) > 1:
                folder_columns.insert(
                    0, {"name": "root_path", "field": "root_path", "label": t("deployment_root")}
                )
            folder_table = (
                ui.table(
                    columns=folder_columns,
                    rows=[row for row in folders if row["needs_attention"]],
                    row_key="id",
                    selection="single",
                    pagination=10,
                )
                .classes("w-full")
                .props("wrap-cells")
            )
            folder_search.bind_value(folder_table, "filter")
            folder_attention.on_value_change(
                lambda event: folder_table.update_rows(
                    [row for row in folders if not event.value or row["needs_attention"]]
                )
            )

            async def open_review():
                term = (folder_search.value or "").casefold()
                queue = [
                    row
                    for row in folder_table.rows
                    if not term
                    or any(
                        term in str(row.get(column["field"], "")).casefold()
                        for column in folder_columns
                    )
                ]
                selected_id = folder_table.selected[0]["id"] if folder_table.selected else None
                await show_review(queue, start_id=selected_id)

            ui.button(
                t("deployment_review_open"), icon="playlist_add_check", on_click=open_review
            ).props("unelevated")
            with ui.expansion(t("deployment_folder_links"), icon="folder").classes("w-full"):
                ui.label(t("deployment_folder_help")).classes("text-caption")
                ui.table(
                    columns=[
                        {"name": k, "field": k, "label": label, "align": "left"}
                        for k, label in (
                            ("folder", "folder_path"),
                            ("deployment", t("deployment_target")),
                            ("source", t("deployment_provenance")),
                        )
                    ],
                    rows=management["links"],
                    row_key="id",
                    pagination=10,
                ).classes("w-full")
                root_picker = ui.select(
                    {r["id"]: r["path"] for r in management["roots"]},
                    label=t("deployment_root"),
                    value=management["roots"][0]["id"] if len(management["roots"]) == 1 else None,
                ).classes("w-full")
                path_input = ui.input(
                    "folder_path", placeholder=t("deployment_folder_placeholder")
                ).classes("w-full")
                known = {
                    (r["root_id"], r["folder"])
                    for r in videos
                    if r["root_id"] and r["folder"] is not None
                }
                known_paths = sorted(known)

                def choose_folder(event):
                    if event.value is not None:
                        root_picker.set_value(known_paths[event.value][0])
                        path_input.set_value(known_paths[event.value][1])

                ui.select(
                    {i: path for i, (_, path) in enumerate(known_paths)},
                    label=t("deployment_existing_folder"),
                    with_input=True,
                    on_change=choose_folder,
                ).classes("w-full")
                ui.button(
                    t("deployment_folder_link"),
                    icon="link",
                    on_click=lambda: assignment_dialog(
                        "folder", {"root_id": root_picker.value, "path": path_input.value or "."}
                    ),
                )
            with ui.expansion(t("deployment_advanced_video_tools"), icon="video_settings").classes(
                "w-full"
            ):
                ui.label(t("deployment_footage")).classes("text-h6 q-mt-md")
                ordinary = {
                    r["deployment_id"] for r in videos if not r["reference"] and r["deployment_id"]
                }

                def attention(row):
                    return (
                        not row["deployment_id"]
                        or row["reference"]
                        and row["deployment_id"] not in ordinary
                    )

                with ui.row().classes("w-full items-center"):
                    search = ui.input(t("deployment_search_footage")).classes("grow")
                    needs_attention = ui.checkbox(t("deployment_needs_attention"), value=True)
                columns = [
                    {"name": k, "field": k, "label": label, "align": "left", "sortable": True}
                    for k, label in (
                        ("filename", t("deployment_file")),
                        ("folder", "folder_path"),
                        ("deployment", t("deployment_current")),
                        ("assignment", t("deployment_provenance")),
                        ("reference", t("deployment_reference")),
                        ("missing", t("deployment_missing_media")),
                        ("inferred", t("deployment_baseline")),
                    )
                ]
                footage = ui.table(
                    columns=columns,
                    rows=[r for r in videos if attention(r)],
                    row_key="video_id",
                    selection="multiple",
                    pagination=20,
                ).classes("w-full")
                search.bind_value(footage, "filter")
                needs_attention.on_value_change(
                    lambda e: footage.update_rows(
                        [r for r in videos if not e.value or attention(r)], clear_selection=False
                    )
                )

                def select_matching():
                    term = (search.value or "").casefold()
                    footage.selected = [
                        r
                        for r in footage.rows
                        if any(term in str(r.get(c["field"], "")).casefold() for c in columns)
                    ]
                    footage.update()

                ui.label(t("deployment_selection_help")).classes("text-caption")
                with ui.row():
                    ui.button(t("deployment_select_matching"), on_click=select_matching).props(
                        "flat"
                    )
                    ui.button(
                        t("deployment_assign"),
                        icon="swap_horiz",
                        on_click=lambda: assignment_dialog(),
                    )
                    ui.button(
                        t("deployment_reset"), on_click=lambda: assignment_dialog("reset")
                    ).props("outline")
                    ui.button(
                        t("deployment_inspect"), icon="movie", on_click=inspect_selected
                    ).props("outline")

    await reload(open_reference_review=review_references)
