from nicegui import run, ui

from review_app.app.pages.review.tags import _color_picker, _tag_label
from review_app.app.translations import t


class TagsSection:
    def __init__(self, dp):
        self.dp = dp

    @ui.refreshable_method
    def render(self) -> None:
        dp = self.dp
        all_tags = dp.get_all_tags()
        builtin_tags = [tag for tag in all_tags if not tag["is_custom"]]
        custom_tags = [tag for tag in all_tags if tag["is_custom"]]

        ui.label(t("settings_tags_builtin_label")).classes("text-caption text-grey-6 q-mb-xs")
        with ui.row().classes("gap-xs flex-wrap q-mb-md"):
            for tag in builtin_tags:
                color = tag.get("color") or "grey"
                icon = tag.get("icon") or "label"
                ui.chip(_tag_label(tag), icon=icon, color=color).props("outline")

        with ui.row().classes("items-center justify-between w-full q-mb-xs"):
            ui.label(t("settings_tags_custom_label")).classes("text-caption text-grey-6")
            ui.button(
                t("add_tag_btn"), icon="add", on_click=lambda: _open_add_tag_dialog(self, all_tags)
            ).props("size=sm outline color=primary")

        if not custom_tags:
            ui.label(t("settings_tags_no_custom")).classes("text-caption text-grey-5 q-mb-sm")
        else:
            with ui.column().classes("w-full gap-sm q-mb-sm"):
                for tag in custom_tags:
                    _render_custom_tag_row(self, tag)


def _open_add_tag_dialog(section: TagsSection, all_tags: list) -> None:
    dp = section.dp
    state = {"color": "teal", "name_input": None, "name_fr_input": None, "submitting": False}

    async def confirm_add():
        if state["submitting"]:
            return
        name = (state["name_input"].value or "").strip()
        name_fr = (state["name_fr_input"].value or "").strip() or None
        if not name and not name_fr:
            ui.notify(t("add_tag_name_required"), type="warning")
            return
        existing_names = {
            n
            for tag in all_tags
            for n in (tag.get("name_en") or "", tag.get("name_fr") or "")
            if n
        }
        if (name and name.lower() in {n.lower() for n in existing_names}) or (
            name_fr and name_fr.lower() in {n.lower() for n in existing_names}
        ):
            ui.notify(t("add_tag_duplicate"), type="warning")
            return
        state["submitting"] = True
        confirm_btn.props("loading")
        try:
            await run.io_bound(dp.create_custom_tag, name, state["color"], name_fr)
        except ValueError as exc:
            ui.notify(str(exc), type="negative")
            return
        finally:
            state["submitting"] = False
        dlg.close()
        section.render.refresh()

    with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width: 380px"):
        ui.label(t("add_tag_warning_title")).classes("text-subtitle2 q-mb-sm")
        state["name_input"] = ui.input(
            label=t("add_tag_name_label"), placeholder=t("add_tag_name_placeholder")
        ).props("outlined dense autofocus class=full-width q-mb-sm")
        state["name_fr_input"] = ui.input(
            label=t("add_tag_name_fr_label"), placeholder=t("add_tag_name_fr_placeholder")
        ).props("outlined dense class=full-width q-mb-sm")
        ui.label(t("tag_color_label")).classes("text-caption q-mb-xs")
        _color_picker("teal", on_select=lambda c: state.update({"color": c}))
        with ui.row().classes("w-full justify-end gap-sm q-mt-xs"):
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
            confirm_btn = ui.button(t("add_tag_confirm"), on_click=confirm_add, color="primary")
    dlg.open()


def _render_custom_tag_row(section: TagsSection, tag: dict) -> None:
    dp = section.dp
    color = tag.get("color") or "grey"

    def open_edit_dialog():
        state = {"color": tag.get("color")}

        def save():
            name = (name_input.value or "").strip()
            name_fr = (name_fr_input.value or "").strip() or None
            if not name and not name_fr:
                ui.notify(t("add_tag_name_required"), type="warning")
                return
            dp.update_tag_names(tag["key"], name, name_fr)
            dp.update_tag_color(tag["key"], state["color"])
            dlg.close()
            section.render.refresh()

        with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width: 340px"):
            ui.label(t("settings_tags_edit_title")).classes("text-subtitle2 q-mb-sm")
            name_input = ui.input(label=t("add_tag_name_label"), value=tag["name_en"]).props(
                "outlined dense autofocus class=full-width q-mb-sm"
            )
            name_fr_input = ui.input(
                label=t("add_tag_name_fr_label"), value=tag.get("name_fr") or ""
            ).props("outlined dense class=full-width q-mb-sm")
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
        section.render.refresh()

    with ui.row().classes("w-full items-center gap-sm"):
        ui.chip(_tag_label(tag), icon="label", color=color).props("outline")
        ui.label(tag["key"]).classes("text-caption text-grey-5 col")
        ui.button(icon="edit", on_click=open_edit_dialog).props(
            "flat round dense size=sm color=grey-6"
        )
        ui.button(icon="delete", on_click=open_delete_dialog).props(
            "flat round dense size=sm color=negative"
        )
