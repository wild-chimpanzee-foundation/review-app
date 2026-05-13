 Workflow Analysis & Implementation Plan: Distributed Annotation

 Context

 10 video analysts each receive a USB drive with a subset of ~100 video folders plus AI predictions. They annotate locally, export a CSV, and return it to the supervisor who merges
 everything and does QA. Three features are needed to make this workflow smooth:

 1. Supervisor/Analyst role toggle — hide advanced controls from analysts
 2. Project bundle import — one-file setup for analysts (species, tags, model annotations, metadata)
 3. Batch annotation import — supervisor imports all 10 analyst CSVs at once

 Species list distribution is handled externally for now.

 ---
 Structured Workflow (Current + Planned)

 Supervisor: Prepare

 1. Annotate/configure project as normal.
 2. Divide 100 folders into 10 batches, copy each to a USB (preserve folder names — the parent_folder/filename path matching depends on it).
 3. NEW: Export a project bundle (ZIP) containing: species CSV, tags CSV, model annotations CSV, metadata CSV. All components optional.
 4. Give each analyst: USB drive + bundle.zip.

 Analyst: Setup & Annotate

 1. Run app, setup wizard: set name, point project at USB video folder.
 2. NEW: Import bundle.zip via the Bundle tab — auto-imports whichever components are present. Should be proposed in the initial project setup flow.
 3. Annotate videos. In analyst mode, UI is simplified (no advanced settings visible).
 4. Export annotations CSV from Overview (prominent button) or Model Import.

 Supervisor: Merge & QA

 1. Collect 10 annotation CSVs.
 2. NEW: Upload all 10 at once in the Annotations tab → batch import with append mode → see aggregated summary.
 3. Filter by review_later, tags, needs_review, annotator name for QA.

 ---
 Feature 1: Supervisor/Analyst Role Toggle

 What changes in analyst mode:

- Settings page: hide the "Advanced Settings" expansion (species, tags, blank thresholds, database management)
- Navigation: hide the "Model Import" nav item
- Overview page: show a prominent "Export Annotations" button (so analysts can export without needing Model Import)
- Project picker: hide the delete-project button

 Implementation:

 ┌───────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                 File                  │                                                Change                                                │
 ├───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/backend/db/models.py       │ Add app_mode: str = "supervisor" to AppSetting via migration                                         │
 ├───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/app/state.py               │ Add get_app_mode() -> str and set_app_mode(mode: str)                                                │
 ├───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/app/pages/settings/page.py │ Add role toggle above Advanced Settings; wrap Advanced Settings in if get_app_mode() == "supervisor" │
 ├───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/app/entry_point.py         │ Conditionally show/hide "Model Import" nav button based on mode                                      │
 ├───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/app/project_picker.py      │ Hide delete button in analyst mode                                                                   │
 ├───────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/app/pages/overview.py      │ Add "Export Annotations" download button visible in analyst mode                                     │
 └───────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────┘

 The toggle is a simple ui.toggle or ui.switch in the basic settings section (not inside Advanced Settings, so analysts can see but not change it — or we make it toggle-to-supervisor always
  visible, toggle-to-analyst only visible in supervisor mode).

 ---
 Feature 2: Project Bundle Import/Export

 Bundle format (ZIP file)

 bundle.zip
 ├── bundle.json          # manifest: {"version": "1", "contents": ["species", "tags", "model_annotations", "metadata"]}
 ├── species.csv          # optional — scientific_name column + display/behavior data
 ├── tags.csv             # optional — name_en, name_fr, color columns
 ├── model_annotations.csv  # optional — long-format model predictions
 └── metadata.csv         # optional — video path, GPS, timestamps

 Detection: read bundle.json for the contents list; fallback to detecting by filename if manifest is missing.

 New backend functions needed

 ┌───────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                     File                      │                                                              Function                                                              │
 ├───────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/species.py        │ export_project_species_csv(project_id) -> str — exports scientific_name + display names                                            │
 ├───────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/tag_repository.py │ export_tags_csv(project_id) -> str — exports tag key, name_en, name_fr, color, icon                                                │
 ├───────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/tag_repository.py │ import_tags_from_csv(project_id, content: str) -> int — idempotent upsert by key                                                   │
 ├───────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/import_service.py │ export_project_bundle(project_id, include: list[str]) -> bytes — zips selected components                                          │
 ├───────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/import_service.py │ import_project_bundle(project_id, zip_bytes: bytes) -> dict — unzips, imports each present component, returns per-component result │
 └───────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

 New UI: Bundle tab in Model Import page

 File: review_app/app/pages/model_import/_bundle_tab.py (new)

 Two sections:

- Export bundle (supervisor mode only): checkboxes for which components to include → Download ZIP button
- Import bundle (both modes): drag-and-drop ZIP upload → auto-detects contents → shows what will be imported → confirm → shows per-component import summary

 Wire into: review_app/app/pages/model_import/__init__.py — add 4th tab "Bundle"

 Also: Add "Import project bundle" card/button to Overview page, especially prominent in analyst mode (since the Model Import nav is hidden for analysts, they need an alternative entry
 point). Clicking it navigates to /model-import with the Bundle tab pre-selected.

 ---
 Feature 3: Batch Annotation Import

 File: review_app/app/pages/model_import/_annotations_tab.py

 Change the import section to accept multiple files (NiceGUI's ui.upload supports multiple=True). After upload:

- Show a list of uploaded files with status badges
- Import each file sequentially in append mode using the existing import_annotations_csv()
- Show aggregated summary: total files, total videos imported, total skipped (with skipped paths)

 No backend changes needed — import_annotations_csv already handles append mode correctly.

 ---
 Files to Modify / Create

 ┌───────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────┐
 │                         File                          │                         Action                          │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/backend/db/models.py                       │ Add app_mode field to AppSetting                        │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/backend/db/migrations.py                   │ Migration to add app_mode column                        │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/state.py                               │ get_app_mode(), set_app_mode()                          │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/pages/settings/page.py                 │ Role toggle UI + conditional Advanced Settings          │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/entry_point.py                         │ Conditional nav item visibility                         │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/project_picker.py                      │ Hide delete in analyst mode                             │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/pages/overview.py                      │ Export button + bundle import shortcut for analyst mode │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/species.py                │ export_project_species_csv()                            │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/tag_repository.py         │ export_tags_csv(), import_tags_from_csv()               │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/backend/provider/import_service.py         │ export_project_bundle(), import_project_bundle()        │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/pages/model_import/__init__.py         │ Add Bundle tab (4th tab)                                │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/pages/model_import/_bundle_tab.py      │ New file — bundle import/export UI                      │
 ├───────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────┤
 │ review_app/app/pages/model_import/_annotations_tab.py │ Multi-file upload for batch import                      │
 └───────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────┘

 ---
 Translation keys needed

 All new UI strings need EN + FR entries in review_app/app/translations.py:

- Role toggle label + descriptions
- Bundle tab labels (export bundle, import bundle, bundle contents, per-component status)
- Batch import summary labels

 ---
 Verification

 1. Role toggle: Switch to analyst mode → Advanced Settings, Model Import nav, and project delete are hidden. Switch back to supervisor → everything visible.
 2. Bundle export: In supervisor mode, export a bundle with all 4 components → download ZIP → verify all 4 CSVs present with correct data.
 3. Bundle import: Fresh project pointing at a USB folder → import the ZIP → verify species, tags, model annotations, and metadata are all imported correctly.
 4. Partial bundle: ZIP with only species.csv → verify only species imported, no errors for missing components.
 5. Batch annotation import: Upload 3 analyst CSVs at once → verify all annotations merged correctly with append mode → check skipped list accuracy.
 6. Full round-trip: Analyst flow end-to-end — setup wizard → bundle import → annotate 5 videos → export → supervisor batch-imports → supervisor sees all 5 annotations + review_later flags.

Correction
Probably skip the special supervisor/analyst modes for now
