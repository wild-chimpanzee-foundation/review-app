import asyncio
from pathlib import Path

from nicegui import run, ui

from review_app.app.config import get_config_path
from review_app.app.state import (
    get_annotator_name,
    get_blank_threshold,
    get_current_idx,
    get_data_provider,
    get_filters,
    get_playback_speed,
    get_queue,
    get_selections,
    get_species_threshold,
    get_state_val,
    is_auto_transcode,
    is_autoplay,
    is_muted,
    set_auto_transcode,
    set_autoplay,
    set_current_idx,
    set_data_provider,
    set_muted,
    set_playback_speed,
    set_queue,
    set_selections,
    set_state_val,
    update_filters,
)
from review_app.app.translations import get_language, t
from review_app.backend.local_data_provider import LocalDataProvider
from review_app.backend.utils import df_to_records


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
                    }
                ]

    set_state_val("review_is_blank", is_blank)
    set_selections(selections)
    set_state_val("review_state_video_id", video.get("video_id"))


@ui.refreshable
def render_annotation_section(
    video, species_map, dp, default_species, default_behavior="does_not_react"
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
        with ui.card().classes("full-width q-pa-md q-mb-sm"):
            with ui.row().classes("w-full gap-sm items-center"):
                with ui.element("div").classes("col"):
                    with ui.row().classes("items-center gap-sm"):
                        ui.badge(t("blank"), color="warning").classes(
                            "text-body2 px-4 py-2 rounded-full"
                        )
                        if blank_prob is not None:
                            ui.label(
                                t("blank_prob_label", prob=f"{blank_prob:.0%}", sp=f"{max_sp:.0%}")
                            ).classes("text-caption text-grey-6")
                ui.element("div").classes("col")
                ui.button(icon="edit", on_click=set_not_blank).props("flat")
    else:
        for i, sel in enumerate(selections):
            with ui.card().classes("full-width q-pa-md q-mb-sm"):
                with ui.row().classes("w-full gap-sm items-center"):
                    behaviors = dp.get_behaviors_for_species(sel["species"])
                    sp_value = sel["species"] if sel["species"] in species_map else None
                    bp_value = (
                        sel["behavior"]
                        if sel["behavior"] in behaviors
                        else (behaviors[0] if behaviors else "does_not_react")
                    )

                    sp = ui.select(
                        label=t("species_label"),
                        options=species_map,
                        value=sp_value,
                        with_input=True,
                    ).props("outlined dense class=col")

                    # # Custom slot to render scientific name as caption
                    # sp.add_slot("option", """
                    #     <q-item v-bind="scope.itemProps">
                    #       <q-item-section>
                    #         <q-item-label>{{ scope.opt.label }}</q-item-label>
                    #         <q-item-label caption class="text-grey-6">{{ scope.opt.value }}</q-item-label>
                    #       </q-item-section>
                    #     </q-item>
                    # """)
                    bp = ui.select(
                        label=t("behavior_label"),
                        options=behaviors,
                        value=bp_value,
                        with_input=True,
                    ).props("outlined dense class=col")

                    ui.button(icon="delete", on_click=lambda idx=i: delete_selection(idx)).props(
                        "flat color=negative"
                    )

                with ui.row().classes("w-full gap-sm items-center q-mt-sm") as time_row:
                    start_in = ui.number(
                        label=t("start_sec"),
                        value=sel.get("start_sec", 0.0),
                        step=0.1,
                        format="%.1f",
                    ).props("outlined dense class=col")
                    end_in = ui.number(
                        label=t("end_sec"),
                        value=sel.get("end_sec"),
                        step=0.1,
                        format="%.1f",
                    ).props("outlined dense class=col")

                time_row.visible = bp_value != "does_not_react"

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
                    tr.visible = bp_el.value != "does_not_react"

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
                "size=md color=teal flat"
            )

    # Source of truth: the video currently rendered. Do NOT recompute from queue —
    # current_idx can change between render_video_section and render_annotation_section refreshes.
    selected_video_id = video.get("video_id")

    async def _advance_to_next(current_video_id):
        """Refetch queue after an annotation and position idx to the next unprocessed video.

        If current_video_id is still in the queue (filter didn't remove it), advance past it.
        If it was filtered out, keep the same index — the queue shifted left so that index
        now points to what was previously the next video.
        """
        filters = get_filters()
        new_queue = await run.io_bound(
            dp.get_video_queue,
            {
                **filters,
                "blank_threshold": get_blank_threshold(),
                "species_threshold": get_species_threshold(),
            },
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
        # Reentry guard: browser/keyboard shortcuts can fire this handler multiple times
        # in parallel, which causes slot-deleted crashes in ui.notify and double-advances.
        if get_state_val("submit_in_progress"):
            return
        set_state_val("submit_in_progress", True)
        try:
            sels = get_selections()
            is_b = get_state_val("review_is_blank", False)
            if not is_b and not sels:
                ui.notify(t("no_species_warning"), type="warning")
                return
            await run.io_bound(
                dp.update_manual_review,
                selected_video_id,
                sels,
                annotator=get_annotator_name(),
                is_blank=is_b,
            )
            ui.notify(t("review_saved"), type="positive")
            await _advance_to_next(selected_video_id)
            _clear_review_state()
            render_video_section.refresh()
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
            await run.io_bound(
                dp.update_manual_review,
                selected_video_id,
                sels,
                annotator=get_annotator_name(),
                is_blank=is_b,
            )
            ui.notify(t("review_saved"), type="positive")
            # Stay on the same video but reload its data (updated labeled_by, selections, etc.)
            set_state_val("review_state_video_id", None)
            render_video_section.refresh()
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
                annotator=get_annotator_name(),
                is_blank=True,
            )
            ui.notify(t("marked_blank"), type="positive")
            if go_next:
                await _advance_to_next(selected_video_id)
            _clear_review_state()
            render_video_section.refresh()
        finally:
            set_state_val("submit_in_progress", False)

    with ui.row().classes("w-full gap-sm q-mt-sm q-mb-md"):
        submit_next_btn = ui.button(t("submit_next"), on_click=submit_and_next, color="warning")
        submit_next_btn._props["data-shortcut"] = "submit-next"
        ui.button(t("submit"), on_click=submit)
        blank_btn = ui.button(t("mark_blank"), on_click=mark_blank_next, color="warning")
        blank_btn._props["data-shortcut"] = "mark-blank"
        ui.button(t("blank"), on_click=mark_blank_stay)

    ui.label(t("shortcuts_help")).classes("text-body2 font-weight-medium")

    with ui.column().classes("w-full q-mt-md gap-1"):
        labeled_by = video.get("labeled_by")
        if labeled_by:
            ui.label(t("labeled_by", name=labeled_by)).classes("text-body3 text-grey-6 q-mb-xs")

        if video.get("is_blank"):
            with ui.row().classes("items-center gap-sm"):
                ui.icon("check_circle", color="warning", size="xs")
                ui.label(t("blank")).classes("text-body2 font-weight-medium")
        else:
            selections = video.get("manual_selections") or []
            if not selections:
                ui.label(t("no_manual_annotations")).classes("text-body2 text-grey-5 italic")
            else:
                for sel in selections:
                    with ui.row().classes("items-center gap-sm"):
                        ui.icon("label", color="primary", size="xs")
                        time_str = (
                            f"{sel['start_sec']:.1f}s"
                            if sel.get("end_sec") is None
                            else f"{sel['start_sec']:.1f}s - {sel['end_sec']:.1f}s"
                        )
                        ui.label(f"{sel['species']} ({sel['behavior']}) @ {time_str}").classes(
                            "text-body2"
                        )


@ui.refreshable
async def render_video_section(dp, species_map):
    queue = get_queue()
    if not queue:
        ui.label(t("no_videos_match")).classes("text-h6 text-grey-5")
        return

    current_idx = get_current_idx()
    current_idx = max(0, min(current_idx, len(queue) - 1))
    selected_video_id = queue[current_idx]

    # Parallelize data fetching to improve performance
    video_task = run.io_bound(
        dp.get_video_detail,
        selected_video_id,
        get_blank_threshold(),
        get_species_threshold(),
    )
    model_ann_task = run.io_bound(dp.get_model_annotations, selected_video_id)
    video, model_ann = await asyncio.gather(video_task, model_ann_task)

    default_behavior = "does_not_react"
    if model_ann is not None and not model_ann.empty:
        behavior_rows = model_ann[
            (model_ann["annotation_type"] == "behavior")
            & (model_ann["value_text"].notna())
            & (model_ann["value_text"].str.lower() != "dummy")
        ]["value_text"]
        if not behavior_rows.empty:
            default_behavior = behavior_rows.mode().iloc[0]

    if not video:
        # Queue can become stale after DB reset/re-sync or ID format changes.
        filters = get_filters()
        fresh_queue = await run.io_bound(
            dp.get_video_queue,
            {
                **filters,
                "blank_threshold": get_blank_threshold(),
                "species_threshold": get_species_threshold(),
            },
        )
        if fresh_queue and fresh_queue != queue:
            set_queue(fresh_queue)
            set_current_idx(max(0, min(current_idx, len(fresh_queue) - 1)))
            render_video_section.refresh()
            return
        ui.label(t("video_load_error")).classes("text-h6 text-negative")
        return

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(t("queue_label", current=current_idx + 1, total=len(queue))).classes("text-h6")

    def navigate(direction):
        new_idx = get_current_idx() + direction
        if 0 <= new_idx < len(queue):
            set_current_idx(new_idx)
            set_state_val("review_active_id", None)
            set_state_val("pending_blank_confirm", False)
            set_state_val("review_state_video_id", None)
            set_state_val("review_is_blank", None)
            set_selections([])
            render_video_section.refresh()

    with ui.row().classes("w-full items-center q-mb-md"):
        prev_btn = ui.button(icon="chevron_left", on_click=lambda: navigate(-1)).props("flat")
        prev_btn._props["data-shortcut"] = "prev"
        next_btn = ui.button(icon="chevron_right", on_click=lambda: navigate(1)).props("flat")
        next_btn._props["data-shortcut"] = "next"
        ui.space()
        ui.label(selected_video_id).classes("text-subtitle1 font-weight-medium")

    with ui.row().classes("w-full gap-md"):
        with ui.column().classes("col"):
            with ui.card().classes("full-width q-mb-md"):
                if video.get("needs_transcode"):
                    attempted = set(get_state_val("transcode_attempted_ids", []))
                    if selected_video_id not in attempted and is_auto_transcode():
                        attempted.add(selected_video_id)
                        set_state_val("transcode_attempted_ids", list(attempted))
                        ui.label(t("transcoding_video")).classes("text-body2 text-grey-7 q-mb-sm")
                        result = await run.io_bound(dp.transcode_video, selected_video_id)
                        if result.get("success"):
                            ui.notify(t("video_transcoded"), type="positive")
                            render_video_section.refresh()
                            return
                        ui.label(
                            t("transcode_failed", error=result.get("error", "unknown error"))
                        ).classes("text-negative text-caption q-mb-sm")

                    async def retry_transcode():
                        result = await run.io_bound(dp.transcode_video, selected_video_id)
                        if result.get("success"):
                            ui.notify(t("video_transcoded"), type="positive")
                            render_video_section.refresh()
                        else:
                            ui.notify(
                                t("transcode_failed", error=result.get("error", "unknown error")),
                                type="negative",
                            )

                    ui.button(
                        t("transcode_for_playback"),
                        icon="movie",
                        color="primary",
                        on_click=retry_transcode,
                    )
                elif video.get("video_path"):
                    # Transcoded sidecars live in the system temp dir, served via /transcoded
                    transcoded_str = video.get("transcoded_path")
                    transcoded_path = Path(transcoded_str) if transcoded_str else None

                    if transcoded_path and transcoded_path.exists():
                        video_url = f"/transcoded/{transcoded_path.name}"
                    else:
                        serve_path = Path(video["video_path"])
                        if serve_path.exists():
                            try:
                                rel_path = serve_path.relative_to(dp.video_dir)
                                video_url = f"/media/{rel_path}"
                            except ValueError:
                                ui.label(t("video_outside_media")).classes("text-negative")
                                video_url = None
                        else:
                            ui.label(t("video_not_found", path=video["video_path"])).classes(
                                "text-negative"
                            )
                            video_url = None

                    if video_url:
                        autoplay = is_autoplay()
                        muted = is_muted()

                        # Use NiceGUI video component
                        v = ui.video(
                            video_url, autoplay=autoplay, muted=muted, controls=True
                        ).classes("w-full")

                        duration = video.get("duration_sec") or 0
                        vid_key = str(v.id)

                        def _fmt(s):
                            try:
                                m, sec = divmod(int(float(s)), 60)
                                return f"{m:02d}:{sec:02d}"
                            except Exception:
                                return "00:00"

                        ui.html(f'''
                            <div style="display:flex;align-items:center;gap:8px;padding:4px 8px 0;width:100%">
                                <input type="range" id="vp-range-{vid_key}"
                                       min="0" max="{duration}" step="0.1" value="0"
                                       style="flex:1;min-width:0;cursor:pointer;accent-color:var(--q-primary);height:4px">
                                <span id="vp-time-{vid_key}"
                                      style="font-size:12px;color:#888;white-space:nowrap;font-family:monospace">
                                    00:00 / {_fmt(duration)}
                                </span>
                            </div>
                        ''').classes("full-width")

                        ui.run_javascript(f"""
                            (function setup() {{
                                const comp = getElement({v.id});
                                if (!comp || !comp.$el) {{ setTimeout(setup, 50); return; }}
                                const el = comp.$el;
                                const videoEl = el.tagName === 'VIDEO' ? el : el.querySelector('video');
                                const range = document.getElementById('vp-range-{vid_key}');
                                const lbl = document.getElementById('vp-time-{vid_key}');
                                if (!videoEl || !range) return;

                                const total = '{_fmt(duration)}';
                                function fmt(s) {{
                                    return String(Math.floor(s/60)).padStart(2,'0') + ':' +
                                           String(Math.floor(s%60)).padStart(2,'0');
                                }}

                                videoEl.addEventListener('timeupdate', function() {{
                                    if (!range._seeking) {{
                                        range.value = videoEl.currentTime;
                                        if (lbl) lbl.textContent = fmt(videoEl.currentTime) + ' / ' + total;
                                    }}
                                }});

                                range.addEventListener('mousedown', function() {{ range._seeking = true; }});
                                range.addEventListener('touchstart', function() {{ range._seeking = true; }}, {{passive:true}});
                                range.addEventListener('input', function() {{
                                    if (lbl) lbl.textContent = fmt(parseFloat(range.value)) + ' / ' + total;
                                }});
                                range.addEventListener('mouseup', function() {{
                                    videoEl.currentTime = parseFloat(range.value);
                                    range._seeking = false;
                                }});
                                range.addEventListener('touchend', function() {{
                                    videoEl.currentTime = parseFloat(range.value);
                                    range._seeking = false;
                                }});
                            }})();
                        """)

                else:
                    ui.label(t("no_video_path")).classes("text-grey-5")

                if not video.get("is_video_valid", True):
                    ui.label(
                        t(
                            "video_validation_failed",
                            error=video.get("video_validation_details", "Unknown error"),
                        )
                    ).classes("text-negative text-caption q-mt-sm")

            with ui.card().classes("full-width"):
                ui.label(t("model_annotations")).classes(
                    "text-subtitle1 font-weight-medium q-mb-sm"
                )

                if model_ann is not None and not model_ann.empty:
                    columns = [
                        {"name": "model_name", "label": t("col_model"), "field": "model_name"},
                        {
                            "name": "annotation_type",
                            "label": t("col_type"),
                            "field": "annotation_type",
                        },
                        {"name": "value_text", "label": t("col_value"), "field": "value_text"},
                        {"name": "probability", "label": t("col_prob"), "field": "probability"},
                    ]
                    ui.table(columns=columns, rows=df_to_records(model_ann, 10))
                else:
                    ui.label(t("no_model_annotations")).classes("text-grey-5")

        with ui.column().classes("col"):
            with ui.card().classes("full-width q-mb-md"):
                ui.label(t("manual_review")).classes("text-subtitle1 font-weight-medium q-mb-sm")
                default_species = video.get("default_species")
                if not default_species:
                    fallback_species = video.get("classification_consensus", "unknown")
                    if not fallback_species or fallback_species == "UNKNOWN":
                        fallback_species = list(species_map.keys())[0]
                    default_species = fallback_species

                render_annotation_section(
                    video, species_map, dp, default_species, default_behavior
                )

    current_speed = get_playback_speed().replace("x", "")
    ui.run_javascript(f"""
        (function() {{
            const rate = {current_speed};
            const applyRate = () => {{
                document.querySelectorAll('video').forEach(v => {{
                    v.playbackRate = rate;
                    if (!v.dataset.speedBound) {{
                        v.addEventListener('loadedmetadata', () => {{
                            v.playbackRate = rate;
                        }});
                        v.dataset.speedBound = '1';
                    }}
                }});
            }};
            applyRate();
            requestAnimationFrame(applyRate);
            setTimeout(applyRate, 120);
        }})();
    """)


async def setup_review():
    dp = get_data_provider()
    if not dp:
        config_path = get_config_path()
        if config_path.exists():
            dp = LocalDataProvider(str(config_path))
            set_data_provider(dp)
        else:
            with ui.card().classes("q-pa-xl"):
                ui.label(t("error_dp_init")).classes("text-h6 text-negative")
                ui.button(t("setup_btn"), on_click=lambda: ui.navigate.to("/setup"))
            return

    species_map = await run.io_bound(dp.get_species_display_map, get_language())
    if not species_map:
        species_map = {"unknown": "unknown"}

    filters = get_filters()
    queue = get_queue()

    # Always refresh queue from DB to avoid stale IDs in session state.
    filter_options_task = run.io_bound(dp.get_queue_filter_options)
    queue_ids_task = run.io_bound(
        dp.get_video_queue,
        {
            **filters,
            "blank_threshold": get_blank_threshold(),
            "species_threshold": get_species_threshold(),
        },
    )
    filter_options, queue_ids = await asyncio.gather(filter_options_task, queue_ids_task)
    set_queue(queue_ids)
    if queue != queue_ids:
        set_current_idx(0)
        set_selections([])
        set_state_val("review_state_video_id", None)
        set_state_val("review_is_blank", None)
        set_state_val("review_active_id", None)
        set_state_val("pending_blank_confirm", False)

    # Move CSS to a single injection
    ui.add_head_html(
        """
        <style>
            .review-sidebar {
                background: #f5f5f5;
            }
            .body--dark .review-sidebar, .q-dark .review-sidebar {
                background: #1d1d1d !important;
            }
        </style>
    """,
        shared=True,
    )

    with ui.row().classes("w-full items-start"):
        with (
            ui.column()
            .classes("review-sidebar")
            .style(
                "width: 280px; min-width: 280px; min-height: calc(100vh - 50px); padding: 16px;"
            )
        ):
            # ── Filters ──────────────────────────────────────────────────────
            with ui.card().classes("full-width q-mb-md"):
                ui.label(t("filters_label")).classes("text-subtitle1 font-weight-medium q-mb-xs")

                search = ui.input(t("search"), placeholder=t("search_placeholder")).props(
                    "outlined dense class=full-width"
                )
                search.value = filters.get("search_query", "")

                camera_values = filter_options.get("camera_values", [])
                camera_select = ui.select(
                    label=t("camera_filter"),
                    options={
                        v: v if v != "All" else t("all_option") for v in ["All"] + camera_values
                    },
                    value=filters.get("selected_camera", "All"),
                    with_input=True,
                ).props("outlined dense class=full-width")

                species_values = filter_options.get("species_values", [])
                species_filter = ui.select(
                    label=t("species_manual_filter"),
                    options={
                        v: species_map.get(v, v) if v != "All" else t("all_option")
                        for v in ["All"] + species_values
                    },
                    value=filters.get("selected_species", "All"),
                    with_input=True,
                ).props("outlined dense class=full-width")

                possible_species_values = filter_options.get("possible_species_values", [])
                possible_species_filter = ui.select(
                    label=t("species_model_filter"),
                    options={
                        v: species_map.get(v, v) if v != "All" else t("all_option")
                        for v in ["All"] + possible_species_values
                    },
                    value=filters.get("selected_possible_species", "All"),
                    with_input=True,
                ).props("outlined dense class=full-width")

                behavior_values = filter_options.get("behavior_values", [])
                behavior_filter = ui.select(
                    label=t("behavior_filter"),
                    options={
                        "All": t("all_option"),
                        "Has Behavior": t("has_behavior"),
                        "No Behavior": t("no_behavior"),
                        **{v: v for v in behavior_values},
                    },
                    value=filters.get("selected_behavior", "All"),
                    with_input=True,
                ).props("outlined dense class=full-width")

                blank_filter = ui.select(
                    label=t("blank_filter"),
                    options={
                        "All": t("all_option"),
                        "Blank": t("blank"),
                        "Non-Blank": t("non_blank"),
                        "Unknown": t("unknown"),
                    },
                    value=filters.get("selected_blank_non_blank", "All"),
                ).props("outlined dense class=full-width")

                annotation_filter = ui.select(
                    label=t("annotation_filter"),
                    options={
                        "All": t("all_option"),
                        "Annotated": t("annotated"),
                        "Not Annotated": t("not_annotated"),
                    },
                    value=filters.get("selected_annotation_status", "All"),
                ).props("outlined dense class=full-width")

                needs_review_filter = ui.select(
                    label=t("needs_review_filter"),
                    options={
                        "All": t("all_option"),
                        "Needs Review": t("needs_review"),
                        "No Review": t("no_review_needed"),
                    },
                    value=filters.get("selected_needs_review", "All"),
                ).props("outlined dense class=full-width")

                web_safe_only_cb = ui.checkbox(
                    t("web_safe_only"), value=bool(filters.get("web_safe_only", False))
                )

                async def apply_filters():
                    new_filters = {
                        "search_query": search.value,
                        "selected_camera": camera_select.value,
                        "selected_sort": sort_select.value,
                        "selected_sort_direction": sort_dir[0],
                        "selected_species": species_filter.value,
                        "selected_possible_species": possible_species_filter.value,
                        "selected_behavior": behavior_filter.value,
                        "selected_blank_non_blank": blank_filter.value,
                        "selected_annotation_status": annotation_filter.value,
                        "selected_needs_review": needs_review_filter.value,
                        "web_safe_only": web_safe_only_cb.value,
                    }
                    update_filters(**new_filters)
                    new_queue = await run.io_bound(
                        dp.get_video_queue,
                        {
                            **new_filters,
                            "blank_threshold": get_blank_threshold(),
                            "species_threshold": get_species_threshold(),
                        },
                    )
                    set_queue(new_queue)
                    set_current_idx(0)
                    set_selections([])
                    set_state_val("review_state_video_id", None)
                    set_state_val("review_is_blank", None)
                    set_state_val("user_cleared_all", False)
                    set_state_val("review_active_id", None)
                    set_state_val("pending_blank_confirm", False)
                    render_video_section.refresh()

                ui.button(t("apply_filters"), on_click=apply_filters, color="primary").props(
                    "full-width"
                )

            # ── Sort ─────────────────────────────────────────────────────────
            with ui.card().classes("full-width q-mb-md"):
                ui.label(t("sort_label")).classes("text-subtitle2 text-grey-7 q-mb-xs")
                with ui.row().classes("w-full items-center gap-sm"):
                    sort_select = ui.select(
                        options={
                            "camera": t("sort_camera"),
                            "unreviewed_first": t("sort_unreviewed"),
                            "species_prob": t("sort_species_prob"),
                            "random": t("sort_random"),
                        },
                        value=filters.get("selected_sort", "camera"),
                    ).props("outlined dense class=col")

                    sort_dir = [filters.get("selected_sort_direction", "desc")]

                    dir_btn = ui.button(
                        icon="arrow_downward" if sort_dir[0] == "desc" else "arrow_upward"
                    ).props("outlined dense")

                async def apply_sort():
                    update_filters(
                        selected_sort=sort_select.value,
                        selected_sort_direction=sort_dir[0],
                    )
                    f = get_filters()
                    new_queue = await run.io_bound(
                        dp.get_video_queue,
                        {
                            **f,
                            "blank_threshold": get_blank_threshold(),
                            "species_threshold": get_species_threshold(),
                        },
                    )
                    set_queue(new_queue)
                    set_current_idx(0)
                    set_selections([])
                    set_state_val("review_state_video_id", None)
                    set_state_val("review_is_blank", None)
                    set_state_val("user_cleared_all", False)
                    set_state_val("review_active_id", None)
                    set_state_val("pending_blank_confirm", False)
                    render_video_section.refresh()

                async def toggle_dir():
                    sort_dir[0] = "asc" if sort_dir[0] == "desc" else "desc"
                    dir_btn.props(
                        f"outlined dense icon={'arrow_upward' if sort_dir[0] == 'asc' else 'arrow_downward'}"
                    )
                    await apply_sort()

                dir_btn.on_click(toggle_dir)
                sort_select.on_value_change(lambda _: apply_sort())

            # ── Playback ──────────────────────────────────────────────────────
            current_speed_str = get_playback_speed()
            current_speed = float(current_speed_str.replace("x", ""))

            with ui.card().classes("full-width q-mb-md"):
                ui.label(t("playback_settings")).classes("text-subtitle2 text-grey-7 q-mb-xs")

                with ui.row().classes("w-full items-center q-mb-xs"):
                    ui.label(t("playback_speed")).classes("text-caption text-grey-6")
                    ui.space()
                    ui.button(
                        icon="restart_alt", on_click=lambda: setattr(speed_slider, "value", 1.0)
                    ).props("flat round dense size=sm").classes("text-grey-7")

                def update_playback_speed(val):
                    speed = round(val, 2)
                    speed_str = f"{speed}x"
                    set_playback_speed(speed_str)
                    ui.run_javascript(f"""
                        document.querySelectorAll('video').forEach(v => {{
                            v.playbackRate = {speed};
                        }});
                    """)

                speed_slider = ui.slider(
                    min=0.25,
                    max=10,
                    step=0.25,
                    value=current_speed,
                    on_change=lambda e: update_playback_speed(e.value),
                ).props("label-always switch-label-side class=q-mx-sm q-mb-sm")

                ui.checkbox(
                    t("autoplay"),
                    value=is_autoplay(),
                    on_change=lambda e: (set_autoplay(e.value), render_video_section.refresh()),
                )
                ui.checkbox(
                    t("muted"),
                    value=is_muted(),
                    on_change=lambda e: (set_muted(e.value), render_video_section.refresh()),
                )
                ui.checkbox(
                    t("auto_transcode"),
                    value=is_auto_transcode(),
                    on_change=lambda e: set_auto_transcode(e.value),
                )

        with ui.column().classes("col q-pa-md"):
            await render_video_section(dp, species_map)

    ui.add_body_html(
        """
        <script>
            // Guard against multiple listener registrations if this script is injected more than once.
            if (!window.__reviewShortcutsBound) {
                window.__reviewShortcutsBound = true;
                document.addEventListener('keydown', function(e) {
                    const tag = e.target.tagName.toLowerCase();
                    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
                    if (e.ctrlKey || e.metaKey || e.altKey) return;
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="submit-next"]')?.click();
                    } else if (e.key === 'n' || e.key === 'N') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="next"]')?.click();
                    } else if (e.key === 'p' || e.key === 'P') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="prev"]')?.click();
                    } else if (e.key === 'b' || e.key === 'B') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="mark-blank"]')?.click();
                    }
                });
            }
        </script>
    """,
        shared=True,
    )
