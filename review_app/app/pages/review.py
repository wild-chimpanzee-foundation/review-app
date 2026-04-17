from pathlib import Path

from nicegui import ui

from review_app.app.state import (
    get_current_idx,
    get_data_provider,
    get_filters,
    get_queue,
    get_selections,
    set_current_idx,
    set_data_provider,
    set_queue,
    set_selections,
    state,
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


def setup_review():
    dp = get_data_provider()
    if not dp:
        config_path = Path("config.yaml")
        if config_path.exists():
            dp = LocalDataProvider(str(config_path))
            set_data_provider(dp)
        else:
            with ui.card().classes("q-pa-xl"):
                ui.label("Data provider not initialized").classes("text-h6 text-negative")
                ui.button("Set up", on_click=lambda: ui.navigate.to("/setup"), icon="settings")
            return

    valid_species = dp.get_valid_species()
    if not valid_species:
        valid_species = ["unknown"]

    filters = get_filters()
    queue = get_queue()
    filter_options = dp.get_queue_filter_options()

    filters["include_unranked"] = state.get("include_unranked", True)
    state["annotator_name"] = state.get("annotator_name", "default")

    if not queue:
        queue_ids = dp.get_video_queue(filters)
        set_queue(queue_ids)
        queue = queue_ids

    annotations_container_holder: list = [None]
    manual_review_card_holder: list = [None]

    with ui.row().classes("w-full items-start"):
        sidebar = ui.column().classes("review-sidebar").style(
            "width: 280px; min-width: 280px; min-height: calc(100vh - 50px); padding: 16px;"
        )
        main_container = ui.column().classes("col q-pa-md")

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

    def refresh_annotations():
        queue = get_queue()
        if not queue:
            return
        container = annotations_container_holder[0]
        if not container:
            return
        current_idx = get_current_idx()
        selected_video_id = queue[max(0, min(current_idx, len(queue) - 1))]
        video = dp.get_video_detail(selected_video_id)
        if video:
            container.clear()
            with container:
                render_annotation_section(video)
            container.update()
            card = manual_review_card_holder[0]
            if card:
                card.update()

    def refresh_all():
        main_container.clear()
        with main_container:
            render_video_section()

    def render_video_section():
        queue = get_queue()
        if not queue:
            ui.label("No videos match your filters").classes("text-h6 text-grey-5")
            return

        current_idx = get_current_idx()
        current_idx = max(0, min(current_idx, len(queue) - 1))
        selected_video_id = queue[current_idx]
        video = dp.get_video_detail(selected_video_id)

        if not video:
            ui.label("Could not load video").classes("text-h6 text-negative")
            return

        with ui.row().classes("w-full items-center q-mb-md"):
            icon = ui.icon("queue", size="md")
            icon.classes("text-primary q-mr-sm")
            ui.label(f"Queue: {current_idx + 1} / {len(queue)}").classes("text-h6")

        def navigate(direction):
            new_idx = get_current_idx() + direction
            if 0 <= new_idx < len(queue):
                set_current_idx(new_idx)
                state["review_active_id"] = None
                state["pending_blank_confirm"] = False
                state["user_cleared_all"] = False
                set_selections([])
                refresh_all()

        with ui.row().classes("w-full items-center q-mb-md"):
            prev_btn = ui.button(icon="chevron_left", on_click=lambda: navigate(-1)).props("flat")
            prev_btn._props["data-shortcut"] = "prev"
            next_btn = ui.button(icon="chevron_right", on_click=lambda: navigate(1)).props("flat")
            next_btn._props["data-shortcut"] = "next"
            ui.space()
            icon = ui.icon("videocam", size="sm")
            icon.classes("text-grey-6 q-mr-sm")
            ui.label(selected_video_id).classes("text-subtitle1 font-weight-medium")

        with ui.row().classes("w-full gap-md"):
            with ui.column().classes("col"):
                with ui.card().classes("full-width q-mb-md"):
                    video_id = f"video-{selected_video_id}"
                    if video.get("video_path"):
                        video_path = Path(video["video_path"])
                        if video_path.exists():
                            ui.video(
                                str(video_path), controls=True, autoplay=True, muted=True
                            ).props(f'id="{video_id}"').classes("full-width")
                        else:
                            ui.label(f"Video file not found: {video_path}").classes(
                                "text-negative"
                            )
                    else:
                        ui.label("No video path available").classes("text-grey-5")

                    if not video.get("is_video_valid", True):
                        ui.label(
                            f"Video validation failed: {video.get('video_validation_details', 'Unknown error')}"
                        ).classes("text-negative text-caption q-mt-sm")

                with ui.card().classes("full-width"):
                    icon = ui.icon("science", size="sm")
                    icon.classes("text-primary q-mr-sm")
                    ui.label("Model Annotations").classes(
                        "text-subtitle1 font-weight-medium q-mb-sm"
                    )

                    model_ann = dp.get_model_annotations(selected_video_id)
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
                manual_review_card_holder[0] = ui.card().classes("full-width q-mb-md")
                with manual_review_card_holder[0]:
                    icon = ui.icon("edit", size="sm")
                    icon.classes("text-primary q-mr-sm")
                    ui.label("Manual Review").classes("text-subtitle1 font-weight-medium q-mb-sm")

                    annotations_container_holder[0] = ui.column().classes("w-full")
                    with annotations_container_holder[0]:
                        render_annotation_section(video)

        current_speed = state.get("playback_speed", "1x").replace("x", "")
        ui.run_javascript(f"""
            setTimeout(() => {{
                const v = document.getElementById('{video_id}');
                if (v) v.playbackRate = {current_speed};
            }}, 100);
        """)

    def render_annotation_section(video):
        selections = get_selections()
        user_cleared_all = state.get("user_cleared_all", False)

        if not selections and not user_cleared_all:
            existing = video.get("manual_selections") or []
            if existing:
                set_selections(list(existing))
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

        if not selections:
            selections = []
            set_selections(selections)
        else:
            state["user_cleared_all"] = False

        def delete_selection(idx):
            queue = get_queue()
            current_idx = get_current_idx()
            video_id = queue[max(0, min(current_idx, len(queue) - 1))]
            new_sels = get_selections()
            if 0 <= idx < len(new_sels):
                new_sels.pop(idx)
                set_selections(new_sels)
                dp.update_manual_review(
                    video_id,
                    new_sels if new_sels else [],
                    annotator=state.get("annotator_name", "default"),
                )
                if not new_sels:
                    state["user_cleared_all"] = True
            refresh_annotations()

        for i, sel in enumerate(selections):
            with ui.card().classes("full-width q-mb-sm").style("padding: 12px;"):
                with ui.row().classes("w-full gap-sm items-center"):
                    behaviors = dp.get_behaviors_for_species(sel["species"])
                    sp_value = (
                        sel["species"] if sel["species"] in valid_species else valid_species[0]
                    )
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
                    "behavior": "unlabeled",
                    "start_sec": 0.0,
                    "end_sec": video.get("duration_sec"),
                }
            )
            set_selections(new_sels)
            refresh_annotations()

        ui.button("Add Species", icon="add", on_click=add_species).props("size=sm")

        queue = get_queue()
        current_idx = get_current_idx()
        selected_video_id = queue[max(0, min(current_idx, len(queue) - 1))]

        def submit_and_next():
            sels = get_selections()
            if sels:
                dp.update_manual_review(
                    selected_video_id, sels, annotator=state.get("annotator_name", "default")
                )
                ui.notify("Review saved!", type="positive")
            set_current_idx(get_current_idx() + 1)
            state["review_active_id"] = None
            state["pending_blank_confirm"] = False
            set_selections([])
            refresh_all()

        def submit():
            sels = get_selections()
            if sels:
                dp.update_manual_review(
                    selected_video_id, sels, annotator=state.get("annotator_name", "default")
                )
                ui.notify("Review saved!", type="positive")

        def mark_blank():
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
            set_current_idx(get_current_idx() + 1)
            state["review_active_id"] = None
            set_selections([])
            refresh_all()

        with ui.row().classes("w-full gap-sm q-mt-sm q-mb-md"):
            submit_next_btn = ui.button(
                "Submit & Next", icon="skip_next", on_click=submit_and_next, color="primary"
            )
            submit_next_btn._props["data-shortcut"] = "submit-next"
            ui.button("Submit", icon="save", on_click=submit)
            blank_btn = ui.button("Mark Blank", icon="block", on_click=mark_blank, color="warning")
            blank_btn._props["data-shortcut"] = "mark-blank"

        ui.label("Shortcuts: Enter=Submit&Next, N=Next, P=Previous, B=Blank").classes(
            "text-caption text-grey-5"
        )

        with ui.row().classes("w-full q-mt-md gap-md"):
            current_label = video.get("manual_review_prediction") or "None"
            consensus = video.get("classification_consensus", "UNKNOWN")
            ui.label(f"Current: {current_label}").classes("text-body2")
            ui.label(f"Consensus: {consensus}").classes("text-body2 text-grey-6")

    with sidebar:
        with ui.card().classes("full-width q-mb-md"):
            ui.label("Annotator").classes("text-subtitle2 text-grey-7")
            annotator_input = ui.input("Name", value=state.get("annotator_name", "default")).props(
                "outlined dense class=full-width"
            )

            def update_annotator():
                state["annotator_name"] = annotator_input.value

            annotator_input.on_value_change(lambda: update_annotator())

        current_speed = float(state.get("playback_speed", "1x").replace("x", ""))

        with ui.card().classes("full-width q-mb-md"):
            icon = ui.icon("speed", size="sm")
            icon.classes("text-primary")
            ui.label("Playback Speed").classes("text-subtitle2 text-grey-7")

            speed_label = ui.label(f"{current_speed}x").classes("text-body2 q-my-sm")

            def update_playback_speed(val):
                speed = round(val, 2)
                speed_str = f"{speed}x"
                state["playback_speed"] = speed_str
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
            icon = ui.icon("filter_list", size="sm")
            icon.classes("text-primary")
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

            include_unranked = ui.checkbox(
                "Include unranked", value=state.get("include_unranked", True)
            )

            def apply_filters():
                new_filters = {
                    "search_query": search.value,
                    "selected_camera": camera_select.value,
                    "selected_species": species_filter.value,
                    "include_unranked": include_unranked.value,
                }
                state["include_unranked"] = include_unranked.value
                update_filters(**new_filters)
                new_queue = dp.get_video_queue(new_filters)
                set_queue(new_queue)
                set_current_idx(0)
                set_selections([])
                refresh_all()

            ui.button("Apply Filters", on_click=apply_filters, color="primary").props("full-width")

    refresh_all()

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
