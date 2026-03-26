import streamlit as st

from review_app.frontend.components.filters import (
    render_video_filters,
)
from review_app.frontend.components.formatting import format_probability
from review_app.frontend.components.history import display_history_expander
from review_app.frontend.components.video_player import (
    apply_video_playback_rate,
    render_video_player,
    render_video_sidebar_settings,
)
from review_app.frontend.data_access import (
    clear_cached_queries,
    data_provider,
    get_filter_options_cached,
    get_filtered_videos_cached,
    get_valid_species_cached,
    get_video_by_id_cached,
)

st.set_page_config(layout="wide")


def display_manual_review_section() -> None:
    render_video_sidebar_settings()

    filter_options = get_filter_options_cached()
    with st.sidebar.expander("Filter & Search", expanded=True):
        filters = render_video_filters(
            filter_options, key_prefix="manual_review", default_review="Needs Review", sidebar=True
        )

    videos_for_review = get_filtered_videos_cached(filters=filters.to_query_params())
    if videos_for_review.empty:
        st.info("No videos require manual review for the selected filters.")
        return

    queue_ids = videos_for_review["video_id"].tolist()
    if "review_queue_idx" not in st.session_state:
        st.session_state.review_queue_idx = 0
    st.session_state.review_queue_idx = max(
        0, min(st.session_state.review_queue_idx, max(0, len(queue_ids) - 1))
    )
    selected_video_id = queue_ids[st.session_state.review_queue_idx]

    video = get_video_by_id_cached(selected_video_id)
    valid_species = get_valid_species_cached()
    if not valid_species:
        valid_species = ["unknown"]

    if "review_undo_stack" not in st.session_state:
        st.session_state.review_undo_stack = []

    def snapshot_video_state(video_state):
        return {
            "video_id": video_state["video_id"],
            "current_stage": video_state["current_stage"],
            "status": video_state["status"],
            "manual_review_prediction": video_state.get("manual_review_prediction"),
            "final_species_prediction": video_state.get("final_species_prediction"),
            "needs_manual_review": video_state.get("needs_manual_review"),
            "blank_non_blank_final_result": video_state.get("blank_non_blank_final_result"),
        }

    def submit_review(prediction: str) -> None:
        st.info(f"New Prediction:\n {prediction}")
        st.session_state.review_undo_stack.append(snapshot_video_state(video))
        if len(st.session_state.review_undo_stack) > 50:
            st.session_state.review_undo_stack = st.session_state.review_undo_stack[-50:]
        data_provider.update_manual_review(selected_video_id, prediction)
        clear_cached_queries()
        st.rerun()

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
            apply_video_playback_rate(float(st.session_state.get("video_playback_speed", 1.0)))
            st.caption(
                f"Playback speed: {float(st.session_state.get('video_playback_speed', 1.0)):.1f}x"
            )
            display_history_expander(selected_video_id)

        with col2:
            st.markdown("**Model Predictions:**")
            st.write(
                f"- SF Disjoint: `{video['species_slowfast_disjoint_prediction']}` ({format_probability(video.get('species_slowfast_disjoint_prediction_probability'))})"
            )
            st.write(
                f"- SF Overlapping: `{video['species_slowfast_overlapping_prediction']}` ({format_probability(video.get('species_slowfast_overlapping_prediction_probability'))})"
            )
            st.write(
                f"- Zamba: `{video['species_zamba_prediction']}` ({format_probability(video.get('species_zamba_prediction_probability'))})"
            )
            st.markdown(f"**Consensus:** `{video['classification_consensus']}`")

            st.markdown("---")
            st.subheader("Action")

            default_species = (
                video["classification_consensus"]
                if video.get("classification_consensus") in valid_species
                else valid_species[0]
            )
            if (
                "review_species_selection" not in st.session_state
                or st.session_state.review_species_selection not in valid_species
            ):
                st.session_state.review_species_selection = default_species

            final_prediction = st.selectbox(
                "Final Species Prediction",
                options=valid_species,
                key="review_species_selection",
            )

            st.caption(
                "Shortcuts: Enter submit, N next, P previous, B blank, U unknown, A consensus, Z undo"
            )

            action_col1, action_col2 = st.columns(2)
            with action_col1:
                if st.button(
                    "Submit & Next",
                    type="primary",
                    width="stretch",
                    key="submit_and_next",
                    shortcut="Enter",
                ):
                    submit_review(final_prediction)
            with action_col2:
                if st.button("Mark Blank", width="stretch", key="mark_blank", shortcut="B"):
                    submit_review("blank")
                if st.button(
                    "Accept Consensus", width="stretch", key="accept_consensus", shortcut="A"
                ):
                    submit_review(default_species)
                if st.button("Mark Unknown", width="stretch", key="mark_unknown", shortcut="U"):
                    submit_review("unknown")
                if st.button("Undo Last", width="stretch", key="undo_last", shortcut="Z"):
                    if st.session_state.review_undo_stack:
                        snapshot = st.session_state.review_undo_stack.pop()
                        data_provider.restore_video_snapshot(snapshot)
                        clear_cached_queries()
                        st.rerun()

    def go_previous():
        st.info("Previous Video")
        st.session_state.review_queue_idx = max(0, st.session_state.review_queue_idx - 1)
        clear_cached_queries()

    def go_next():
        st.info("Skipped Video")
        st.session_state.review_queue_idx = min(
            len(queue_ids) - 1, st.session_state.review_queue_idx + 1
        )
        clear_cached_queries()

    def on_select_change():
        st.session_state.review_queue_idx = queue_ids.index(st.session_state.review_queue_select)
        clear_cached_queries()

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
