"""Compact assignment workspace with focused creation and return-acceptance flows."""

from datetime import datetime

from nicegui import run, ui

from review_app.app.components.assignment_handoff import (
    _error_message,
    assignment_filename,
    assignment_stage_label,
    render_assignment_delivery,
    render_return_acceptance,
)
from review_app.app.components.assignment_video_table import AssignmentVideoTable
from review_app.app.state import get_active_project_id
from review_app.app.translations import get_language, t

from .assignment_bulk import render_bulk_actions
from .assignment_creation import render_assignment_creation


def assignment_date(value):
    """A readable display date; retain the precise original in technical details."""
    date = datetime.fromisoformat(value)
    return date.strftime("%d/%m/%Y %H:%M" if get_language() == "fr" else "%Y-%m-%d %H:%M")


def render_assignment_details(dp, project_id, binding, require_project, on_close, on_removed=None):
    with ui.row().classes("w-full items-start justify-between gap-3"):
        ui.label(binding["recipient"]).classes("text-h6")
        if on_close is not None:
            ui.button(icon="close", on_click=on_close).props("flat round").tooltip(t("close"))
    ui.label(assignment_stage_label(binding)).classes("text-subtitle1")
    ui.label(t("assign_video_count", count=binding["video_count"]))
    ui.label(t("assign_prepared_date", date=assignment_date(binding["created_at"]))).classes(
        "text-caption text-grey-7"
    )
    working = binding["role"] == "working"
    if working:
        ui.label(t("handoff_working_help")).classes("text-body2 text-grey-7")
        ui.button(
            t("data_continue_review"),
            icon="play_arrow",
            on_click=lambda: ui.navigate.to(
                "/distance-review" if binding["stage"] == "distance" else "/review"
            ),
        )

    async def download():
        download_button.disable()
        try:
            require_project()
            raw = await run.io_bound(
                dp.export_assignment_return if working else dp.export_assignment,
                project_id if working else binding["id"],
            )
            require_project()
            ui.download(
                raw,
                assignment_filename(
                    "return" if working else "assignment",
                    binding["recipient"],
                    binding["assignment_id"],
                ),
            )
            if working:
                ui.notify(t("data_return_downloaded"), type="positive")
        except Exception as exc:
            ui.notify(_error_message(exc), type="negative")
        finally:
            download_button.enable()

    download_button = ui.button(
        t("handoff_return" if working else "handoff_download"), on_click=download, icon="download"
    )
    if not working:
        ui.separator().classes("q-my-sm")
        download_button.props("flat")
        render_assignment_delivery(dp, project_id, binding, require_project)
    with ui.expansion(t("assign_saved_scope"), icon="list").classes("w-full") as scope:
        media_content = ui.column().classes("w-full")
        loaded = False

        async def load_media():
            nonlocal loaded
            if loaded or not scope.value:
                return
            try:
                require_project()
                media = await run.io_bound(dp.get_assignment_media, project_id, binding["id"])
                if media_content.is_deleted:
                    return
                require_project()
                media_content.clear()
                roots = {
                    key: index
                    for index, key in enumerate(dict.fromkeys(row["root_id"] for row in media), 1)
                }
                with media_content:
                    table = AssignmentVideoTable(
                        [
                            ("path", t("handoff_column_video")),
                            ("camera", t("distribution_camera_col")),
                            ("role", t("assign_media_role")),
                        ],
                        filters=[
                            ("camera", t("distribution_camera_col")),
                            ("role", t("assign_media_role")),
                        ],
                        marker="assignment-media",
                    )
                    table.set_rows(
                        [
                            {
                                "id": f"{row['root_id']}/{row['video_path']}",
                                "path": t(
                                    "assign_media_path",
                                    number=roots[row["root_id"]],
                                    path=row["video_path"],
                                ),
                                "camera": row["camera_id"] or "",
                                "role": t(
                                    "assign_work_media"
                                    if row["assigned"]
                                    else "assign_reference_media"
                                ),
                            }
                            for row in media
                        ]
                    )
                loaded = True
            except Exception as exc:
                if not media_content.is_deleted:
                    with media_content:
                        ui.label(_error_message(exc)).classes("text-negative")

        scope.on_value_change(load_media)
    with ui.expansion(t("assign_technical"), icon="info_outline").classes("w-full"):
        ui.label(binding["assignment_id"]).classes("text-caption break-all")
        ui.label(binding["created_at"]).classes("text-caption")

    if not working:
        with ui.dialog() as confirm, ui.card().classes("w-full max-w-xl gap-3"):
            ui.label(t("assign_remove_title")).classes("text-h6")
            ui.label(t("assign_remove_help")).classes("text-body2")
            removal_status = ui.label("").classes("text-negative")

            async def remove():
                remove_button.disable()
                cancel_button.disable()
                try:
                    require_project()
                    await run.io_bound(dp.remove_assignment, project_id, binding["id"])
                    require_project()
                    confirm.close()
                    ui.notify(t("assign_removed"), type="positive")
                    if on_removed is not None:
                        on_removed()
                except Exception as exc:
                    removal_status.set_text(_error_message(exc))
                finally:
                    remove_button.enable()
                    cancel_button.enable()

            with ui.row().classes("w-full justify-end gap-2"):
                cancel_button = ui.button(t("cancel"), on_click=confirm.close).props("flat")
                remove_button = (
                    ui.button(t("assign_remove"), on_click=remove)
                    .props("color=negative")
                    .mark("confirm-remove-assignment")
                )
        ui.button(t("assign_remove"), icon="delete_outline", on_click=confirm.open).props(
            "flat color=negative"
        ).mark("remove-assignment")


def render_assignments(dp, project_id: str) -> None:
    def require_project():
        if get_active_project_id() != project_id:
            raise ValueError(t("handoff_project_changed"))

    screen = "home"
    selected_id = None

    def show_home(assignment_id=None):
        nonlocal screen, selected_id
        screen, selected_id = "home", assignment_id
        workspace.refresh()

    def show_screen(value):
        nonlocal screen
        require_project()
        screen = value
        workspace.refresh()

    @ui.refreshable
    def workspace():
        bindings = dp.list_assignments(project_id)
        working = next((row for row in bindings if row["role"] == "working"), None)
        if screen == "create" and working is None:
            render_assignment_creation(dp, project_id, require_project, show_home, show_home)
            return
        if screen == "accept" and working is None:
            ui.button(t("assign_back"), on_click=show_home, icon="arrow_back").props("flat")
            ui.label(t("handoff_accept_title")).classes("text-h6")
            render_return_acceptance(dp, project_id, require_project)
            return
        if working:
            with ui.card().props("flat bordered").classes("w-full gap-3"):
                render_assignment_details(dp, project_id, working, require_project, None)
            return

        with ui.row().classes("w-full items-center justify-between gap-3 q-mb-md"):
            with ui.column().classes("gap-1"):
                ui.label(t("data_assignments")).classes("text-h6")
                ui.label(t("data_saved_assignments_help")).classes("text-body2 text-grey-7")
            with ui.row().classes("gap-2"):
                ui.button(
                    t("assign_accept"),
                    icon="move_to_inbox",
                    on_click=lambda: show_screen("accept"),
                ).props("outline")
                ui.button(t("assign_title"), icon="add", on_click=lambda: show_screen("create"))
        if not bindings:
            with ui.column().classes("w-full items-center q-pa-xl gap-3"):
                ui.icon("assignment", size="lg").classes("text-grey-5")
                ui.label(t("data_no_assignments")).classes("text-grey-7")
            return
        selected_ids = {selected_id} if selected_id else set()

        @ui.refreshable
        def bulk_actions():
            render_bulk_actions(
                dp,
                project_id,
                [row for row in bindings if row["id"] in selected_ids],
                require_project,
                show_home,
            )

        bulk_actions()
        with ui.row().classes("w-full items-start gap-4 flex-wrap"):
            rows = [
                {
                    "id": row["id"],
                    "recipient": row["recipient"],
                    "review": assignment_stage_label(row),
                    "videos": row["video_count"],
                    "prepared": assignment_date(row["created_at"]),
                }
                for row in bindings
            ]

            def select(assignment_id):
                nonlocal selected_id
                selected_id = assignment_id
                details.refresh()

            def selection_changed(event):
                nonlocal selected_ids
                selected_ids = {row["id"] for row in event.selection}
                select(event.selection[0]["id"] if len(event.selection) == 1 else None)
                bulk_actions.refresh()

            table = (
                ui.table(
                    columns=[
                        {
                            "name": key,
                            "field": key,
                            "label": t(label),
                            "align": "left",
                            "sortable": True,
                        }
                        for key, label in (
                            ("recipient", "handoff_recipient"),
                            ("review", "handoff_stage"),
                            ("videos", "distribution_videos_col"),
                            ("prepared", "assign_prepared_column"),
                        )
                    ],
                    rows=rows,
                    row_key="id",
                    pagination=10,
                    selection="multiple",
                    on_select=selection_changed,
                )
                .props("flat dense bordered")
                .classes("grow min-w-0 w-full lg:w-1/2")
            )
            table.on("rowClick", lambda event: select(event.args[1]["id"]))
            table.selected = [row for row in rows if row["id"] in selected_ids]

            @ui.refreshable
            def details():
                binding = next((row for row in bindings if row["id"] == selected_id), None)
                if binding:
                    with ui.card().props("flat bordered").classes("w-full gap-3"):
                        render_assignment_details(
                            dp,
                            project_id,
                            binding,
                            require_project,
                            lambda: select(None),
                            on_removed=show_home,
                        )
                else:
                    ui.label(t("assign_select_details")).classes("text-grey-7 q-pa-md")
                table.selected = [row for row in rows if row["id"] in selected_ids]
                table.update()

            with ui.column().classes("grow min-w-0 w-full lg:w-1/3"):
                details()

    workspace()
