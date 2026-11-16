# Data, assignments, and results: UX rework

**Status:** Agreed UI direction; initial implementation and unified assignment
workspace are in place, with operator visual confirmation pending.
**Scope clarification:** This is a reorganization and simplification of the
existing UI, not a transfer-functionality expansion. The operator is happy with
functionality in general. Earlier requirements for persistent preparation batches,
advanced batch delivery and related new backend capabilities are withdrawn from
this rework. Existing transfer behavior remains authoritative.

See the [implementation plan and tracker](data-and-assignments-ux-implementation-plan.md)
for the UI slices, existing code to reuse, and focused validation.

## Purpose

Give users a coherent way to add project data, delegate review, return progress,
and export research results without learning the application's transfer formats.

The current functionality is spread across Import / Export, Distribution,
Settings, and project setup. Related actions appear in different places, while
operations with very different consequences share generic import/export labels.
Replace that fragmentation rather than add another navigation layer over it.

## Scope

In scope:

- One main **Import / Export** destination (**Importation / Exportation** in French)
  with **Add data**, **Assignments**, and **Export results** areas.
- Relocating existing workflows into these areas, with clearer labels, hierarchy,
  help text, counts, busy/error states and summaries.
- Keeping received-assignment opening in the project picker, vocabulary/configuration
  in Settings, and database backup/recovery and diagnostic logs in Settings.
- Making saved assignments and useful actions more prominent than preparation forms.
- Sensible default selections and collapsed advanced controls using existing
  capabilities. Avoid making users select videos unnecessarily.
- Reusing existing renderers and backend operations, with local refactoring where
  it removes actual duplicated UI or coordination.
- Removing superseded forms and making contextual shortcuts invoke the same workflows.
- Preserving independent use and English/French coverage.

Not required:

- Persistent preparation batches, batch history, durable job/entry state or a new
  retry/resume subsystem.
- Advanced multi-recipient delivery, nested recipient ZIP layouts, coordinated
  delivery-attempt management or a durable bulk-copy subsystem.
- New allocation storage or algorithms, including a new Distance species-allocation
  subsystem.
- New export schemas, completion columns, scientific dataset eligibility rules,
  or changes to existing CSV/TSV formats.
- A new scope-resolution framework, expanded filter backend or universal transfer engine.
- New import/conflict policies or a rewrite of format-specific preview/apply behavior,
  except the subsequently requested Species-only Append described in the
  [handoff contract](assignment-handoff.md#species-only-append).
- Broader package recognition or a backend rewrite as a prerequisite for the UI.

Small backend adjustments may be appropriate when directly necessary for a UI
improvement, but they are not a separate workstream. If an improvement requires
schema changes or materially different transfer behavior, defer it and surface
that trade-off instead of quietly expanding this plan. The explicitly agreed
exceptions are a persistent removal timestamp (retaining return authority),
Species-only Append, package-content inspection, and local recipient-name
reconciliation. These are bounded follow-ups, not a universal transfer engine.

## Domain language and existing contracts

Use [GLOSSARY.md](../../GLOSSARY.md) for canonical language:

- An **assignment** is defined work delegated for one review stage.
- A **Distance task** is work for one video and one target species.
- A **return** is a checkpoint of all saved state in assignment scope. It does not
  close the assignment or prevent continued annotation.

A package is a delivery file, not another name for delegated work. CSV and ZIP
are formats, not top-level user intentions.

The [assignment handoff design](assignment-handoff.md) remains authoritative for
scope, snapshots, stage isolation, return acceptance, references and provenance.
The legacy [manual Distance transfer contract](distance-manual-export-import.md)
is historical. Editable Distance work uses assignment packages; standalone
snapshot export and restoration controls have been removed.

Do not change those contracts merely to make screens look consistent. Existing
saved assignments already provide frozen scope and saved-package downloads;
retaining saved scope does not require a new batch concept. The current UI writes
setup ZIPs through delivery folders rather than browser downloads; return ZIPs
remain browser downloads.

## Information architecture

```text
Project picker
  Create project
  Open assignment/package

Inside a project
  Overview
  Species
  Distance
  Import / Export
    Add data
    Assignments
    Export results
  Settings
    Vocabulary and configuration
    Backup & recovery
```

Contextual shortcuts lead into the same workflows. Overview may show **Return
progress**, and review views may link to **Export results**. These are not
separate implementations with different semantics.

## Add data

Choose the meaning of incoming data before its format:

| Entry | Existing content and purpose |
| --- | --- |
| AI predictions | Supported Species/behavior/blank/detection predictions and Distance prediction packages |
| Video metadata | Recording times, camera information and locations supported by the current importer |
| Existing annotations | Historic/external spreadsheets and app-format Species CSV |

Vocabulary imports remain alongside configuration, with a link from Add data.
They configure annotation language rather than add observations.

Use a recognizable file/configuration/preview/apply/summary presentation where
supported. Reuse existing mapping, validation and mutation rules; do not invent
a richer impact preview that the current implementation cannot provide.

- Keep the destination project visible.
- Preserve explicit apply and preview-invalidation safeguards where present.
- Explain AI suggestions versus human annotations, append/update/replace behavior,
  unmatched paths, manual-only restrictions and other existing limitations.
- Do not imply ordinary metadata is verified Distance timing.
- Report actual outcomes and errors without describing unapplied work as imported.
- For legacy paths with different behavior, explain that behavior rather than
  silently changing it during relocation. For example, legacy batch CSV intake
  currently imports on upload; label that consequence and preserve its limitations.

Share presentation where it genuinely agrees. Parsing, mapping, validation,
atomicity and mutation rules remain format-specific.

## Assignments

### Coordinator workspace

Use a compact saved-assignment list as the home screen, with recipient, stage,
target species where relevant, saved scope/counts and a readable preparation date.
Open download/copy actions and scope details in a detail panel; put technical IDs
and raw timestamps behind disclosure. Do not invent delivery or acceptance status.
Offer confirmed removal from delivery, retaining the original snapshot and identity
for future returns. This requires a small persistent removal-state migration, not
physical deletion of the handoff or its annotation work.

The primary **Assign work** action opens one focused workflow to choose the stage,
distribute work to recipients, inspect counts, and prepare assignments. Allocation
and preparation are not separate user-facing workflows: do not require saving an
allocation, navigating elsewhere, and selecting that allocation again.

For Species, prepare only from the displayed allocation; remove the separate
all-project-videos shortcut. Reuse automatic distribution and manual adjustment.
For Distance, show compact rows of target species, recipient, count and an options
action. Hide video/time controls until requested, select the full matching set by
default, and group review entries by annotator without merging their assignments.
Preserve one assignment and working project per Distance target species.

The meaningful boundary is editable work planning versus prepared, frozen
assignments. Preparation freezes each row's work; subsequent redistribution must
not change existing assignments. On success, expose the saved assignment's
delivery action rather than another preparation form. **Save delivery folder**
writes the saved assignment ZIP and optionally copies its required footage alongside
it. Footage is off by default when original files can be reused on the same machine.
Setup ZIP delivery currently uses this filesystem action only; a secondary browser
setup download is not part of the current follow-up scope. Return downloads remain
available. Do not embed videos in ZIPs.

Keep the existing prepare/delivery and preview/accept operations. During footage
copying, show attempted/total files and a progress bar, including missing/failed
attempts. Show per-recipient camera/video counts, known footage hours and assigned
camera/chunk labels while planning and reviewing; saved details use frozen work
metadata, not live redistribution.
**Accept returned work** opens a separate focused flow with full impact warnings,
not another accordion on the home screen.

Prefer existing allocation and matching-set defaults over unnecessary video picking.
Where current controls already support a complete matching set, select it by
default and make inspection or narrowing optional. Preserve eligibility and
completion rules; do not add a new allocation or filtering system to remove a
control.

Preparing multiple distribution rows may orchestrate the existing single-assignment
operation in the UI, with each row's success or failure made explicit. This is not
an atomic batch or a requirement for persistent batch history, new allocation
storage, coordinated delivery, or durable retries. ZIP saving and footage copying
use individual saved assignments, combined in the local delivery-folder action.

Saved ZIP delivery and footage copying continue to use saved assignment scope and
automatically collected dependencies. Missing footage and copy failures remain
visible. Downloads and filesystem delivery must be clearly distinguished.
Video-detail tables support text search, sortable columns and relevant camera,
footage, role or return-action filters. Inspection filters do not silently drop
selected work: retain selection outside the filter and provide explicit actions
to select/deselect matching videos.

### Annotator workspace

Opening shows a compact **Included in this package** inventory for both assignment
and legacy setup formats. Explain vocabulary, predictions, existing human work,
and Distance task/reference dependencies without foregrounding UUIDs. A local
annotator selector reconciles the recipient with the current login while keeping
portable identity and historical attribution unchanged; legacy bundles remain
independent projects without returns.

Make **Continue review** and **Return progress** easy to find. A compact assignment
summary or shortcut on Overview can reuse the existing assignment operation.
Explain that returning includes all saved in-scope state and reviewing can continue.

Assignment scope is not an editing restriction. Local edits outside it must not
silently enter that assignment's return.

A successful download means **return file downloaded**, not remotely delivered or
accepted. Do not invent receipt status.

### Accept returned progress

Retain originating-project identity, existing whole-return conflict policies,
impact preview, reference mapping, stale-preview protection and atomic acceptance.
Make replacement/removal consequences legible without weakening safeguards or
adding a new conflict editor. Use assignment packages for editable Distance work.

## Export results

Give scientific outputs their own home instead of placing download buttons inside
annotation-import screens.

| Output | Purpose | Existing format |
| --- | --- | --- |
| Species observations | Human annotation data with existing blank/review-state semantics | CSV |
| Distance measurements | Saved samples, including visibility skips | TSV |
| AI predictions | Supported raw model output for inspection/comparison | CSV |
| Species-selected footage | Copy footage into species folders | Filesystem copy |

Reuse current export functions and supported scopes. Present dataset choice,
existing scope controls and contents clearly before download/copy. Put optional
narrowing controls under **Advanced options** where that improves hierarchy;
required stage/target-species choices stay visible.

Explain what a row represents, timing basis, units, attribution and relevant
limitations using the data the existing export provides. In particular:

- Human answers and AI suggestions are different datasets.
- Species context rows without observations are not confirmed evidence of presence.
- No Distance sample row does not necessarily mean confirmed absence.
- The Distance analysis table is not an editable whole-task snapshot.
- Manual Distance/AI-linked exclusions remain visible.

Preserve existing column names, file formatting, scientific semantics and
compatibility. New completion-state columns or eligibility changes are not part
of this UI rework. Do not hide existing unfinished saved work or imply stronger
review/completion guarantees than the output actually records.

Footage copying states its destination on the computer running the app; file
downloads state that they produce downloadable artifacts.

## Existing filters and scope controls

Retain applicable existing filters and their rules rather than building a
universal scope framework. In particular, preserve the Distance recurring
camera-local start-time window and its existing unknown-time/boundary rules.
Do not fabricate a recording date, timezone or verified camera time.

Show counts and explain empty results where already available. A video/task list
can support inspection; it should not create needless selection work when the
existing operation can act on the full matching set.

Preparation freezes assignment scope as it does today. Later UI filter/allocation
changes must not affect saved downloads, footage dependencies or return scope.

## Opening, recovery and legacy paths

Keep the common assignment/legacy setup opening entry in the project picker.
Preserve fresh-project opening, media-location guidance and missing-footage
warnings. Improve explanatory copy where helpful; expanded format detection is
not required.

Keep backup/restore and logs under Settings, with installation/database scope
explicit. Assignment handoff is not whole-project migration or synchronization.

Move legacy batch annotation CSV intake under Existing annotations with its
limitations clear. Retire old Import / Export and Distribution forms as their
replacements land. Preserve useful old URLs as redirects or thin navigation
adapters, not a second long-lived interface.

## Consolidation and validation

Extract small shared UI helpers only when migrating real repeated behavior:
section layout, file/busy/error presentation, destination context and result
summaries. Keep existing domain knowledge in its current backend home unless a
small directly necessary adjustment is identified.

Validate the rework with focused UI tests and the existing backend regressions
for the operations touched:

- Main and contextual entry points reach the same workflows.
- Relocated actions call the same operations for the correct project.
- Existing upload, preview/apply, conflict and stale-preview safeguards survive.
- Saved assignment scope, partial/repeated returns, stage isolation and AI
  provenance remain unchanged.
- Existing CSV/TSV and package compatibility remain unchanged.
- Old duplicate forms are gone; recovery stays in Settings; independent use works.
- Changed labels and flows have English/French coverage.

A sensible first slice is the destination with existing result exports moved into
it, followed by assignments, Add data, and final navigation/contextual cleanup.
Do not make new backend functionality a prerequisite for that sequence.
