# Assignment handoff

**Status:** Agreed product design. Not a description of shipped package behavior.
Serialization and storage design remain undecided.

## Purpose

An admin delegates a defined piece of annotation work, an annotator works in a
fresh project on another instance, and the admin accepts returned progress into
the originating project. This is assignment handoff, not general-purpose project
transfer or synchronization.

The interface is:

**Prepare assignment → Open assignment → Return progress → Preview and accept**

Packaging, dependency collection, identity mapping, and scope validation belong
inside the module's implementation. Users should not assemble tables or choose
which dependencies make a package valid.

## Assignment scope

Each assignment has exactly one stage:

| Stage | Assigned work | Whole-work replacement unit |
| --- | --- | --- |
| Species Review | Selected videos | Complete Species Review state for one video |
| Distance Review | Selected videos for exactly one target species | Complete Distance task for one video and that species |

An assignment may contain many videos. An assignment is not a Distance task: a
Distance assignment distributes multiple video/species tasks. Two target species
require two assignments and two annotator projects.

Distance selection is species-focused. Use confirmed Species Review results to
identify eligible videos, not unconfirmed model predictions. Retain the existing
recurring camera-local start-time filter where useful; its current selection rules
are documented in [manual Distance export/import](./distance-manual-export-import.md#confirmed-species-and-time-selection).
Distance assignment selection is independent of ordinary Species assignments.

Remember the originating assignment, source admin project, recipient, stage, and
selected work. The annotator must not reconstruct this selection when returning
progress. Assignment scope must not silently change when source annotations change.
Returned Distance tasks remain eligible if the admin subsequently changes or
removes the source Species Review labels. Show changed source context as a warning;
accepting Distance work does not restore or modify those labels.

## Prepare and open

Preparing an assignment collects the assigned work and everything required to
annotate it:

- Species Review: required vocabulary, predictions, metadata, and review context.
- Distance Review: confirmed species context, relevant Distance state and
  predictions, calibration references, required vocabulary, and provenance.

Preserve editable state, attribution, alignment, relationships, completion/absence,
resume state, and relevant AI provenance rather than silently discarding them.
Existing task-level annotation and completion rules remain unchanged.

Opening an assignment creates a **fresh project**, never merges setup data into
an existing annotator project. A Distance project is focused on its one target
species. Carried context is not permission to edit or return unrelated work.

### Footage stays separate

Videos are external to the annotation package. Preparing an assignment optionally
copies the required footage alongside it, using the same saved assignment scope.
Users can also deliver footage separately and locate it when opening the project.

Collect reference assets and account for any additional reference footage needed
by Distance annotation automatically. Explain the required footage; do not make
the admin manually discover dependencies.

Illustrative delivery arrangement, not a fixed archive layout:

```text
assignment/
  assignment.zip
  videos/
    camera-a/...
    camera-b/...
```

Return exports contain annotation data, not another copy of the videos.

## Partial progress and repeated returns

Annotators may return partial progress and continue working. A return is a
checkpoint, not assignment completion or closure.

Each return contains all current saved state in the assignment scope as the
annotator sees it, including unfinished work, untouched carried annotations,
and empty video/task states. Do not filter returns by whether the annotator edited
or visited a work unit.
Repeated returns use the same assignment scope and whole-work replacement units.
No baseline-aware synchronization history is required.

- Identical work is a no-op and must not create duplicates.
- New work is added.
- Different existing work follows the selected conflict policy.
- An absent work unit does not imply deletion.
- A whole-work snapshot can represent deletions, including an empty state that
  clears existing annotations under Use imported. Warn about these removals before
  acceptance, including when the returned unit is unfinished.

An empty unfinished Distance task remains distinct from the explicit completed
outcome **No target species visible**. Neither requires a separate change-tracking
mechanism to be included in a return.

## Preview and accept

Validate the originating assignment and admin project before applying returned
work. Only assigned work for the assignment's stage is eligible:

- Species returns update Species Review state for assigned videos, not Distance.
- Distance returns update assigned tasks for the target species, not Species
  Review labels or other species' tasks.
- Work outside the assignment stays untouched.
- Shared reference context follows the reference mapping rules below.

The admin chooses one policy once for all eligible work in the return, with no
per-video or per-task policy selection:

| Situation | Keep existing — default | Use imported |
| --- | --- | --- |
| New work | Add | Add |
| Identical work | No-op | No-op |
| Different existing work | Preserve local unit; do not apply returned unit | Replace local unit with returned unit |

Do not merge individual fields, individuals, observations, or samples within a
replacement unit. For example, an extra observation within an existing Species
video snapshot is a difference between whole video states, not an independent
addition to merge automatically.

Existing work includes progress accepted from an earlier return, not just admin
edits. **Keep existing can leave an annotator's later updates unapplied.** Make
this consequence explicit; Use imported accepts those updated versions. There is
no attempt to distinguish concurrent edits from changes relative to handout.
Use imported also replaces admin corrections made since an earlier return; the
preview must make the resulting replacements and removals clear.

### Distance reference mapping

A reference identifies a reference video, a timestamp within it, and a distance.
Match references by reference video and distance:

- If that pair exists in the admin project, reuse the admin's reference and
  timestamp. Accepted samples retain their returned distance; explain any frame
  substitution in the preview.
- Otherwise, import the annotator's reference. A timestamp already used for a
  different distance does not block acceptance: accept the returned reference
  without replacing the admin's other calibration.

Enforce one reference per reference-video/distance pair at database level. The
current schema permits repeated pairs and instead makes video/timestamp unique;
it must be adjusted to support the agreed rule. Distance annotation is not yet in
use, so legacy duplicate handling does not need a product workflow.

### Explain the impact before confirmation

For the selected policy, quantify:

- New work added.
- Identical work left unchanged.
- Existing work replaced.
- Existing annotations removed as part of replacement.
- Returned work left unapplied because existing work is kept.

Show affected video/task counts and meaningful annotation counts, with affected
videos identifiable. Do not hide destructive consequences behind a generic
conflict count or describe unapplied returned work as imported.

### Validation is not a conflict policy

Use imported does not override integrity checks. Validate scope, ownership,
relationships, media mapping, and required dependencies. Reference timestamp
differences are handled by the mapping rules above, not treated as blocking
calibration conflicts. Preserve unrelated work and block invalid imports with an
explanation rather than introduce another conflict editor.

Preview before applying, revalidate when applying, and apply the eligible import
atomically. A failure must leave destination annotation state unchanged.

## Module design

Design one assignment-handoff module with a small interface. Species and Distance
have different internal implementations for collecting context and restoring work;
they do not need a universal annotation data model or separate transfer systems.

Preparing setup and accepting returned annotations are deliberately different
operations. Sharing serialization does not require a general-purpose import
interface or the same carried content in both directions.

Depth means callers get a valid assignment and a safe, explained return without
coordinating format mechanics. Locality means scope selection, dependency rules,
and restoration knowledge each have one home rather than being repeated across
package export, footage copying, the UI, and import callers.

## Existing code to reuse

These are implementation inputs, not sufficient implementations of this design:

- `review_app/backend/provider/import_service/_bundles.py`: Species setup bundles
  and per-annotator bundle export.
- `review_app/backend/provider/assignment_service.py`: assignment selection and
  `export_annotator_videos()` footage copying with progress reporting.
- `review_app/app/pages/distribution/page.py`: existing bundle and footage controls.
- `review_app/backend/provider/distance_transfer.py` and
  `review_app/backend/distance_transfer.py`: current manual Distance snapshots.

Current Species bundle export excludes assigned videos with Species labels;
footage export selects all assigned non-missing videos. Do not reproduce these
independent scope queries: package generation and footage copying must consume
the same saved assignment scope.

The [manual Distance transfer contract](./distance-manual-export-import.md)
documents shipped behavior, not the future assignment format.

## Not required

- Arbitrary cross-project reconciliation or peer-to-peer synchronization.
- Moving an ongoing project between machines through this workflow.
- Importing assignment setup into an existing annotator project.
- Mixed-stage or multi-species Distance assignments.
- Locking admin work while an assignment is away.
- Baseline-aware change detection, per-record choices, or a merge editor.
- A pending-return resolution inbox or a general warnings dashboard as a prerequisite.
- Embedding footage in annotation archives.
- Bundling analysis tables into every assignment or return.

Analysis exports and external-data imports may exist independently; they must not
dictate the assignment-handoff interface.

## Next design decision

Compare two implementation designs against this workflow:

1. Explicit serialized assignment snapshots.
2. Scoped project-database snapshots.

Evaluate interface depth, locality, dependency handling, safe return application,
and maintenance cost. Inspect existing project storage before choosing. Neither
is selected yet. ZIP layout, table formats, versioning details, and storage schema
must follow that decision, not lead it.
