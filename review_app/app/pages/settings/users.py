from nicegui import run, ui

from review_app.app.state import get_annotator_name
from review_app.app.translations import t


def render_users_section(dp) -> None:
    """List all annotators and allow deleting those without any annotations.

    Annotators are global (shared across projects), so an annotator may only be
    removed when they have no saved annotations in *any* project. The currently
    signed-in user can't delete their own account.
    """
    current = get_annotator_name()

    ui.label(t("users_section_desc")).classes("text-caption text-grey-6 q-mb-md")

    @ui.refreshable
    def _user_list():
        annotators = dp.get_all_annotators()
        counts = dp.get_annotator_annotation_counts()

        if not annotators:
            ui.label(t("users_none")).classes("text-caption text-grey-5")
            return

        with ui.grid(columns="1fr auto auto").classes("w-full gap-x-md gap-y-xs items-center"):
            for name in annotators:
                n = counts.get(name, 0)

                ui.label(name).classes("text-body2")
                if n:
                    ui.label(t("users_annotation_count", n=n)).classes(
                        "text-caption text-grey-6 text-right"
                    )
                else:
                    ui.label(t("users_no_annotations")).classes(
                        "text-caption text-grey-5 text-right"
                    )

                if n or name == current:
                    reason = (
                        t("users_cannot_delete_self")
                        if name == current
                        else t("users_cannot_delete_has_annotations")
                    )
                    ui.button(icon="delete").props("flat dense color=grey-5").classes(
                        "disabled"
                    ).tooltip(reason)
                else:
                    ui.button(
                        icon="delete",
                        on_click=lambda _e, nm=name: _confirm_delete(nm),
                    ).props("flat dense color=negative").tooltip(t("users_delete_btn"))

    async def _delete(name: str):
        await run.io_bound(dp.remove_annotator, name)
        ui.notify(t("users_deleted", name=name), type="positive")
        _user_list.refresh()

    def _confirm_delete(name: str):
        with ui.dialog() as dialog, ui.card().classes("q-pa-lg"):
            ui.label(t("users_delete_confirm", name=name)).classes("text-body1 q-mb-md")
            with ui.row().classes("w-full justify-end gap-sm"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")

                async def _do():
                    dialog.close()
                    await _delete(name)

                ui.button(t("yes_delete"), icon="delete_forever", color="negative", on_click=_do)
        dialog.open()

    _user_list()
