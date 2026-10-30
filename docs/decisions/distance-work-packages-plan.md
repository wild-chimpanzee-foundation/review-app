# Distance Annotator Tasks and Work Packages

**Status:** Agreed direction and phased delivery scope. Not implemented.

Requirements originate in [Distance Annotation Feedback, item 1](./distance-annotation-feedback.md#1-independent-species-based-annotator-packages).
The [current Distance workflow](./reference-based-distance-annotation.md) remains
the authoritative description of shipped behavior.

## Confirmed direction

- Make a species-specific task the fundamental unit of Distance review, with one
  canonical task per video and target species. Packages distribute tasks; they do
  not define task identity.
- Create tasks from manual/confirmed Species Review species and counts.
  Unconfirmed model predictions do not establish eligibility. No additional
  ordinary Review completion flag or ordinary assignment is required.
- A video with confirmed chimps and duikers appears twice in the Distance queue:
  once for the chimp task and once for the duiker task.
- Each task permits annotation only of its target species. Its individuals,
  samples, alignment, resume state, and completion belong to that task.
- Different species tasks for the same video may use different alignment anchors
  and finish independently.
- Provide an explicit **No target species visible** completion outcome. Preserve
  it as a discrepancy against source Species Review, not an automatic correction.
- Handle conflicting returns with an explicit per-task overwrite-or-skip choice.
  Skipped returns are not stored in the app; users retain the original package for
  later inspection or retry. See [Review issues](./review-issues.md).

Independent duplicate reviews of the same species are not a requirement. A
source task and its distributed copy can nevertheless diverge and need comparison.

## 1. Task and package identity

Proposed model:

- `DistanceReviewTask`: stable ID, video, target species, task-owned alignment,
  completion/absence outcome, and attribution. Enforce one canonical task per
  `(video, target species)` in the source project.
- `DistanceIndividual`: belongs to a task; its samples use that task's alignment.
  Enforce target-species restrictions in backend writes and import validation,
  not only in the UI. Relationships such as carried infants stay within a task.
- `DistanceReviewProgress`: keyed by task and annotator, with active individual
  and resume state.
- Distance packages and package-task membership: stable package ID, source
  project identity, recipient, target species, and format revision. Distributed
  copies retain source task/entity identities. Do not reuse ordinary exclusive
  `video_assignments`.

Adding a species column to `DistanceVideoReview` alone is insufficient: individual
ownership, sample lookup, alignment, queue, completion, and resume APIs must all
be task-scoped.

Preview eligible task counts, footage length, cameras, and missing media/references
before package creation. Snapshot task membership, manual species/count context,
and the evidence baseline at generation. Subsequent source corrections must not
silently change distributed work. Explicit previewed refresh is deferred; the
first release uses the saved snapshot.

## 2. Alignment, reference context, and completion

Every individual and sample belongs to one task and uses that task's alignment.
Changing/resetting a task's alignment requires destructive confirmation and affects
only that task's evidence and completion, not another species task for the video.

Reference calibration used by evidence must have stable/versioned context. Shared
reference edits must not silently reinterpret existing task evidence. Preserve the
calibration/provenance used by each task; incompatible returned reference context
requires an explicit overwrite-or-skip decision rather than implicit substitution.

Within a task:

- Validate scheduled evidence and required age/sex for its target-species
  individuals. Explicit Unknown satisfies age/sex requirements.
- Normal **Finish task** requires at least one target-species individual and
  complete human evidence. An empty task requires the explicit absence action.
- **No target species visible** completes the task without a placeholder animal.
  It is unavailable while human target-species individuals remain. Retain author,
  time, and the original manual species/count context.
- Pending AI-only identities are not human presence evidence or completed work.
  The absence action must explicitly reject any pending target-species suggestions,
  retaining their provenance; pending drafts cannot silently survive as accepted
  evidence alongside absence.
- Adding or accepting a target-species individual invalidates a prior absence.
  Relevant individual, sample, attribute, and alignment changes reopen current
  task completion as needed.
- No action in one species task automatically completes or reopens another.
- Queue/resume state includes task identity, so the same video in two tasks restores
  the correct alignment, individual, and progress.

Distinguish two completion records:

1. **Submitted completion:** the annotator's completion/absence claim tied to the
   returned task evidence revision, preserved as provenance when the task is accepted.
2. **Current source completion:** completion of the currently accepted task evidence.
   Accepted edits invalidate or revalidate it as appropriate. Apply a returned
   completion claim only when its corresponding evidence is accepted consistently.

A skipped return does not change source completion, and its completion claim is not
stored in the app. Later source edits do not erase accepted historical submitted claims.

Anyone with editing access may finish the task; completion is not restricted to
its package recipient. The task has one shared current completion status tied to
its accepted evidence revision, with the finishing author's attribution. Per-
annotator progress is only a bookmark/resume state, not a separate completion
checklist. Relevant edits may reopen the task, and any editor may finish it again
once requirements are satisfied. Preserve earlier completion claims in history.
For example, Alice finishes the chimp task; Bob adds an incompletely annotated
chimp, reopening it; after completing that evidence, Bob may finish the same task.

## 3. Supervisor and annotator UI

Add a separate Distance work-packages section rather than changing ordinary
Distribution semantics.

Supervisor: choose target species and recipient, preview tasks/footage, generate
packages, view task progress, and export data plus optional media.

Annotator: show one queue entry per video/species task, with a persistent target
species label. Individual creation uses that species; do not offer cross-species
annotation or species changes inside the task. Provide **Finish task** and
**No target species visible** actions, clearly scoped to the current task.

If a chimp task contains only duikers, record no target species visible. Correcting
Species Review and creating any missing duiker task happen separately; neither is
an automatic side effect of completing the chimp task.

## 4. Portable handoff and return

Use a versioned Distance-specific format, distinct from AI Distance input ZIPs and
ordinary project bundles. Existing ordinary bundles do not preserve Distance state.

Preserve:

- Stable source/project/package/task/entity identities and evidence baselines.
- Original task scope and manual species/count context.
- Task-owned individuals, attributes, anchors, samples including skips, behaviors,
  tags, and within-task relationships.
- Reference provenance and stable/versioned calibration context, with resolvable
  relative media paths.
- Task timing/alignment, including unknown absolute camera times.
- Submitted completion, absence outcomes, attribution, and resume state.
- Relevant AI drafts and acceptance/rejection provenance when included in the work.

Keep data archives separate from optional footage ZIPs/folder-copy manifests for
large datasets. Validate imports, including task/species ownership, and apply
accepted changes transactionally. Reimporting identical returned data must not
duplicate evidence, submissions, or issues.

## 5. Conservative return-merge contract

Use stable identities and baseline-aware comparison. Local database IDs and
annotator names alone are insufficient. Never identify independent animals solely
by their local individual numbers or silently update ordinary Species Review labels.

Different species tasks have independent alignment and evidence, so their returns
can coexist. Divergent copies of the same task still require reconciliation.
Distinguish unchanged baseline values, unambiguous edits/additions, concurrent
edits/deletions, possible duplicate individuals, and incompatible alignment or
reference calibration.

For the initial release, use the task's returned evidence as the conservative
acceptance unit:

- Accept a task return only when its changes and required dependencies can be
  applied consistently against the current source and saved baseline.
- If task alignment, required reference context, entity relationships, or evidence
  changes conflict, ask the importer whether to overwrite the source's changes for
  that task with the returned task, or skip importing that task. The choice applies
  to the conflicting species task, not every task for the video; other safe tasks
  may still import. Overwrite applies the returned task and its completion claim
  atomically, without partially attaching individuals or samples, retaining the
  accepted submitted completion claim as provenance. Skip does not alter source
  evidence or completion and does not store the skipped payload, baseline, or
  completion claim in the app.
- A new individual UUID does not make its samples independent of alignment,
  calibration, or carried-infant dependencies.
- Other tasks in the same package may still be accepted when their dependencies
  are safe. Do not mutate shared references in a way that changes other tasks'
  accepted evidence.
- Apply each accepted task and its completion reconciliation transactionally.
  Finer-grained partial merges are deferred.

Provide an import summary showing each conflict, the importer's overwrite-or-skip
choice, and which tasks were imported or skipped. Record the choice with author/time,
but do not retain skipped evidence or create pending-return records. Users must
retain the original import package for later inspection or retry; the app does not
archive it. A retry compares against the then-current source and prompts again if
needed. No unresolved-results list, in-app skipped-evidence inspection, or later-
resolution workflow is required for the first release. A completed import may
include skipped tasks; the summary must not describe those tasks as imported.

Keep the issue model usable by future ordinary Species Review reconciliation,
without implementing that entire pipeline as part of packages.

## 6. Focused validation

- Manual species/count annotations for A/B produce two tasks for the same video;
  unconfirmed predictions alone do not. Ordinary Review need not be marked complete.
- A and B can use different alignment anchors, preserve them on round-trip, and
  finish independently. Resetting A leaves B unchanged.
- UI, backend writes, and imports reject cross-species evidence and cross-task
  individual relationships.
- Normal finish rejects empty tasks; explicit absence completes without a placeholder
  and retains a discrepancy. Existing human individuals block absence; pending AI
  suggestions require explicit rejection. Later human presence reopens the task.
- A returned completion claim only completes the source when its evidence is accepted;
  skipped returns leave source completion unchanged and are not retained in the app.
- Divergent copies of the same task prompt the importer to overwrite that task or
  skip it; the choice does not affect other species tasks for the video. A new
  returned individual with samples tied to a conflicting alignment cannot be
  attached to the source's current alignment unless the task is overwritten
  atomically.
- Conflicting calibration or carried-infant dependencies trigger the per-task
  overwrite-or-skip choice; independent safe tasks in the package can still import.
- Skipped tasks leave no retained payload or pending-return record. Their decisions
  appear in the import summary; retry from the original package uses current source state.
- Repeated import does not duplicate evidence, submissions, or discrepancies.
- Skips, fractional reference distances, provenance, and unknown absolute camera
  time survive transfer.
- Existing reference frames and their calibration/media remain available through
  the schema transition; ordinary Distribution remains functional. Legacy Distance
  annotations do not require an automatic evidence-preserving migration.

## Agreed delivery phases

### Work-package release

1. Task model, schema transition, eligibility preview, task-owned alignment,
   species restrictions, and completion/backend behavior.
2. Supervisor/annotator task and package UI with explicit absence.
3. Full-fidelity handoff/return using the conservative task-level merge contract,
   explicit per-task overwrite-or-skip decisions and an import summary.
   Portable distribution is not complete without a safe return path.

Treat Distance annotation as an unreleased feature: no live databases depend on
its existing whole-video model. The only annotated database is the developer's
and is disposable or may be migrated manually. Automatic preservation of legacy
Distance individuals, samples, schedules, progress, completion, and cross-species
relationships is not required. Do preserve existing reference frames and their
calibration/media; these are costly to recreate. Do not reset unrelated project
data. Exact schema-transition mechanics remain implementation design work.

### First QA follow-up

A simple Review issues page with four fixed checks: animal count mismatch, species
mismatch/no target species visible, species outside project scope, and overlapping
annotator submissions requiring comparison. Provide explanation, evidence, links
to existing review screens, recheck, and acknowledgement. Corrections initially
happen in the existing review screens.

### Later

Richer side-by-side comparisons, selective merge actions, explicit package refresh,
and shared reconciliation for ordinary Species Review. These are not prerequisites
for work packages.

## Main implementation touchpoints

- `review_app/backend/db/models.py` and migrations: task identity, ownership,
  task alignment, completion, packages, and accepted-submission provenance.
- `review_app/backend/provider/distance_repository.py`: task-aware provider APIs.
- `review_app/backend/provider/distance_review.py`: task alignment, completion, resume.
- `review_app/backend/provider/distance_identities.py` and sample writers: task/species
  restrictions and completion invalidation.
- `review_app/app/pages/distance_review/`: task queue, species label, completion actions.
- Existing distribution UI and `import_service/_bundles.py`: reusable patterns, not
  an already sufficient Distance format.
