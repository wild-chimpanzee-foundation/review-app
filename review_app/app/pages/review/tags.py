from nicegui import run, ui

from review_app.app.state import get_current_idx, get_queue
from review_app.app.translations import get_language, t

PALETTE = [
    "red",
    "pink",
    "purple",
    "deep-purple",
    "indigo",
    "blue",
    "cyan",
    "teal",
    "green",
    "lime",
    "amber",
    "orange",
    "deep-orange",
    "brown",
]


def _tag_label(tag: dict) -> str:
    if get_language() == "fr" and tag.get("name_fr"):
        return tag["name_fr"]
    return tag.get("name_en") or tag["key"]


def _color_picker(initial_color: str | None, on_select):
    """Render a row of color swatches. Calls on_select(color) when one is clicked."""
    selected = {"color": initial_color}
    btns: dict[str, ui.button] = {}

    def _refresh_swatches():
        for c, btn in btns.items():
            if c == selected["color"]:
                btn.props(remove="outline")
            else:
                btn.props(add="outline")

    def pick(c):
        selected["color"] = c
        on_select(c)
        _refresh_swatches()

    with ui.row().classes("gap-xs flex-wrap q-mb-sm"):
        for c in PALETTE:
            is_sel = c == initial_color
            btn = ui.button(on_click=lambda col=c: pick(col)).props(
                f"round dense {'color=' + c if is_sel else 'outline color=' + c} size=sm"
            )
            btns[c] = btn


@ui.refreshable
async def render_video_tags(video_id: str, dp, annotator: str | None):
    all_tags = dp.get_all_tags()
    active_tag_keys = await run.io_bound(dp.get_video_tags, video_id)

    async def toggle_tag(tag_key: str):
        await run.io_bound(dp.toggle_video_tag, video_id, tag_key, annotator)
        # Abort if the user navigated away before the IO completed
        queue = get_queue()
        if not queue or queue[get_current_idx()] != video_id:
            return
        try:
            render_video_tags.refresh()
        except RuntimeError:
            pass

    def open_add_tag_dialog():
        state = {"color": None, "name_input": None}

        async def confirm_add():
            name = (state["name_input"].value or "").strip()
            if not name:
                return
            try:
                tag_key = await run.io_bound(dp.create_custom_tag, name, state["color"])
                await toggle_tag(tag_key)
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
            dlg.close()

        with ui.dialog() as dlg, ui.card().classes("q-pa-md").style("min-width: 380px"):
            with ui.row().classes("items-center gap-sm q-mb-sm"):
                ui.icon("warning", color="warning", size="sm")
                ui.label(t("add_tag_warning_title")).classes("text-subtitle2")
            ui.label(t("add_tag_warning_body")).classes("text-caption text-grey-7 q-mb-sm")
            state["name_input"] = ui.input(
                label=t("add_tag_name_label"), placeholder=t("add_tag_name_placeholder")
            ).props("outlined dense autofocus class=full-width q-mb-sm")
            ui.label(t("tag_color_label")).classes("text-caption q-mb-xs")
            _color_picker(None, on_select=lambda c: state.update({"color": c}))
            with ui.row().classes("w-full justify-end gap-sm q-mt-xs"):
                ui.button(t("cancel"), on_click=dlg.close).props("flat")
                ui.button(t("add_tag_confirm"), on_click=confirm_add, color="primary")

        dlg.open()

    with ui.row().classes("w-full gap-xs flex-wrap items-center"):
        for tag in all_tags:
            is_active = tag["key"] in active_tag_keys
            color = tag.get("color") or "grey"
            icon = tag.get("icon") or "label"
            label = _tag_label(tag)
            chip = ui.chip(
                label,
                icon=icon,
                color=color if is_active else "grey-5",
                on_click=lambda k=tag["key"]: toggle_tag(k),
            ).props("clickable")
            if not is_active:
                chip.props("outline")

        ui.chip(
            t("add_tag_btn"),
            icon="add",
            color="grey-5",
            on_click=open_add_tag_dialog,
        ).props("outline clickable")
