# To-dos

## Code quality and data integrity audit

The audit was performed at `c8fb566`. Commit `4e9652d` closes the AI Distance
acceptance bug for new Species observations. Acceptance now saves the nonblank
label and observation in one transaction. Unchecked findings below are open.

### High priority

- [x] **Make database restore and rollback safe.** Commit `0c3edf7` stages
  compressed and raw backups and checks SQLite integrity and foreign keys before
  replacing the live database. A separate SQLite rollback snapshot preserves the
  original records if reopening fails or the swap is interrupted.
  [Settings restore](../../review_app/app/pages/settings/database.py#L94) ·
  [Restore validation](../../review_app/backend/db/backup.py#L398) ·
  [Database and recovery work checklist](database-and-recovery.md#work-checklist)
- [x] **Preserve Distance measurements when calibration changes.** Review uses
  the current position of the saved frame. Deletion preserves the distance and
  review completion, with display fallback to the same calibration distance on
  the original reference clip. Covered by completed-review and handoff tests.
- [x] **Enforce Species observation ownership and behavior links.** Single and
  bulk review writes use the video's project and reject requests scoped to a
  different project. Schema v33 aligns existing observations with their videos,
  clears conflicting blank flags, and removes behavior links whose observation
  is missing. A composite foreign key enforces the observation link and removes
  its behavior links on deletion. Upgrade coverage checks overview counts and
  CSV export.
  [Observation schema](../../review_app/backend/db/models.py#L185) ·
  [Species writer](../../review_app/backend/provider/annotation_repository.py#L495) ·
  [Upgrade tests](../../tests/test_migrations.py#L75)

### Import and performance

- [x] **Reject ambiguous media paths in CSV imports.** The shared lookup retains
  all candidates and stops at a tie. Manual, historic, and model CSV previews
  report ambiguous paths and skip those rows. A unique full local path can
  resolve a shared suffix. Covered by imports with duplicate paths under two roots.
  [Path lookup](../../review_app/backend/path_matching.py#L39) ·
  [Annotation CSV resolution](../../review_app/backend/provider/import_service/_shared.py#L95)
- **Deferred: Preserve multiple AI detections with the same label.** Current
  Species AI imports work for the models in use, which supply no start or end
  times. Revisit if a model starts emitting timed detections or multiple rows
  for the same video, model, type, and species. The current import upserts those
  rows under one identity and reports each input row as imported.
  [Model CSV import](../../review_app/backend/provider/import_service/_model_csv.py#L503) ·
  [Model annotation identity](../../review_app/backend/db/models.py#L238)
- [x] **Keep Species blank state consistent during Append returns.** The
  preview reports when the merged observations will clear a blank label. The
  accepted observations survive an annotation CSV export and Override import.
  [Species Append merge](../../review_app/backend/annotation_package/species.py#L159) ·
  [Annotation CSV import](../../review_app/backend/provider/import_service/_annotations_csv.py#L207)
- [x] **Make CSV dry-run counts match the writes.** The app-format annotation
  CSV preview and import share the per-clip review decision. The preview counts
  behavior updates, shows when a blank request preserves existing observations
  in Append, and reports clips without a Species review. Regression tests
  compare the preview with the saved results in Append and Override.
  [CSV preview](../../review_app/backend/provider/import_service/_annotations_csv.py#L414) ·
  [CSV apply](../../review_app/backend/provider/import_service/_annotations_csv.py#L146)
- [x] **Ensure prepared Distance assignments are reviewable after opening.**
  Selection reports and excludes clips without an effective camera or deployment
  identity. Preparation enforces the same requirement, and the recipient queue
  test verifies the remaining clip is reviewable.
  [Distance selection](../../review_app/backend/annotation_package/scope.py#L111) ·
  [Distance queue](../../review_app/backend/provider/distance_references.py#L48)
- [x] **Use one canonical path for packaged reference footage.** Distance
  setup uses the same resolved path for the footage, its reference record, and
  its deployment link. The Distance handoff test opens a package with symlinked
  reference footage and verifies that its return previews as unchanged.
  [Media location](../../review_app/backend/annotation_package/handoff.py#L124) ·
  [Distance references](../../review_app/backend/annotation_package/distance.py#L834) ·
  [Deployment restore](../../review_app/backend/annotation_package/deployments.py#L188)
- [x] **Avoid exposing partial copies as delivered footage.** Each file is
  staged in the destination directory and published only after a successful
  copy. The delivery test simulates a partial write, checks that package opening
  reports the clip as missing, and verifies a retry in a new output folder.
  [Footage copy](../../review_app/backend/annotation_package/delivery.py#L80) ·
  [Media check](../../review_app/backend/annotation_package/handoff.py#L137)
- [x] **Align legacy package detection and assignment version support.** The
  package picker, contents preview, and importer recognize CSV-only legacy
  bundles. The delivery guide documents the required recipient support for v2
  deployment packages and current support for opening v1 assignments.
  [Package detection](../../review_app/backend/annotation_package/detection.py#L37) ·
  [Assignment version selection](../../review_app/backend/annotation_package/handoff.py#L55) ·
  [Delivery guide](../../userdocs/exporting.md#assignment-delivery)

- [x] **Share Species decisions across review writers and CSV preview.** The
  single-clip editor and CSV imports use the same transactional writer for
  attribution, behavior links, and blank state. CSV preview uses the same
  selection normalization, observation matching and comparison, and blank-state
  rule. Bulk writes remain batched.
  [CSV preview](../../review_app/backend/provider/import_service/_annotations_csv.py#L414) ·
  [Bulk writer](../../review_app/backend/provider/annotation_repository.py#L495) ·
  [Single-video writer](../../review_app/backend/provider/annotation_repository.py#L791)
- [x] **Make app-format annotation CSV writes atomic across vocabulary and observations.**
  Import plans the matched rows before writing and saves mapped species, custom
  tags, clip reviews, and assignment metadata in one transaction. Regression
  tests cover a malformed count and a failed tag link after earlier writes.
  [CSV import](../../review_app/backend/provider/import_service/_annotations_csv.py#L237) ·
  [Rollback tests](../../tests/test_project_and_import.py#L963)
- [x] **Cut redundant writes from Distance queues and previews.** Task creation
  now selects and batches only missing tasks. The stress check verifies zero
  insert attempts on refresh at 1,000 and 10,000 tasks. At 10,000 existing
  tasks, queue refresh fell from 1.81 s to 0.27 s and export preview from
  3.65 s to 2.21 s. A concurrent queue and preview converge on the same task
  and preserve saved review state.
  [Stress check](../../tests/test_workflow_stress.py#L32) ·
  [Task initialization](../../review_app/backend/provider/distance_tasks.py#L12) ·
  [Export preview](../../review_app/backend/provider/distance_transfer.py#L492)
- [x] **Batch manual Distance export eligibility checks.** The task query
  now includes AI provenance flags for individuals and samples. On the
  10,000-task fixture, preview refresh fell from 20,005 SELECTs and 2.21 s
  to 5 SELECTs and 0.27 s. Export and restore keep the same eligibility
  errors and precedence.
  [Eligibility check](../../review_app/backend/provider/distance_transfer.py#L80)

### Maintainability and verification

- [ ] **Narrow provider and UI interfaces where workflows change.** The audit
  counted 174 public methods and properties on `LocalDataProvider`. The Distance
  page and setup wizard also hold large amounts of workflow and UI state. Start
  with ownership of playback and sample selection, reference editing, task
  completion, and project setup. Keep tests on the interfaces used by production
  callers.
  [Provider](../../review_app/backend/provider/local_data_provider.py#L37) ·
  [Distance page](../../review_app/app/pages/distance_review/page.py#L143) ·
  [Setup wizard](../../review_app/app/setup_wizard.py#L51)
- [x] **Extract shared Distance selection rules from the retired ZIP code.**
  Assignment selection and manual export share confirmed-species and camera-time
  rules. The UI uses the manual analysis TSV and assignment ZIPs. Legacy editable
  ZIP methods remain available in the backend and have test coverage.
  [Handoff selection](../../review_app/backend/annotation_package/scope.py#L117) ·
  [Distance design](reference-based-distance-annotation.md#manual-distance-export-and-restore) ·
  [Current export guide](../../userdocs/exporting.md)
- [x] **Isolate desktop test data and stabilize simulated handoff tests.** The
  shared fixture directs desktop database and backup lookups to a per-test
  directory. Background compression prunes in the snapshot's own directory.
  Species and Distance round trips wait for the next UI control after
  preparation. The Codex sandbox blocks writes to asyncio's local wakeup
  socket, which can stall executor shutdown after a passing test. Outside the
  sandbox, a single pytest process passed the 1,964 default tests and four
  CSV performance tests and exited cleanly in 7 minutes 10 seconds.
  [Storage fixture](../../tests/conftest.py#L51) ·
  [Distance round trip](../../tests/test_assignment_distance.py#L792)
- [ ] **Make CI and release builds verify the artifacts they produce.** The
  manual release workflow uses its tag input for versioning without passing it
  to checkout. Build from the selected ref and smoke-test startup of the packaged
  app. Add type checking to CI in stages, starting with the module interfaces
  changed by the work above.
  [Release workflow](../../.github/workflows/build.yml#L31) ·
  [CI workflow](../../.github/workflows/ci.yml#L25) ·
  [Type-check configuration](../../pyproject.toml#L36)
- [x] **Reconcile the unresolved-footage sample test with overview selection.**
  The test verifies that the overview picks the first available non-reference
  path and skips it when the video is marked missing.
  [Overview query](../../review_app/backend/provider/stats_service.py#L167) ·
  [Overview test](../../tests/test_project_and_import.py#L706)

### Operator verification

- [ ] Validate Distance timing, multiple individuals, and carried infants with
  real footage. [Distance validation](reference-based-distance-annotation.md#remaining-work)
- [ ] Walk through the Import / Export UI and run a Species and Distance package
  round trip to close M7. [Handoff checkpoint](assignment-handoff-implementation-plan.md#current-checkpoint) ·
  [Round-trip guide](assignment-handoff-ui-roundtrip.md)
- [ ] Exercise folder-based deployment setup, folder and clip assignment, rescans, and
  protection of existing evidence in the UI. [Deployment checklist](deployment-management.md#implemented-scope)

---

## Queue & Filtering

### Fix "not annotated / review later" filter - done

The queue in `video_queue.py` computes a derived `is_annotated` flag via SQL CTE, and `review_later` is a column on `VideoLabel`. The bug likely involves an interaction between these two — e.g. a "review later" video is being counted as annotated (because a `VideoLabel` row exists), causing it to vanish from the "not annotated" queue. Fix is a targeted SQL logic change.
**Effort: S**

### Don't disappear annotated videos from the "not annotated" queue - done

Right now the queue is rebuilt after every submission, which removes the just-annotated video from a "not annotated" filter immediately. The request is to keep the current video in place even after saving — only update queue position when explicitly moving to the next video. Requires decoupling queue refresh from annotation save.
**Effort: M**

### Sort queue by camera and video date/time

The `Video` model already has `recorded_at` and `camera_id`. Sorting by camera exists, but date/time sort within camera may be missing. Would add a new sort option to the queue SQL `ORDER BY` clause and expose it in the filter UI.
**Effort: S**

---

## Video Playback

### No mute as default - done

The `autoplay/muted` state is stored in NiceGUI app storage and honored on video load. The default is currently muted (safe browser behavior). Changing this is a one-line default value change, though browsers may block unmuted autoplay — may need a user click to unmute on first load.
**Effort: XS**

### Limit speed to max 2x - done

The player has a speed dropdown with 15 options up to 10x (defined in `video_player.py`). Just remove values above 2x from that list. Zero risk of side effects.
**Effort: XS**

---

## Annotation UI

### Fix species selector width

The species selector re-renders on selection change, causing the dropdown to resize based on content. Needs a fixed `min-width` CSS rule applied to the NiceGUI select component. Likely a one-liner in `annotations.py`.
**Effort: XS–S**

### Fix "Mark as empty and next" button overflow in French — superseded

The review form now uses a shared, full-width Submit and next button for
empty and annotated clips.
[Review submit control](../../review_app/app/pages/review/annotations.py#L481)

### Add multiple individuals per observation (e.g. "5 chimps seen") — done

Species Review has a count control per observation. The database stores the
count, and the annotation CSV round-trip test verifies that export and import
preserve it.
[Review count control](../../review_app/app/pages/review/annotations.py#L271) ·
[CSV round-trip test](../../tests/test_project_and_import.py#L539)

### Comment box per video

No free-text notes field exists on `Video` or `VideoLabel`. Needs a DB migration (add `notes TEXT` column to `VideoLabel`), a textarea in the review UI, and a new export column. Straightforward but touches multiple layers.
**Effort: M**

### Video tags (fire, nice shot, broken metadata, custom) — done

Review supports per-video tags, and Settings supports creating custom tags.
[Tag design](video-tags.md) ·
[Review tag selector](../../review_app/app/pages/review/tags.py#L230)

### "Are you sure?" popup when creating a new tag

When a user creates a custom behavior/species from the annotation UI, it's immediately persisted. Add a NiceGUI confirmation dialog before committing. Low-risk UI change.
**Effort: S**

---

## Data & Export

### Assignment workflow parity follow-ups — implemented

Compared against `main` at `eb808e5`; implementation starts from `46959cb`.

- [x] Add **Append** to Species assignment-return acceptance. Add new observations
  and update matching observation UUIDs, never delete observations absent from the
  return, merge tags, and preserve existing video-level labels on conflict. Keep
  preview, stale-preview protection, and atomic acceptance. Distance remains
  whole-task Keep existing / Use imported only. Existing CSV append stays intact.
- [x] Restore per-recipient planning/review information: camera count, video count,
  total footage hours, and assigned camera/chunk labels. Show frozen workload
  information in saved-assignment details where available.
- [x] Restore live footage-copy progress: attempted/total files and a progress bar,
  including meaningful feedback while a large individual delivery is running.
- [x] Add a compact **Included in this package** summary for assignment setup and
  legacy bundle opening. Explain vocabulary, predictions, existing human work,
  and Distance task/reference dependencies without foregrounding technical IDs.
- [x] Reconcile packaged recipient names with local annotator names when opening
  assignments. Preserve portable assignment identity and historical attribution;
  make the current user/recipient distinction explicit and ensure local assignment
  filtering works after reconciliation.
- [x] Update English and French user documentation, UI help, and relevant workflow
  decisions to match the actual Import / Export navigation, delivery controls,
  package types, append semantics, and name reconciliation. Do not describe a
  browser setup-package download that is not present.

Scope: these follow-ups do not restore the old standalone allocation Apply/Reset
workflow or add browser setup-package download. Keep implementation and validation
focused on the requested behaviors. Mark items complete only after integration
and focused validation.

Validation: focused handoff/append/workload/translation tests, simulated UI checks,
Ruff lint/format, and the strict English/French documentation build. Copy-progress
checks pause the backend to assert an intermediate value and retry reset. Real
browser/media visual confirmation remains a manual check.

### Figure out a shared import interface

Define a shared import interface and workflow across the supported import types.

### Export: change model blank/non-blank column to raw probability — superseded

AI results export one row per model annotation, with separate class
(`value_text`) and raw `probability` columns.
[Model CSV export](../../review_app/backend/provider/import_service/_model_csv.py#L575)

### Remove timestamps from behavior export

The export includes `labeled_at` per observation. Either drop the column entirely or make it a filter option. One-line change to the `SELECT` / column list in the export SQL.
**Effort: XS**

### Import historical manual annotations — done

The annotation import accepts custom-format historical CSVs with column and
species mapping, validation, and Append or Override.
[Historical import UI](../../review_app/app/pages/data_assignments/imports/_annotations_tab.py#L99) ·
[Historical import tests](../../tests/test_historic_import.py)

### Import/export multiple annotator files, merge, re-export

Currently export is per-project and import is append/override per-video. Supporting multiple annotator CSVs requires: a merge strategy for conflicting labels (who wins? majority vote? flag disagreement?), UI to upload multiple files, and a combined export. This is the most complex item on the list — it's essentially a new data pipeline.
**Effort: XL**

---

## Video Management

### Add video metadata display

FFprobe already runs at sync time and stores `duration_sec`, `camera_id`, `recorded_at`. "More metadata" likely means resolution, codec, framerate — these could be stored on `Video` and displayed in a details panel. DB migration + sync logic change + UI display.
**Effort: M**

### More testing on adding/removing videos and subfolders

`sync_videos()` walks `ProjectDir` entries and reconciles with existing `Video` records. Edge cases: symlinks, subfolders added mid-session, videos moved between cameras. This is a testing/QA task more than a code task — but might surface bugs that need fixing.
**Effort: S** (as code task, M if bugs surface)

---

## Effort summary

| Effort | Items |
|--------|-------|
| XS | Mute default, speed cap, button overflow, remove timestamps |
| S | Filter bug, species width, confirm popup, sort by date, export probability, sync testing |
| M | Queue refresh behavior, comment box, video metadata, historical import |
| L | Multiple individuals, video tags |
| XL | Multi-annotator merge pipeline |
