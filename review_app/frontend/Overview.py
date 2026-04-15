import pandas as pd
import plotly.express as px
import streamlit as st

from review_app.frontend.data_access import get_overview_stats, get_queue_filter_options_cached
from review_app.frontend.data_access import data_provider

st.set_page_config(page_title="Overview", layout="wide")


st.title("Overview")

# Use a session_state flag so we don't ask to sync every time they change pages
if "synced" not in st.session_state:
    st.session_state.synced = False

if not st.session_state.synced:
    st.info("New videos detected or first run initialization required.")

    if st.button("Start System Sync"):
        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_ui(current, total, filename):
            progress = current / total
            progress_bar.progress(progress)
            status_text.text(f"Processing {current}/{total}: {filename}")

        # Call the method we moved out of __init__
        data_provider.sync_videos(progress_callback=update_ui)

        st.session_state.synced = True
        st.success("Sync complete!")
        status_text.empty()
        progress_bar.empty()
        st.rerun()  # Refresh to show the newly loaded data
else:
    st.success("Database is in sync with local storage.")


with st.sidebar:
    st.header("Filters")

    min_confidence = st.slider(
        "Min model confidence",
        0.0,
        1.0,
        0.0,
        0.05,
        help="Hide model predictions below this threshold",
    )
    selected_cameras = st.multiselect(
        "Cameras",
        options=["All"] + get_queue_filter_options_cached()["camera_values"],
        default=["All"],
    )
    date_range = st.date_input("Date range", value=[], help="Filter videos by creation date")
    show_invalid = st.toggle("Include invalid videos", value=False)


# ── Load data ────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner="Loading stats…")
def load_stats():
    return get_overview_stats()


if st.session_state.synced:
    stats = load_stats()
    # ── Top-level KPIs ────────────────────────────────────────────────────────────
    v = stats["videos"]
    l = stats["labeling"]
    failed_videos = stats["failed_videos"]

    st.subheader("Videos")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total", int(v["total"]))
    c2.metric("Cameras", int(v["cameras"]))
    c3.metric("Total hours", f"{v['total_hours']:.1f} h")
    c4.metric(
        "Labeled",
        int(l["labeled"]),
        delta=f"{100 * l['labeled'] / max(l['total_videos'], 1):.0f}%",
    )
    c5.metric("Blank", int(l["blank"] or 0))
    c6.metric("Invalid", int(v["invalid"]), delta_color="inverse")

    st.divider()

    # ── Charts: two columns ───────────────────────────────────────────────────────
    with st.expander("Failed Videos"):
        st.dataframe(failed_videos[["video_path", "validation_error"]])

    left, right = st.columns(2)

    with left:
        st.subheader("Manual observations — species")
        species_df = pd.DataFrame(stats["species_counts"])
        if not species_df.empty:
            fig = px.bar(
                species_df.head(20),
                x="observations",
                y="species",
                orientation="h",
                color="videos",
                labels={"observations": "Observations", "species": ""},
                color_continuous_scale="Teal",
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=400)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No manual observations yet.")

    with right:
        st.subheader("Manual observations — behaviors")
        beh_df = pd.DataFrame(stats["behavior_counts"])
        if not beh_df.empty:
            fig = px.pie(
                beh_df,
                names="behavior",
                values="observations",
                hole=0.4,
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No behavior data yet.")

    # ── Model coverage ────────────────────────────────────────────────────────────
    st.subheader("Model annotation coverage")
    cov_df = pd.DataFrame(stats["model_coverage"])
    if not cov_df.empty:
        # Apply confidence filter from sidebar
        if min_confidence > 0:
            cov_df = cov_df[cov_df["avg_probability"] >= min_confidence]
        st.dataframe(
            cov_df,
            width="stretch",
            hide_index=True,
        )

    # ── Model vs human agreement ──────────────────────────────────────────────────
    st.subheader("Model ↔ human agreement")
    agree_df = pd.DataFrame(stats["model_human_agreement"])
    if not agree_df.empty:
        fig = px.bar(
            agree_df,
            x="model_name",
            y="agreement_pct",
            text="agreement_pct",
            color="agreement_pct",
            range_y=[0, 100],
            color_continuous_scale="RdYlGn",
            labels={"agreement_pct": "Agreement %"},
        )
        fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Not enough overlapping model + manual labels to compare yet.")

    # ── Per-camera table ──────────────────────────────────────────────────────────
    st.subheader("Per-camera summary")
    cam_df = pd.DataFrame(stats["camera_summary"])
    if not cam_df.empty:
        if "All" not in selected_cameras and selected_cameras:
            cam_df = cam_df[cam_df["camera_id"].isin(selected_cameras)]
        cam_df["labeled_%"] = (100 * cam_df["labeled"] / cam_df["total_videos"]).round(1)
        st.dataframe(
            cam_df,
            width="stretch",
            hide_index=True,
        )
