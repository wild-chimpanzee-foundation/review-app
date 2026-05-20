from __future__ import annotations

from review_app.app.state import get_active_project_id, get_data_provider, reset_app_state
from review_app.app.translations import t
from review_app.app.utils import switch_project
from review_app.backend.db.backup import BackupError, create_backup


def _warn_missing_dirs(missing: list[str]) -> None:
    from nicegui import ui

    for path in missing:
        ui.notify(t("dir_not_found", path=path), type="warning", timeout=0)


def build_project_picker():
    """Build the project-picker dialog. Returns (dialog, refresh_fn)."""
    from nicegui import ui

    with ui.dialog() as dialog, ui.card().classes("q-pa-lg").style("min-width: 480px"):
        ui.label(t("switch_project")).classes("text-h6 q-mb-md")
        projects_col = ui.column().classes("w-full gap-xs q-mb-md")

        def refresh():
            projects_col.clear()
            _dp = get_data_provider()
            if not _dp:
                return
            projects = _dp.list_projects()
            active_id = get_active_project_id()
            with projects_col:
                for proj in projects:
                    is_active = proj.id == active_id
                    vid_count = _dp.get_project_video_count(proj.id)

                    with ui.row().classes("w-full items-center gap-sm"):

                        def make_switch(pid):
                            async def do_switch():
                                dp = get_data_provider()
                                missing = switch_project(dp, pid)
                                _warn_missing_dirs(missing)
                                dialog.close()
                                ui.navigate.to("/overview")

                            return do_switch

                        def make_delete(pid, pname, count):
                            def do_delete():
                                with (
                                    ui.dialog() as confirm_dialog,
                                    ui.card().classes("q-pa-lg").style("min-width: 360px"),
                                ):
                                    ui.label(
                                        t("delete_project_confirm_title", name=pname)
                                    ).classes("text-h6 q-mb-sm")
                                    if count > 0:
                                        ui.label(
                                            t("delete_project_confirm_body", count=count)
                                        ).classes("text-body2 text-negative q-mb-md")
                                    else:
                                        ui.label(t("delete_project_confirm_empty")).classes(
                                            "text-body2 q-mb-md"
                                        )

                                    async def confirm():
                                        dp = get_data_provider()
                                        try:
                                            create_backup(reason="delete_project")
                                        except BackupError as exc:
                                            ui.notify(
                                                t(
                                                    "backup_failed_proceed",
                                                    error=t(exc.user_message_key),
                                                ),
                                                type="warning",
                                            )
                                        dp.delete_project(pid)
                                        confirm_dialog.close()
                                        if pid == get_active_project_id():
                                            other = dp.get_most_recent_project()
                                            if other:
                                                missing = switch_project(dp, other.id)
                                                _warn_missing_dirs(missing)
                                                dialog.close()
                                                ui.navigate.to("/overview")
                                            else:
                                                from review_app.app.state import set_active_project

                                                reset_app_state()
                                                set_active_project(None)
                                                dialog.close()
                                                ui.navigate.to("/setup")
                                        else:
                                            refresh()

                                    with ui.row().classes("gap-sm justify-end w-full"):
                                        ui.button(
                                            t("cancel"), on_click=confirm_dialog.close
                                        ).props("flat")
                                        ui.button(t("delete"), color="negative", on_click=confirm)
                                confirm_dialog.open()

                            return do_delete

                        ui.button(
                            proj.name,
                            on_click=make_switch(proj.id),
                            color="primary" if is_active else None,
                        ).props(f"{'unelevated' if is_active else 'flat'} dense").classes(
                            "col text-left"
                        )
                        if is_active:
                            ui.icon("check_circle", color="positive", size="sm")
                        ui.button(
                            icon="delete_outline",
                            on_click=make_delete(proj.id, proj.name, vid_count),
                        ).props("flat round dense color=negative").tooltip(
                            t("delete_project_tooltip")
                        )

        refresh()

        ui.separator().classes("q-my-sm")

        def go_to_setup():
            dialog.close()
            ui.navigate.to("/setup")

        ui.button(t("new_project"), icon="add", color="primary", on_click=go_to_setup).props(
            "flat dense"
        ).classes("w-full")

    return dialog, refresh
