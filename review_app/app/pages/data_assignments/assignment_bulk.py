"""Bulk actions for saved assignments; each delivery retains its original snapshot."""

from nicegui import run, ui

from review_app.app.components.assignment_handoff import (
    _error_message,
    render_assignment_delivery,
)
from review_app.app.translations import t


def render_bulk_actions(dp, project_id, selected, require_project, on_removed):
    if len(selected) < 2:
        return
    with ui.row().classes("w-full items-center gap-2").mark("assignment-bulk-actions"):
        ui.label(t("assign_selected_count", count=len(selected)))

        render_assignment_delivery(dp, project_id, selected, require_project)
        with (
            ui.dialog().props("persistent") as confirm,
            ui.card().classes("w-full max-w-xl gap-3"),
        ):
            ui.label(t("assign_remove_selected_title", count=len(selected))).classes("text-h6")
            ui.label(t("assign_remove_help")).classes("text-body2")
            status = ui.label("").classes("text-negative whitespace-pre-wrap")
            removed = set()

            def close():
                confirm.close()
                if removed:
                    on_removed()

            async def remove():
                remove_button.disable()
                cancel_button.disable()
                errors = []
                try:
                    for binding in selected:
                        if binding["id"] in removed:
                            continue
                        require_project()
                        try:
                            await run.io_bound(dp.remove_assignment, project_id, binding["id"])
                            removed.add(binding["id"])
                        except Exception as exc:
                            errors.append(f"{binding['recipient']}: {_error_message(exc)}")
                    require_project()
                    if errors:
                        status.set_text("\n".join(errors))
                    else:
                        ui.notify(t("assign_removed"), type="positive")
                        close()
                except Exception as exc:
                    status.set_text(_error_message(exc))
                finally:
                    if not confirm.is_deleted:
                        remove_button.enable()
                        cancel_button.enable()

            with ui.row().classes("w-full justify-end gap-2"):
                cancel_button = ui.button(t("cancel"), on_click=close).props("flat")
                remove_button = (
                    ui.button(t("assign_remove_selected"), on_click=remove)
                    .props("color=negative")
                    .mark("confirm-remove-selected-assignments")
                )
        ui.button(t("assign_remove_selected"), icon="delete_outline", on_click=confirm.open).props(
            "flat color=negative"
        ).mark("remove-selected-assignments")
