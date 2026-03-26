import streamlit as st

from review_app.frontend.components.filters import (
    render_video_filters,
)
from review_app.frontend.components.history import display_history_expander
from review_app.frontend.components.video_player import (
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
    get_video_history_cached,
)

st.set_page_config(layout="wide")


def display_database_management_section() -> None:
    render_video_sidebar_settings()

    st.header("Database Management & Overrides")

    st.subheader("Filter & Search")
    filter_options = get_filter_options_cached()
    filters = render_video_filters(filter_options, key_prefix="db_management")
    filtered_df = get_filtered_videos_cached(filters=filters.to_query_params())

    st.subheader("Export Options")
    export_filename = st.text_input(
        "Filtered Export Filename",
        value="database_management_filtered.csv",
    )
    export_columns = st.multiselect(
        "Columns to Include",
        options=filtered_df.columns.tolist(),
        default=filtered_df.columns.tolist(),
    )
    export_df = filtered_df[export_columns] if export_columns else filtered_df
    st.download_button(
        label=f"Download Filtered Results ({len(filtered_df)} rows)",
        data=export_df.to_csv(index=False),
        file_name=export_filename,
        mime="text/csv",
    )

    st.markdown("---")

    if filtered_df.empty:
        st.info("No videos match your search criteria.")
        return

    video_id_search = st.selectbox(
        f"Select Video to Edit ({len(filtered_df)} found)",
        options=filtered_df["video_id"].tolist(),
    )

    if not video_id_search:
        return

    video = get_video_by_id_cached(video_id_search)
    history_df_export = get_video_history_cached(video_id_search)
    st.download_button(
        label=f"Download History for {video_id_search}",
        data=history_df_export.to_csv(index=False),
        file_name=f"{video_id_search}_history.csv",
        mime="text/csv",
    )

    with st.container(border=True):
        st.subheader(f"Edit State: {video_id_search}")

        with st.form("override_form", clear_on_submit=False):
            col1, col2 = st.columns(2)

            with col1:
                stages = [
                    "initial",
                    "blank_non_blank",
                    "species_classification",
                    "behavior_classification",
                    "depth_estimation",
                    "manual_review",
                    "completed",
                ]
                current_stage = st.selectbox(
                    "Current Stage",
                    options=stages,
                    index=stages.index(video["current_stage"])
                    if video["current_stage"] in stages
                    else 0,
                )

                statuses = ["pending", "running", "success", "failed", "completed", "NEEDS_REVIEW"]
                current_status = st.selectbox(
                    "Status",
                    options=statuses,
                    index=statuses.index(video["status"]) if video["status"] in statuses else 0,
                )

                blank_options = ["non_blank", "blank"]
                current_blank_result = st.selectbox(
                    "Blank/Non-Blank",
                    options=blank_options,
                    index=blank_options.index(video["blank_non_blank_final_result"])
                    if video["blank_non_blank_final_result"] in blank_options
                    else 0,
                )

            with col2:
                valid_species = get_valid_species_cached()
                if not valid_species:
                    valid_species = ["unknown"]
                current_species = st.selectbox(
                    "Final Species",
                    options=valid_species,
                    index=valid_species.index(video["final_species_prediction"])
                    if video["final_species_prediction"] in valid_species
                    else 0,
                )
                needs_review = st.checkbox(
                    "Needs Manual Review",
                    value=bool(video["needs_manual_review"]),
                )

            if st.form_submit_button("Save Overrides", width="stretch"):
                data_provider.force_update_video(
                    video_id_search,
                    stage=current_stage,
                    status=current_status,
                    species=current_species,
                    needs_review=needs_review,
                    blank_result=current_blank_result,
                )
                clear_cached_queries()
                st.success(f"Changes saved for {video_id_search}!")
                st.rerun()

        display_history_expander(video_id_search)
        st.markdown("**Video Preview:**")
        render_video_player(video["video_path"])


display_database_management_section()
