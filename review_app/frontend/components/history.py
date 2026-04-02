import pandas as pd
import streamlit as st

from review_app.frontend.data_access import get_video_history_cached


def display_history_expander(video_id: str) -> None:
    with st.expander("📜 View Audit Trail (History)"):
        history_df = get_video_history_cached(video_id)
        if not history_df.empty:
            history_df["timestamp"] = pd.to_datetime(history_df["timestamp"]).dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            st.table(history_df[["timestamp", "stage", "status", "details", "payload_json"]])
        else:
            st.write("No history records found for this video.")
