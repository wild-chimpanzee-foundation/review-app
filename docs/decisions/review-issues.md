# Review Issues: Warnings and Conflicts

**Status:** Agreed product direction and phased scope. Backend/frontend structure
below is a design proposal, not implemented behavior.

Related: [Distance work packages](./distance-work-packages-plan.md) and
[Distance feedback](./distance-annotation-feedback.md#1-independent-species-based-annotator-packages).

## Purpose and terminology

Provide a fixed catalog of issues the app knows how to detect and explain, rather
than a user-configurable rules engine. Use Review issues as the umbrella section:

- **Warnings:** possible omissions or inconsistencies that need human inspection.
- **Conflicts:** competing returned answers that require a reconciliation decision.

Do not assume a discrepancy identifies which review is wrong. Do not silently
correct annotations or overwrite conflicting work. Any overwrite of a conflicting
task requires the importer's explicit choice. Skipping a task leaves source data
unchanged; the app does not retain the skipped payload or archive the import package.

Two annotators reviewing the same video is not automatically a disagreement.
Distance tasks for different target species are independent, including their
alignment, and are not overlapping submissions merely because they share a video.
Divergent copies of the same task warrant comparison; equivalent answers need not
become a blocking conflict. Ordinary Species Review submissions can also overlap.

## Agreed delivery scope

### 1. Work-package release: safe return, minimal UI

- Packages distribute stable species-specific tasks. Each task permits annotation
  only of its target species and owns its alignment, individuals, and samples.
- Completion is independent per task, with an explicit **No target species visible**
  outcome. Source labels are manual Species Review species and counts; no additional
  ordinary Review completion flag is required.
- Accept unambiguous task returns only when their evidence and dependencies can be
  applied consistently. If alignment, reference context, relationships, or evidence
  conflict, the importer chooses per task whether to overwrite source changes with
  the returned task or skip that task. Overwrite applies the returned evidence and
  completion claim atomically; skip leaves source evidence and completion unchanged
  without storing the skipped return in the app. Independent safe tasks may still import, and
  the choice for one task does not affect other species tasks for the same video.
- Preserve accepted submitted completion as provenance separately from current
  source task completion. Skipped completion claims are not stored in the app.
- Provide an import summary showing each conflict, the importer's overwrite-or-skip
  choice, and which tasks were imported or skipped. Record the choice with author/time.
  Users must retain the original import package for later inspection or retry; the
  app does not archive it. Retry compares against the then-current source.
- No retained skipped payloads, pending-return list, or later-resolution workflow
  is required for the first release. A completed import may include skipped tasks;
  the summary must not describe those tasks as imported. A sophisticated merge
  editor or general warnings dashboard is not a prerequisite.

Use the [conservative task-level return-merge contract](./distance-work-packages-plan.md#5-conservative-return-merge-contract).
Apply each accepted task and its completion reconciliation transactionally;
finer-grained partial merges are deferred.

### 2. First QA follow-up: simple Review issues page

Four fixed checks:

| Check | Meaning |
| --- | --- |
| Animal count mismatch | Manual Species Review count differs from human individuals in the completed Distance task for that video/species. |
| Species mismatch / no target species visible | A completed Distance task reports absence against manual Species Review labels. No additional ordinary Review completion flag is required. |
| Species outside project scope | A human annotation or task target uses a species not enabled for the project. |
| Overlapping annotator submissions | Separate submissions cover the same Distance task, or overlapping ordinary Species Review work, and need comparison. Different target-species tasks alone do not trigger this check. |

Initial UI capabilities: explanation, evidence, links to existing review screens,
recheck, and acknowledgement for warnings. Package-return conflicts are handled
at import with a per-task overwrite-or-skip choice; inspecting or retrying skipped
returns requires the user-retained original package. Corrections to source annotations happen in the existing review
screens, not a new merge editor. Record explicit reconciliation separately from
warning acknowledgement.

### 3. Later: richer reconciliation

Add side-by-side comparisons, selective merge actions, and shared reconciliation
for ordinary Species Review. Potential additional fixed types include concurrent
edits/deletions, possible duplicate Distance individuals, and incompatible returned
alignment/calibration. Phase 1 reports conflicts and overwrite-or-skip decisions
in the import summary, but does not retain skipped evidence. In-app preservation
of alternative returned answers and richer resolution can follow later.

## Confirmed animal-count semantics

This is a scientifically meaningful completeness check, not an unresolved question
about incompatible count definitions. Compare per video and species:

- **Species Review count > Distance individuals:** an animal may not have been
  distance annotated.
- **Distance individuals > Species Review count:** an animal may have been missed
  during Species Review.

Use manual/confirmed Species Review species and counts and the completed Distance
task for that video/species. Do not require an additional ordinary Review completion
flag. Compare current accepted source evidence; skipped returned individuals do
not enter the count. Retain the accepted task's original
species/count snapshot as context when source labels have since changed.

An unfinished task or unconfirmed AI draft must not generate a count mismatch
merely because work is incomplete. **No target species visible** is an answered
outcome with zero human individuals, not unfinished work.

Show both counts and direct links to each review. Do not choose a correct side
automatically. Avoid duplicate alerts when an explicit absence already explains
the count/species discrepancy; show related evidence together.

## Proposed backend structure

### Fixed type registry

Stable type codes define a title/explanation, warning/conflict category, severity,
detection rule, evidence shape, and supported resolution actions. Keep display
strings translatable. Do not add a configurable rule builder.

### Persistent issues and history

Suggested issue fields:

- Identity, project/video, task where applicable, type, and subject (for example
  species or observation).
- Status: open, resolved, acknowledged, or obsolete.
- Structured evidence and references to its sources.
- Evidence fingerprint, first/last detection times.
- Resolution action, note, author, and time.

Keep an event history. An acknowledgement applies to the evidence inspected, not
all future disagreements of that type: a materially changed discrepancy must
surface again. Repeated detection/import must not duplicate the same issue.

### Detection lifecycle

Run focused checks after relevant species/Distance edits, task completion, package
returns, or project-species changes. Recheck only affected videos/tasks/species; offer a
project-level recheck for recovery/backfills.

- Same evidence: update the existing occurrence.
- Disagreement removed: resolve automatically.
- Supporting records removed: mark obsolete as appropriate.
- Materially changed evidence: reopen or record a new occurrence with history.

Acknowledgement records a human decision; it does not mutate annotation data.

### Returned submissions

For the first release, retain the baseline needed for comparison and accepted-task
provenance, including package/task identities and submitted completion/absence
claims. Stable source identities and baseline-aware comparison distinguish unchanged
old values from competing edits. Record overwrite-or-skip decisions with author/time,
but do not store skipped payloads or create pending submissions for them. Users
retain the original import package if they want to inspect or retry skipped tasks.
Later source edits must not erase accepted historical submitted completion claims;
source completion reflects only consistently accepted task evidence.

Later reconciliation may add stored alternative returned payloads, source references,
and reconciliation status. An issue row alone would not preserve an alternative
returned answer; that storage and workflow are outside the first-release scope.

Do not match independent animals solely by local individual number. Do not
silently change ordinary Species Review labels from a Distance discrepancy.

## Proposed frontend structure

### Project inbox

Review issues page with counts and filters for type, status, species, camera, and
annotator. Prefer grouping by video so related discrepancies can be inspected
against the same footage.

Each row shows video, target species/task where applicable, issue explanation,
compared values, and status. Contextual
badges in Species Review, Distance Review, dashboard, and import summaries should
open the same issue detail rather than separate resolution systems.

### Detail view

Common structure: plain-language explanation, video/context, evidence from each
review, source/annotator information, relevant actions, and history.

For count mismatch, show Species Review count versus Distance individuals and
Open Species Review / Open Distance task links. Initial actions are recheck and
acknowledge, with corrections made in the existing review screens.

For outside-scope species, show the annotation and project scope, with correction
links. A direct Add species to project action is a possible later convenience.

For returned conflicts, the initial import summary shows the importer's per-task
overwrite-or-skip decision. Skipped evidence is not available in an in-app detail
view; inspecting or retrying it requires the original package. Record import
decisions separately from warning acknowledgement.

Later offer type-appropriate Keep current, Accept returned,
or Resolve manually actions. Do not provide a universal Keep both action that
could turn alternative edits into duplicate animals.

## Safety and unresolved details

- No generic Resolve button that only hides a problem; distinguish correction,
  acknowledgement, and data-changing reconciliation.
- Warnings should not block ordinary annotation. Block only unsafe import/merge
  operations that require a decision.
- Revalidate evidence before applying a data-changing resolution, and show its
  consequences. Retain decision provenance.
- Exact issue schema, finer-grained merge actions, broader species-set comparison
  rules, duplicate matching, severity choices, and detailed layouts remain
  implementation design. The initial per-task overwrite-or-skip behavior is
  specified in the work-package plan.
- Keep scope proportional: do not implement the entire future reconciliation
  system as a prerequisite for Distance work packages.
