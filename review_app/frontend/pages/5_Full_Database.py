import math

import streamlit as st

from review_app.frontend.data_access import get_all_videos_cached

st.set_page_config(layout="wide")


def display_full_database_section() -> None:
    st.header("Full Database View")
    all_videos = get_all_videos_cached()

    if all_videos.empty:
        st.info("The database is currently empty.")
        return

    st.subheader(f"Total Videos: {len(all_videos)}")
    page_size = st.selectbox("Rows per page", options=[100, 250, 500, 1000], index=1)
    total_rows = len(all_videos)
    total_pages = max(1, math.ceil(total_rows / page_size))
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
    start_idx = (page - 1) * page_size
    end_idx = min(start_idx + page_size, total_rows)
    st.caption(f"Showing rows {start_idx + 1}-{end_idx} of {total_rows}")
    st.dataframe(all_videos.iloc[start_idx:end_idx], width="stretch")


display_full_database_section()
