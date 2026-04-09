import streamlit as st

from review_app.frontend.components.filters import (
    render_video_filters,
)
from review_app.frontend.components.formatting import format_probability
from review_app.frontend.components.video_player import (
    apply_video_playback_rate,
    render_video_player,
    render_video_sidebar_settings,
)
from review_app.frontend.data_access import (
    clear_cached_queries,
    data_provider,
    get_queue_filter_options_cached,
    get_valid_species_cached,
    get_video_detail_cached,
    get_video_queue_cached,
    get_video_annotations_cached,
)

st.set_page_config(layout="wide")


def display_manual_review_section() -> None:
    render_video_sidebar_settings()

    filter_options = get_queue_filter_options_cached()
    with st.sidebar.expander("Filter & Search", expanded=True):
        filters = render_video_filters(
            filter_options, key_prefix="manual_review", default_review="All", sidebar=True
        )
        include_unranked = st.checkbox(
            "Include videos not listed in priority CSV",
            value=False,
            key="manual_review_include_unranked",
        )

    query_filters = filters.to_query_params()
    query_filters["include_unranked"] = include_unranked
    queue_ids = get_video_queue_cached(filters=query_filters)
    if not queue_ids:
        st.info("No videos require manual review for the selected filters.")
        return

    if "review_queue_idx" not in st.session_state:
        st.session_state.review_queue_idx = 0
    st.session_state.review_queue_idx = max(
        0, min(st.session_state.review_queue_idx, max(0, len(queue_ids) - 1))
    )
    selected_video_id = queue_ids[st.session_state.review_queue_idx]

    video = get_video_detail_cached(selected_video_id)
    if video is None:
        st.error("Selected video details could not be loaded.")
        return
    valid_species = get_valid_species_cached()
    if not valid_species:
        valid_species = ["unknown"]

    # --- Initialize current review selections in session state ---
    if (
        "review_selections" not in st.session_state
        or st.session_state.get("review_active_id") != selected_video_id
    ):
        st.session_state.review_active_id = selected_video_id
        existing = video.get("manual_selections") or []
        if existing:
            st.session_state.review_selections = existing
        else:
            default_species = valid_species[0]
            default_end = video.get("duration_sec")
            st.session_state.review_selections = [
                {
                    "species": default_species,
                    "behavior": "unlabeled",
                    "start_sec": 0.0,
                    "end_sec": default_end,
                }
            ]

    if "review_undo_stack" not in st.session_state:
        st.session_state.review_undo_stack = []

    def go_previous():
        st.session_state.review_queue_idx = max(0, st.session_state.review_queue_idx - 1)
        if "review_active_id" in st.session_state:
            del st.session_state.review_active_id
        clear_cached_queries()

    def go_next():
        st.session_state.review_queue_idx = min(
            len(queue_ids) - 1, st.session_state.review_queue_idx + 1
        )
        if "review_active_id" in st.session_state:
            del st.session_state.review_active_id
        clear_cached_queries()

    def on_select_change():
        st.session_state.review_queue_idx = queue_ids.index(st.session_state.review_queue_select)
        if "review_active_id" in st.session_state:
            del st.session_state.review_active_id
        clear_cached_queries()

    def submit_review(selections: list[dict]) -> None:
        data_provider.update_manual_review(selected_video_id, selections)

    st.info(f"Queue: {st.session_state.review_queue_idx + 1}/{len(queue_ids)} currently selected.")
    with st.container(border=True):
        st.subheader(f"Reviewing: {selected_video_id}")

        col1, col2 = st.columns([1, 1])

        with col1:
            if video.get("video_path"):
                render_video_player(video["video_path"])
            else:
                st.warning(
                    "No local video path mapped for this video/user in `user_video_locations`."
                )
            if not bool(video.get("is_video_valid", True)):
                details = video.get("video_validation_details") or "ffprobe failed to open this video."
                st.error(f"Video validation failed: {details}")
            apply_video_playback_rate(float(st.session_state.get("video_playback_speed", 1.0)))
            st.caption(
                f"Playback speed: {float(st.session_state.get('video_playback_speed', 1.0)):.1f}x"
            )

        with col2:
            st.subheader("Manual Review")

            # --- Render Dynamic Species-Behavior Rows ---
            new_selections = []
            for i, sel in enumerate(st.session_state.review_selections):
                with st.container(border=True):
                    rcol1, rcol2, rcol3, rcol4, rcol5 = st.columns([2, 2, 1.3, 1.3, 0.5])
                    with rcol1:
                        s_idx = (
                            valid_species.index(sel["species"])
                            if sel["species"] in valid_species
                            else 0
                        )
                        species = st.selectbox(
                            "Species", options=valid_species, index=s_idx, key=f"species_{i}"
                        )
                    with rcol2:
                        behaviors = data_provider.get_behaviors_for_species(species)
                        b_idx = (
                            behaviors.index(sel["behavior"]) if sel["behavior"] in behaviors else 0
                        )
                        behavior = st.selectbox(
                            "Behavior", options=behaviors, index=b_idx, key=f"behavior_{i}"
                        )
                    with rcol3:
                        start_sec = st.number_input(
                            "Start (s)",
                            value=float(sel.get("start_sec", sel.get("timestamp", 0.0)) or 0.0),
                            step=0.1,
                            key=f"start_{i}",
                        )
                    with rcol4:
                        end_raw = sel.get("end_sec")
                        end_text = "" if end_raw is None else str(end_raw)
                        end_sec_text = st.text_input("End (s)", value=end_text, key=f"end_{i}")
                    with rcol5:
                        st.write("")  # spacing
                        if st.button("🗑️", key=f"del_{i}"):
                            st.session_state.review_selections.pop(i)
                            st.rerun()
                    end_sec = None
                    if end_sec_text.strip():
                        try:
                            end_sec = float(end_sec_text.strip())
                        except ValueError:
                            st.warning("End time must be numeric or empty.")
                    new_selections.append(
                        {
                            "species": species,
                            "behavior": behavior,
                            "start_sec": start_sec,
                            "end_sec": end_sec,
                        }
                    )

            st.session_state.review_selections = new_selections

            if st.button("➕ Add Species"):
                last_species = (
                    st.session_state.review_selections[-1]["species"]
                    if st.session_state.review_selections
                    else valid_species[0]
                )
                st.session_state.review_selections.append(
                    {
                        "species": last_species,
                        "behavior": "unlabeled",
                        "start_sec": 0.0,
                        "end_sec": video.get("duration_sec"),
                    }
                )
                st.rerun()

            st.markdown("---")
            st.caption("Shortcuts: Enter submit, N next, P previous, B blank")

            action_col1, action_col2 = st.columns(2)
            with action_col1:
                if st.button(
                    "Submit & Next",
                    type="primary",
                    width="stretch",
                    key="submit_and_next",
                    shortcut="Enter",
                ):
                    submit_review(st.session_state.review_selections)
                    go_next()
                    st.rerun()
                if st.button(
                    "Submit",
                    type="secondary",
                    width="stretch",
                    key="submit",
                ):
                    submit_review(st.session_state.review_selections)
                    st.rerun()
            with action_col2:
                if st.button("Mark Blank", width="stretch", key="mark_blank", shortcut="B"):
                    if st.session_state.review_selections:
                        st.session_state.pending_blank_confirm = True
                    else:
                        submit_review(
                            [
                                {
                                    "species": "blank",
                                    "behavior": "unlabeled",
                                    "start_sec": 0.0,
                                    "end_sec": None,
                                }
                            ]
                        )
                        go_next()
                        st.rerun()

            if st.session_state.get("pending_blank_confirm", False):
                st.error(
                    "Marking blank will remove existing species rows for this video. Confirm to continue."
                )
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("Confirm Blank", type="primary", width="stretch"):
                        submit_review(
                            [
                                {
                                    "species": "blank",
                                    "behavior": "unlabeled",
                                    "start_sec": 0.0,
                                    "end_sec": None,
                                }
                            ]
                        )
                        st.session_state.pending_blank_confirm = False
                        go_next()
                        st.rerun()
                with c2:
                    if st.button("Cancel", width="stretch"):
                        st.session_state.pending_blank_confirm = False
                        st.rerun()

            st.subheader("All Current Annotations")
            all_ann = get_video_annotations_cached(selected_video_id)
            if not all_ann.empty:
                # Format for display
                display_df = all_ann[
                    ["model_name", "annotation_type", "value_text", "probability", "created_at"]
                ].copy()
                display_df["probability"] = display_df["probability"].apply(format_probability)
                st.dataframe(display_df, width="stretch", hide_index=True)
            else:
                st.info("No model annotations found for this video.")

            st.markdown(f"**Current Label:** `{video['manual_review_prediction'] or 'None'}`")
            st.markdown(f"**Consensus:** `{video['classification_consensus']}`")

    if "review_queue_idx" not in st.session_state:
        st.session_state.review_queue_idx = 0

    # Clamp index
    st.session_state.review_queue_idx = max(
        0, min(st.session_state.review_queue_idx, len(queue_ids) - 1)
    )

    # Sync selectbox value (only when needed)
    current_id = queue_ids[st.session_state.review_queue_idx]
    if (
        "review_queue_select" not in st.session_state
        or st.session_state.review_queue_select != current_id
    ):
        st.session_state.review_queue_select = current_id
    nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])

    with nav_col1:
        st.button("Previous", key="nav_previous", on_click=go_previous, shortcut="P")

    with nav_col2:

        def format_qid(qid):
            idx = queue_ids.index(qid)
            return f"({idx + 1})  {qid}"

        st.selectbox(
            "Select Video to Review",
            options=queue_ids,
            format_func=format_qid,
            key="review_queue_select",
            on_change=on_select_change,
        )

    with nav_col3:
        st.button("Next", key="nav_next", on_click=go_next, shortcut="N")


display_manual_review_section()
