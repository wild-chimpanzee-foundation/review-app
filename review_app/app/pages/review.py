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

    def navigate(direction):
        new_idx = get_current_idx() + direction
        if 0 <= new_idx < len(queue):
            set_current_idx(new_idx)
            state["review_active_id"] = None
            state["pending_blank_confirm"] = False
            set_selections([])
            ui.navigate.to("/review")

    ui.button("Previous", icon="chevron_left", on_click=lambda: navigate(-1)).props("flat")
    ui.button("Next", icon="chevron_right", on_click=lambda: navigate(1)).props("flat")
    ui.space()
    ui.label(f"Video: {selected_video_id}").classes("text-lg font-semibold")

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

            if not video.get("is_video_valid", True):
                ui.label(
                    f"Video validation failed: {video.get('video_validation_details', 'Unknown error')}"
                ).classes("text-red-600 text-sm")

            ui.label(f"Duration: {video.get('duration_sec', 0):.1f}s").classes(
                "text-sm text-gray-600"
            )

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

            ui.label("Annotations").classes("text-lg font-semibold")

            if not selections:
                selections = []
                set_selections(selections)

            for i, sel in enumerate(selections):
                with ui.card().classes("w-full").style("padding: 12px; margin: 8px 0;"):
                    behaviors = dp.get_behaviors_for_species(sel["species"])
                    species_select = (
                        ui.select(
                            "Species",
                            options=valid_species,
                            value=sel["species"]
                            if sel["species"] in valid_species
                            else valid_species[0],
                        )
                        .props("outlined dense")
                        .classes("flex-grow")
                    )
                    behavior_select = (
                        ui.select(
                            "Behavior",
                            options=behaviors,
                            value=sel["behavior"]
                            if sel["behavior"] in behaviors
                            else behaviors[0],
                        )
                        .props("outlined dense")
                        .classes("flex-grow")
                    )

                    start_val = sel.get("start_sec", 0.0)
                    end_val = sel.get("end_sec")
                    start_input = ui.number("Start (s)", value=start_val, precision=1).props(
                        "outlined dense"
                    )
                    end_input = ui.number(
                        "End (s)", value=end_val if end_val is not None else 0
                    ).props("outlined dense")

                    ui.button(icon="delete", on_click=lambda idx=i: delete_selection(idx)).props(
                        "flat color=negative"
                    )

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
                        lambda e, idx=i: update_selection(
                            idx, species_select, behavior_select, start_input, end_input
                        )
                    )
                    behavior_select.on_value_change(
                        lambda e, idx=i: update_selection(
                            idx, species_select, behavior_select, start_input, end_input
                        )
                    )
                    start_input.on_value_change(
                        lambda e, idx=i: update_selection(
                            idx, species_select, behavior_select, start_input, end_input
                        )
                    )
                    end_input.on_value_change(
                        lambda e, idx=i: update_selection(
                            idx, species_select, behavior_select, start_input, end_input
                        )
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

            def submit_and_next():
                sels = get_selections()
                if sels:
                    dp.update_manual_review(
                        selected_video_id, sels, annotator=state.get("annotator_name", "default")
                    )
                    ui.notify("Review saved!", type="positive")
                navigate(1)

            def submit():
                sels = get_selections()
                if sels:
                    dp.update_manual_review(
                        selected_video_id, sels, annotator=state.get("annotator_name", "default")
                    )
                    ui.notify("Review saved!", type="positive")

            def mark_blank():
                if get_selections():
                    state["pending_blank_confirm"] = True
                else:
                    dp.update_manual_review(
                        selected_video_id,
                        [
                            {
                                "species": "blank",
                                "behavior": "unlabeled",
                                "start_sec": 0.0,
                                "end_sec": None,
                            }
                        ],
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
