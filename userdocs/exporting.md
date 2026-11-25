# Exporting Annotations

Open **Import / Export → Export results**. Choose a dataset from the list; its description, scope and download/copy controls appear alongside it. Existing CSV/TSV formats and eligibility rules are unchanged.

## Export annotations

Exports the human-reviewed (manual) annotations as a CSV.

- Filename: `annotations_{project_name}_{annotator_name}_{YYYY-MM-DD_HH-MM-SS}.csv`
- One row per species observation; blank videos get a single row with empty species fields.
- Columns: `project_name`, `video_path`, `camera_id`, `recorded_at`, `latitude`, `longitude`, `duration_sec`, `assigned_to`, `is_blank`, `review_later`, `is_annotated`, `annotator`, `labeled_at`, `observation_id`, `observation_uuid`, `species` (scientific name), `attributes` (comma-joined behavior/tag keys), `count`, `start_sec`, `end_sec`, plus one `tag_<key>` column (0/1) per built-in tag and a `custom_tags` column (comma-separated custom tag keys). `observation_uuid` is preserved across exports and lets repeated append imports update the same observation instead of duplicating it.

## Export AI annotations

Exports the raw model predictions as a CSV.

- Filename: `ai_annotations_{project_name}_{YYYY-MM-DD_HH-MM-SS}.csv`

## Assignment delivery

Use **Import / Export → Assignments** to prepare assignments, then **Save delivery folder**. The app saves the setup ZIP and any selected footage on the computer running the app. The ZIP and its delivery folder share a name such as `Lope-species-Alice-014-setup-20261125-143012Z`. Distance ZIPs include the target species after `distance`. Send the delivery folder to the recipient, who can open the ZIP from the project picker. See [Getting started](getting-started.md).

The assignment number stays the same on return ZIPs. The setup timestamp records when the assignment was prepared. The return timestamp records when progress was downloaded. Both use UTC.

Use a recipient build that supports assignment format v2 for packages with deployment records, such as those containing catalog metadata or folder links. A build limited to v1 cannot open these packages. Builds with v2 support also open v1 assignments. A Distance package with saved measurements linked to deleted reference frames requires assignment format v3 support.

Annotators download a **return progress** ZIP from their working project's **Assignments** area. The coordinator previews and accepts it in the origin project. Species returns support **Append** (add/update by observation UUID, without deleting absent observations), **Keep existing**, or **Use imported**. Distance returns use only the last two whole-task policies. See [Assignments](settings.md#assignments) for conflict and replacement details.

## Manual Distance annotations

Open **Import / Export → Export results** and choose **Distance measurements** (TSV).

1. Choose a species with **manually confirmed Species Review labels**.
2. Optionally enter a camera-local time-of-day window, such as **18:00–20:00**.
   It applies across all days and checks **video start time only**, not overlap.
   Start is included, end is excluded; an earlier end crosses midnight. Leave both
   fields empty for all times.
3. Click **Preview videos**. All supported matching tasks start selected; expand the inspection list only if you want to inspect or narrow them.
   Unknown camera times are listed separately when a time filter is active.
   Only verified Distance camera times are used—not file metadata.
4. Download the **Analysis TSV**: one row per saved sample, including visibility
   skips. This table cannot restore editable tasks or explicit absence outcomes.

Ordinary annotator assignments do not restrict selection. This analysis export
supports manual tasks only. Use assignment packages for editable manual or
AI-linked Distance work.

TSV files use UTF-8, tab delimiters and decimal points, regardless of UI language.
For spreadsheets, use Text/CSV Import, select tab as delimiter and a decimal-point
numeric locale, and treat identifiers as text. Double-clicking a TSV is not a
reliable cross-locale workflow.

Next: [Settings](settings.md)
