from nicegui import ui

from review_app.app.pages.review.tags import _color_picker, _tag_label
from review_app.app.translations import t


@ui.refreshable
def render_tags_section(dp) -> None:
    all_tags = dp.get_all_tags()
    builtin_tags = [tag for tag in all_tags if not tag["is_custom"]]
    custom_tags = [tag for tag in all_tags if tag["is_custom"]]

    ui.label(t("settings_tags_builtin_label")).classes("text-caption text-grey-6 q-mb-xs")
    with ui.row().classes("gap-xs flex-wrap q-mb-md"):
        for tag in builtin_tags:
            color = tag.get("color") or "grey"
            icon = tag.get("icon") or "label"
            ui.chip(_tag_label(tag), icon=icon, color=color).props("outline")

    ui.label(t("settings_tags_custom_label")).classes("text-caption text-grey-6 q-mb-xs")

    if not custom_tags:
        ui.label(t("settings_tags_no_custom")).classes("text-caption text-grey-5 q-mb-sm")
    else:
        with ui.column().classes("w-full gap-sm q-mb-sm"):
            for tag in custom_tags:
                _render_custom_tag_row(dp, tag)


def _render_custom_tag_row(dp, tag: dict) -> None:
    color = tag.get("color") or "grey"

    def open_edit_dialog():
        state = {"color": tag.get("color")}

        def save():
            name = (name_input.value or "").strip()
            if not name:
                return
            dp.update_tag_name(tag["key"], name)
            dp.update_tag_color(tag["key"], state["color"])
            dlg.close()
            render_tags_section.refresh()

        with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width: 340px"):
            ui.label(t("settings_tags_edit_title")).classes("text-subtitle2 q-mb-sm")
            name_input = ui.input(value=tag["name_en"]).props(
                "outlined dense autofocus class=full-width q-mb-sm"
            )
            ui.label(t("tag_color_label")).classes("text-caption q-mb-xs")
            _color_picker(tag.get("color"), on_select=lambda c: state.update({"color": c}))
            with ui.row().classes("w-full justify-end gap-sm q-mt-xs"):
                ui.button(t("cancel"), on_click=dlg.close).props("flat")
                ui.button(t("save"), on_click=save, color="primary")
        dlg.open()

    def open_delete_dialog():
        with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width: 340px"):
            ui.label(t("settings_tags_delete_title")).classes("text-subtitle2 q-mb-xs")
            ui.label(t("settings_tags_delete_body", name=_tag_label(tag))).classes(
                "text-caption text-grey-7 q-mb-md"
            )
            with ui.row().classes("w-full justify-end gap-sm"):
                ui.button(t("cancel"), on_click=dlg.close).props("flat")
                ui.button(t("delete"), on_click=lambda: _confirm_delete(dlg), color="negative")
        dlg.open()

    def _confirm_delete(dlg):
        dp.delete_custom_tag(tag["key"])
        dlg.close()
        render_tags_section.refresh()

    with ui.row().classes("w-full items-center gap-sm"):
        ui.chip(_tag_label(tag), icon="label", color=color).props("outline")
        ui.label(tag["key"]).classes("text-caption text-grey-5 col")
        ui.button(icon="edit", on_click=open_edit_dialog).props(
            "flat round dense size=sm color=grey-6"
        )
        ui.button(icon="delete", on_click=open_delete_dialog).props(
            "flat round dense size=sm color=negative"
        )
