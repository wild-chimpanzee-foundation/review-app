from nicegui import run, ui

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


def _fuzzy(query: str, text: str) -> bool:
    it = iter(text)
    return all(c in it for c in query)


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


def tag_selector(
    all_tags: list[dict], active_keys: set[str], on_toggle, shortcut: str | None = None
) -> callable:
    """
    Renders a searchable tag dropdown. active_keys is a shared mutable set that the
    component reads and writes. on_toggle(tag_key) is called after each toggle.
    Returns a callable that refreshes the chip display (use after external resets).
    """
    is_open = {"value": False}
    search_state = {"query": "", "input": None}

    def open_menu():
        is_open["value"] = True
        render_dropdown.refresh()

    def toggle_menu():
        is_open["value"] = not is_open["value"]
        if not is_open["value"]:
            search_state["query"] = ""
            if search_state["input"]:
                search_state["input"].value = ""
            render_menu_chips.refresh()
        render_dropdown.refresh()

    async def toggle_tag(tag_key: str):
        if tag_key in active_keys:
            active_keys.discard(tag_key)
        else:
            active_keys.add(tag_key)
        render_active_chips.refresh()
        render_menu_chips.refresh()
        await on_toggle(tag_key)

    @ui.refreshable
    def render_active_chips():
        for tag in all_tags:
            if tag["key"] not in active_keys:
                continue
            ui.chip(
                _tag_label(tag),
                icon=tag.get("icon") or "label",
                color=tag.get("color") or "grey",
                on_click=lambda k=tag["key"]: toggle_tag(k),
            ).props("dense")

    @ui.refreshable
    def render_menu_chips():
        query = search_state["query"].lower()
        for tag in all_tags:
            if tag["key"] in active_keys:
                continue
            if query and not _fuzzy(query, _tag_label(tag).lower()):
                continue
            ui.chip(
                _tag_label(tag),
                icon=tag.get("icon") or "label",
                color="grey-5",
                on_click=lambda k=tag["key"]: toggle_tag(k),
            ).props("clickable dense outline")

    @ui.refreshable
    def render_dropdown():
        if not is_open["value"]:
            return
        # transparent backdrop so clicking outside the dropdown closes it
        ui.element("div").style("position: fixed; inset: 0; z-index: 1999;").on(
            "click", toggle_menu
        )
        with (
            ui.element("div")
            .classes("q-card q-pa-sm")
            .style(
                "position: absolute; left: 0; right: 0; top: 100%; z-index: 2000; "
                "max-height: 250px; overflow-y: auto; border-radius: 4px;"
            )
        ):
            with ui.row().classes("gap-xs flex-wrap"):
                render_menu_chips()

    with ui.element("div").classes("full-width").style("position: relative;"):
        trigger = (
            ui.element("div")
            .classes("row items-center q-pa-xs gap-xs")
            .style(
                "border: 1px solid rgba(128,128,128,0.4); border-radius: 4px; "
                "min-height: 40px; flex-wrap: wrap; cursor: pointer; width: 100%; "
                "position: relative; z-index: 2001;"
            )
        )
        with trigger:
            with (
                ui.element("div")
                .classes("row gap-xs flex-wrap items-center")
                .on("click", js_handler="(e) => e.stopPropagation()")
            ):
                render_active_chips()
            search_input = (
                ui.input(placeholder=t("add_tag_btn"))
                .props("borderless dense")
                .style("min-width: 60px; flex: 1;")
            )
            if shortcut:
                search_input._props["data-shortcut"] = shortcut

            def _filtered_remaining():
                query = search_state["query"].lower()
                return [
                    t
                    for t in all_tags
                    if t["key"] not in active_keys
                    and (not query or _fuzzy(query, _tag_label(t).lower()))
                ]

            # input click must not bubble to trigger (would re-toggle menu)
            async def on_enter():
                remaining = _filtered_remaining()
                if len(remaining) == 1:
                    await toggle_tag(remaining[0]["key"])
                    search_state["query"] = ""
                    search_state["input"].value = ""

            def on_search_change(e):
                search_state["query"] = e.value or ""
                render_menu_chips.refresh()

            search_input.on("click", js_handler="(e) => e.stopPropagation()")
            search_input.on("focus", lambda: open_menu())
            # intercept at keydown to block global shortcuts before they fire
            search_input.on(
                "keydown.enter", js_handler="(e) => { e.stopPropagation(); e.preventDefault(); }"
            )
            search_input.on("keyup.enter", lambda _: on_enter())
            search_input.on_value_change(on_search_change)
            search_state["input"] = search_input

        trigger.on("click", toggle_menu)
        render_dropdown()

    def refresh_display():
        render_active_chips.refresh()
        render_menu_chips.refresh()

    return refresh_display


@ui.refreshable
async def render_video_tags(video_id: str, dp, annotator: str | None):
    all_tags = sorted(dp.get_all_tags(), key=_tag_label)
    active_keys = set(await run.io_bound(dp.get_video_tags, video_id))

    async def on_toggle(tag_key: str):
        await run.io_bound(dp.toggle_video_tag, video_id, tag_key, annotator)

    tag_selector(all_tags, active_keys, on_toggle, shortcut="focus-tags")
