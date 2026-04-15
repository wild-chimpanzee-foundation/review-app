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


@st.cache_data(ttl=3600)
def get_overview_stats():
    return data_provider.get_overview_stats()


def get_csv_templates():
    return data_provider.get_csv_templates()


def validate_model_csv(df):
    return data_provider.validate_model_csv(df=df)


def import_model_csv(cleaned_df):
    return data_provider.import_model_csv(cleaned_df=cleaned_df)
