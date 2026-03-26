# Model CSV Import

Use the Streamlit page `Model Output Import` to upload model outputs into `model_annotations`.

Templates:
- `docs/csv_templates/blank_non_blank_template.csv`
- `docs/csv_templates/species_template.csv`
- `docs/csv_templates/behavior_template.csv`
- `docs/csv_templates/distance_template.csv`

Rules:
- Every CSV needs `video_uid`.
- Species rows must contain a valid species code from the `species` table.
- Behavior rows accept behavior codes (preferred) and will store text values.
- Distance rows require a numeric value (`value_num` / `distance` / `distance_m`).
- `probability` is optional but must be between 0 and 1 when provided.

- Blank/non-blank rows require `blank_non_blank` with values `blank` or `non_blank`.
