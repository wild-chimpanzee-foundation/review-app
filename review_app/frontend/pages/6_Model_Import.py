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
    st.subheader("CSV Template")
    templates = get_csv_templates()
    csv_content = templates["model_annotations"]
    st.download_button(
        label="Download Unified Annotation Template",
        data=csv_content,
        file_name="model_annotations_template.csv",
        mime="text/csv",
    )


def display_model_import_section() -> None:
    st.header("Model Output Import")
    st.caption("Upload model outputs as CSV and import them into `model_annotations`.")

    _render_template_downloads()
    st.markdown("---")

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
        cleaned_df, errors_df = validate_model_csv(source_df)
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
        try:
            result = import_model_csv(
                cleaned_df=cleaned_df,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Import failed: {exc}")
            return

        clear_cached_queries()
        st.success(f"Imported {result['inserted_rows']} rows.")


display_model_import_section()
