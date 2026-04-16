import pandas as pd
import streamlit as st

from review_app.frontend.data_access import (
    clear_cached_queries,
    get_csv_templates,
    get_valid_species_cached,
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
    st.dataframe(source_df.head(20), width="stretch")
    st.caption(f"Rows in file: {len(source_df)}")

    try:
        cleaned_df, errors_df, species_mappings, unmapped_species = validate_model_csv(source_df)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Validation failed: {exc}")
        return

    st.subheader("Validation Result")
    st.metric("Valid Rows", len(cleaned_df))
    st.metric("Invalid Rows", len(errors_df) + len(unmapped_species))

    unique_mappings = {m["original"]: m["mapped_to"] for m in species_mappings}

    all_unmapped = {u["original"] for u in unmapped_species}
    all_unmapped.update(unique_mappings.keys())

    session_key = f"species_mapping_{len(source_df)}_{hash(tuple(sorted(all_unmapped)))}"
    if session_key not in st.session_state:
        st.session_state[session_key] = unique_mappings.copy()

    if all_unmapped:
        st.subheader("Species Mappings")
        st.caption("Edit species mappings. Click 'Apply Mappings' when done.")
        mapping_data = [{"Original": name, "Mapped To": st.session_state[session_key].get(name, "")} for name in sorted(all_unmapped)]
        mapping_df = pd.DataFrame(mapping_data)
        valid_species = get_valid_species_cached()
        edited_mapping_df = st.data_editor(
            mapping_df,
            column_config={
                "Mapped To": st.column_config.SelectboxColumn(
                    "Mapped To",
                    options=valid_species,
                    required=True,
                ),
            },
            hide_index=True,
            width="stretch",
            key="species_mapping_editor",
        )
        if st.button("Apply Mappings", key="apply_mappings_btn"):
            st.session_state[session_key] = dict(zip(edited_mapping_df["Original"], edited_mapping_df["Mapped To"]))
            st.rerun()

    final_mappings = st.session_state.get(session_key, {})

    if unmapped_species:
        st.warning(f"{len(unmapped_species)} species without fuzzy match. Map them above to include them.")

    if not errors_df.empty:
        st.error(f"{len(errors_df)} rows have other validation errors. Fix the CSV and re-upload.")
        st.dataframe(errors_df, width="stretch")
        return

    if cleaned_df.empty and not final_mappings:
        st.warning("No valid rows found to import.")
        return

    pending_unmapped = [orig for orig, mapped in final_mappings.items() if not mapped]
    if pending_unmapped:
        st.warning(f"Please map all species. Missing mappings for: {', '.join(pending_unmapped)}")
        return

    if final_mappings:
        cleaned_df = cleaned_df.copy()
        species_mask = cleaned_df["annotation_type"] == "species"
        cleaned_df.loc[species_mask, "value_text"] = cleaned_df.loc[species_mask, "value_text"].replace(final_mappings)

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
