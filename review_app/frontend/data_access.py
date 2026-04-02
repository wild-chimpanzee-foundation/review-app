import streamlit as st

from review_app.backend.local_data_provider import LocalDataProvider


@st.cache_resource
def get_data_provider() -> LocalDataProvider:
    return LocalDataProvider()


data_provider = get_data_provider()


def clear_cached_queries() -> None:
    st.cache_data.clear()


@st.cache_data(ttl=3600)
def get_all_videos_cached():
    return data_provider.get_all_videos()


@st.cache_data(ttl=3600)
def get_videos_for_review_cached():
    return data_provider.get_videos_for_review()


@st.cache_data(ttl=3600)
def get_filter_options_cached():
    return data_provider.get_filter_options()


@st.cache_data(ttl=3600)
def get_filtered_videos_cached(filters: dict):
    return data_provider.get_filtered_videos(filters=filters)


@st.cache_data(ttl=3600)
def get_flow_data_cached():
    return data_provider.get_flow_data()


@st.cache_data(ttl=3600)
def get_pipeline_progress_summary_cached():
    return data_provider.get_pipeline_progress_summary()


@st.cache_data(ttl=30)
def get_valid_species_cached():
    return data_provider.get_valid_species()


@st.cache_data(ttl=3600)
def get_video_by_id_cached(video_id):
    return data_provider.get_video_by_id(video_id)


@st.cache_data(ttl=3600)
def get_video_history_cached(video_id):
    return data_provider.get_video_history(video_id)


@st.cache_data(ttl=3600)
def get_config_cached():
    return data_provider.get_config()


@st.cache_data(ttl=3600)
def get_overrides_cached():
    return data_provider.get_overrides()


def get_csv_templates():
    return data_provider.get_csv_templates()


def validate_model_csv(df, annotation_type: str):
    return data_provider.validate_model_csv(df=df, annotation_type=annotation_type)


def import_model_csv(cleaned_df, model_name: str, model_version: str, config_version: str | None):
    return data_provider.import_model_csv(
        cleaned_df=cleaned_df,
        model_name=model_name,
        model_version=model_version,
        config_version=config_version,
    )
