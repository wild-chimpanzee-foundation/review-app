from nicegui import run, ui

from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_current_idx,
    get_filters,
    get_selections,
    get_species_threshold,
    get_state_val,
    set_current_idx,
    set_queue,
    set_selections,
    set_state_val,
)
from review_app.app.translations import t
from review_app.app.utils import get_probability_color


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


def _init_annotation_state(video, default_species, default_behavior):
    is_blank = _normalize_is_blank(video.get("is_blank"))
    selections = list(video.get("manual_selections") or [])

    if is_blank is None and not selections:
        if video.get("predicted_blank"):
            is_blank = True
        else:
            is_blank = False
            species = default_species or video.get("classification_consensus", "unknown")
            if species and species != "UNKNOWN":
                selections = [
                    {
                        "species": species,
                        "behavior": default_behavior,
                        "start_sec": 0.0,
                        "end_sec": video.get("duration_sec"),
                        "source": "model",
                        "probability": video.get("max_species_confidence"),
                    }
                ]

    set_state_val("review_is_blank", is_blank)
    set_selections(selections)
    set_state_val("review_state_video_id", video.get("video_id"))


@ui.refreshable
def render_annotation_section(
    video,
    species_map,
    dp,
    default_species,
    default_behavior,
    render_video_section_callback,
    render_filter_drawer_callback,
):
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
        render_annotation_section.refresh()

    def set_not_blank():
        set_state_val("review_is_blank", False)
        existing = list(video.get("manual_selections") or [])
        if not existing:
            default = list(species_map.keys())[0] if species_map else "unknown"
            species = default_species or default
            existing = [
                {
                    "species": species,
                    "behavior": default_behavior,
                    "start_sec": 0.0,
                    "end_sec": video.get("duration_sec"),
                }
            ]
        set_selections(existing)
        render_annotation_section.refresh()

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
            with ui.row().classes("w-full gap-sm items-center"):
                with ui.element("div").classes("col"):
                    with ui.row().classes("items-center gap-sm"):
                        ui.badge(t("blank"), color="warning").classes(
                            "text-body2 px-4 py-2 rounded-full"
                        )
                        if human_labeled:
                            ui.icon("person", size="xs").classes("text-grey-5")
                            annotator = video.get("blank_labeled_by")
                            label = (
                                t("labeled_by", name=annotator)
                                if annotator
                                else t("blank_source_human")
                            )
                            ui.label(label).classes("text-caption text-grey-5")
                        else:
                            ui.icon("smart_toy", size="xs").classes("text-grey-5")
                            ui.label(t("blank_source_model")).classes("text-caption text-grey-5")
                            if blank_prob is not None:
                                with ui.row().classes("items-center gap-xs"):
                                    ui.label(t("blank")).classes("text-caption text-grey-6")
                                    ui.label(f"{blank_prob:.0%}").style(
                                        f"color: {get_probability_color(blank_prob)}; font-weight: bold"
                                    ).classes("text-caption")
                                    ui.label("·").classes("text-caption text-grey-6")
                                    ui.label(t("species_label")).classes(
                                        "text-caption text-grey-6"
                                    )
                                    ui.label(f"{max_sp:.0%}").style(
                                        f"color: {get_probability_color(max_sp)}; font-weight: bold"
                                    ).classes("text-caption")
                ui.element("div").classes("col")
                ui.button(icon="edit", on_click=set_not_blank).props("flat")
    else:
        for i, sel in enumerate(selections):
            with (
                ui.card()
                .classes("full-width q-pa-md q-mb-sm")
                .style("border: 2px solid var(--q-primary)")
            ):
                with ui.row().classes("w-full gap-sm items-center"):
                    behaviors = dp.get_behaviors_for_species(sel["species"])
                    sp_value = sel["species"] if sel["species"] in species_map else None
                    bp_value = sel["behavior"] if sel["behavior"] in behaviors else behaviors[0]

                    sp = ui.select(
                        label=t("species_label"),
                        options=species_map,
                        value=sp_value,
                        with_input=True,
                    ).props("outlined dense class=col")

                    ui.button(icon="delete", on_click=lambda idx=i: delete_selection(idx)).props(
                        "flat color=negative"
                    )

                    with ui.row().classes(
                        "w-full gap-sm items-center q-mt-sm no-wrap"
                    ) as time_row:
                        bp = (
                            ui.select(
                                label=t("behavior_label"),
                                options=behaviors,
                                value=bp_value,
                                with_input=True,
                            )
                            .props("outlined dense")
                            .style("flex: 2; min-width: 0;")
                        )

                        start_in = (
                            ui.number(
                                label=t("start_sec"),
                                value=sel.get("start_sec", 0.0),
                                step=0.1,
                                format="%.1f",
                            )
                            .props("outlined dense")
                            .style("flex: 1; min-width: 0;")
                        )

                        end_in = (
                            ui.number(
                                label=t("end_sec"),
                                value=sel.get("end_sec"),
                                step=0.1,
                                format="%.1f",
                            )
                            .props("outlined dense")
                            .style("flex: 1; min-width: 0;")
                        )

                labeled_by = sel.get("labeled_by")
                labeled_at = sel.get("labeled_at")
                source = sel.get("source")
                if labeled_by or source == "model":
                    with ui.row().classes("items-center gap-xs q-mt-xs"):
                        if labeled_by:
                            ui.icon("person", size="xs").classes("text-grey-5")
                            date_str = (
                                str(labeled_at)[:16].replace("T", " ") if labeled_at else None
                            )
                            meta = t("labeled_by", name=labeled_by)
                            if date_str:
                                meta += f" · {t('labeled_at', date=date_str)}"
                            ui.label(meta).classes("text-caption text-grey-5")
                        elif source == "model":
                            ui.icon("smart_toy", size="xs").classes("text-grey-5")
                            ui.label(t("model_suggestion")).classes("text-caption text-grey-5")
                            prob = sel.get("probability")
                            if prob is not None:
                                ui.label(f"{prob:.0%}").style(
                                    f"color: {get_probability_color(prob)}; font-weight: bold"
                                ).classes("text-caption")

                def update_sel(idx, sp_el, bp_el, start_el, end_el, tr):
                    new_sels = get_selections()
                    if 0 <= idx < len(new_sels):
                        new_sels[idx] = {
                            "species": sp_el.value,
                            "behavior": bp_el.value,
                            "start_sec": start_el.value if start_el.value is not None else 0.0,
                            "end_sec": end_el.value,
                        }
                        set_selections(new_sels)

                sp.on_value_change(
                    lambda _, s=sp, b=bp, st=start_in, en=end_in, tr=time_row, idx=i: update_sel(
                        idx, s, b, st, en, tr
                    )
                )
                bp.on_value_change(
                    lambda _, s=sp, b=bp, st=start_in, en=end_in, tr=time_row, idx=i: update_sel(
                        idx, s, b, st, en, tr
                    )
                )
                start_in.on_value_change(
                    lambda _, s=sp, b=bp, st=start_in, en=end_in, tr=time_row, idx=i: update_sel(
                        idx, s, b, st, en, tr
                    )
                )
                end_in.on_value_change(
                    lambda _, s=sp, b=bp, st=start_in, en=end_in, tr=time_row, idx=i: update_sel(
                        idx, s, b, st, en, tr
                    )
                )

        def add_species():
            # Default to the first available scientific name if nothing selected
            default = list(species_map.keys())[0] if species_map else "unknown"
            last = selections[-1]["species"] if selections else default
            new_sels = get_selections()
            new_sels.append(
                {
                    "species": last,
                    "behavior": default_behavior,
                    "start_sec": 0.0,
                    "end_sec": video.get("duration_sec"),
                }
            )
            set_selections(new_sels)
            set_state_val("review_is_blank", False)
            render_annotation_section.refresh()

        with ui.row().classes("w-full justify-center q-mt-xs"):
            ui.button(t("add_species"), icon="add", on_click=add_species).props(
                "size=md color=primary outline"
            ).style("border-style: dashed")

    # Source of truth: the video currently rendered.
    selected_video_id = video.get("video_id")

    async def _advance_to_next(current_video_id):
        """Refetch queue after an annotation and position idx to the next unprocessed video."""
        filters = get_filters()
        new_queue = await run.io_bound(
            dp.get_video_queue,
            {
                **filters,
                "blank_threshold": get_blank_threshold(),
                "species_threshold": get_species_threshold(),
            },
            get_active_project_id(),
        )
        prev_idx = get_current_idx()
        set_queue(new_queue)
        if not new_queue:
            set_current_idx(0)
        elif current_video_id in new_queue:
            set_current_idx(min(new_queue.index(current_video_id) + 1, len(new_queue) - 1))
        else:
            set_current_idx(max(0, min(prev_idx, len(new_queue) - 1)))

    def _clear_review_state():
        set_state_val("review_state_video_id", None)
        set_state_val("review_is_blank", None)
        set_selections([])
        set_state_val("review_active_id", None)
        set_state_val("pending_blank_confirm", False)

    async def submit_and_next():
        if get_state_val("submit_in_progress"):
            return
        set_state_val("submit_in_progress", True)
        try:
            sels = get_selections()
            is_b = get_state_val("review_is_blank", False)
            if not is_b and not sels:
                ui.notify(t("no_species_warning"), type="warning")
                return
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
            await _advance_to_next(selected_video_id)
            _clear_review_state()
            render_video_section_callback.refresh()
            render_filter_drawer_callback.refresh()
        finally:
            set_state_val("submit_in_progress", False)

    async def submit():
        if get_state_val("submit_in_progress"):
            return
        set_state_val("submit_in_progress", True)
        try:
            sels = get_selections()
            is_b = get_state_val("review_is_blank", False)
            if not is_b and not sels:
                ui.notify(t("no_species_warning"), type="warning")
                return
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
            # Stay on the same video but reload its data
            set_state_val("review_state_video_id", None)
            render_video_section_callback.refresh()
            render_filter_drawer_callback.refresh()
        finally:
            set_state_val("submit_in_progress", False)

    async def mark_review_later():
        if get_state_val("submit_in_progress"):
            return
        set_state_val("submit_in_progress", True)
        try:
            await run.io_bound(dp.set_review_later, selected_video_id)
            ui.notify(t("marked_review_later"), type="info")
            await _advance_to_next(selected_video_id)
            _clear_review_state()
            render_video_section_callback.refresh()
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
                await _advance_to_next(selected_video_id)
            _clear_review_state()
            render_video_section_callback.refresh()
            render_filter_drawer_callback.refresh()
        finally:
            set_state_val("submit_in_progress", False)

    # ── Action buttons ────────────────────────────────────────────────────────
    # Primary row: the two most-used actions, full-width, visually dominant
    with ui.row().classes("w-full gap-sm q-mt-sm"):
        with (
            ui.button(on_click=submit_and_next, color="warning")
            .classes("col")
            .style("height: 60px; font-weight:700") as submit_next_btn
        ):
            with ui.row().classes("items-center justify-between w-full no-wrap q-px-xs"):
                ui.label(t("submit_next"))
                _shortcut_badge("↵ Enter")
        submit_next_btn._props["data-shortcut"] = "submit-next"

        with (
            ui.button(on_click=mark_blank_next, color="primary")
            .props("outline")
            .classes("col")
            .style("height: 60px;") as blank_next_btn
        ):
            with ui.row().classes("items-center justify-between w-full no-wrap q-px-xs"):
                ui.label(t("mark_blank"))
                _shortcut_badge("B")
        blank_next_btn._props["data-shortcut"] = "mark-blank"

    # Secondary row: less-common actions, subtle styling
    with ui.row().classes("w-full gap-sm q-mb-md q-mt-xs"):
        with (
            ui.button(on_click=submit, color="primary")
            .props("outline")
            .classes("col")
            .style("height: 60px;")
        ):
            with ui.row().classes("items-center justify-center w-full no-wrap"):
                ui.label(t("submit"))

        with (
            ui.button(on_click=mark_blank_stay, color="primary")
            .props("outline")
            .classes("col")
            .style("height: 60px;")
        ):
            with ui.row().classes("items-center justify-center w-full no-wrap"):
                ui.label(t("blank"))

        with (
            ui.button(on_click=mark_review_later)
            .props("outline color=grey")
            .classes("col")
            .style("height: 60px;") as review_later_btn
        ):
            with ui.row().classes("items-center justify-between w-full no-wrap q-px-xs"):
                ui.label(t("mark_review_later"))
                _shortcut_badge("M")
        review_later_btn._props["data-shortcut"] = "mark-unknown"
