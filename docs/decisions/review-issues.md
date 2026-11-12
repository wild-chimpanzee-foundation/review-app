# Review Issues: Warnings and Conflicts

**Status:** Agreed product direction and phased scope. Backend/frontend structure
below is a design proposal, not implemented behavior.

Related: [assignment handoff](./assignment-handoff.md).

## Purpose and terminology

Provide a fixed catalog of issues the app knows how to detect and explain, rather
than a user-configurable rules engine. Use Review issues as the umbrella section:

- **Warnings:** possible omissions or inconsistencies that need human inspection.
- **Conflicts:** competing returned answers that require a reconciliation decision.

Do not assume a discrepancy identifies which review is wrong. Do not silently
correct annotations or overwrite conflicting work. Replacing existing work requires the importer's explicit choice through the
assignment-handoff preview. Keeping existing work leaves that unit unchanged.

Two annotators reviewing the same video is not automatically a disagreement.
Distance tasks for different target species are independent, including their
alignment, and are not overlapping submissions merely because they share a video.
Divergent copies of the same task warrant comparison; equivalent answers need not
become a blocking conflict. Ordinary Species Review submissions can also overlap.

## Agreed delivery scope

### Assignment handoff

The [assignment-handoff design](./assignment-handoff.md) owns return validation,
impact previews, and Keep existing / Use imported policies for whole Species video
states or Distance tasks. It requires neither baseline-aware comparison nor
per-task conflict choices. A general Review issues page is not a prerequisite.

### QA follow-up: simple Review issues page

Four fixed checks:

| Check | Meaning |
| --- | --- |
| Animal count mismatch | Manual Species Review count differs from human individuals in the completed Distance task for that video/species. |
| Species mismatch / no target species visible | A completed Distance task reports absence against manual Species Review labels. No additional ordinary Review completion flag is required. |
| Species outside project scope | A human annotation or task target uses a species not enabled for the project. |
| Overlapping annotator submissions | Separate submissions cover the same Distance task, or overlapping ordinary Species Review work, and need comparison. Different target-species tasks alone do not trigger this check. |

Initial UI capabilities: explanation, evidence, links to existing review screens,
recheck, and acknowledgement for warnings. Corrections happen in existing review
screens. Return acceptance stays in the assignment-handoff preview; this page does
not add a merge editor or a pending-return workflow.

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

### Returned work

Compare current accepted evidence. Assignment identity, scope validation, and
whole-work replacement belong to assignment handoff; this proposal adds no
baseline storage or alternative-return archive requirements.

Do not match independent animals solely by local individual number. Do not
silently change Species Review labels from a Distance discrepancy.

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

For returned differences, use the assignment-handoff preview and its impact
summary. Keep warning acknowledgement separate from data-changing acceptance.

## Safety and unresolved details

- No generic Resolve button that only hides a problem; distinguish correction,
  acknowledgement, and data-changing reconciliation.
- Warnings should not block ordinary annotation. Block only unsafe import/merge
  operations that require a decision.
- Revalidate evidence before applying a data-changing resolution, and show its
  consequences. Retain decision provenance.
- Exact issue schema, finer-grained merge actions, broader species-set comparison
  rules, duplicate matching, severity choices, and detailed layouts remain
  implementation design. Return acceptance is specified in the assignment-handoff design.
- Keep scope proportional: do not implement the entire future reconciliation
  system as a prerequisite for assignment handoff.
