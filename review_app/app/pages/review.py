import mimetypes
from pathlib import Path

from nicegui import ui

from review_app.app.state import (
    get_current_idx,
    get_data_provider,
    get_filters,
    get_queue,
    get_selections,
    set_current_idx,
    set_queue,
    set_selections,
    state,
    update_filters,
)


def setup_review():
    dp = get_data_provider()
    if not dp:
        ui.label("Error: Data provider not initialized")
        return

    valid_species = dp.get_valid_species()
    if not valid_species:
        valid_species = ["unknown"]

    filter_options = dp.get_queue_filter_options()

    filters = get_filters()
    queue = get_queue()
    current_idx = get_current_idx()

    filters["include_unranked"] = state.get("include_unranked", True)
    state["annotator_name"] = state.get("annotator_name", "default")

    if not queue:
        queue_ids = dp.get_video_queue(filters)
        set_queue(queue_ids)
        queue = queue_ids

    ui.add_css("""
        <style>
            .sidebar { background: #f5f5f5; padding: 16px; height: 100%; }
            .video-settings { background: #e8f4e8; padding: 12px; border-radius: 8px; }
            .confirm-dialog { background: #fff3cd; padding: 16px; border-radius: 8px; border: 2px solid #ffc107; }
        </style>
    """)

    with ui.column().classes("w-full h-screen").style("padding: 0; margin: 0;"):
        with ui.navigation_bar().classes("bg-primary text-white"):
            ui.label("Video Annotation").classes("text-xl font-bold")
            ui.space()
            ui.button("Overview", on_click=lambda: ui.navigate.to("/overview"))
            ui.button("Review", on_click=lambda: ui.navigate.to("/review"))

        with ui.row().classes("w-full").style("flex: 1; overflow: hidden;"):
            with ui.column().classes("sidebar w-64"):
                with ui.expander("Filter & Search", icon="filter_list").classes("w-full"):
                    search_input = ui.input(
                        "Search by ID or Path",
                        placeholder="e.g. 20200322...",
                        value=filters.get("search_query", ""),
                    ).props("outlined dense")

                    camera_values = filter_options.get("camera_values", [])
                    camera_select = ui.select(
                        "Camera",
                        options=["All"] + camera_values,
                        value=filters.get("selected_camera", "All"),
                    ).props("outlined dense")

                    species_values = filter_options.get("species_values", [])
                    species_filter = ui.select(
                        "Species",
                        options=["All"] + species_values,
                        value=filters.get("selected_species", "All"),
                    ).props("outlined dense")

                    possible_species = filter_options.get("possible_species_values", [])
                    possible_species_filter = ui.select(
                        "Possible Species",
                        options=["All"] + possible_species,
                        value=filters.get("selected_possible_species", "All"),
                    ).props("outlined dense")

                    blank_filter = ui.select(
                        "Blank/Non-Blank",
                        options=["All", "Blank", "Non-Blank", "Unknown"],
                        value=filters.get("selected_blank_non_blank", "All"),
                    ).props("outlined dense")

                    behavior_values = filter_options.get("behavior_values", [])
                    behavior_filter = ui.select(
                        "Behavior",
                        options=["All", "Has Behavior", "No Behavior"] + behavior_values,
                        value=filters.get("selected_behavior", "All"),
                    ).props("outlined dense")

                    include_unranked = ui.checkbox(
                        "Include videos not listed in priority CSV",
                        value=state.get("include_unranked", True),
                    )

                    def apply_filters():
                        new_filters = {
                            "search_query": search_input.value,
                            "selected_camera": camera_select.value,
                            "selected_species": species_filter.value,
                            "selected_possible_species": possible_species_filter.value,
                            "selected_blank_non_blank": blank_filter.value,
                            "selected_behavior": behavior_filter.value,
                            "include_unranked": include_unranked.value,
                        }
                        state["include_unranked"] = include_unranked.value
                        update_filters(**new_filters)
                        new_queue = dp.get_video_queue(new_filters)
                        set_queue(new_queue)
                        set_current_idx(0)
                        set_selections([])
                        ui.navigate.to("/review")

                    ui.button("Apply Filters", on_click=apply_filters, icon="search").props("color=primary")

                with ui.card().classes("w-full"):
                    ui.label("Annotator").classes("text-sm font-semibold")
                    annotator_input = ui.input(
                        "Name",
                        value=state.get("annotator_name", "default"),
                    ).props("outlined dense")

                    def update_annotator():
                        state["annotator_name"] = annotator_input.value

                    annotator_input.on_value_change(lambda: update_annotator())

                with ui.card().classes("w-full video-settings"):
                    ui.label("Video Playback").classes("text-sm font-semibold")
                    autoplay = ui.checkbox("Autoplay", value=state.get("video_autoplay", True))
                    muted = ui.checkbox("Muted", value=state.get("video_muted", True))
                    speed = ui.select(
                        "Speed",
                        options=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
                        value=state.get("video_playback_speed", 1.0),
                    ).props("outlined dense")

                    def save_settings():
                        state["video_autoplay"] = autoplay.value
                        state["video_muted"] = muted.value
                        state["video_playback_speed"] = speed.value

                    autoplay.on_value_change(lambda: save_settings())
                    muted.on_value_change(lambda: save_settings())
                    speed.on_value_change(lambda: save_settings())

            with ui.column().classes("flex-grow p-4 overflow-auto"):
                if not queue:
                    ui.label("No videos in queue for the selected filters").classes("text-lg text-gray-600")
                    return

                current_idx = max(0, min(current_idx, len(queue) - 1))
                selected_video_id = queue[current_idx]
                video = dp.get_video_detail(selected_video_id)

                if not video:
                    ui.label(f"Could not load video: {selected_video_id}")
                    return

                ui.label(f"Queue: {current_idx + 1}/{len(queue)}").classes("text-lg")

                with ui.row().classes("w-full items-center"):
                    ui.button(
                        "Previous",
                        icon="chevron_left",
                        on_click=lambda: navigate(-1),
                    ).props("flat")
                    ui.button(
                        "Next",
                        icon="chevron_right",
                        on_click=lambda: navigate(1),
                    ).props("flat")
                    ui.space()
                    ui.label(f"Video: {selected_video_id}").classes("text-lg font-semibold")

                if not state.get("pending_blank_confirm", False):
                    with ui.row().classes("w-full gap-4"):
                        with ui.column().classes("w-1/2"):
                            if video.get("video_path"):
                                video_path = Path(video["video_path"])
                                if video_path.exists():
                                    mime_type, _ = mimetypes.guess_type(str(video_path))
                                    if not mime_type or not mime_type.startswith("video/"):
                                        suffix = video_path.suffix.lower()
                                        mime_fallback = {
                                            ".mp4": "video/mp4",
                                            ".mov": "video/quicktime",
                                            ".avi": "video/x-msvideo",
                                            ".mkv": "video/x-matroska",
                                            ".webm": "video/webm",
                                        }
                                        mime_type = mime_fallback.get(suffix, "video/mp4")

                                    ui.video(
                                        str(video_path),
                                        controls=True,
                                        autoplay=state.get("video_autoplay", True),
                                        muted=state.get("video_muted", True),
                                    ).classes("w-full")
                                else:
                                    ui.label(f"Video file not found: {video_path}").classes("text-red-600")
                            else:
                                ui.label("No video path available").classes("text-gray-500")

                            if not video.get("is_video_valid", True):
                                ui.label(f"Video validation failed: {video.get('video_validation_details', 'Unknown error')}").classes(
                                    "text-red-600 text-sm"
                                )

                            ui.label(f"Duration: {video.get('duration_sec', 0):.1f}s").classes("text-sm text-gray-600")
                            ui.label(f"Speed: {state.get('video_playback_speed', 1.0):.1f}x").classes("text-sm text-gray-600")

                            with ui.card().classes("w-full"):
                                ui.label("Model Annotations").classes("text-lg font-semibold")
                                model_annotations = dp.get_model_annotations(selected_video_id)
                                if not model_annotations.empty:
                                    columns = [
                                        {"name": "model_name", "label": "Model", "field": "model_name"},
                                        {"name": "annotation_type", "label": "Type", "field": "annotation_type"},
                                        {"name": "value_text", "label": "Value", "field": "value_text"},
                                        {"name": "probability", "label": "Prob", "field": "probability"},
                                    ]
                                    rows = model_annotations[["model_name", "annotation_type", "value_text", "probability"]].to_dict("records")
                                    ui.table(columns=columns, rows=rows)
                                else:
                                    ui.label("No model annotations found").classes("text-gray-500")

                        with ui.column().classes("w-1/2"):
                            selections = get_selections()
                            if not selections:
                                existing = video.get("manual_selections") or []
                                if existing:
                                    set_selections(existing)
                                    selections = existing
                                else:
                                    species = video.get("classification_consensus", "unknown")
                                    behavior = video.get("model_behavior_prediction", "unlabeled")
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

                            with ui.card().classes("w-full"):
                                ui.label("Annotations").classes("text-lg font-semibold")

                                if not selections:
                                    selections = []
                                    set_selections(selections)

                                for i, sel in enumerate(selections):
                                    with ui.card().classes("w-full").style("padding: 12px; margin: 8px 0;"):
                                        with ui.row().classes("w-full gap-2 items-center"):
                                            behaviors = dp.get_behaviors_for_species(sel["species"])
                                            species_select = ui.select(
                                                "Species",
                                                options=valid_species,
                                                value=sel["species"] if sel["species"] in valid_species else valid_species[0],
                                            ).props("outlined dense").classes("flex-grow")

                                            behavior_select = ui.select(
                                                "Behavior",
                                                options=behaviors,
                                                value=sel["behavior"] if sel["behavior"] in behaviors else behaviors[0],
                                            ).props("outlined dense").classes("flex-grow")

                                            start_input = ui.number_input(
                                                "Start (s)",
                                                value=sel.get("start_sec", 0.0),
                                                precision=1,
                                            ).props("outlined dense")

                                            end_val = sel.get("end_sec")
                                            end_input = ui.number_input(
                                                "End (s)",
                                                value=end_val if end_val is not None else 0,
                                            ).props("outlined dense")

                                            ui.button(
                                                icon="delete",
                                                on_click=lambda i=i: delete_selection(i),
                                            ).props("flat color=negative")

                                            def update_selection(idx, sp, bp, si, ei):
                                                new_sels = get_selections()
                                                if 0 <= idx < len(new_sels):
                                                    new_sels[idx] = {
                                                        "species": sp.value,
                                                        "behavior": bp.value,
                                                        "start_sec": si.value or 0.0,
                                                        "end_sec": ei.value if ei.value else None,
                                                    }
                                                    set_selections(new_sels)

                                            species_select.on_value_change(
                                                lambda e, i=i, bp=behavior_select, si=start_input, ei=end_input: update_selection(i, species_select, bp, si, ei)
                                            )
                                            behavior_select.on_value_change(
                                                lambda e, i=i, sp=species_select, si=start_input, ei=end_input: update_selection(i, sp, behavior_select, si, ei)
                                            )
                                            start_input.on_value_change(
                                                lambda e, i=i, sp=species_select, bp=behavior_select, ei=end_input: update_selection(i, sp, bp, start_input, ei)
                                            )
                                            end_input.on_value_change(
                                                lambda e, i=i, sp=species_select, bp=behavior_select, si=start_input: update_selection(i, sp, bp, si, end_input)
                                            )

                                def delete_selection(idx):
                                    new_selections = get_selections()
                                    if 0 <= idx < len(new_selections):
                                        new_selections.pop(idx)
                                        set_selections(new_selections)
                                        ui.navigate.to("/review")

                                def add_species():
                                    last_species = selections[-1]["species"] if selections else valid_species[0]
                                    new_selections = get_selections()
                                    new_selections.append(
                                        {
                                            "species": last_species,
                                            "behavior": "unlabeled",
                                            "start_sec": 0.0,
                                            "end_sec": video.get("duration_sec"),
                                        }
                                    )
                                    set_selections(new_selections)
                                    ui.navigate.to("/review")

                                ui.button("Add Species", icon="add", on_click=add_species)

                            def navigate(direction):
                                new_idx = get_current_idx() + direction
                                if 0 <= new_idx < len(queue):
                                    set_current_idx(new_idx)
                                    state["review_active_id"] = None
                                    state["pending_blank_confirm"] = False
                                    set_selections([])
                                    ui.navigate.to("/review")

                            def submit_and_next():
                                sels = get_selections()
                                if sels:
                                    dp.update_manual_review(selected_video_id, sels, annotator=state.get("annotator_name", "default"))
                                    ui.notify("Review saved!", type="positive")
                                navigate(1)

                            def submit():
                                sels = get_selections()
                                if sels:
                                    dp.update_manual_review(selected_video_id, sels, annotator=state.get("annotator_name", "default"))
                                    ui.notify("Review saved!", type="positive")

                            def mark_blank():
                                if get_selections():
                                    state["pending_blank_confirm"] = True
                                else:
                                    dp.update_manual_review(
                                        selected_video_id,
                                        [{"species": "blank", "behavior": "unlabeled", "start_sec": 0.0, "end_sec": None}],
                                        annotator=state.get("annotator_name", "default"),
                                    )
                                    ui.notify("Marked as blank!", type="positive")
                                    navigate(1)

                            ui.label("Shortcuts: Enter=Submit&Next, B=Blank").classes("text-xs text-gray-500 mt-4")

                            with ui.row().classes("w-full gap-2 mt-2"):
                                ui.button("Submit & Next", icon="save", on_click=submit_and_next, color="primary")
                                ui.button("Submit", icon="save", on_click=submit)
                                ui.button("Mark Blank", icon="block", on_click=mark_blank, color="warning")

                            current_label = video.get("manual_review_prediction") or "None"
                            consensus = video.get("classification_consensus", "UNKNOWN")
                            ui.markdown(f"**Current Label:** `{current_label}`").classes("mt-4")
                            ui.markdown(f"**Consensus:** `{consensus}`").classes("text-sm")

                else:
                    with ui.card().classes("w-full confirm-dialog"):
                        ui.label("Confirm Mark Blank").classes("text-lg font-bold")
                        ui.label("Marking blank will remove existing species rows for this video. Confirm to continue.")

                        def confirm_blank():
                            dp.update_manual_review(
                                selected_video_id,
                                [{"species": "blank", "behavior": "unlabeled", "start_sec": 0.0, "end_sec": None}],
                                annotator=state.get("annotator_name", "default"),
                            )
                            state["pending_blank_confirm"] = False
                            ui.notify("Marked as blank!", type="positive")
                            navigate(1)

                        def cancel_blank():
                            state["pending_blank_confirm"] = False
                            ui.navigate.to("/review")

                        with ui.row().classes("gap-2"):
                            ui.button("Confirm", icon="check", on_click=confirm_blank, color="primary")
                            ui.button("Cancel", icon="close", on_click=cancel_blank)

    def setup_keyboard_shortcuts():
        ui.add_body_html("""
            <script>
                document.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter' && !e.target.matches('input, textarea, select')) {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="submit-next"]')?.click();
                    }
                    if (e.key === 'b' && !e.target.matches('input, textarea, select')) {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="mark-blank"]')?.click();
                    }
                    if (e.key === 'ArrowLeft' && !e.target.matches('input, textarea, select')) {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="prev"]')?.click();
                    }
                    if (e.key === 'ArrowRight' && !e.target.matches('input, textarea, select')) {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="next"]')?.click();
                    }
                });
            </script>
        """)

    setup_keyboard_shortcuts()
