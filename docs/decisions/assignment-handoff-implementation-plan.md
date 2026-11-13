# Assignment handoff implementation plan

**Status:** M1 and M2 complete; M3 in progress (first UI round trip ready for manual testing).
**Direction:** Explicit serialized assignment snapshots, not database snapshots.
**Product contract:** [Assignment handoff](./assignment-handoff.md).

## Milestone tracker

This section is the implementation source of truth. Update it with each completed
slice, not only at the end of a milestone. Git commits record the code changes;
this tracker records readiness, validation, and the next action.

| Milestone | Status | Completion gate |
| --- | --- | --- |
| M1 — Persistence, identity, archive | Done | Immutable preparation/download; portable graph mapping; fresh/upgrade schema parity; archive validation tests. |
| M2 — Species backend round trip | Done | Prepare/open/return/preview/accept across instances and same-instance projects; both policies, empty states, import-scope validation, scoped media sync, unrestricted local annotation, stale previews, and rollback tested. |
| M3 — Species UI and delivery | In progress | Usable prepare/open/return/accept flow; package and footage copy use saved scope; focused UI and multi-root copy tests pass. |
| M4 — Reference identity | Not started | Video/distance uniqueness on fresh and upgraded databases; dependent samples preserved; reference write paths and affected legacy imports tested. |
| M5 — Complete Distance backend | Not started | AI/manual state and reference dependencies round-trip; saved-scope import eligibility and reference substitution tested, without restricting local annotation. |
| M6 — Distance UI and integration | Not started | Distance handoff usable end to end; translations/docs updated; combined workflow and relevant legacy regressions pass. |

Use `Not started`, `In progress`, `Blocked`, or `Done`. Mark a milestone Done only
when its completion gate is met. If blocked, record the concrete blocker below.

### Current checkpoint

- **Current milestone:** M3 — Species UI and delivery.
- **Next action:** Operator tests the first Species UI round trip using
  [the manual checklist](./assignment-handoff-ui-roundtrip.md). Then implement
  saved-scope footage copying and multi-root copy tests, and finish replacing
  assignment-facing legacy delivery controls. This slice locates externally
  delivered footage manually; it does not claim M3's copy gate is met.
- **Blockers:** None identified.
- **Recorded validation:** M1 baseline: 115 focused tests passed. M2 completion:
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

M3 remains in progress; saved-scope footage copying is still pending.

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
preview_assignment_return(project_id, data, policy="keep_existing") -> ReturnPreview
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
6. Distance UI, final workflow documentation, and combined integration checks.

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
