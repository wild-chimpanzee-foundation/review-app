import pandas as pd
import streamlit as st

from review_app.frontend.data_access import (
    clear_cached_queries,
    get_csv_templates,
    import_model_csv,
    validate_model_csv,
)

st.set_page_config(layout="wide")


def _render_template_downloads() -> None:
    st.subheader("CSV Templates")
    templates = get_csv_templates()
    template_types = ["blank_non_blank", "species", "behavior", "distance"]
    cols = st.columns(len(template_types))
    for idx, annotation_type in enumerate(template_types):
        with cols[idx]:
            csv_content = templates[annotation_type]
            st.download_button(
                label=f"Download {annotation_type.replace('_', ' ').title()} Template",
                data=csv_content,
                file_name=f"model_import_{annotation_type}_template.csv",
                mime="text/csv",
            )


def display_model_import_section() -> None:
    st.header("Model Output Import")
    st.caption("Upload model outputs as CSV and import them into `model_annotations`.")

    _render_template_downloads()
    st.markdown("---")

    col1, col2, col3 = st.columns(3)
    with col1:
        annotation_type = st.selectbox(
            "Annotation Type",
            options=["blank_non_blank", "species", "behavior", "distance"],
            index=0,
        )
    with col2:
        model_name = st.text_input("Model Name", value=f"{annotation_type}_model")
    with col3:
        model_version = st.text_input("Model Version", value="v1")

    config_version = st.text_input("Config Version (optional)", value="")
    uploaded = st.file_uploader("Upload CSV", type=["csv"])

    if uploaded is None:
        st.info("Upload a CSV file to validate and import.")
        return

    try:
        source_df = pd.read_csv(uploaded)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not parse CSV: {exc}")
        return

    st.subheader("Preview")
    st.dataframe(source_df.head(20), use_container_width=True)
    st.caption(f"Rows in file: {len(source_df)}")

    try:
        cleaned_df, errors_df = validate_model_csv(source_df, annotation_type=annotation_type)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Validation failed: {exc}")
        return

    st.subheader("Validation Result")
    st.metric("Valid Rows", len(cleaned_df))
    st.metric("Invalid Rows", len(errors_df))

    if not errors_df.empty:
        st.warning("Some rows are invalid. Fix the CSV and re-upload.")
        st.dataframe(errors_df, use_container_width=True)
        return

    if cleaned_df.empty:
        st.warning("No valid rows found to import.")
        return

    if st.button("Import Valid Rows", type="primary", width="stretch"):
        if not model_name.strip():
            st.error("Model Name is required.")
            return
        if not model_version.strip():
            st.error("Model Version is required.")
            return

        try:
            result = import_model_csv(
                cleaned_df=cleaned_df,
                model_name=model_name,
                model_version=model_version,
                config_version=config_version or None,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Import failed: {exc}")
            return

        clear_cached_queries()
        st.success(
            f"Imported {result['inserted_rows']} rows. Model run id: {result['model_run_id']}"
        )


display_model_import_section()
