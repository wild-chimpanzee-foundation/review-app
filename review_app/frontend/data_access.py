import streamlit as st
import pandas as pd

from review_app.backend.local_data_provider import LocalDataProvider


@st.cache_resource
def get_data_provider() -> LocalDataProvider:
    return LocalDataProvider()


data_provider = get_data_provider()


def clear_cached_queries() -> None:
    st.cache_data.clear()


@st.cache_data(ttl=3600)
def get_queue_filter_options_cached():
    return data_provider.get_queue_filter_options()


@st.cache_data(ttl=3600)
def get_video_queue_cached(filters: dict):
    return data_provider.get_video_queue(filters=filters)


@st.cache_data(ttl=30)
def get_valid_species_cached():
    return data_provider.get_valid_species()


@st.cache_data(ttl=3600)
def get_video_detail_cached(video_id):
    return data_provider.get_video_detail(video_id)


def get_video_annotations_cached(video_id):
    return data_provider.get_model_annotations(video_id)


# Compatibility wrappers for legacy pages that are being phased out.
@st.cache_data(ttl=3600)
def get_filter_options_cached():
    return get_queue_filter_options_cached()


@st.cache_data(ttl=3600)
def get_filtered_videos_cached(filters: dict):
    queue_ids = get_video_queue_cached(filters=filters)
    return pd.DataFrame({"video_id": queue_ids})


@st.cache_data(ttl=3600)
def get_all_videos_cached():
    queue_ids = get_video_queue_cached(filters={})
    rows = []
    for vid in queue_ids:
        detail = get_video_detail_cached(vid)
        if detail:
            rows.append(detail)
    return pd.DataFrame(rows)


@st.cache_data(ttl=3600)
def get_videos_for_review_cached():
    queue_ids = get_video_queue_cached(filters={"selected_review": "Needs Review"})
    return pd.DataFrame({"video_id": queue_ids})


@st.cache_data(ttl=3600)
def get_video_by_id_cached(video_id):
    return get_video_detail_cached(video_id)


@st.cache_data(ttl=3600)
def get_flow_data_cached():
    return pd.DataFrame(columns=["source", "target", "value"])


@st.cache_data(ttl=3600)
def get_pipeline_progress_summary_cached():
    return pd.DataFrame(columns=["current_stage", "status", "count"])


def get_csv_templates():
    return data_provider.get_csv_templates()


def validate_model_csv(df):
    return data_provider.validate_model_csv(df=df)


def import_model_csv(cleaned_df):
    return data_provider.import_model_csv(cleaned_df=cleaned_df)
