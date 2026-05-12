from nicegui import run, ui

from review_app.app.state import get_current_idx, get_queue
from review_app.app.translations import get_language

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

    with ui.row().classes("w-full gap-xs flex-wrap items-center"):
        for tag in all_tags:
            is_active = tag["key"] in active_tag_keys
            color = tag.get("color") or "grey"
            icon = tag.get("icon") or "label"
            label = _tag_label(tag)
            ui.chip(
                label,
                icon=icon,
                color=color if is_active else "grey-5",
                on_click=lambda k=tag["key"]: toggle_tag(k),
            ).props("clickable outline")
