# Data & assignments UI implementation plan

**Status:** Unified assignment-workspace overhaul implemented; operator visual
walkthrough pending.
**Direction:** [Data, assignments, and results: UX rework](data-and-assignments-ux.md).
**Scope:** Reorganize and simplify the existing UI. Preserve functionality and
transfer contracts rather than build a new batch or transfer subsystem.

This plan replaces the earlier feature-heavy draft. Persistent batches, new
allocation storage, advanced batch ZIP/copy delivery, durable retries, export
schema changes and new import policies are not required.

## Checkpoint

**UI decision:** Operator selected prototype **Variant A — Three clear tabs**.
Use Add data / Assignments / Export results as top-level tabs within the existing
app shell, with a main content panel below. Saved assignments lead the Assignments
tab; the follow-up below replaces the separate preparation/allocation sections.
Results use a dataset list alongside its details and action, with optional controls
collapsed. The sidebar-workspace
and action-led alternatives were not selected. Prototype code was removed at the
operator's request; implement this layout using production-quality existing UI.

**Assignment feedback and agreed follow-up:** The initial accordion layout is still
too crowded. Replace it with a compact saved-assignment list and detail panel,
a primary **Assign work** flow, and a separate **Accept returned work** flow.
Assign work combines stage selection, allocation to recipients, scope/count review,
and preparation. Do not preserve a separate allocation screen or a save-allocation /
choose-allocated-work / prepare sequence. Species uses the existing distribution
controls; Distance groups matching videos by target species and recipient. Multiple
rows may invoke existing preparation individually, without persistent batch state.
Editable planning ends at preparation; saved assignments remain frozen.

**Checkpoint:** `7c35ede` records the initial UI reorganization before this follow-up.
The assignment overhaul is now implemented in the working tree. Focused NiceGUI
user-simulation tests were run without launching a browser; no broad UI smoke suite
or browser automation was run. Visual confirmation of the assignment layout,
Add data and results remains the operator's next action.

| Slice | Status | Completion gate |
| --- | --- | --- |
| S1 — Destination and results | Implemented; operator check pending | Main navigation reaches Data & assignments; existing exports have their own area rather than living in import screens. |
| S2 — Assignments | Unified flow implemented; visual check pending | Compact saved list/details; unified assign-and-prepare flow; separate return acceptance; existing transfer safeguards preserved. |
| S3 — Add data | Implemented; operator check pending | Existing imports are organized by intent, with clearer presentation and unchanged format-specific behavior. |
| S4 — Cleanup and contextual entry points | Implemented; walkthrough pending | Superseded forms are removed, shortcuts reuse canonical flows, translations/help and focused UI regressions are updated. |

Record changed paths, focused validation and remaining gaps as each slice lands.
The existing handoff plan's M1–M6 remain complete; this rework includes its
outstanding M7 UI refinement, not a replacement implementation of handoff.

## Existing code to reuse

Paths are relative to `review_app/`.

| Existing home | UI work |
| --- | --- |
| `app/entry_point.py` | Add the canonical destination; update desktop/mobile navigation and old-route adapters. |
| `app/pages/data_assignments/imports/_annotations_tab.py` | Separate Species/AI result download controls from annotation import. |
| `app/pages/data_assignments/imports/_distance_manual_tab.py` | Separate Distance analysis/editable exports from manual restoration. |
| `app/pages/data_assignments/footage_export.py` | Move species-selected footage copying into results. |
| `app/components/assignment_handoff.py` | Separate saved-assignment actions, preparation and return acceptance into clearer sections without replacing their operations. |
| `app/pages/data_assignments/assignments.py` | Reuse allocation controls; move legacy CSV intake into Existing annotations. |
| `app/pages/data_assignments/imports/` | Reuse import renderers, mapping and validation under intent-based headings. |
| `app/pages/settings/page.py` | Remove embedded transfer duplicates; retain configuration, vocabulary, recovery and logs. |
| `app/pages/overview.py`, `app/setup_wizard.py`, `app/project_picker.py` | Update links and optional assignment shortcuts; retain existing package opening. |

Backend operations in `backend/provider/assignment_handoff.py`,
`backend/provider/import_service/`, `backend/provider/distance_transfer.py` and
saved-scope delivery remain the implementation. UI callers should not duplicate
their domain rules.

## S1 — Consolidated destination and existing results

1. Add `app/pages/data_assignments/` with **Add data**, **Assignments** and
   **Export results** areas and a canonical `/data-assignments` route.
2. Switch the main desktop/mobile entry from Import / Export to the new
   destination. Initially reuse the existing import/assignment renderers in their
   areas; do not leave a second independently implemented transfer page.
3. Move Species CSV, AI CSV, Distance TSV/editable export and species-selected
   footage controls into Export results. Extract rendering functions as needed;
   keep current provider calls, parameters, output formats and eligibility.
4. Use clear dataset descriptions, scope labels, download/copy wording and
   existing counts. Put optional controls under Advanced options where useful;
   keep required target-species choices visible.
5. Remove the corresponding export controls from import screens and Settings as
   their replacements land. Add English/French strings and focused UI tests.

**Validation:** Relocated exports call the same operations with the correct
project/scope; existing Species CSV and Distance TSV regression tests still pass.
No new columns, dataset collector or shared filtering framework is needed.

## S2 — Clearer assignment workspace

1. Lead with a compact saved-assignment list. Keep stage, recipient, target species,
   scope/count and readable date visible; show download/copy and technical details
   in a detail panel rather than repeated full-width blocks.
2. Combine allocation and preparation in a focused **Assign work** flow. Reuse
   Species distribution controls and Distance matching selection, with recipient
   and count per row. Prepare through current single-assignment operations and
   show individual outcomes; no persistent batch records or batch history.
3. Simplify unnecessary selection steps using existing allocation/matching-set
   defaults; do not expose an all-project-videos bypass. The Distance selection already loads a matching set: keep it
   selected by default and make inspection/narrowing secondary where safe.
   Do not build new eligibility rules or allocation storage to achieve this.
4. Keep return acceptance distinct from preparation, with existing policy,
   replacement/removal warnings, preview and stale-preview handling intact.
5. Reuse saved-scope footage delivery and individual downloads. Preserve visible
   missing/failed-file outcomes; no new batch ZIP or copy/retry system is needed.

**Validation:** Existing prepare/open/return/accept and saved-scope copy tests,
plus focused UI coverage for the new hierarchy. Verify partial/repeated returns
and stage isolation remain unchanged.

## S3 — Intent-based Add data

1. Group existing workflows under **AI predictions**, **Video metadata** and
   **Existing annotations**. Distinguish Species/Distance choices within the
   relevant group rather than exposing file formats as the main intentions.
2. Reuse existing upload/configuration, path/column/species mapping, preview/apply
   and result behavior. Share small layout/busy/error helpers only where multiple
   flows actually need the same presentation.
3. Make destination, consequences and limitations explicit. Keep AI provenance,
   replacement protections, manual Distance restrictions and current per-task
   restoration choices; no upload-wide policy redesign is required.
4. Relocate legacy batch CSV intake under Existing annotations and label its
   import-on-upload behavior. Do not accidentally present it as a read-only
   preview or an assignment return.
5. Link vocabulary configuration back to Settings. Delete superseded import forms
   rather than maintain two implementations.

**Validation:** Existing mapping/import regressions and focused UI tests. Check
project targeting, async busy/error state, explicit confirmation where supported,
and preview invalidation where present. Do not change mutation/atomicity rules
just to make all import screens identical.

## S4 — Contextual actions and final cutover

1. Point old `/model-import` and `/distribution` URLs to the corresponding new
   areas. Remove Settings' duplicate transfer renderers while retaining useful links.
2. Add or update Overview/review shortcuts to these same workflows. An Overview
   Return progress action should reuse the existing return operation, not define
   a second export path. Say “return file downloaded,” not remotely delivered.
3. Keep opening packages in the project picker with the existing recognition and
   media-location flow. Expanded recognition is not a prerequisite.
4. Update onboarding/setup links, relevant user docs and English/French labels;
   remove unused renderers/imports and obsolete navigation labels.
5. Walk through coordinator, annotator and independent-use flows to check that
   the reorganization is genuinely easier to navigate.

**Validation:** Focused route, shortcut, translation and UI regressions; verify
backup/recovery/logs remain in Settings. Record operator/browser checks separately
from automated NiceGUI tests.

## Implementation guardrails

- The agreed removal action adds only migration v27's nullable removal timestamp.
  Retain the original snapshot and identity maps for future returns. No new
  persistent batch/allocation models are planned.
- No archive/CSV/TSV changes, new transfer policies or silent eligibility changes.
- Preserve frozen assignment scope, identity, provenance and existing safeguards.
- Prefer moving/reusing renderers, then extracting small repeated UI helpers.
- A small directly necessary backend adjustment is acceptable; if it materially
  changes functionality or storage, defer it and discuss separately.
- No full-suite baseline for a UI slice. Run focused tests for touched behavior,
  applicable existing backend regressions, Ruff checks for changed Python files
  and `git diff --check`. Broaden validation only if integration risk warrants it.

## Initial implementation checkpoint (`7c35ede`)

- Added `app/pages/data_assignments/` with Variant A's three top-level NiceGUI
  tabs. Results use a dataset list and detail panel; imports retain their existing
  format-specific controls under intent-based headings. Tab panels keep their
  in-page state when switching areas.
- Reused existing CSV, manual Distance TSV/ZIP, footage-copy and handoff provider
  operations. No backend/schema/format changes or custom stylesheet were added.
  Optional Distance time filters and scope inspection are collapsed, with the
  supported matching tasks selected by default.
- Saved assignment actions now precede preparation/acceptance/allocation.
  Overview links to the same return workflow; returned-file download wording does
  not imply delivery or acceptance. Working assignment screens do not show
  coordinator preparation/acceptance controls; ordinary review remains unrestricted.
- Import controls/mapping helpers now live in `app/pages/data_assignments/imports/`;
  allocation and assignment-page composition live in `app/pages/data_assignments/assignments.py`.
  The old `model_import/` and `distribution/` page packages are removed. Old URLs
  are simple redirects in `app/entry_point.py`, not separate page implementations.
  Consolidation validation: 37 import-helper/species-mapping tests passed, 14 app
  modules imported successfully, and Ruff/diff checks passed. No UI tests run.
- Old Import / Export and Distribution routes redirect to the matching area.
  Settings retains configuration, vocabulary, recovery and logs, with a link
  instead of embedded duplicate transfer forms. Legacy immediate-on-upload CSV
  intake moved into Existing annotations with an explicit warning.
- English/French strings, onboarding link text and relevant user documentation
  updated. Existing UI test fixtures/route expectations were adjusted for moved
  controls; those tests were not executed.
- Recorded regression command:
  `uv run pytest tests/test_queue_and_export.py tests/test_assignments_import_export.py tests/test_assignment_handoff.py tests/test_assignment_delivery.py tests/test_assignment_distance.py tests/test_distance_transfer.py tests/test_translations.py -k 'not ui'`
  — **138 passed, 8 deselected**. Full suite and UI smoke tests not run.
- Validation also includes Ruff lint/format on changed Python files, module import
  verification and `git diff --check`. Visual/layout confirmation remains the
  operator's check, not an automated claim.

## Unified assignment-workspace checkpoint (initial overhaul)

- `app/pages/data_assignments/assignments.py` now renders the compact saved list
  and responsive detail panel. Download, separate footage copy, saved-scope
  inspection and collapsed technical details live in the selected assignment.
  Working projects show Continue review / Return progress without coordinator forms.
- `assignment_creation.py` combines editable allocation, scope review and
  preparation in one focused flow. Species uses existing distribution and manual
  group adjustment, preserving split-camera allocations and labelled/missing/
  cameraless media in the initial draft. Automatic distribution retains its
  existing unlabelled-camera eligibility, explicitly explained in the UI.
  The all-project-videos alternative remains available.
- `assignment_plan.py` keeps exact Species selections and immutable review
  requests separate from controls. Allocation is applied during preparation,
  including explicitly unallocated work; no separate Apply step is exposed.
  These existing allocation writes and assignment preparation are not one atomic
  transaction. A preparation error can leave allocation applied, but cannot
  change an earlier saved assignment.
- Distance rows combine target species, recipient, matching count and optional
  narrowing/time filters. Selection reloads automatically, with revision checks
  against stale responses. Each species produces an independent saved assignment.
- Preparation records per-row success/failure in this UI session and retries only
  unsuccessful rows. Successful single-row preparation opens delivery details.
  No persistent batch state, schema migration, archive change or new algorithm.
- `app/components/assignment_handoff.py` retains shared package opening and
  footage delivery, and extracts existing return preview/accept into its own
  focused screen. Existing replacement/removal warnings and stale-preview checks
  remain intact. The old render entry delegates to the canonical workspace.
- The only new provider read, `get_assignment_media(project_id, assignment_id)`,
  describes frozen assigned/dependency footage and validates project ownership;
  it does not recompute scope from live allocation.
- English/French labels, user help and the manual round-trip guide reflect the flow.
- Combined handoff/distribution regression run: **129 passed** across
  `test_assignment_planning.py`, `test_assignment_handoff_ui.py`,
  `test_assignment_distance.py`, `test_translations.py`,
  `test_assignment_handoff.py`, `test_assignment_handoff_roundtrip.py`,
  `test_assignment_delivery.py`, `test_distribution_bundle.py` and
  `test_assignments_import_export.py`.
- After the explicit-unallocation adjustment and added regression, the latest
  focused run of planning, simulated-user handoff, Distance and translation tests
  passed **54 tests**. Reused the earlier unaffected regression results rather
  than repeating the broad run. Ruff lint/format and whitespace checks passed.
  No full suite, broad UI smoke suite or browser automation run. Operator visual
  confirmation, including narrow-screen layout, remains pending.

## Operator-feedback follow-up

- **Remove assignment** is confirmed removal from delivery, not revocation or
  physical deletion. The nullable `removed_at` field (migration v27) hides the
  origin assignment and blocks new setup downloads/copies. Original snapshots
  and identity maps remain so future/repeated Species and Distance returns still
  validate and accept. Existing annotations, projects, footage and exported files
  are untouched. This deliberately does not free the retained snapshot's storage.
- **Save delivery folder** uses the original assignment ZIP with an optional
  footage checkbox, off by default. It creates one new folder containing
  `assignment.zip` and, when requested, the same saved-scope footage/root layout.
  Package formats are unchanged. Browser ZIP download remains secondary.
- Removed the all-project-videos shortcut. Species preparation always uses the
  displayed allocation; manual allocation retains access to annotated/missing/
  cameraless work without inheriting review-queue exclusions.
- Compact Distance rows expose species, annotator, count and options; video/time
  controls stay hidden until requested. Review groups rows by annotator while
  preparing separate per-species assignments and retaining per-row retry outcomes.
- `components/assignment_video_table.py` provides sortable headings, search and
  relevant camera/availability/role/action filters in Distance selection, saved
  media and return-impact details. Filters only inspect; hidden selection is
  retained, with explicit select/deselect-matching actions.
- Focused workflow/translation checks: **58 passed**. Migration, schema parity
  and legacy Distance upgrade checks: **31 passed**. Expanded handoff round-trip
  and footage-delivery regression results were reused from the integration run.
  The v22 schema fixture is deliberately retained because legacy upgrade tests
  depend on its original shape; the populated-v26 removal-state upgrade is also
  tested directly.
- English/French help, glossary and handoff contract updated. No full suite,
  broad UI smoke suite or browser automation run; visual confirmation is pending.
