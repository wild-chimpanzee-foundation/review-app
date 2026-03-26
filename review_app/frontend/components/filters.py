from dataclasses import dataclass

import streamlit as st


@dataclass
class VideoFilters:
    search_query: str
    selected_species: str
    selected_camera: str
    selected_review: str
    selected_possible_species: str
    selected_blank_non_blank: str
    selected_behavior: str
    selected_validity: str
    distance_min: float | None
    distance_max: float | None

    def to_query_params(self) -> dict:
        return {
            "search_query": self.search_query,
            "selected_species": self.selected_species,
            "selected_camera": self.selected_camera,
            "selected_review": self.selected_review,
            "selected_possible_species": self.selected_possible_species,
            "selected_blank_non_blank": self.selected_blank_non_blank,
            "selected_behavior": self.selected_behavior,
            "selected_validity": self.selected_validity,
            "distance_min": self.distance_min,
            "distance_max": self.distance_max,
        }


def render_video_filters(
    filter_options: dict, key_prefix: str, default_review: str = "All", sidebar=False
) -> VideoFilters:
    if sidebar:
        f1, f2 = st.columns([1, 1])
        f3, f4 = st.columns([1, 1])
        f5, f6 = st.columns([1, 1])
        f7, f8 = st.columns([1, 1])
    else:
        f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
        f5, f6, f7, f8 = st.columns([1, 1, 1, 1])

    camera_values = filter_options.get("camera_values", [])
    species_values = filter_options.get("species_values", [])
    possible_species_values = filter_options.get("possible_species_values", [])
    behavior_values = filter_options.get("behavior_values", [])

    with f1:
        search_query = st.text_input(
            "Search by ID or Path",
            placeholder="e.g. 20200322...",
            key=f"{key_prefix}_search_query",
        )

    with f2:
        selected_camera = st.selectbox(
            "Filter by Camera",
            options=["All"] + camera_values,
            key=f"{key_prefix}_selected_camera",
        )

    with f3:
        selected_species = st.selectbox(
            "Filter by Species",
            options=["All"] + species_values,
            key=f"{key_prefix}_selected_species",
        )

    with f4:
        selected_possible_species = st.selectbox(
            "Filter by Possible Species",
            options=["All"] + possible_species_values,
            key=f"{key_prefix}_selected_possible_species",
        )

    with f5:
        selected_blank_non_blank = st.selectbox(
            "Filter by Blank/Non-Blank",
            options=["All", "Blank", "Non-Blank", "Unknown"],
            key=f"{key_prefix}_selected_blank_non_blank",
        )

    with f6:
        selected_behavior = st.selectbox(
            "Filter by Behavior",
            options=["All", "Has Behavior", "No Behavior"] + behavior_values,
            key=f"{key_prefix}_selected_behavior",
        )

    with f7:
        selected_validity = st.selectbox(
            "Filter by Validation",
            options=["All", "Valid Only", "Invalid Only", "Unknown"],
            key=f"{key_prefix}_selected_validity",
        )

    with f8:
        selected_review = st.selectbox(
            "Filter by Review Flag",
            options=["All", "Needs Review", "No Review"],
            index=["All", "Needs Review", "No Review"].index(default_review)
            if default_review in ["All", "Needs Review", "No Review"]
            else 0,
            key=f"{key_prefix}_selected_review",
        )

    d1, d2 = st.columns(2)
    with d1:
        distance_min_enabled = st.checkbox(
            "Min Distance",
            value=False,
            key=f"{key_prefix}_distance_min_enabled",
        )
        distance_min = (
            st.number_input(
                "Distance Min",
                value=0.0,
                step=0.1,
                key=f"{key_prefix}_distance_min",
            )
            if distance_min_enabled
            else None
        )

    with d2:
        distance_max_enabled = st.checkbox(
            "Max Distance",
            value=False,
            key=f"{key_prefix}_distance_max_enabled",
        )
        distance_max = (
            st.number_input(
                "Distance Max",
                value=100.0,
                step=0.1,
                key=f"{key_prefix}_distance_max",
            )
            if distance_max_enabled
            else None
        )

    return VideoFilters(
        search_query=search_query,
        selected_species=selected_species,
        selected_camera=selected_camera,
        selected_review=selected_review,
        selected_possible_species=selected_possible_species,
        selected_blank_non_blank=selected_blank_non_blank,
        selected_behavior=selected_behavior,
        selected_validity=selected_validity,
        distance_min=distance_min,
        distance_max=distance_max,
    )
