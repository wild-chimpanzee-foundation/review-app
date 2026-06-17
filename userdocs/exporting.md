# Exporting Annotations

Exports are available on the **Model Import** page, under the **Annotations** tab.

## Export annotations

Exports the human-reviewed (manual) annotations as a CSV.

- Filename: `annotations_{project_name}_{annotator_name}_{YYYY-MM-DD_HH-MM-SS}.csv`
- One row per species observation; blank videos get a single row with empty species fields.
- Columns: `project_name`, `video_path`, `camera_id`, `recorded_at`, `latitude`, `longitude`, `duration_sec`, `assigned_to`, `is_blank`, `review_later`, `is_annotated`, `annotator`, `labeled_at`, `observation_id`, `species` (scientific name), `attributes` (comma-joined behavior/tag keys), `count`, `start_sec`, `end_sec`, plus one `tag_<key>` column (0/1) per built-in tag and a `custom_tags` column (comma-separated custom tag keys).

## Export AI annotations

Exports the raw model predictions as a CSV.

- Filename: `ai_annotations_{project_name}_{YYYY-MM-DD_HH-MM-SS}.csv`

## Bundle export

Also from the Model Import page, you can export a `.zip` bundle containing the project's species list, tags, model annotations, and metadata as separate CSVs. This is meant for distributing project setup to other annotators — see [Getting started](getting-started.md) for the matching bundle import flow.
