# Assignment handoff implementation plan

**Status:** M1–M6 complete. M7 UI rework implemented; operator walkthrough pending.
**Direction:** Explicit serialized assignment snapshots, not database snapshots.
**Product contract:** [Assignment handoff](./assignment-handoff.md).

**UX planning update:** The [Data & assignments UI implementation plan](./data-and-assignments-ux-implementation-plan.md)
now sequences navigation and presentation changes, including the outstanding M7
refinement below. This is a UI rework preserving existing functionality, not new
persistent batches or advanced delivery infrastructure. M1–M6 and their recorded
validation remain complete; M7 has not been marked complete.

## Milestone tracker

This section is the implementation source of truth. Update it with each completed
slice, not only at the end of a milestone. Git commits record the code changes;
this tracker records readiness, validation, and the next action.

| Milestone | Status | Completion gate |
| --- | --- | --- |
| M1 — Persistence, identity, archive | Done | Immutable preparation/download; portable graph mapping; fresh/upgrade schema parity; archive validation tests. |
| M2 — Species backend round trip | Done | Prepare/open/return/preview/accept across instances and same-instance projects; both policies, empty states, import-scope validation, scoped media sync, unrestricted local annotation, stale previews, and rollback tested. |
| M3 — Species UI and delivery | Done | Usable prepare/open/return/accept flow; package and footage copy use saved scope; focused UI and multi-root copy tests pass. |
| M4 — Reference identity | Done | Video/distance uniqueness on fresh and upgraded databases; dependent samples preserved; reference write paths and affected legacy imports tested. |
| M5 — Complete Distance backend | Done | AI/manual state and reference dependencies round-trip; saved-scope import eligibility and reference substitution tested, without restricting local annotation. |
| M6 — Distance UI and integration | Done | Distance handoff usable end to end; translations/docs updated; combined workflow and relevant legacy regressions pass. |
| M7 — Final UI/UX refinement | In progress | Species and Distance handoff screens are clear and compact across origin/working roles; prepare/open/deliver/return/accept actions are discoverable; operator walkthrough and focused UI regressions pass; translations/docs reflect the final UI. |

Use `Not started`, `In progress`, `Blocked`, or `Done`. Mark a milestone Done only
when its completion gate is met. If blocked, record the concrete blocker below.

### Current checkpoint

- **Current milestone:** M7 — Final UI/UX refinement (implemented; operator check pending).
- **Next action:** Operator walkthrough of the actual NiceGUI Data & assignments
  destination. The [UI implementation plan](./data-and-assignments-ux-implementation-plan.md)
  records the implementation and 138 passing focused non-UI regressions. UI smoke
  tests/browser automation were not run, as requested; M7 remains In progress.
  Functional Distance preparation, delivery, common package opening, return
  preview, substitutions, and acceptance are implemented and tested. The manual
  round-trip guide includes Distance checks; real browser/playback validation
  has not been performed by the coding agent. Keep legacy manual v1 separate.
- **Blockers:** None identified.
- **Recorded validation:** M6: 342 affected tests passed, including combined
  Species/Distance handoff, common opening/delivery, media sync, AI/manual legacy
  transfer, reference, translation, and UI regressions. Ruff lint/format (7 changed
  Python files) and diff checks passed. Full suite not run.
  M5 pre-commit revalidation: 268 affected tests passed,
  including the Distance assignment cases and reference-deletion follow-up;
  Ruff lint/format (13 files) and diff checks passed. Full suite not run.
  Reference-deletion follow-up: operator confirmed reopening clears
  `completion_outcome`; corrected the test expectation, and all 3 reference-deletion
  tests passed with Ruff lint/format and diff checks. M4 historically recorded
  580 affected tests passed; one pre-existing reference-deletion assertion failure
  reproduced on clean pre-M4 commit
  `4fe5c86` (details below). Ruff lint/format (12 files) and diff checks passed.
  M1 baseline: 115 focused tests passed. M2 completion:
  350 focused handoff, schema, queue, transfer/import, video, path relocation,
  provider, and review-equivalence tests passed. Ruff check, Ruff format check
  (10 files), and `git diff --check` passed. M3 first UI slice: 116 focused tests
  passed; Ruff lint/format (7 files) and `git diff --check` passed. Full suite not run.
- **Completed implementation slices:** M1 complete. M2 now includes fresh opening,
  listed-media-only sync, unrestricted ordinary Species/Distance annotation and
  imports, full-scope return export, preview/acceptance, stale-preview detection,
  and rollback tests. Tests asserting assignment write/stage guards were replaced
  with listed-media sync and ordinary local-write regressions. Completion checks
  additionally prove identical returns perform no annotation writes under either
  policy, skipped work imports no new dependencies, applied snapshots preserve
  saved evidence, and Species working projects can create/edit Distance tasks.

For each completed slice, append a short entry here with milestone ID, behavior
implemented, relevant paths, exact validation command/result, and commit hash
when committed. Update the next action and any remaining gaps at the same time.
Do not rely on conversation history to reconstruct progress. Record substantive
design deviations in this plan before continuing with a changed approach.

### M6 completion — Distance UI and integration

Implemented:

- `review_app/app/components/assignment_handoff.py`: one stage selector in the
  existing Distribution handoff controls. Distance chooses a confirmed target
  scientific species and loads eligible videos, with all initially selected and
  an editable subset. Optional recurring camera-local start/end filtering supports
  overnight windows and reports excluded unknown-time tasks. Changing filters
  clears loaded selection; superseded loads cannot repopulate it. Preparation
  revalidates current eligibility and never silently drops selected tasks.
- `annotation_package/scope.py` and provider `assignment_handoff.py`: one shared
  Distance eligibility implementation for selection and preparation, including
  AI-linked and manual work, known-missing media, and exclusion of reference work.
  This selection neither reads nor changes Species distribution. Saved bindings
  and setup inspection expose stage and target species.
- Return preview carries structured stage/target/context-change metadata and
  reference substitutions with footage identity and precise playback positions.
  The UI localizes these details and warns that existing admin position/mask is
  reused while returned sample distances are retained. Existing overwrite,
  removal, skipped-progress, file/policy invalidation, and stale-preview behavior
  applies to both stages. Changed Species context does not block saved-scope
  acceptance or restore labels. Ordinary local review remains unrestricted.
- The shared saved download/copy and format-detecting Open package flow now
  serves Distance end to end. Integration testing exposed that working-project
  sync used only `work_keys`, omitting dependency footage. Updated provider
  `video.py` to use the immutable listed `scope.media` identities for scanning
  and rechecking. Listed reference footage is now probed, marked missing, and
  rechecked; unrelated files are still never discovered. This corrects the
  existing listed-media contract, not a new scope rule.
- English/French translations and `userdocs/settings.md` /
  `userdocs/settings.fr.md` document both workflows, timing, reference reuse,
  changed context, unrestricted local editing, and legacy-format distinctions.
  `assignment-handoff-ui-roundtrip.md` now includes the Distance operator checklist.
- `tests/test_assignment_distance.py`: seven additional selection/UI cases and
  extended dependency-sync regression. Real database/UI interactions cover
  preparation, saved delivery, common package opening and probing, AI/manual
  eligibility, subset selection, time filtering and invalidation, post-selection
  eligibility revalidation, both conflict policies, reference substitution,
  stale calibration previews, acceptance, repeated identical returns, and empty
  replacement warnings with omitted-task preservation.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_distance.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_assignment_handoff_ui.py tests/test_assignment_delivery.py tests/test_open_package_ui.py tests/test_video_sync.py tests/test_distance_ai_import.py tests/test_distance_transfer.py tests/test_reference_identity.py tests/test_distance_repository.py tests/test_reference_frame_deletion.py tests/test_translations.py tests/test_ui_smoke.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py tests/test_onboarding.py tests/test_update_project_dir.py
# 342 passed in 44.68s.
.venv/bin/ruff check review_app/app/components/assignment_handoff.py review_app/app/translations.py review_app/backend/annotation_package/scope.py review_app/backend/annotation_package/distance_handoff.py review_app/backend/provider/assignment_handoff.py review_app/backend/provider/video.py tests/test_assignment_distance.py
.venv/bin/ruff format --check review_app/app/components/assignment_handoff.py review_app/app/translations.py review_app/backend/annotation_package/scope.py review_app/backend/annotation_package/distance_handoff.py review_app/backend/provider/assignment_handoff.py review_app/backend/provider/video.py tests/test_assignment_distance.py
# Passed; 7 files already formatted.
git diff --check
# Passed.
```

Full suite and real-browser/playback walkthrough not run. Functional M6 is complete;
final visual refinement and operator walkthrough remain M7. Legacy manual Distance
v1 and legacy setup/CSV semantics are unchanged. Included in the M6 checkpoint
commit; Git records its identity.

### M6 operator feedback — explain reference footage in copy totals

The operator observed 78 assigned videos but 106 copied files. The saved header
now explicitly says **assigned videos**. Distance copy controls and package-opening
details show the immutable saved breakdown: assigned videos + reference videos =
total footage files, with an explanation that copy results count both and references
are dependencies, not additional delegated annotation work. Counts come from the
saved media list, not current distribution or folder contents. English/French UI
and user docs updated. The real Distance UI round trip asserts the two assigned /
one reference / three total breakdown at delivery and opening.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_distance.py tests/test_assignment_handoff_ui.py tests/test_assignment_delivery.py tests/test_open_package_ui.py tests/test_translations.py
# 58 passed in 14.59s.
.venv/bin/ruff check review_app/backend/provider/assignment_handoff.py review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_assignment_distance.py
.venv/bin/ruff format --check review_app/backend/provider/assignment_handoff.py review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_assignment_distance.py
git diff --check
# Passed.
```

Included in the M6 checkpoint commit. Pre-commit revalidation: the same 58
focused tests passed in 13.53s; Ruff lint/format passed for all 7 changed Python
files, and `git diff --check` passed. M7 visual refinement and browser walkthrough
remain pending.

### M5 completion — complete Distance backend round trip

Implemented:

- `annotation_package/distance.py` carries task alignment, verified/imported
  schedules, started/completed/absent state and history, rejected AI evidence,
  individuals, anchors, first-seen playback positions, infant/carrier relationships,
  samples, behaviors, individual tags, attribution, timestamps, and resume progress.
  It is independent of the legacy manual Distance archive contract.
- `annotation_package/distance_handoff.py` integrates collection, portable identity,
  return comparison, policy-specific preview, and atomic acceptance behind the
  existing provider interface. Pristine task scaffolding is not existing work;
  saved zero-valued clock anchors are state. Identical work performs no annotation,
  reference, provenance, or identity-map writes under either policy.
- Distance preparation uses explicit videos and a target scientific species name,
  confirmed Species context, and optional camera-local `start_time` / `end_time`
  boundaries. Selected tasks outside a requested window, including unknown times,
  are rejected rather than silently dropped. Return export/acceptance uses saved
  scope, not current labels. Species edits warn without blocking acceptance or
  restoring labels; ordinary local editing remains unrestricted.
- Setup includes prepared calibration and discoverable reference footage for the
  selected deployments, even for untouched tasks. Registered-only reference clips
  become listed dependency media with discovery tags. Delivery includes those
  dependencies; opening/sync still does not recursively ingest unlisted files.
- Two people at different distances in the same video frame retain separate masks,
  precise positions, and evidence. Returns reuse existing admin video/distance
  calibration without overwriting it; preview exposes structured substitutions.
  A new distance may be inserted at an occupied timestamp, and saved sample
  distances remain the returned values rather than the calibration distance.
- AI sources are projected to selected work/dependencies, including pending
  suggestions and rejected originals. Original source identity/hash remain distinct
  from the projected payload hash. Embedded runtime IDs are remapped, unrelated
  source individuals/predictions/schedules are excluded, and sampling metadata uses
  portable video identity instead of source-machine absolute paths.
- Archive validation rejects invalid scope/ownership, wrong-species sources,
  dangling relationships, cross-task carriers/identity reuse, invalid sample
  states, and cross-deployment reference evidence. Preview fingerprints include
  current work and dependencies; acceptance replans under `BEGIN IMMEDIATE` and
  rolls back work, dependencies, and identity mappings together on failure.
- Return identity allocations are persisted transactionally so repeated exports
  use stable portable IDs for newly created local work.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_distance.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_assignment_handoff_ui.py tests/test_distance_ai_import.py tests/test_distance_transfer.py tests/test_reference_identity.py tests/test_distance_repository.py
# 265 passed in 21.38s (17 new Distance assignment cases).
.venv/bin/ruff check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py tests/test_assignment_distance.py
.venv/bin/ruff format --check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py tests/test_assignment_distance.py
git diff --check
# Passed.
```

New tests cover separate-database and repeated same-instance opening, complete
AI/manual evidence, same-frame calibration, reference discovery and listed-only
sync, saved-scope returns after Species edits, both policies, true no-ops, empty
replacement/omission, new work over pristine scaffolding, stale previews, invalid
returns, and late-failure rollback. Full suite not run. The pre-existing M4
reference-deletion failure is documented historically below; the subsequent
operator-approved test correction resolves it without changing production behavior.

Reference-deletion follow-up: the operator confirmed that reopening an affected
Distance task must clear `completion_outcome` as well as `completed_at` and
`completed_by`. Updated
`test_delete_mapping_clears_shared_evidence_and_reopens_affected_reviews` to assert
that contract explicitly and exclude all three completion fields from its
unchanged-field comparison. Production behavior already matches this decision.

```bash
.venv/bin/python -m pytest -q tests/test_reference_frame_deletion.py
# 3 passed in 0.52s.
.venv/bin/ruff check tests/test_reference_frame_deletion.py
.venv/bin/ruff format --check tests/test_reference_frame_deletion.py
git diff --check
# Passed.
```

M5 adds backend functionality only; Distance UI is M6. Legacy manual archive v1
still retains its frozen timestamp-uniqueness validation. Git records the commit
identity when these changes are committed.

Pre-commit revalidation at the operator's request to commit M5:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_distance.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_assignment_handoff_ui.py tests/test_distance_ai_import.py tests/test_distance_transfer.py tests/test_reference_identity.py tests/test_distance_repository.py tests/test_reference_frame_deletion.py
# 268 passed in 21.48s.
.venv/bin/ruff check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py tests/test_assignment_distance.py tests/test_reference_frame_deletion.py
.venv/bin/ruff format --check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py tests/test_assignment_distance.py tests/test_reference_frame_deletion.py
# Passed; 13 files already formatted.
git diff --check
# Passed. Full suite not run.
```

The M5 commit includes the approved reference-deletion expectation correction.
Next action remains M6; no Distance UI implementation is included.

### M4 completion — reference video/distance identity

Implemented:

- `review_app/backend/db/models.py`: reference-frame uniqueness is now
  `(reference_video_id, distance_m)`, not timestamp. Distinct distances can share
  a timestamp; duplicate distances cannot be inserted at another timestamp.
- Frozen migration v26 in `review_app/backend/db/migrations.py`: atomically
  rebuilds `reference_frames`, selecting the lexically smallest frame ID for each
  video/distance pair and remapping dependent `distance_samples` foreign keys.
  Sample IDs, distances, authors, timestamps, carried relationships, and behavior
  links are not rewritten. Keeper position, precise playback, mask, and import
  provenance survive. Historical import payloads remain unchanged evidence.
  Foreign-key enforcement is restored; failure rolls back DDL/data/version.
- `distance_references.py`: configuration uses a conflict-safe video/distance
  insert and reuses existing calibration without overwriting its position;
  explicit position edits and new distances may share timestamps. Frame ordering
  is deterministic at equal timestamps. Duplicate-distance UI text now describes
  the actual constraint in English/French; no UI layout work was added.
- AI import matches existing calibration by video/distance, permits distinct
  distances at a shared timestamp, rejects repeated video/distance definitions,
  and still blocks conflicting prepared positions without overwriting them.
- Manual import no longer assumes one frame at a timestamp. It reuses exact
  video/distance calibration or inserts a new distance at an occupied timestamp;
  differing position/mask calibration still conflicts. Repeated-distance legacy
  archives receive dependent-task preview errors rather than a late integrity
  failure. Manual v1's frozen timestamp-uniqueness validation is unchanged: it is
  not silently expanded to the future assignment snapshot contract.
- `tests/test_reference_identity.py` covers fresh/upgraded constraints,
  deterministic consolidation with sample/behavior preservation, repeat startup,
  and rollback. Repository/UI tests assert new timestamp-sharing semantics.
  AI/manual regression tests cover ambiguous timestamps, matching versus new
  distances, duplicate definitions, calibration conflicts, and unchanged v1
  validation. Unordered snapshot comparisons now sort by record identity rather
  than relying on SQLite's incidental row order after a rebuild.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_reference_identity.py tests/test_schema_parity.py tests/test_migrations.py tests/test_distance_schema_upgrade.py tests/test_distance_skip_schema.py tests/test_distance_repository.py tests/test_reference_on_demand.py tests/test_reference_preparation.py tests/test_reference_frame_deletion.py tests/test_reference_source_switch.py tests/test_reference_probe_cache.py tests/test_distance_ai_import.py tests/test_distance_transfer.py tests/test_distance_timing.py tests/test_distance_apply_to_end.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_translations.py
# 580 passed, 1 failed in 37.89s.
```

The sole failure is pre-existing:
`test_reference_frame_deletion.py::test_delete_mapping_clears_shared_evidence_and_reopens_affected_reviews`
expects `completion_outcome='present'` to survive reopening, while the existing
implementation clears it. Reproduced the identical failure by exporting clean
commit `4fe5c86` to a temporary checkout and running that test with the same Python
environment. Left this unrelated deletion-behavior/test mismatch unchanged.
The earlier targeted schema/repository/import batch passed all 228 tests; the
subsequently extended AI/manual batch passed all 82 tests.

An attempted broader `tests/test_distance*.py tests/test_reference*.py` run
(with schema, migration, handoff, and translation tests) hit the 120-second command
time limit without a reported failure. It is not a passing full-suite result.
The bounded affected regression run above completed; full suite not run.

```bash
.venv/bin/ruff check review_app/backend/db/models.py review_app/backend/db/migrations.py review_app/backend/provider/distance_references.py review_app/backend/provider/distance_transfer_import.py review_app/backend/provider/import_service/_distance_ai.py review_app/app/translations.py tests/test_reference_identity.py tests/test_distance_repository.py tests/test_reference_on_demand.py tests/test_reference_preparation.py tests/test_distance_ai_import.py tests/test_distance_transfer.py
.venv/bin/ruff format --check review_app/backend/db/models.py review_app/backend/db/migrations.py review_app/backend/provider/distance_references.py review_app/backend/provider/distance_transfer_import.py review_app/backend/provider/import_service/_distance_ai.py review_app/app/translations.py tests/test_reference_identity.py tests/test_distance_repository.py tests/test_reference_on_demand.py tests/test_reference_preparation.py tests/test_distance_ai_import.py tests/test_distance_transfer.py
git diff --check
# Passed.
```

M4 is complete; no M5 implementation is included. Commit identity is recorded by
Git. The known baseline failure is recorded above, not hidden by an expected-fail
marker or a changed deletion contract.

### M3 completion and operator sign-off

Operator accepted the Species workflow after the package-opening and automatic
sync fixes and requested proceeding to M4, with a commit first. M3 is now Done;
the functional completion gate is met. Crowded layouts and other cross-workflow
visual refinement are explicitly tracked in final M7, not M3 blockers.

This commit captures saved-scope multi-root delivery, replacement of legacy
assignment-facing delivery controls, unified Open package detection, compact
uploading, editable missing-footage checks, automatic opening sync with retry,
existing-unprobed sync support, and the previously requested Use imported default.
Individual slice records below preserve their validation and historical readiness
states. Commit identity is supplied by Git; no M4 code changes are included.

Combined pre-commit validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_delivery.py tests/test_open_package_ui.py tests/test_video_sync.py tests/test_assignment_handoff_ui.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_ui_smoke.py tests/test_translations.py tests/test_onboarding.py tests/test_local_data_provider.py tests/test_update_project_dir.py tests/test_video_tools.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py
# 154 passed in 14.56s.
```

Ruff lint and format checks passed for all 14 changed/new Python files.
`git diff --check` passed. Full suite not run.

### M3 first UI slice — ready for operator round-trip testing

Included in the Species backend/UI round-trip commit containing this record:

- `review_app/app/components/assignment_handoff.py`: prepare Species work from
  applied recipient distribution or all project videos (including labeled/missing
  videos); list/download frozen saved packages; show working binding and download
  complete saved-scope returns; upload, preview, and accept with one global policy.
  Impact tables identify videos and record/link counts; overwrites, removals, and
  unapplied progress have explicit warnings. File/policy changes invalidate
  confirmation; backend stale previews remain blocking. New-project opening
  validates setup, locates every media root, and reports missing footage.
- `review_app/app/pages/distribution/page.py`, `project_picker.py`, and
  `setup_wizard.py`: expose handoff controls in Distribution and fresh opening
  through the picker and setup wizard. Legacy import/export remains available,
  explicitly separate from assignment delivery. No stage editing locks added.
- `review_app/backend/provider/assignment_handoff.py`: small read interfaces for
  listing saved bindings, inspecting setup/root requirements, and selecting all
  project videos without inheriting Review's missing-footage filter.
- `review_app/app/translations.py`: English/French UI text.
- `tests/test_assignment_handoff_ui.py`: real-database UI prepare/download/open/
  return/preview/accept round trip; missing-footage selection/opening; policy/file
  invalidation; invalid ZIP and stale-preview rejection. Video edits use the
  ordinary provider API; browser playback is left to operator testing.
- [Manual test guide](./assignment-handoff-ui-roundtrip.md): same-machine and
  remote-instance testing instructions, both conflict policies, repeated returns,
  empty snapshots, and sync checks.

Remaining M3 work: saved-assignment footage copying, multi-root delivery tests,
final assignment-facing legacy-control replacement, and operator feedback. The
first UI slice deliberately permits separately delivered/located footage so the
round trip can be tested now. No claim that the existing legacy footage controls
copy a saved assignment; the UI explicitly explains their independence.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_handoff_ui.py tests/test_ui_smoke.py tests/test_translations.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_onboarding.py tests/test_app_utils.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py tests/test_update_project_dir.py
# 116 passed in 7.90s

.venv/bin/ruff check review_app/app/components/assignment_handoff.py review_app/app/pages/distribution/page.py review_app/app/project_picker.py review_app/app/setup_wizard.py review_app/app/translations.py review_app/backend/provider/assignment_handoff.py tests/test_assignment_handoff_ui.py
# All checks passed

.venv/bin/ruff format --check review_app/app/components/assignment_handoff.py review_app/app/pages/distribution/page.py review_app/app/project_picker.py review_app/app/setup_wizard.py review_app/app/translations.py review_app/backend/provider/assignment_handoff.py tests/test_assignment_handoff_ui.py
# 7 files already formatted

git diff --check
# Passed
```

### M3 operator feedback — recipient and compact preview

Included in the Species backend/UI round-trip commit containing this record:

- `review_app/app/components/assignment_handoff.py`: choose registered recipients
  from a dropdown (refresh names on focus after adding an annotator below).
  Setup and return filenames include a filesystem-safe recipient name and retain
  the assignment UUID. Policy changes automatically regenerate the preview;
  superseded previews cannot enable acceptance. The default impact is a compact
  summary plus applicable safety warnings, with the per-video table collapsed
  behind **Show per-video details**.
- English/French strings and the manual round-trip checklist updated.
- `tests/test_assignment_handoff_ui.py`: recipient selection, both actual download
  filenames, safe name formatting, collapsed details, and automatic policy-driven
  reclassification/acceptance without a second Preview click.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_handoff_ui.py tests/test_translations.py tests/test_ui_smoke.py
# 28 passed in 4.26s

.venv/bin/ruff check review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_assignment_handoff_ui.py
# All checks passed

.venv/bin/ruff format --check review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_assignment_handoff_ui.py
git diff --check
# Passed
```

M3 remained in progress after this slice; saved-scope copying is implemented below.

### M3 saved-scope footage delivery

- `review_app/backend/annotation_package/delivery.py`: resolve media from the
  immutable setup manifest and bound current local paths, then copy the resolved
  list with four workers. Destinations retain saved relative paths under distinct
  root-ID folders. Relocation is supported without rebuilding scope from mutable
  distribution or scanning folders. Missing files and per-file failures are
  explicit results; progress counts every attempt.
- Delivery creates a new `assignment-<UUID>` folder outside registered scanned
  roots and refuses existing delivery folders instead of overwriting them.
  This is an explicit retry limitation: use a different output folder for a new
  attempt after a partial delivery. No legacy copy semantics were changed.
- Provider exposes `copy_assignment_footage`; each saved origin assignment has
  English/French copy controls showing the output, root mapping, and failure
  details. Working bindings do not offer setup-footage delivery.
- `tests/test_assignment_delivery.py`: saved selection after distribution changes
  and added media, duplicate relative names across roots, copied-root opening,
  relocation, missing files, copy failures, progress, repeat-copy refusal, and
  source/output containment. UI tests exercise the real copy control.
- M3 remains in progress: operator round-trip feedback and final legacy-control
  replacement are pending. Not committed.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_delivery.py tests/test_assignment_handoff_ui.py tests/test_translations.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_ui_smoke.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py tests/test_update_project_dir.py
# 111 passed (including the relocated-source regression).

.venv/bin/ruff check review_app/backend/annotation_package/delivery.py review_app/backend/provider/assignment_handoff.py review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_assignment_delivery.py tests/test_assignment_handoff_ui.py
.venv/bin/ruff format --check review_app/backend/annotation_package/delivery.py review_app/backend/provider/assignment_handoff.py review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_assignment_delivery.py tests/test_assignment_handoff_ui.py
git diff --check
# Passed; full suite not run.
```

### M3 assignment-facing legacy delivery replacement

- `review_app/app/pages/distribution/page.py`: removed live-distribution bundle
  downloads and annotator-footage copy controls from Distribution. The saved
  handoff controls are now the only assignment delivery path on that page. Kept
  Work Distribution as an explicit preparation-selection input and kept CSV batch
  import independently available. Legacy provider APIs, setup import, manual
  Distance import, and analysis exports are unchanged.
- English/French explanatory text explicitly distinguishes legacy append-mode CSV
  import from scope-validated return preview/acceptance. Updated
  `userdocs/settings.md` and `userdocs/settings.fr.md` with the actual Species
  handoff, copy, fresh-open, and return workflow, including replacement warnings.
- `tests/test_assignment_handoff_ui.py`: full Distribution render regression
  verifies saved download/copy controls, absence of legacy delivery buttons, and
  real CSV upload restoring annotation without changing the saved binding.
- M3 automated implementation is complete; manual operator validation remains
  pending. This slice is not committed.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_delivery.py tests/test_assignment_handoff_ui.py tests/test_translations.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_ui_smoke.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py tests/test_update_project_dir.py
# 112 passed.

.venv/bin/ruff check review_app/app/pages/distribution/page.py review_app/app/translations.py tests/test_assignment_handoff_ui.py
.venv/bin/ruff format --check review_app/app/pages/distribution/page.py review_app/app/translations.py tests/test_assignment_handoff_ui.py
git diff --check
# Passed; full suite not run.
```

### M3 UX update — one format-detecting Open package entry

Operator-approved direction: users should not choose between legacy bundle import
and assignment opening before uploading a file. Preserve the format distinction
internally, not as competing top-level setup actions.

- Setup wizard now has Manual and Open package tabs; the project picker uses the
  same Open package flow. After upload, assignments show the existing linked
  working-project form; legacy bundles show an explicit independent-project /
  no-assignment-returns warning and the existing legacy importer. No legacy file
  is promoted into an authenticated assignment.
- `review_app/backend/annotation_package/detection.py` validates assignment setups,
  rejects returns, and identifies legacy bundle manifests. An archive declaring an
  assignment manifest never falls back to legacy interpretation if invalid.
- `review_app/app/setup_wizard.py` reuses its legacy creation, annotator mapping,
  sync, and import mechanics in a package-only rendering mode, also used by the
  picker. Shared upload limits apply. Replacing a file hides both previous forms;
  superseded detection cannot reveal an obsolete form.
- Assignment opening can be embedded without a second uploader. Updated
  English/French text and user/manual docs.
- `tests/test_open_package_ui.py`: actual assignment opening, legacy project
  creation with no binding, invalid replacement, single wizard tab/picker entry,
  and return/malformed-assignment rejection. Not committed.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_open_package_ui.py tests/test_assignment_delivery.py tests/test_assignment_handoff_ui.py tests/test_translations.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_ui_smoke.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py tests/test_update_project_dir.py tests/test_onboarding.py
# 119 passed.

.venv/bin/ruff check review_app/app/setup_wizard.py review_app/app/project_picker.py review_app/app/components/assignment_handoff.py review_app/app/translations.py review_app/backend/annotation_package/detection.py tests/test_open_package_ui.py
.venv/bin/ruff format --check review_app/app/setup_wizard.py review_app/app/project_picker.py review_app/app/components/assignment_handoff.py review_app/app/translations.py review_app/backend/annotation_package/detection.py tests/test_open_package_ui.py
git diff --check
# Passed; full suite not run.
```

### M3 package upload visual simplification

- Removed the introductory upload sentence from Open package. Replaced the
  visible full uploader panel with one outlined Choose package button backed by
  the same hidden single-file ZIP uploader, preserving size limits and format
  detection. The selected filename appears beside the button. Embedded assignment
  opening no longer repeats its standalone upload instructions.
- `tests/test_open_package_ui.py` verifies the compact control, hidden uploader,
  absence of repeated instructions, and filename display. Browser-native file
  chooser interaction is not exercised by NiceGUI's simulated user.
- Validation: `.venv/bin/python -m pytest -q tests/test_open_package_ui.py
  tests/test_assignment_handoff_ui.py tests/test_translations.py` — 28 passed.
  Ruff check and format check on `setup_wizard.py`, `assignment_handoff.py`,
  `translations.py`, and `test_open_package_ui.py`, plus `git diff --check`, passed.
  Not committed; manual UI validation remains pending.

### M3 opening — correct footage folders before creating the project

- Operator feedback exposed that missing-footage opening created/activated the
  project immediately, discarded the upload state, and disabled Open assignment.
  Folder edits then had no retry path. Reproduced with the real UI regression
  `test_missing_footage_can_be_corrected_before_creating_project` (failed on the
  disabled button before the fix).
- Provider `check_assignment_media` validates proposed folders with the same
  listed-media resolver, without writing a project. Opening now checks paths
  first: missing-file warnings leave the folders editable and Open assignment
  enabled. Correct and retry, or explicitly Open with missing footage. Changing
  a folder clears the previous warning/confirmation. No provisional or duplicate
  projects are created by retries. Final opening still rechecks media paths.
- English/French warning and confirmation text updated. Regression tests cover
  correcting a wrong folder and opting into missing footage, including project
  counts, activation, and retained missing rows. The manual checklist reflects
  the new behavior. Not committed.
- Validation: `.venv/bin/python -m pytest -q tests/test_assignment_handoff_ui.py
  tests/test_open_package_ui.py tests/test_assignment_handoff_roundtrip.py
  tests/test_translations.py` — 43 passed. Ruff lint/format on the changed UI,
  provider, translations, and UI tests, plus `git diff --check`, passed.

### M3 operator fix — sync probes existing unknown media

- Reproduced the reported Overview Unprobed count: opening an assignment creates
  listed video rows without local probe status, but sync previously probed only
  newly discovered rows. Existing rows were merely marked present. The focused
  regression failed with Unprobed still 2 after sync before the fix.
- `review_app/backend/provider/video.py`: sync now probes present existing rows
  whose `is_valid` is unknown, alongside newly discovered files. It applies the
  normal probe result and reconciles Distance completion; existing timestamps
  and location metadata are preserved. Already checked valid/invalid videos are
  not automatically re-probed on subsequent syncs. Progress stays monotonic and
  within the scanned total, including mixed new/existing work.
- Assignment sync still considers only listed media: no unrelated discovery,
  no probing origin-project rows, and no expansion of return scope.
- `tests/test_assignment_handoff_roundtrip.py` checks the actual Overview count,
  local validity/duration, listed-only probing, and no repeated probes.
  `tests/test_video_sync.py` covers ordinary mixed new/unknown/checked media,
  valid/invalid outcomes, metadata preservation, and progress. Not committed.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_video_sync.py tests/test_assignment_handoff_roundtrip.py tests/test_local_data_provider.py tests/test_update_project_dir.py tests/test_video_tools.py
# 53 passed; existing pytest warning: unknown config option main_file.

.venv/bin/ruff check review_app/backend/provider/video.py tests/test_video_sync.py tests/test_assignment_handoff_roundtrip.py
.venv/bin/ruff format --check review_app/backend/provider/video.py tests/test_video_sync.py tests/test_assignment_handoff_roundtrip.py
git diff --check
# Passed; full suite not run.
```

### M3 operator fix — opening automatically syncs listed footage

- Follow-up feedback clarified that the opening workflow itself must sync/probe
  footage. The earlier fix only made a later manual sync effective. Reproduced
  through Open package: after the completion callback, Overview still reported
  two Unprobed videos.
- `review_app/app/components/assignment_handoff.py`: after creating the working
  project, run the existing progress-reporting sync helper for that project before
  switching/continuing. This probes present listed media without scanning unrelated
  footage, including when using the explicit missing-footage opening option.
- Retain the created binding if sync fails and offer Retry footage sync; retries
  do not create duplicate projects. Project name/root fields are locked after
  creation, and the original project stays active until sync succeeds. Low-level
  backend `open_assignment` remains an atomic snapshot operation, unchanged.
- `tests/test_open_package_ui.py`: actual package opening now asserts Overview's
  Unprobed count is zero and unrelated files are not imported. A failed initial
  sync then successful retry verifies the same project is reused and activation
  happens only on success. Manual guide updated. Not committed.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_open_package_ui.py tests/test_assignment_handoff_ui.py tests/test_video_sync.py tests/test_assignment_handoff_roundtrip.py tests/test_onboarding.py tests/test_translations.py tests/test_ui_smoke.py
# 57 passed.

.venv/bin/ruff check review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_open_package_ui.py
.venv/bin/ruff format --check review_app/app/components/assignment_handoff.py review_app/app/translations.py tests/test_open_package_ui.py
git diff --check
# Passed; full suite not run.
```

### Planning decision — defer cross-workflow UI refinement to final M7

Operator requested UI work as the final milestone because further issues are
likely to emerge while implementing Distance handoff. M3/M6 remain functional
integration milestones; neither requires final visual polish. This does not mark
unperformed manual validation complete or defer correctness/safety fixes.

M7 follows M6 and reviews Species and Distance together. Initial feedback backlog:

- Distribution's handoff card is crowded: preparation fields, explanatory text,
  saved assignments, raw UUIDs/timestamps, and repeated download/copy controls
  compete in one long section. Review information hierarchy and separate
  preparation from browsing/actioning saved assignments.
- Make recipient, stage, scope, and actions scannable; place technical IDs and
  secondary details behind progressive disclosure where appropriate. Consider a
  compact saved-assignment list rather than repeated full-width blocks. These are
  design candidates, not a committed layout.
- Review origin versus working-project actions, empty/loading/error states,
  package opening/root correction, and return preview/confirmation together.
- Preserve safety warnings and preview invalidation; compactness must not hide
  destructive consequences or change saved-scope semantics.

No UI code changed for this planning decision. Validation: `git diff --check`.
Next implementation work is M4 reference identity; M3 manual sign-off remains
tracked independently.

### Decision update — unrestricted local annotation, scoped media sync

Assignment stage and scope describe delegated/transferable work, not project types
or local editing permissions. The recipient can edit Species Review during a
Distance assignment and Distance Review during a Species assignment, subject only
to the ordinary annotation/task integrity rules. Local changes do not silently
expand the original handoff. The admin's return preview and acceptance are the
place to control updates to the originating project.

Sync in a project opened from an assignment should check only its listed media,
including dependency/reference footage, instead of discovering everything below
its folders. The exported footage normally supplies exactly those files; a larger
folder should not require a permission system around every annotation operation.

Implementation consequence: remove M2's `SPECIES_QUEUE_SCOPE`,
`require_species_write`, `require_stage`, and context-based legacy-import guards
from ordinary queues/writes/imports and Distance task creation. Do not replace them
with UI stage locks. Keep safe archive handling, frozen-scope package selection,
identity/ownership/media/relationship validation, preview, stale-preview checks,
and atomic return acceptance. Those protect destination data, not local editing.

This does not expand the return contract to import unrelated stages or introduce
additional admin per-record selectors. The existing single-stage scope and global
conflict policy still determine eligible assignment return work. Any future option
to return/import extra cross-stage work needs an explicit transfer/UI decision,
not an annotation guard.

**Implemented in the M2 working tree:** ordinary annotation remains unrestricted;
assignment-project sync checks only listed footage. The backend alignment and its
regressions are complete; UI/delivery integration remains M3 work.

### M1 completion record

Implemented:

- `review_app/backend/db/models.py` and appended frozen migration v25 in
  `review_app/backend/db/migrations.py`: handoff bindings, origin/working/stage
  invariants, per-project assignment uniqueness, one working handoff per project,
  bidirectional identity uniqueness, and cascade cleanup on project deletion.
- `review_app/backend/annotation_package/identity.py`: stable bidirectional
  mapping, collision rejection, and fresh local UUID allocation per handoff.
- `review_app/backend/annotation_package/archive.py`: explicit Species setup v1
  schema in `manifest.json`, `context.json`, and `work.json`; strict record/graph
  validation, archive size limits, safe relative media paths, duplicate-member/
  JSON-key rejection, and finite-number validation.
- `review_app/backend/annotation_package/scope.py`, `species.py`, and `handoff.py`:
  explicit selection, multi-root media identity, complete Species setup collection,
  transactional preparation, and persisted original archive bytes. Selection does
  not silently exclude labeled or known-missing videos, or query current mutable
  distribution state. Large selections use one JSON-bound ID list.
- `review_app/backend/provider/assignment_handoff.py` and provider composition:
  `prepare_assignment(project_id, AssignmentRequest(...))` and
  `export_assignment(local_handoff_id)`. `PreparedAssignment` reports the local
  and portable IDs, recipient, video count, and known-missing video IDs.
- `tests/test_assignment_handoff.py`: preservation, immutable downloads after
  edits/reassignment/restart, root namespaces, invalid selections/archives,
  same-instance identity allocation, rollback, constraints on fresh/upgraded
  databases, and preservation of Species work during v24-to-v25 upgrade.

Scope clarification: Species collection was brought forward from M2 only to
exercise real immutable preparation. Species opening, return export, comparison,
and acceptance are not implemented yet. Distance and return payloads are
explicitly rejected, not partially serialized in the committed M1 implementation.
Existing transfer interfaces/UI were not replaced.

Focused commits:

- `33920fb` — persist assignment scope and portable identities; its isolated staged
  tree passed 35 persistence/migration/schema tests.
- `871d829` — prepare immutable Species assignment snapshots; its isolated staged
  tree passed the 115 focused tests below, plus Ruff lint/format checks.

M2 and the first M3 UI slice are recorded in the subsequent Species backend/UI
round-trip commit containing this tracker. Neither validated M1 commit includes
M2's draft failures or superseded editing guards.

Validation:

```bash
.venv/bin/python -m pytest -q tests/test_assignment_handoff.py tests/test_migrations.py tests/test_schema_parity.py tests/test_distance_schema_upgrade.py tests/test_distance_transfer.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py
# 115 passed

.venv/bin/ruff check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py review_app/backend/provider/local_data_provider.py review_app/backend/db/models.py review_app/backend/db/migrations.py tests/test_assignment_handoff.py
# All checks passed

.venv/bin/ruff format --check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py review_app/backend/provider/local_data_provider.py review_app/backend/db/models.py review_app/backend/db/migrations.py tests/test_assignment_handoff.py
# 11 files already formatted

git diff --check
# Passed
```

### M2 completion record

Included in the Species backend/UI round-trip commit containing this record:

- `review_app/backend/annotation_package/archive.py`, `handoff.py`, `scope.py`,
  and `species.py`: Species setup opening into fresh projects, complete saved-scope
  returns, whole-video comparison and policy-specific previews, strict origin/
  scope/media/relationship validation, stale-preview fingerprints, and atomic
  acceptance. Identical work is a true no-op; dependencies are imported only for
  applied work. Omitted units do not delete; explicit empty units preview removals.
- `review_app/backend/provider/assignment_handoff.py`: public open/return/preview/
  accept operations with read snapshots and write-reserved transactions.
- `review_app/backend/provider/video.py`: working-project sync checks listed files
  without recursively discovering unrelated footage, preserving missing entries
  for later relocation/recheck. Ordinary annotation paths remain unrestricted.
- `tests/test_assignment_handoff_roundtrip.py`: same-instance repeated opening,
  cross-instance round trips, saved evidence/identity preservation, both policies,
  no-op write rejection, dependency selection, empty/omitted units, local Distance
  editing, media sync/relocation, invalid return rejection, stale previews, and
  late-failure rollback. Existing M1/legacy tests remain green.

Validation (final combined run):

```bash
.venv/bin/python -m pytest -q tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py tests/test_migrations.py tests/test_schema_parity.py tests/test_distance_schema_upgrade.py tests/test_distance_transfer.py tests/test_distribution_bundle.py tests/test_assignments_import_export.py tests/test_queue_and_export.py tests/test_distance_queue_state.py tests/test_distance_tasks.py tests/test_distance_ai_import.py tests/test_video_tools.py tests/test_update_project_dir.py tests/test_path_matching.py tests/test_local_data_provider.py tests/test_project_and_import.py tests/test_bulk_review_equivalence.py
# 350 passed in 21.72s

.venv/bin/ruff check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py review_app/backend/provider/video.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py
# All checks passed

.venv/bin/ruff format --check review_app/backend/annotation_package review_app/backend/provider/assignment_handoff.py review_app/backend/provider/video.py tests/test_assignment_handoff.py tests/test_assignment_handoff_roundtrip.py
# 10 files already formatted

git diff --check
# Passed
```

No M2 completion blockers remain. Full-suite and UI validation were not run;
Species UI and saved-scope footage copying belong to M3. Distance payloads remain
explicitly unsupported until their later milestones.

## Inspection baseline (before M1)

| Existing code | Reuse | Gap relative to the product contract |
| --- | --- | --- |
| `review_app/backend/provider/import_service/_bundles.py` | Species vocabulary, prediction, and metadata collection knowledge | Setup-only CSV ZIPs; no saved handoff identity/scope or complete Species work snapshot. Import catches errors separately and can partially succeed. |
| `review_app/backend/provider/assignment_service.py` | Camera/chunk distribution and threaded footage copying with progress | `video_assignments` is mutable, Species-only distribution state, not a historical handoff. Bundle and footage exports query scope independently. |
| `review_app/backend/distance_transfer.py` | Bounded ZIP reading, explicit versioning, portable structured records | The shipped format is deliberately manual-only; it is not the assignment contract. |
| `review_app/backend/provider/distance_transfer.py` | Distance graph collection, validation, confirmed-species/time selection | Rejects AI state, collects only references used by saved samples, and rechecks current Species labels during export. These are unsuitable for ongoing assignment returns. |
| `review_app/backend/provider/distance_transfer_import.py` | Session-aware graph restoration and transaction ownership | Per-task replace/skip; no identical-state comparison or removal preview. Reference matching is by timestamp/identity and blocks calibration differences. |
| `review_app/app/setup_wizard.py` | Fresh-project entry point and media location UI | Creates and syncs a project before importing setup; scans the supplied directory rather than opening only packaged media. |
| `review_app/app/pages/distribution/page.py` | Distribution UI, delivery progress, return entry point | Independent setup/footage controls and append-mode CSV batch imports rather than assignment-aware acceptance. |

At initial inspection, `review_app/backend/annotation_package/` contained only
local bytecode, not a tracked Python implementation. M1 adds the initial handoff
implementation described above.

The database is shared across projects. Videos, Distance tasks, individuals,
samples, references, and imports have database-wide identities. Copying their
primary keys into a fresh project on the same instance would collide.

## Implementation shape

Add one handoff module with its interface on `LocalDataProvider`, following the
existing mixin composition. Use a thin facade in
`review_app/backend/provider/assignment_handoff.py`; keep the implementation in
`review_app/backend/annotation_package/`:

- `archive.py`: bounded archive encoding/decoding and explicit versioned records.
- `scope.py`: saved scope, selection, media dependencies, and return eligibility.
- `species.py`: collect, validate, compare, and restore Species video snapshots.
- `distance.py`: collect, validate, compare, and restore Distance task snapshots.
- `identity.py`: portable-to-local identity and vocabulary mapping.
- `handoff.py`: prepare/open/return orchestration and shared preview/accept logic.

These are internal responsibilities, not separate public transfer systems. Do not
introduce a universal annotation model or a plugin/adapter framework. Species and
Distance should have explicit implementations behind the same workflow.

Proposed caller-facing operations:

```python
prepare_assignment(project_id, request) -> PreparedAssignment
export_assignment(assignment_id) -> bytes
copy_assignment_footage(assignment_id, output_dir, progress_callback) -> CopyResult
open_assignment(data, project_name, media_roots) -> OpenedAssignment
export_assignment_return(working_project_id) -> bytes
preview_assignment_return(project_id, data, policy="use_imported") -> ReturnPreview
accept_assignment_return(project_id, data, policy, preview_token) -> AcceptanceResult
```

Identifiers passed to local operations identify a locally stored handoff. The
portable assignment UUID is separate. Callers select work, locate footage, choose
one policy, and render results; they do not coordinate tables or dependencies.

## 1. Persist immutable assignment scope and portable identity

Append migrations in `review_app/backend/db/migrations.py` and update
`review_app/backend/db/models.py`; do not rewrite published migration entries.

Add:

- `handoff_assignments`: local primary key, portable assignment UUID, local project
  foreign key, role (`origin` or `working`), source admin project UUID, recipient,
  stage, optional target scientific name, creation time, and immutable scope.
  Enforce one assignment binding per portable UUID/local project. A working
  project belongs to exactly one handoff.
- `handoff_identity_maps`: handoff, entity kind, portable identity, local identity.
  Enforce unambiguous mappings in both directions within a handoff/entity kind.
- Store the prepared setup snapshot with the origin handoff so downloading it
  again does not silently resnapshot changed annotations. This is one immutable
  handout, not synchronization history.

Scope records the exact assigned source videos and, for Distance, the one target
species. Record the original relevant Species context for changed-context
warnings. Keep required/reference media separately from assigned work: carrying
reference footage must not assign Species work on that footage.

Use source video identities and scientific names as portable work keys. Map
vocabulary by scientific name/behavior key/tag key. Allocate local graph IDs when
opening a project and record mappings; never assume primary keys are portable
across projects. Preserve observation UUIDs and map their local integer handles.
New annotator entities receive stable portable identities when first exported.
Persist accepted mappings so repeated returns resolve to the same local entities.

Existing `video_assignments` remains the mutable Species distribution input. A
prepared handoff freezes its selected videos. Distance assignments do not read or
modify those Species distribution assignments.

## 2. Define the assignment snapshot format

Use a new, explicitly versioned ZIP contract, separate from legacy bundles and
`review-app.distance-manual` v1. Proposed members:

- `manifest.json`: format/version, direction (`setup` or `return`), assignment
  UUID, source admin project UUID, stage, recipient, target species, frozen work
  keys, and portable media-root identifiers.
- `context.json`: required setup/dependencies; stage-specific content.
- `work.json`: stage-specific whole-video or whole-task snapshots.

JSON is a practical choice here because the records already contain masks,
histories, relationships, and nullable values. Assignment packages are not
spreadsheet interchange. Keep analysis TSV/CSV exports independent.

Freeze field definitions independently of ORM columns. Do not serialize arbitrary
ORM rows or reuse manual v1 validation as though its restrictions were universal.
Reuse archive size/path safety techniques, with strict member/version checks,
finite numbers, and graph validation before writes.

Media records use `(root_key, safe_relative_path)`, not source absolute paths.
Maintain separate root namespaces in copied footage to avoid filename collisions
between project directories. Never fall back to basename matching.

Setup carries initial work and context/dependencies. Assignment context is not a
read-only annotation mode: users can edit through the normal workflows regardless
of the delegated stage. Returns carry all current saved assigned work, including
untouched and explicit empty snapshots, and only the dependencies required to
interpret/apply it. Local cross-stage edits do not automatically expand eligible
return work. A missing work record never means delete; normal return generation
emits every assigned work key.

## 3. Deliver a complete Species vertical slice

Implement Species collection/restoration directly against a caller-owned session:

- `VideoLabel`, including nullable blank/completion state, review-later state,
  attribution, and timestamps.
- `IndividualObservation`, including UUID, count, interval, attribution, and
  timestamps; associated `ObservationTag` behavior links.
- `VideoTag` links with their attribution and timestamps.
- Required species/behavior/tag definitions, project vocabulary, predictions,
  video metadata, and review context for setup.

Restore one complete video snapshot without modifying Distance state, predictions,
metadata, distribution ownership, or unrelated videos during return acceptance.
Do not route snapshot restoration through `update_manual_review()` or
`apply_manual_reviews()`: those methods implement interactive/CSV update semantics
and may generate timestamps or reuse identities differently. Reuse lower-level
rules only where their contracts actually match.

Opening must validate the package/media mapping first, then create the fresh
project, listed videos, context, work, and assignment binding in one transaction.
Probe listed files outside the write transaction as needed. Do not call the old
create-project, recursive-sync, component-import sequence. Missing footage must be
explicitly reported and remain locatable later, not cause scope to shrink.

Apply the same preview/accept path described below from this first slice. Validate
assignment scope at return export/acceptance, not by restricting normal annotation
writes or queues. Add listed-media sync and regression tests for unrestricted
cross-stage editing before exposing the UI. This establishes a usable end-to-end
workflow before adding Distance complexity.

## 4. Implement shared comparison, preview, and atomic acceptance

Both stages produce canonical work snapshots for comparison, independent of local
surrogate IDs, local paths, collection ordering, and import bookkeeping. Preserve
meaningful saved evidence, attribution, and timestamps in the comparison. Separate
reference dependencies from work comparison and compare mapped references by their
agreed video/distance identity.

Classify each returned work key as new, identical, or different existing work.
Do not equate an auto-created empty Distance task row with existing annotation
work: pristine task scaffolding must not cause Keep existing to discard the first
returned annotation. Conversely, saved setup/alignment, verification, progress,
completion, or explicit absence is state, not merely an empty database row.

Apply one policy across all eligible returned work:

- New: add under either policy.
- Identical: true no-op, without timestamp churn or dependency duplication.
- Different existing: preserve the complete local snapshot under Keep existing;
  replace the complete snapshot under Use imported.
- Missing work record: leave local work untouched.
- Explicit empty snapshot: may clear existing work under Use imported, even if
  unfinished; quantify and warn before confirmation.

Return a structured preview with identifiable videos/tasks, classification,
policy-specific action, annotation counts before/after, removed records or links,
unapplied returned work, reference substitutions, context warnings, and blocking
validation errors. Explain that Keep existing can skip later annotator updates,
and Use imported can overwrite admin corrections. Neither policy merges fields.

Validate assignment/project ownership against the locally saved scope, not just
claims in the uploaded manifest. Reject wrong-stage, wrong-species, out-of-scope,
cross-project, dangling-relationship, and ambiguous-media payloads. Invalid work is
not made acceptable by choosing Use imported.

Preview returns a fingerprint of the payload, policy, and relevant destination
state/dependencies. Acceptance acquires `BEGIN IMMEDIATE`, replans and revalidates,
and requires a fresh preview if that fingerprint changed. Apply all eligible work
and necessary dependencies/mappings in the same transaction. A late failure rolls
back everything. Do not import dependencies for work left unapplied.

## 5. Extend snapshots to complete Distance state

Reuse the manual transfer graph knowledge, but implement assignment collection
without its manual-only and current-label eligibility checks:

- Carry task alignment, verified schedule, completion/absence/history, started
  state, individuals, anchors, infant/carrier relationships, samples, behaviors,
  tags, precise playback positions, attribution, and resume progress.
- Preserve AI-linked pending suggestions, imported schedules, `ai_original`,
  rejected AI records, and relevant source provenance. The manual format currently
  rejects these; removing its guards alone would silently lose information.
- Project source provenance to the selected graph and distinguish its original
  source identity/hash from any scoped payload identity. Do not copy unrelated
  source-package work or claim a projected payload is the complete original.
  Remap embedded IDs in provenance where runtime suggestion lookup needs local IDs.
- For setup, collect usable reference clips and prepared calibration for the
  selected deployments, not only frames already used by samples. Otherwise an
  untouched task could arrive without anything to annotate against.
- Include required reference media and discovery tags/metadata, so existing
  reference discovery still works after opening a fresh project.

Use confirmed Species context and the existing camera-local time-window rules
only when selecting/preparing work. Saved scope governs return export/acceptance
afterward. Later admin Species-label changes produce a warning, not ineligibility
and not restoration of those labels.

### Change reference identity before enabling Distance handoff

Replace reference-frame uniqueness on `(reference_video_id, offset_ms)` with
`(reference_video_id, distance_m)`. Keep timestamps non-unique. Append a table-rebuild
migration and retain sample foreign keys. If development data contains duplicate
pairs, choose a deterministic keeper and remap dependent samples without changing
their distances; no user-facing legacy reconciliation workflow is needed.

Update `distance_references.py` write paths and audit timestamp lookups in the AI
importer and manual importer. Allow multiple distances at the same timestamp and
never call `one_or_none()` on timestamp alone. Do not silently change the legacy
manual archive contract while making those callers safe for the new schema.

For assignment returns, reuse the admin frame for an existing video/distance pair,
including its timestamp; explain substitution. Otherwise add the returned frame,
even if its timestamp is used for another distance. Samples retain their returned
distance. Never overwrite unrelated calibration.

## 6. Keep delivered media and return scope consistent, without editing restrictions

Use persisted handoff context for initial work selection, delivery, media sync,
return export, and acceptance. Do not use it to restrict ordinary queues,
target-species choices, navigation, annotation writes, or existing annotation
imports. Reference/context footage may be annotated locally without becoming
eligible assigned return work.

Keep normal Distance task creation from confirmed Species labels, including in
projects opened from Species assignments. A Distance assignment can likewise edit
Species labels or other species' Distance tasks. These changes are local work;
they do not rewrite the handoff's stage, target species, or saved selection.

Opening and later sync must check only the listed assigned and dependency media,
not recursively ingest unrelated folder contents. Missing listed footage remains
represented and can be located/rechecked later. Test this discovery behavior
rather than adding guards around every annotation operation. Ordinary data
integrity checks still apply, and return import must strictly validate its eligible
scope and relationships. There is no new project type or permissions layer.

Refactor threaded footage copying into a helper consuming an already resolved
media list. The handoff package, dependency explanation, and copy operation must
all consume the same saved scope and dependency manifest. Report missing files
and copy failures explicitly; never silently drop them from the assignment.
Keep generated delivery directories outside recursively scanned footage roots.

## 7. Wire the UI and retain independent legacy imports

- Distribution: Prepare assignment with recipient/stage/work selection; existing
  Species chunk distribution is a selection input. Distance uses confirmed
  species/time selection independently. Show saved assignments with package
  download and optional footage copying from those same assignments.
- Setup wizard/project picker: Open assignment into a fresh project, locate each
  required media root, and show missing/reference footage requirements.
- Working project: show assignment identity/stage/scope and Return progress;
  export requires no reconstruction of the original selection. Returning does not
  close the assignment.
- Origin project: upload return, display policy-specific preview, then accept.
  Invalidate confirmation after policy/payload/state changes.
- Add English/French strings in `review_app/app/translations.py` and focused
  NiceGUI interaction tests.

Replace the assignment-facing legacy bundle/CSV controls only when their new
workflow is usable. Keep legacy setup/CSV/manual Distance import and analysis
exports independently available where still needed; do not reinterpret an old
file as an authenticated assignment return.

## Delivery order and validation

Implement in these increments:

1. Persistence, identity mapping, archive contract, and immutable preparation.
2. Species prepare/open/return/preview/accept through the module interface.
3. Species UI and scope-consistent footage delivery.
4. Reference schema/write-path change with focused legacy regression tests.
5. Complete Distance collection/open/return/accept with scoped return eligibility
   and unrestricted local annotation.
6. Functional Distance UI, workflow documentation, and combined integration checks.
7. Final cross-workflow UI/UX refinement with operator walkthrough, focused UI
   regressions, and final translation/documentation alignment. Address layout and
   visual polish here; correctness and workflow-blocking fixes remain in scope
   throughout earlier milestones.

Each increment runs its smallest focused tests and Ruff lint/format checks. Add
interface-level database tests for:

- Opening on another instance and twice on the same instance without identity
  collisions; preserving attribution, UUIDs, relationships, and timestamps.
- Full-scope returns, unfinished/untouched/empty states, and explicit absence.
- New work versus pristine task scaffolding; identical returns as true no-ops;
  both policies on later updates and admin corrections.
- Empty-snapshot removals accurately previewed; omitted work never deleted.
- Scope/project/stage/species rejection at return import and unchanged unrelated
  destination state; unrestricted local annotation across review stages.
- Listed-media-only sync, including dependency footage, ignoring unrelated files
  without hiding valid annotations or changing ordinary editing permissions.
- Changed Species context warning without blocking assigned Distance work.
- Reference reuse/substitution, same-timestamp different-distance acceptance,
  AI provenance/suggestions, and complete setup reference dependencies.
- Stale-preview rejection and rollback after a failure late in acceptance.
- Multi-root footage copying matching saved scope, including reference footage.

Reuse `tests/test_distance_transfer.py`, `tests/test_distribution_bundle.py`, and
`tests/test_assignments_import_export.py` as legacy regressions. Extend
`tests/test_migrations.py`, `tests/test_distance_schema_upgrade.py`, and
`tests/test_schema_parity.py` for schema changes. Preserve the published v22 schema
fixture rather than replacing it to hide upgrade drift.

Initial inspection baseline: the focused legacy transfer/distribution/assignment
and schema-parity tests passed: **58 tests**. Subsequent implementation and
validation are recorded in the milestone tracker above.
