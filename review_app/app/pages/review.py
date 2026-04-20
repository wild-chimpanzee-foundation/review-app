import asyncio
from pathlib import Path

from nicegui import run, ui

from review_app.app.setup_wizard import get_config_path
from review_app.app.state import (
    get_annotator_name,
    get_current_idx,
    get_data_provider,
    get_filters,
    get_playback_speed,
    get_queue,
    get_selections,
    get_state_val,
    set_annotator_name,
    set_current_idx,
    set_data_provider,
    set_playback_speed,
    set_queue,
    set_selections,
    set_state_val,
    update_filters,
)
from review_app.backend.local_data_provider import LocalDataProvider


def _make_serializable(val):
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def _df_to_records(df, limit=10):
    records = []
    for _, row in df.head(limit).iterrows():
        records.append({k: _make_serializable(v) for k, v in row.items()})
    return records


def _needs_browser_transcode(video: dict) -> bool:
    video_path = str(video.get("video_path") or "")
    suffix = Path(video_path).suffix.lower()
    browser_suffixes = {".mp4", ".webm", ".ogg"}
    return bool(video.get("is_web_safe") is False or suffix not in browser_suffixes)


def _default_species_from_annotations(model_ann, valid_species, fallback_species: str) -> str:
    if model_ann is None or model_ann.empty:
        return fallback_species
    if "annotation_type" not in model_ann.columns or "value_text" not in model_ann.columns:
        return fallback_species

    species_rows = model_ann[
        (model_ann["annotation_type"] == "species")
        & model_ann["value_text"].notna()
        & (model_ann["value_text"].astype(str).str.strip() != "")
    ].copy()
    if species_rows.empty:
        return fallback_species

    if "probability" not in species_rows.columns:
        return fallback_species

    species_rows["probability"] = species_rows["probability"].fillna(0.0).astype(float)
    probs = species_rows.groupby("value_text", as_index=False)["probability"].sum()
    probs = probs.sort_values("probability", ascending=False)
    if probs.empty:
        return fallback_species

    candidate = str(probs.iloc[0]["value_text"]).strip()
    return candidate if candidate in valid_species else fallback_species


@ui.refreshable
def render_annotation_section(video, valid_species, dp, default_species):
    selections = get_selections()
    user_cleared_all = get_state_val("user_cleared_all", False)

    if not selections and not user_cleared_all:
        existing = video.get("manual_selections") or []
        if existing:
            set_selections(list(existing))
            selections = existing
        else:
            species = default_species or video.get("classification_consensus", "unknown")
            behavior = "does_not_react"
            if species and species != "UNKNOWN":
                selections = [
                    {
                        "species": species,
                        "behavior": behavior,
                        "start_sec": 0.0,
                        "end_sec": video.get("duration_sec"),
                    }
                ]
                set_selections(selections)

    if not selections:
        selections = []
        set_selections(selections)
    else:
        set_state_val("user_cleared_all", False)

    async def delete_selection(idx):
        queue = get_queue()
        current_idx = get_current_idx()
        video_id = queue[max(0, min(current_idx, len(queue) - 1))]
        new_sels = get_selections()
        if 0 <= idx < len(new_sels):
            new_sels.pop(idx)
            set_selections(new_sels)
            await run.io_bound(
                dp.update_manual_review,
                video_id,
                new_sels if new_sels else [],
                annotator=get_annotator_name(),
            )
            if not new_sels:
                set_state_val("user_cleared_all", True)
        render_annotation_section.refresh()

    for i, sel in enumerate(selections):
        with ui.card().classes("full-width q-pa-md q-mb-sm"):
            with ui.row().classes("w-full gap-sm items-center"):
                behaviors = dp.get_behaviors_for_species(sel["species"])
                sp_value = sel["species"] if sel["species"] in valid_species else valid_species[0]
                bp_value = sel["behavior"] if sel["behavior"] in behaviors else behaviors[0]
                sp = ui.select(
                    label="Species",
                    options=valid_species,
                    value=sp_value,
                    with_input=True,
                ).props("outlined dense class=col")
                bp = ui.select(
                    label="Behavior", options=behaviors, value=bp_value, with_input=True
                ).props("outlined dense class=col")

                def update_sel(idx, sp_el, bp_el):
                    new_sels = get_selections()
                    if 0 <= idx < len(new_sels):
                        new_sels[idx] = {
                            "species": sp_el.value,
                            "behavior": bp_el.value,
                            "start_sec": 0.0,
                            "end_sec": video.get("duration_sec"),
                        }
                        set_selections(new_sels)

                sp.on_value_change(lambda _, s=sp, b=bp, idx=i: update_sel(idx, s, b))
                bp.on_value_change(lambda _, s=sp, b=bp, idx=i: update_sel(idx, s, b))

                ui.button(icon="delete", on_click=lambda idx=i: delete_selection(idx)).props(
                    "flat color=negative"
                )

    def add_species():
        last = selections[-1]["species"] if selections else valid_species[0]
        new_sels = get_selections()
        new_sels.append(
            {
                "species": last,
                "behavior": "does_not_react",
                "start_sec": 0.0,
                "end_sec": video.get("duration_sec"),
            }
        )
        set_selections(new_sels)
        render_annotation_section.refresh()

    ui.button("Add Species", on_click=add_species).props("size=sm")

    queue = get_queue()
    current_idx = get_current_idx()
    selected_video_id = queue[max(0, min(current_idx, len(queue) - 1))]

    async def submit_and_next():
        sels = get_selections()
        if sels:
            await run.io_bound(
                dp.update_manual_review,
                selected_video_id,
                sels,
                annotator=get_annotator_name(),
            )
            ui.notify("Review saved!", type="positive")
        set_current_idx(get_current_idx() + 1)
        set_state_val("review_active_id", None)
        set_state_val("pending_blank_confirm", False)
        set_selections([])
        render_video_section.refresh()

    async def submit():
        sels = get_selections()
        if sels:
            await run.io_bound(
                dp.update_manual_review,
                selected_video_id,
                sels,
                annotator=get_annotator_name(),
            )
            ui.notify("Review saved!", type="positive")

    async def mark_blank():
        await run.io_bound(
            dp.update_manual_review,
            selected_video_id,
            [
                {
                    "species": "blank",
                    "behavior": "unlabeled",
                    "start_sec": 0.0,
                    "end_sec": None,
                }
            ],
            annotator=get_annotator_name(),
        )
        ui.notify("Marked as blank!", type="positive")
        set_current_idx(get_current_idx() + 1)
        set_state_val("review_active_id", None)
        set_selections([])
        render_video_section.refresh()

    with ui.row().classes("w-full gap-sm q-mt-sm q-mb-md"):
        submit_next_btn = ui.button(
            "Submit & Next", on_click=submit_and_next, color="primary"
        )
        submit_next_btn._props["data-shortcut"] = "submit-next"
        ui.button("Submit", on_click=submit)
        blank_btn = ui.button("Mark Blank", on_click=mark_blank, color="warning")
        blank_btn._props["data-shortcut"] = "mark-blank"

    ui.label("Shortcuts: Enter=Submit&Next, N=Next, P=Previous, B=Blank").classes(
        "text-caption text-grey-5"
    )

    with ui.row().classes("w-full q-mt-md gap-md"):
        current_label = video.get("manual_review_prediction") or "None"
        consensus = video.get("classification_consensus", "UNKNOWN")
        ui.label(f"Current: {current_label}").classes("text-body2")
        ui.label(f"Consensus: {consensus}").classes("text-body2 text-grey-6")


@ui.refreshable
async def render_video_section(dp, valid_species):
    queue = get_queue()
    if not queue:
        ui.label("No videos match your filters").classes("text-h6 text-grey-5")
        return

    current_idx = get_current_idx()
    current_idx = max(0, min(current_idx, len(queue) - 1))
    selected_video_id = queue[current_idx]

    # Parallelize data fetching to improve performance
    video_task = run.io_bound(dp.get_video_detail, selected_video_id)
    model_ann_task = run.io_bound(dp.get_model_annotations, selected_video_id)
    video, model_ann = await asyncio.gather(video_task, model_ann_task)

    if not video:
        # Queue can become stale after DB reset/re-sync or ID format changes.
        filters = get_filters()
        fresh_queue = await run.io_bound(dp.get_video_queue, filters)
        if fresh_queue and fresh_queue != queue:
            set_queue(fresh_queue)
            set_current_idx(max(0, min(current_idx, len(fresh_queue) - 1)))
            render_video_section.refresh()
            return
        ui.label("Could not load video").classes("text-h6 text-negative")
        return

    with ui.row().classes("w-full items-center q-mb-md"):
        ui.label(f"Queue: {current_idx + 1} / {len(queue)}").classes("text-h6")

    def navigate(direction):
        new_idx = get_current_idx() + direction
        if 0 <= new_idx < len(queue):
            set_current_idx(new_idx)
            set_state_val("review_active_id", None)
            set_state_val("pending_blank_confirm", False)
            set_state_val("user_cleared_all", False)
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
                video_id = f"video-{selected_video_id}"
                if _needs_browser_transcode(video):
                    attempted = set(get_state_val("transcode_attempted_ids", []))
                    if selected_video_id not in attempted:
                        attempted.add(selected_video_id)
                        set_state_val("transcode_attempted_ids", list(attempted))
                        ui.label("Transcoding video for browser playback...").classes(
                            "text-body2 text-grey-7 q-mb-sm"
                        )
                        result = await run.io_bound(dp.transcode_video, selected_video_id)
                        if result.get("success"):
                            ui.notify("Video transcoded for web playback", type="positive")
                            render_video_section.refresh()
                            return
                        ui.label(
                            f"Auto-transcode failed: {result.get('error', 'unknown error')}"
                        ).classes("text-negative text-caption q-mb-sm")

                    async def retry_transcode():
                        result = await run.io_bound(dp.transcode_video, selected_video_id)
                        if result.get("success"):
                            ui.notify("Video transcoded for web playback", type="positive")
                            render_video_section.refresh()
                        else:
                            ui.notify(
                                f"Transcode failed: {result.get('error', 'unknown error')}",
                                type="negative",
                            )

                    ui.button(
                        "Transcode for Playback",
                        icon="movie",
                        color="primary",
                        on_click=retry_transcode,
                    )
                elif video.get("video_path"):
                    video_path = Path(video["video_path"])
                    if video_path.exists():
                        # Use /media/ prefix for reliable serving via NiceGUI media server
                        # We need the path relative to the video directory
                        try:
                            rel_path = video_path.relative_to(dp.video_dir)
                            video_url = f"/media/{rel_path}"
                        except ValueError:
                            # Fallback if somehow not under video_dir
                            video_url = str(video_path)
                            
                        ui.video(video_url, controls=True, autoplay=True, muted=True).props(
                            f'id="{video_id}"'
                        ).classes("full-width")
                    else:
                        ui.label(f"Video file not found: {video_path}").classes("text-negative")
                else:
                    ui.label("No video path available").classes("text-grey-5")

                if not video.get("is_video_valid", True):
                    ui.label(
                        f"Video validation failed: {video.get('video_validation_details', 'Unknown error')}"
                    ).classes("text-negative text-caption q-mt-sm")

            with ui.card().classes("full-width"):
                ui.label("Model Annotations").classes("text-subtitle1 font-weight-medium q-mb-sm")

                if model_ann is not None and not model_ann.empty:
                    columns = [
                        {"name": "model_name", "label": "Model", "field": "model_name"},
                        {
                            "name": "annotation_type",
                            "label": "Type",
                            "field": "annotation_type",
                        },
                        {"name": "value_text", "label": "Value", "field": "value_text"},
                        {"name": "probability", "label": "Prob", "field": "probability"},
                    ]
                    ui.table(columns=columns, rows=_df_to_records(model_ann, 10))
                else:
                    ui.label("No model annotations found").classes("text-grey-5")

        with ui.column().classes("col"):
            with ui.card().classes("full-width q-mb-md"):
                ui.label("Manual Review").classes("text-subtitle1 font-weight-medium q-mb-sm")
                fallback_species = video.get("classification_consensus", "unknown")
                if not fallback_species or fallback_species == "UNKNOWN":
                    fallback_species = valid_species[0]
                default_species = _default_species_from_annotations(
                    model_ann,
                    valid_species,
                    fallback_species,
                )
                render_annotation_section(video, valid_species, dp, default_species)

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
                ui.label("Data provider not initialized").classes("text-h6 text-negative")
                ui.button("Set up", on_click=lambda: ui.navigate.to("/setup"))
            return

    valid_species = await run.io_bound(dp.get_valid_species)
    if not valid_species:
        valid_species = ["unknown"]

    filters = get_filters()
    queue = get_queue()

    # Always refresh queue from DB to avoid stale IDs in session state.
    filter_options_task = run.io_bound(dp.get_queue_filter_options)
    queue_ids_task = run.io_bound(dp.get_video_queue, filters)
    filter_options, queue_ids = await asyncio.gather(filter_options_task, queue_ids_task)
    set_queue(queue_ids)
    if queue != queue_ids:
        set_current_idx(0)
        set_selections([])

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
        with ui.column().classes("review-sidebar").style(
            "width: 280px; min-width: 280px; min-height: calc(100vh - 50px); padding: 16px;"
        ):
            with ui.card().classes("full-width q-mb-md"):
                ui.label("Annotator").classes("text-subtitle2 text-grey-7")
                annotator_input = ui.input("Name", value=get_annotator_name()).props(
                    "outlined dense class=full-width"
                )

                annotator_input.on_value_change(lambda: set_annotator_name(annotator_input.value))

            current_speed_str = get_playback_speed()
            current_speed = float(current_speed_str.replace("x", ""))

            with ui.card().classes("full-width q-mb-md"):
                ui.label("Playback Speed").classes("text-subtitle2 text-grey-7")

                speed_label = ui.label(f"{current_speed}x").classes("text-body2 q-my-sm")

                def update_playback_speed(val):
                    speed = round(val, 2)
                    speed_str = f"{speed}x"
                    set_playback_speed(speed_str)
                    speed_label.text = f"{speed}x"
                    ui.run_javascript(f"""
                        document.querySelectorAll('video').forEach(v => {{
                            v.playbackRate = {speed};
                        }});
                    """)

                ui.slider(
                    min=0.25,
                    max=10,
                    step=0.25,
                    value=current_speed,
                    on_change=lambda e: update_playback_speed(e.value),
                ).props("label-always class=q-mx-sm")

            with ui.card().classes("full-width"):
                ui.label("Filters").classes("text-subtitle1 font-weight-medium")

                search = ui.input("Search", placeholder="Video ID or path...").props(
                    "outlined dense class=full-width"
                )
                search.value = filters.get("search_query", "")

                camera_values = filter_options.get("camera_values", [])
                camera_select = ui.select(
                    label="Camera",
                    options=["All"] + camera_values,
                    value=filters.get("selected_camera", "All"),
                    with_input=True,
                ).props("outlined dense class=full-width")

                species_values = filter_options.get("species_values", [])
                species_filter = ui.select(
                    label="Species",
                    options=["All"] + species_values,
                    value=filters.get("selected_species", "All"),
                    with_input=True,
                ).props("outlined dense class=full-width")

                include_unranked_cb = ui.checkbox(
                    "Include unranked", value=get_state_val("include_unranked", True)
                )
                web_safe_only_cb = ui.checkbox(
                    "Web-safe only", value=bool(filters.get("web_safe_only", False))
                )

                async def apply_filters():
                    new_filters = {
                        "search_query": search.value,
                        "selected_camera": camera_select.value,
                        "selected_species": species_filter.value,
                        "include_unranked": include_unranked_cb.value,
                        "web_safe_only": web_safe_only_cb.value,
                    }
                    set_state_val("include_unranked", include_unranked_cb.value)
                    update_filters(**new_filters)
                    new_queue = await run.io_bound(dp.get_video_queue, new_filters)
                    set_queue(new_queue)
                    set_current_idx(0)
                    set_selections([])
                    render_video_section.refresh()

                ui.button("Apply Filters", on_click=apply_filters, color="primary").props("full-width")

        with ui.column().classes("col q-pa-md"):
            await render_video_section(dp, valid_species)

    ui.add_body_html(
        """
        <script>
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
        </script>
    """,
        shared=True,
    )
