# Data & assignments UI implementation plan

**Status:** UI implementation in place; operator walkthrough pending.
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
tab; preparation/allocation are secondary. Results use a dataset list alongside
its details and action, with optional controls collapsed. The sidebar-workspace
and action-led alternatives were not selected. Prototype code was removed at the
operator's request; implement this layout using production-quality existing UI.

**Next action:** Operator check of `/data-assignments` in the actual app, especially
Add data, saved assignment actions and the results dataset list. No UI smoke tests
or browser automation were run, as requested. Remaining presentation adjustments
should follow that feedback rather than expand backend scope.

| Slice | Status | Completion gate |
| --- | --- | --- |
| S1 — Destination and results | Implemented; operator check pending | Main navigation reaches Data & assignments; existing exports have their own area rather than living in import screens. |
| S2 — Assignments | Implemented; operator check pending | Saved assignments/actions lead the workspace; existing allocation, prepare, copy, return and acceptance remain usable. |
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

1. Lead with saved assignments and download/copy/return actions. Keep stage,
   recipient, target species, scope/count and date visible using existing data.
2. Put existing preparation and allocation controls in secondary sections. Keep
   current single-assignment operations; persistent batch records and batch
   history are not part of this slice.
3. Simplify unnecessary selection steps using existing complete/all/allocated
   defaults. The Distance selection already loads a matching set: keep it
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

- No schema migrations or new persistent batch/allocation models are planned.
- No archive/CSV/TSV changes, new transfer policies or silent eligibility changes.
- Preserve frozen assignment scope, identity, provenance and existing safeguards.
- Prefer moving/reusing renderers, then extracting small repeated UI helpers.
- A small directly necessary backend adjustment is acceptable; if it materially
  changes functionality or storage, defer it and discuss separately.
- No full-suite baseline for a UI slice. Run focused tests for touched behavior,
  applicable existing backend regressions, Ruff checks for changed Python files
  and `git diff --check`. Broaden validation only if integration risk warrants it.

## Implementation checkpoint

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
