import streamlit as st

from review_app.frontend.data_access import (
    get_all_videos_cached,
    get_pipeline_progress_summary_cached,
)

st.set_page_config(layout="wide")


def display_pipeline_progress_section() -> None:

    if get_all_videos_cached().empty:
        st.warning("No pipeline data found.")
        st.markdown(
            """
It looks like you have not run the pipeline yet, or the database is empty.

### How to start:
1. Run your pipeline so it writes results into Postgres.
2. Start this review app from your terminal:
```bash
uv run streamlit run review_app/frontend/1_Pipeline_Overview.py
```
3. Once data is available in Postgres, this dashboard will update automatically.
"""
        )
        return
    st.header("Pipeline Analytics")

    all_videos = get_all_videos_cached()
    if all_videos.empty:
        st.info("No videos in the system yet.")
        return

    total_count = len(all_videos)
    completed_df = all_videos[all_videos["current_stage"] == "completed"]
    completed_count = len(completed_df)
    failed_count = len(all_videos.query("status == 'failed' or is_video_valid == 0"))
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Videos", total_count)
    m2.metric("Completion Rate", f"{(completed_count / total_count) * 100:.1f}%")
    m3.metric(
        "Failed",
        failed_count,
        delta=None if failed_count == 0 else f"{failed_count} errors",
        delta_color="inverse",
    )

    # st.markdown("---")
    # st.subheader("Video Flow Analysis")
    # flow_df = get_flow_data_cached()
    # if not flow_df.empty:
    #     preferred_order = [
    #         "All Videos",
    #         "Ingested",
    #         "Invalid (ffmpeg)",
    #         "Runtime Failed",
    #         "Active Pipeline",
    #         "Blank/Non-Blank Pending",
    #         "Blank/Non-Blank Decided",
    #         "Interim Blank",
    #         "Interim Non-Blank",
    #         "Species Classification",
    #         "SF Disjoint Pending",
    #         "SF Disjoint Done",
    #         "SF Overlapping Pending",
    #         "SF Overlapping Done",
    #         "Zamba Species Pending",
    #         "Zamba Species Done",
    #         "Species Consensus Pending",
    #         "Species Consensus Done",
    #         "Final Blank Result: Pending",
    #         "Final Blank Result: Blank",
    #         "Final Blank Result: Non-Blank",
    #     ]
    #     present_nodes = set(flow_df["source"].tolist() + flow_df["target"].tolist())
    #     all_nodes = [n for n in preferred_order if n in present_nodes]
    #     all_nodes.extend(sorted(present_nodes - set(all_nodes)))
    #     node_map = {node: i for i, node in enumerate(all_nodes)}
    #
    #     node_color_map = {
    #         "All Videos": "#6B7280",
    #         "Ingested": "#94A3B8",
    #         "Invalid (ffmpeg)": "#DC2626",
    #         "Runtime Failed": "#F97316",
    #         "Active Pipeline": "#2563EB",
    #         "Blank/Non-Blank Pending": "#60A5FA",
    #         "Blank/Non-Blank Decided": "#3B82F6",
    #         "Interim Blank": "#B91C1C",
    #         "Interim Non-Blank": "#16A34A",
    #         "Species Classification": "#0EA5A4",
    #         "SF Disjoint Pending": "#5EEAD4",
    #         "SF Disjoint Done": "#14B8A6",
    #         "SF Overlapping Pending": "#99F6E4",
    #         "SF Overlapping Done": "#0F766E",
    #         "Zamba Species Pending": "#A7F3D0",
    #         "Zamba Species Done": "#059669",
    #         "Species Consensus Pending": "#F59E0B",
    #         "Species Consensus Done": "#D97706",
    #         "Final Blank Result: Pending": "#C084FC",
    #         "Final Blank Result: Blank": "#7C3AED",
    #         "Final Blank Result: Non-Blank": "#4F46E5",
    #     }
    #     def get_color(name: str) -> str:
    #         return node_color_map.get(name, "#9CA3AF")
    #
    #     def rgba_from_hex(hex_color: str, alpha: float = 0.28) -> str:
    #         hex_color = hex_color.lstrip("#")
    #         r = int(hex_color[0:2], 16)
    #         g = int(hex_color[2:4], 16)
    #         b = int(hex_color[4:6], 16)
    #         return f"rgba({r}, {g}, {b}, {alpha})"
    #
    #     fig = go.Figure(
    #         data=[
    #             go.Sankey(
    #                 arrangement="snap",
    #                 node=dict(
    #                     pad=24,
    #                     thickness=22,
    #                     line=dict(color="rgba(30,41,59,0.45)", width=0.6),
    #                     label=all_nodes,
    #                     color=[get_color(n) for n in all_nodes],
    #                 ),
    #                 link=dict(
    #                     source=[node_map[s] for s in flow_df["source"]],
    #                     target=[node_map[t] for t in flow_df["target"]],
    #                     value=flow_df["value"],
    #                     color=[rgba_from_hex(get_color(s)) for s in flow_df["source"]],
    #                 ),
    #             )
    #         ]
    #     )
    #
    #     fig.update_layout(
    #         height=620,
    #         margin=dict(l=14, r=14, t=20, b=20),
    #         font=dict(size=13, color="#334155"),
    #         paper_bgcolor="rgba(0,0,0,0)",
    #         plot_bgcolor="rgba(0,0,0,0)",
    #     )
    #     st.plotly_chart(fig, width="stretch")
    # else:
    #     st.info("Insufficient data for flow analysis.")
    #
    st.markdown("---")
    col_prog, col_stat = st.columns([2, 1])

    with col_prog:
        st.subheader("Stage Completion")

        def count_passed(stage_name: str) -> int:
            stages = [
                "initial",
                "blank_non_blank",
                "species_classification",
                "behavior_classification",
                "depth_estimation",
                "manual_review",
                "completed",
            ]
            try:
                stage_idx = stages.index(stage_name)
                passed = all_videos[
                    (
                        all_videos["current_stage"].apply(
                            lambda x: stages.index(x) > stage_idx if x in stages else 0
                        )
                    )
                    | (
                        (all_videos["current_stage"] == stage_name)
                        & (all_videos["status"].isin(["success", "completed"]))
                    )
                ]
                return len(passed)
            except ValueError:
                return 0

        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Blank Check", f"{count_passed('blank_non_blank')}/{total_count}")
        p2.metric("Species ID", f"{count_passed('species_classification')}/{total_count}")
        p3.metric("Behavior (dummy)", f"{count_passed('behavior_classification')}/{total_count}")
        p4.metric("Depth (dummy)", f"{count_passed('depth_estimation')}/{total_count}")

    with col_stat:
        st.subheader("Current Status")
        progress_summary = get_pipeline_progress_summary_cached()
        if not progress_summary.empty:
            pivot = progress_summary.pivot_table(
                index="current_stage", columns="status", values="count", aggfunc="sum"
            ).fillna(0)
            st.dataframe(pivot, width="stretch")

    st.markdown("---")
    st.subheader("Blank/ Non-Blank")
    vids_by_species = (
        get_all_videos_cached()
        .groupby("blank_non_blank_final_result")
        .size()
        .reset_index(name="count")
    )
    st.dataframe(vids_by_species)
    st.subheader("Species Classification Results")
    vids_by_species = (
        get_all_videos_cached()
        .groupby("final_species_prediction")
        .size()
        .reset_index(name="count")
    )
    st.dataframe(vids_by_species)


display_pipeline_progress_section()
