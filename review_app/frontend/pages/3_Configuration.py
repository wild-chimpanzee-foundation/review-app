import copy

import streamlit as st

from review_app.frontend.data_access import (
    clear_cached_queries,
    data_provider,
    get_config_cached,
    get_overrides_cached,
)

st.set_page_config(layout="wide")


def display_configuration_section() -> None:
    st.header("Pipeline Configuration")

    config = get_config_cached()
    overrides = copy.deepcopy(get_overrides_cached())

    if not config:
        st.error("Could not load configuration.")
        return

    st.subheader("Global Thresholds")

    current_blank_threshold = config.get("blank_non_blank_threshold", 0.5)
    new_blank_threshold = st.slider(
        "Global Blank/Non-Blank Threshold",
        min_value=0.0,
        max_value=1.0,
        value=float(current_blank_threshold),
        step=0.01,
        help="Videos with blank probability above this threshold are considered blank unless overridden by a species classifier.",
    )
    overrides["blank_non_blank_threshold"] = new_blank_threshold

    st.markdown("---")
    st.subheader("Species Classifier Thresholds")

    if "species_classifiers" in config:
        if "species_classifiers" not in overrides:
            overrides["species_classifiers"] = []

        for i, classifier in enumerate(config["species_classifiers"]):
            if len(overrides["species_classifiers"]) <= i:
                overrides["species_classifiers"].append({"name": classifier.get("name")})

            with st.expander(
                f"Classifier: {classifier.get('name', f'Unknown {i}')}", expanded=True
            ):
                col1, col2 = st.columns(2)

                with col1:
                    res_threshold = st.slider(
                        "Result Probability Threshold",
                        min_value=0.0,
                        max_value=1.0,
                        value=float(classifier.get("result_probability_threshold", 0.5)),
                        step=0.01,
                        key=f"res_threshold_{i}",
                        help="Minimum confidence required for a species prediction to be considered during voting.",
                    )
                    overrides["species_classifiers"][i]["result_probability_threshold"] = (
                        res_threshold
                    )

                with col2:
                    override_threshold = st.slider(
                        "Blank Override Threshold",
                        min_value=0.0,
                        max_value=1.0,
                        value=float(classifier.get("blank_override_threshold", 0.6)),
                        step=0.01,
                        key=f"override_threshold_{i}",
                        help="If any species is predicted with confidence above this threshold, the video is forced to 'non-blank'.",
                    )
                    overrides["species_classifiers"][i]["blank_override_threshold"] = (
                        override_threshold
                    )
    else:
        st.info("No species classifiers defined in configuration.")

    if st.button("Save Overrides to Database", type="primary"):
        try:
            data_provider.save_config(overrides)
            clear_cached_queries()
            st.success(
                "Configuration overrides saved successfully! Note: Changes will apply to future pipeline runs."
            )
        except Exception as exc:
            st.error(f"Could not save overrides: {exc}")

    st.markdown("---")
    st.subheader("Data Management")
    st.write(
        "If you have changed thresholds above, you can re-apply them to all videos already in the database."
    )
    if st.button("Re-apply Thresholds to Existing Data"):
        with st.spinner("Recalculating all results..."):
            try:
                count = data_provider.reapply_thresholds_to_all()
                clear_cached_queries()
                st.success(f"Successfully updated {count} videos based on new thresholds.")
                st.rerun()
            except KeyError as exc:
                st.error(f"Configuration is incomplete: {exc}")
            except Exception as exc:
                st.error(f"Failed to re-apply thresholds: {exc}")


display_configuration_section()
