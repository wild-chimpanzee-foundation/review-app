# Exporting Annotations

Exports are available on the **Model Import** page, under the **Annotations** tab.

## Export annotations

Exports the human-reviewed (manual) annotations as a CSV.

- Filename: `annotations_{project_name}_{annotator_name}_{YYYY-MM-DD_HH-MM-SS}.csv`
- One row per species observation; blank videos get a single row with empty species fields.
- Columns: `project_name`, `video_path`, `camera_id`, `recorded_at`, `latitude`, `longitude`, `duration_sec`, `assigned_to`, `is_blank`, `review_later`, `is_annotated`, `annotator`, `labeled_at`, `observation_id`, `observation_uuid`, `species` (scientific name), `attributes` (comma-joined behavior/tag keys), `count`, `start_sec`, `end_sec`, plus one `tag_<key>` column (0/1) per built-in tag and a `custom_tags` column (comma-separated custom tag keys). `observation_uuid` is preserved across exports and lets repeated append imports update the same observation instead of duplicating it.

## Export AI annotations

Exports the raw model predictions as a CSV.

- Filename: `ai_annotations_{project_name}_{YYYY-MM-DD_HH-MM-SS}.csv`

## Bundle export

Also from the Model Import page, you can export a `.zip` bundle containing the project's species list, tags, model annotations, and metadata as separate CSVs. This is meant for distributing project setup to other annotators — see [Getting started](getting-started.md) for the matching bundle import flow.

## Manual Distance annotations

Open **Model Import → Manual Distance**.

1. Choose a species with **manually confirmed Species Review labels**.
2. Optionally enter a camera-local time-of-day window, such as **18:00–20:00**.
   It applies across all days and checks **video start time only**, not overlap.
   Start is included, end is excluded; an earlier end crosses midnight. Leave both
   fields empty for all times.
3. Click **Preview videos**, inspect the list, and select the tasks to export.
   Unknown camera times are listed separately when a time filter is active.
   Only verified Distance camera times are used—not file metadata.
4. Download either:
   - **Editable ZIP:** whole tasks with individuals, alignment, samples/skips,
     calibrated reference dependencies, relationships, completion and bookmarks.
   - **Analysis TSV:** one row per saved sample, including visibility skips.
     This table alone cannot restore editable tasks or explicit absence outcomes.

Ordinary annotator assignments do not restrict selection. Videos stay separate.
This first format rejects AI-linked tasks/references rather than dropping AI
provenance; annotator work packages are not implemented yet.

### Restore an editable Distance ZIP

Videos must already be imported/synced in the destination project. Under
**Restore Distance annotations**, upload the ZIP (maximum 50 MiB), match source
media roots to local project folders, and inspect the preview. A single source
and local root can be matched automatically.

Choose **Replace task** or leave **Skip** for each task, then apply your choices.
Replace removes the task's current evidence and restores the exported alignment,
individuals, samples, completion and bookmarks together. Other species tasks
and ordinary Species Review labels remain unchanged. Conflicting shared reference
calibration is never silently overwritten; skip that task or resolve its
dependencies separately.

Repeated restoration does not duplicate evidence. The summary lists imported and
skipped tasks. Keep the original ZIP for retries: the app does not archive skipped
evidence or retain your uploaded file.

TSV files use UTF-8, tab delimiters and decimal points, regardless of UI language.
For spreadsheets, use Text/CSV Import, select tab as delimiter and a decimal-point
numeric locale, and treat identifiers as text. Double-clicking a TSV is not a
reliable cross-locale workflow.

Next: [Settings](settings.md)
