from nicegui import run, ui

from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_current_idx,
    get_queue,
    get_selections,
    get_state_val,
    set_current_idx,
    set_selections,
    set_state_val,
)
from review_app.app.translations import get_language, t
from review_app.app.utils import format_utc_timestamp, get_probability_color
from review_app.backend.errors import SpeciesError


def _shortcut_badge(key):
    ui.badge(key).props("color=grey-9").classes("text-caption")


def _normalize_is_blank(raw):
    """Coerce DB-origin values (numpy bools, floats, NaN) to Python bool | None."""
    if raw is None:
        return None
    # NaN check without requiring a pandas import here
    if isinstance(raw, float) and raw != raw:
        return None
    return bool(raw)


def _render_labeled_by_meta(labeled_by, labeled_at=None):
    """Render person icon and labeled-by metadata with optional timestamp."""
    ui.icon("person", size="xs").classes("")
    meta = t("labeled_by", name=labeled_by)
    if labeled_at:
        date_str = format_utc_timestamp(labeled_at)
        meta += f" · {t('labeled_at', date=date_str)}"
    ui.label(meta).classes("text-caption")


def _resolve_behavior(behaviors_map, current_value=None):
    if current_value in behaviors_map:
        return current_value
    if "does_not_react" in behaviors_map:
        return "does_not_react"
    if behaviors_map:
        return list(behaviors_map.keys())[0]
    return None


def _new_annotation(species, behavior, duration_sec, source=None, probability=None, count=1):
    ann = {
        "species": species,
        "behavior": behavior,
        "start_sec": 0.0,
        "end_sec": duration_sec,
        "count": count,
    }
    if source:
        ann["source"] = source
        ann["probability"] = probability
    return ann


def _init_annotation_state(video, default_species, default_behavior):
    is_blank = _normalize_is_blank(video.get("is_blank"))
    selections = list(video.get("manual_selections") or [])

    if is_blank is None and not selections:
        if video.get("predicted_blank"):
            is_blank = True
        else:
            is_blank = False
            model_suggestions = video.get("model_suggestions") or []
            if model_suggestions:
                selections = [
                    _new_annotation(
                        s["species"],
                        default_behavior,
                        video.get("duration_sec"),
                        source="model",
                        probability=s["probability"],
                        count=s["count"],
                    )
                    for s in model_suggestions
                ]

    set_state_val("review_is_blank", is_blank)
    set_selections(selections)
    set_state_val("review_state_video_id", video.get("video_id"))
    set_state_val("video_tags", list(video.get("video_tags") or []))


def _build_group_options(species_groups: dict) -> dict[str, str]:
    seen = sorted({grp for grp in species_groups.values() if grp})
    return {"": t("all_groups"), **{g: g for g in seen}}


def _filter_species_by_group(species_map: dict, species_groups: dict, group: str) -> dict:
    if not group:
        return species_map
    return {sci: label for sci, label in species_map.items() if species_groups.get(sci) == group}


def render_annotation_section_body(page, video, default_species, default_behavior):
    species_map = page.species_map
    species_groups = page.species_groups
    dp = page.dp
    # Always reinitialize state when the rendered video differs from what state belongs to
    cached_video_id = get_state_val("review_state_video_id")
    if cached_video_id != video.get("video_id"):
        _init_annotation_state(video, default_species, default_behavior)

    is_blank = get_state_val("review_is_blank")
    selections = get_selections()
    if selections is None:
        selections = []

    async def delete_selection(idx):
        new_sels = get_selections()
        if 0 <= idx < len(new_sels):
            new_sels.pop(idx)
            set_selections(new_sels)
        page.render_annotation_section.refresh()

    def set_not_blank():
        set_state_val("review_is_blank", False)
        existing = list(video.get("manual_selections") or [])
        if not existing:
            existing = [
                _new_annotation(default_species, default_behavior, video.get("duration_sec"))
            ]
        set_selections(existing)
        page.render_annotation_section.refresh()

    # UI Rendering
    if is_blank:
        blank_prob = video.get("blank_model_probability")
        max_sp = video.get("max_species_confidence")
        human_labeled = _normalize_is_blank(video.get("is_blank")) is True
        with (
            ui.card()
            .classes("full-width q-pa-md q-mb-sm")
            .style("border: 2px solid var(--q-warning)")
        ):
            with ui.row().classes("items-center gap-sm"):
                ui.badge(t("blank"), color="warning").classes("text-body2 px-4 py-2 rounded-full")

            with ui.row().classes("items-center gap-xs q-mt-xs w-full"):
                if human_labeled:
                    annotator = video.get("blank_labeled_by")
                    labeled_at = video.get("blank_labeled_at")
                    if annotator:
                        _render_labeled_by_meta(annotator, labeled_at)
                    else:
                        ui.icon("person", size="xs").classes("")
                        ui.label(t("blank_source_human")).classes("text-caption")
                else:
                    ui.icon("smart_toy", size="xs").classes("")
                    ui.label(t("blank_source_model")).classes("text-caption")
                    if blank_prob is not None:
                        ui.label(t("blank")).classes("text-caption")
                        ui.label(f"{blank_prob:.0%}").style(
                            f"color: {get_probability_color(blank_prob)}; font-weight: bold"
                        ).classes("text-caption")
                        ui.label("·").classes("text-caption")
                        ui.label(t("species_label")).classes("text-caption")
                        ui.label(f"{max_sp:.0%}").style(
                            f"color: {get_probability_color(max_sp)}; font-weight: bold"
                        ).classes("text-caption")
                ui.element("div").classes("col")
                ui.button(icon="delete", on_click=set_not_blank, color="negative").props("flat")
    else:

        def add_species():
            new_sels = get_selections()
            new_sels.insert(0, _new_annotation(None, default_behavior, video.get("duration_sec")))
            set_selections(new_sels)
            set_state_val("review_is_blank", False)
            set_state_val("focus_new_species", True)
            page.render_annotation_section.refresh()

        with ui.element("div").style(
            "overflow-y: auto; overflow-x: hidden; max-height: calc(100vh - 360px); width: 100%;"
        ):
            with ui.row().classes("w-full justify-center q-mb-xs"):
                with ui.button(on_click=add_species).props(
                    "size=md color=primary outline"
                ).style("border-style: dashed") as add_species_btn:
                    with ui.row().classes("items-center justify-between w-full q-px-xs"):
                        with ui.row().classes("items-center gap-xs col justify-center"):
                            ui.icon("add", size="sm")
                            ui.label(t("add_species"))
                        _shortcut_badge("A")
                add_species_btn._props["data-shortcut"] = "add-species"
            for i, sel in enumerate(selections):
                ann_card = (
                    ui.card()
                    .classes("full-width q-pa-md q-mb-sm")
                    .style("border: 2px solid var(--q-primary)")
                )
                ann_card._props["data-annotation-idx"] = str(i)
                with ann_card:
                    active_project_id = get_active_project_id()
                    behaviors_map = dp.get_behavior_display_map(
                        lang=get_language(),
                        species_name=sel["species"],
                        project_id=active_project_id,
                    )
                    sp_value = sel["species"] if sel["species"] in species_map else None
                    bp_value = _resolve_behavior(behaviors_map, sel.get("behavior"))
                    group_options = _build_group_options(species_groups)
                    initial_group = species_groups.get(sp_value, "") if sp_value else ""
                    filtered_species = _filter_species_by_group(
                        species_map, species_groups, initial_group
                    )

                    with ui.row().classes("w-full gap-sm items-center"):
                        gp = (
                            ui.select(
                                label=t("group_label"),
                                options=group_options,
                                value=initial_group,
                                with_input=True,
                            )
                            .props("outlined dense clearable")
                            .classes("col")
                        )

                    with ui.row().classes("w-full gap-sm items-center q-mt-sm"):
                        sp = (
                            ui.select(
                                label=t("species_label"),
                                options=filtered_species,
                                value=sp_value,
                                with_input=True,
                            )
                            .props("outlined dense")
                            .classes("col")
                        )
                        if i == 0 and get_state_val("focus_new_species"):
                            set_state_val("focus_new_species", False)
                            sp.run_method("focus")

                    with ui.row().classes("w-full gap-sm items-center q-mt-sm"):
                        bp = (
                            ui.select(
                                label=t("behavior_label"),
                                options=behaviors_map,
                                value=bp_value,
                                with_input=True,
                            )
                            .props("outlined dense")
                            .style("flex: 2; min-width: 120px;")
                        )

                    current_count = min(sel.get("count") or 1, 11)
                    count_options = {i: str(i) for i in range(1, 11)}
                    count_options[11] = ">10"
                    with ui.row().classes("items-center gap-xs q-mt-sm w-full"):
                        ct = (
                            ui.select(
                                label=t("count_label"),
                                options=count_options,
                                value=current_count,
                            )
                            .props("outlined dense")
                            .classes("col")
                        )
                        if i == 0 and get_state_val("focus_new_count"):
                            set_state_val("focus_new_count", False)
                            ct.run_method("focus")

                        def step_count(idx, delta, c=ct):
                            new_sels = get_selections()
                            if 0 <= idx < len(new_sels):
                                new_count = max(
                                    1, min(11, (new_sels[idx].get("count") or 1) + delta)
                                )
                                new_sels[idx] = {**new_sels[idx], "count": new_count}
                                set_selections(new_sels)
                                c.value = new_count
                                c.update()

                        ui.button(icon="remove").props("flat dense round size=sm").on(
                            "click", lambda idx=i, fn=step_count: fn(idx, -1)
                        )
                        ui.button(icon="add").props("flat dense round size=sm").on(
                            "click", lambda idx=i, fn=step_count: fn(idx, 1)
                        )

                    labeled_by = sel.get("labeled_by")
                    labeled_at = sel.get("labeled_at")
                    source = sel.get("source")
                    with ui.row().classes("items-center gap-xs q-mt-xs w-full"):
                        if labeled_by:
                            _render_labeled_by_meta(labeled_by, labeled_at)
                        elif source == "model":
                            ui.icon("smart_toy", size="xs").classes("")
                            ui.label(t("model_suggestion")).classes("text-caption")
                            prob = sel.get("probability")
                            if prob is not None:
                                ui.label(f"{prob:.0%}").style(
                                    f"color: {get_probability_color(prob)}; font-weight: bold"
                                ).classes("text-caption")
                        if sp_value is None and sel.get("species"):
                            ui.label(t("predicted_species", species=sel["species"])).classes(
                                "text-caption text-warning"
                            ).tooltip(t("predicted_not_in_list_tooltip"))
                        if bp_value is None and sel.get("behavior"):
                            ui.label(t("predicted_behavior", behavior=sel["behavior"])).classes(
                                "text-caption text-warning"
                            ).tooltip(t("predicted_not_in_list_tooltip"))
                        ui.element("div").classes("col")
                        ui.button(
                            icon="delete", on_click=lambda idx=i: delete_selection(idx)
                        ).props("flat color=negative dense")

                    def update_sel(idx, sp_el, bp_el, ct_el):
                        new_sels = get_selections()
                        if 0 <= idx < len(new_sels):
                            new_sels[idx] = {
                                "species": sp_el.value,
                                "behavior": bp_el.value,
                                "count": ct_el.value or 1,
                                "start_sec": new_sels[idx].get("start_sec"),
                                "end_sec": new_sels[idx].get("end_sec"),
                            }
                            set_selections(new_sels)

                    def on_group_change(_, g=gp, s=sp):
                        new_filtered = _filter_species_by_group(
                            species_map, species_groups, g.value or ""
                        )
                        s.options = new_filtered
                        if s.value not in new_filtered:
                            s.value = None
                        s.update()

                    def on_species_change(_, s=sp, b=bp, c=ct, g=gp, idx=i):
                        new_behaviors = dp.get_behavior_display_map(
                            lang=get_language(),
                            species_name=s.value,
                            project_id=get_active_project_id(),
                        )
                        b.options = new_behaviors
                        b.value = _resolve_behavior(new_behaviors, b.value)
                        b.update()
                        if s.value:
                            inferred_group = species_groups.get(s.value, "")
                            if inferred_group != (g.value or ""):
                                g.value = inferred_group
                                g.update()
                        update_sel(idx, s, b, c)

                    gp.on_value_change(on_group_change)
                    sp.on_value_change(on_species_change)
                    bp.on_value_change(lambda _, s=sp, b=bp, c=ct, idx=i: update_sel(idx, s, b, c))
                    ct.on_value_change(lambda _, s=sp, b=bp, c=ct, idx=i: update_sel(idx, s, b, c))

    # Source of truth: the video currently rendered.
    selected_video_id = video.get("video_id")

    def _advance_to_next(current_video_id):
        """Move to the next position in the current queue without rebuilding it."""
        queue = get_queue()
        prev_idx = get_current_idx()
        if not queue:
            set_current_idx(0)
        elif current_video_id in queue:
            set_current_idx(min(queue.index(current_video_id) + 1, len(queue) - 1))
        else:
            set_current_idx(max(0, min(prev_idx + 1, len(queue) - 1)))

    def _clear_review_state():
        set_state_val("review_state_video_id", None)
        set_state_val("review_is_blank", None)
        set_selections([])
        set_state_val("review_active_id", None)
        set_state_val("pending_blank_confirm", False)

    async def update_annotation() -> bool:
        if get_state_val("submit_in_progress"):
            return False
        set_state_val("submit_in_progress", True)
        try:
            sels = get_selections()
            is_b = get_state_val("review_is_blank", False)
            if not is_b and not sels:
                await run.io_bound(
                    dp.update_manual_review, selected_video_id, [], is_blank=None, labeled_by=None
                )
                return True
            annotator = get_annotator_name()
            labeled_sels = [{**s, "labeled_by": annotator} for s in sels]
            await run.io_bound(
                dp.update_manual_review,
                selected_video_id,
                labeled_sels,
                is_blank=is_b,
                active_project_id=get_active_project_id(),
            )
            ui.notify(t("review_saved"), type="positive")
            return True
        except SpeciesError as exc:
            ui.notify(t(exc.user_message_key, name=exc.name), type="negative")
            return False
        finally:
            set_state_val("submit_in_progress", False)

    async def submit_and_next():
        if not await update_annotation():
            return
        _advance_to_next(selected_video_id)
        _clear_review_state()
        page.render_video_section.refresh()
        page.render_filter_drawer.refresh()

    async def submit():
        if not await update_annotation():
            return
        set_state_val("review_state_video_id", None)
        page.render_video_section.refresh()
        page.render_filter_drawer.refresh()

    async def mark_review_later():
        if get_state_val("submit_in_progress"):
            return
        set_state_val("submit_in_progress", True)
        try:
            await run.io_bound(dp.set_review_later, selected_video_id)
            ui.notify(t("marked_review_later"), type="info")
            _advance_to_next(selected_video_id)
            _clear_review_state()
            page.render_video_section.refresh()
        finally:
            set_state_val("submit_in_progress", False)

    async def mark_blank_stay():
        await mark_blank(go_next=False)

    async def mark_blank_next():
        await mark_blank(go_next=True)

    async def mark_blank(go_next=True):
        if get_state_val("submit_in_progress"):
            return
        set_state_val("submit_in_progress", True)
        try:
            await run.io_bound(
                dp.update_manual_review,
                selected_video_id,
                [],
                is_blank=True,
                labeled_by=get_annotator_name(),
            )
            ui.notify(t("marked_blank"), type="positive")
            if go_next:
                _advance_to_next(selected_video_id)
            _clear_review_state()
            page.render_video_section.refresh()
            page.render_filter_drawer.refresh()
        finally:
            set_state_val("submit_in_progress", False)

    with ui.element("div").style(
        "position: sticky; bottom: 0; z-index: 10; background: inherit; "
        "padding-top: 8px; border-top: 1px solid rgba(128,128,128,0.15);"
    ):
        def clear_annotations():
            set_selections([])
            set_state_val("review_is_blank", False)
            page.render_annotation_section.refresh()

        with ui.row().classes("w-full justify-end q-mb-xs"):
            with ui.button(on_click=clear_annotations).props(
                "flat dense color=grey size=sm"
            ) as clear_btn:
                with ui.row().classes("items-center gap-xs q-px-xs"):
                    ui.icon("clear_all", size="xs")
                    ui.label(t("clear_annotations")).classes("text-caption")
                    _shortcut_badge("C")
            clear_btn._props["data-shortcut"] = "clear-annotations"

        with ui.row().classes("w-full gap-sm q-mt-sm tour-target-action-buttons"):
            with (
                ui.button(on_click=submit_and_next, color="warning")
                .classes("col")
                .style("min-height: 60px; font-weight:700; min-width: 160px;") as submit_next_btn
            ):
                with ui.row().classes("items-center justify-between w-full q-px-xs q-py-sm"):
                    ui.label(t("submit_next")).classes("text-center col")
                    _shortcut_badge("↵ Enter")
            submit_next_btn._props["data-shortcut"] = "submit-next"
            submit_next_btn.tooltip(t("tooltip_submit_next"))

            with (
                ui.button(on_click=mark_blank_next, color="primary")
                .props("outline")
                .classes("col")
                .style("min-height: 60px; min-width: 160px;") as blank_next_btn
            ):
                with ui.row().classes("items-center justify-between w-full q-px-xs q-py-sm"):
                    ui.label(t("mark_blank")).classes("text-center col")
                    _shortcut_badge("B")
            blank_next_btn._props["data-shortcut"] = "mark-blank"
            blank_next_btn.tooltip(t("tooltip_mark_blank"))

            with (
                ui.row()
                .classes("col items-center gap-xs tour-target-review-later")
                .style("min-width: 160px")
            ):
                with (
                    ui.button(on_click=mark_review_later)
                    .props("outline color=grey")
                    .classes("col")
                    .style("min-height: 60px;") as review_later_btn
                ):
                    with ui.row().classes("items-center justify-between w-full q-px-xs q-py-sm"):
                        ui.label(t("mark_review_later")).classes("text-center col")
                        _shortcut_badge("M")
                review_later_btn._props["data-shortcut"] = "mark-unknown"
                review_later_btn.tooltip(t("tooltip_review_later_btn"))
