# Unified Annotation Packages and Import / Export

**Status:** Design discussion recorded; not an implementation specification or a description of shipped behavior.

## Motivation

The Model Import page is crowded and mixes unrelated user intentions: importing model predictions, restoring reviewed work, importing metadata, exporting analysis results, and transferring annotations. Formats and import semantics differ between Species Review and Distance Review.

The redesign should simplify the product model, not just rearrange existing tabs.

## Primary workflow and assumptions

One admin maintains the authoritative project and has access to all videos. Annotation work is distributed and collected in stages:

1. Admin distributes Species Review work packages for assigned video subsets.
2. Annotators complete Species Review and export return packages.
3. Admin imports the completed Species work.
4. Admin selects target species and distributes Distance Review work packages for assigned videos and those species.
5. Annotators complete Distance Review and export return packages.
6. Admin imports the completed Distance work.

Assignments are normally non-overlapping within a stage. The expected exchange is admin → annotator → same admin project, not repeated exchanges between annotators or peer-to-peer synchronization. Conflict handling is a safety mechanism for accepting returned work, not a collaborative merge system.

Ordinary project/subset transfer still uses the same package format, but arbitrary cross-project identity reconciliation and divergent synchronization histories are not first-version requirements.

## Agreed direction

### One standard annotation ZIP

Use one versioned ZIP package format for annotation import/export. A package contains **all available annotation data for the selected project or project subset**, rather than asking users to choose separate Species or Distance exports.

The package includes, where present:

- Species observations and review state.
- Distance tasks, individuals, samples, alignment, completion, and other editable state.
- Model predictions and their provenance, including AI-linked Distance work.
- Required vocabularies, metadata, references, and other dependencies.
- Predefined analysis tables.

Species and Distance remain different internal data models, but are not separate transfer workflows. Inclusion of supported annotation types is automatic, not a checklist of export options.

Required dependencies outside the selected scope must be included without automatically including all unrelated annotations belonging to those dependency sources.

### Analysis exports live inside the package

Do not make users choose between an analysis export and an editable transfer as the primary workflow. Standard analysis tables are included in the ZIP alongside round-trippable annotation data.

The proposed distinction inside the package is:

- Authoritative structured data for restoring editable state.
- Derived, predefined tables for analysis and spreadsheet use.

Derived tables must not become a second source of truth. Editing a table inside the ZIP should not silently change restored annotations. Spreadsheet editing/reimport, if supported, belongs to an explicit external-data import route.

### The same format serves annotator work packages

Annotator work packages use the standard annotation ZIP, with optional assignment metadata. They are not a second package format or a separate import/export subsystem.

Assignment creation and return follow the staged workflow above. The admin chooses the assignment scope; the annotator imports the package, completes the assigned work, and exports a return package without reconstructing that selection.

Persisted assignment metadata should stay small:

- Stable work-package ID and source admin project identity.
- Assigned annotator.
- Assigned videos.
- Stage: Species Review or Distance Review.
- Target species for a Distance Review assignment.
- Originating assignment identity on return.

### Carried context is distinct from return write scope

An assignment does not filter which annotation types are carried in the package. Existing annotations, predictions, and required dependencies remain available as context. However, not all carried content is eligible to replace admin data when the package returns:

| Assignment stage | Context carried in package | Work eligible for application on return |
|---|---|---|
| Species Review | Media metadata, predictions, vocabularies, and existing annotations | Species Review state for assigned videos |
| Distance Review | Confirmed Species results, references, predictions, and existing annotations | Distance tasks for assigned videos and target species |

For example, accepting a Distance return must not replace the admin's Species labels with older labels carried as context. Scope restrictions govern return application; they do not introduce a second package format or per-record conflict choices.

Required dependencies for accepted work may be installed if missing and compatible, but carried context is not permission to overwrite existing shared dependencies.

The app remembers assignment scope on both sides. The admin validates a return against the stored assignment and source project before applying work. The global conflict policy applies only within that assignment's return write scope. Ordinary project/subset imports instead apply to the annotation units declared by the package, without assignment-stage restrictions.

### One global conflict policy at import time

The user chooses between:

- **Keep existing work:** import new work, but preserve existing work on conflict.
- **Use imported work:** import new work and replace existing work on conflict.

Apply that choice across all eligible work units in the import. For assignment returns, eligibility is restricted by the stored assignment's videos, stage, and target species. **No elaborate conflict resolution, per-record decisions, or merge editor for now.** Do not infer the winner from timestamps.

Basic semantics:

| Situation | Keep existing work | Use imported work |
|---|---|---|
| New work | Import | Import |
| Identical work | No-op | No-op |
| Conflicting work | Keep local version | Use package version |
| Destination work outside eligible import scope, including carried context on assignment returns | Leave untouched | Leave untouched |

The preview should show the assignment and eligible scope, additions, unchanged work, conflicts, unmatched media, and the effect of the selected policy before applying changes. **Use imported work** is the deliberate way to accept a returned version when it conflicts with existing admin work; the safe default remains **Keep existing work**.

## Proposed supporting rules

The following details were proposed during the discussion. They support the direction above, but need to be made precise during format and implementation design.

### Scope and media

- Start with video-based subsets, rather than arbitrary observation-level slices.
- Include all annotation types for the selected videos; assignment return write scope remains restricted to the assigned stage and, for Distance Review, target species.
- Keep videos external initially; make this prominent on export and import.
- Match media roots and videos during import, reporting missing or ambiguous media.
- Include required non-video assets such as portable reference assets.
- Video-file inclusion can be considered later.

The exact selection UI, including how existing species/time filters feed a video subset, remains to be designed.

### Coherent replacement units

The global policy should act on coherent work units, not merge individual fields. Candidate units include the complete Species Review state for a video and a complete Distance task.

Define the units and dependency boundaries needed for this workflow before implementation. A single global choice does not mean replacing the whole destination project or applying all carried context on a return.

Classification is at work-unit level: an additional observation within an existing complete Species video snapshot is a conflict between snapshots, not an independently mergeable addition. Define how untouched/default state is distinguished from existing annotation work so generated empty Distance tasks do not unnecessarily block incoming completed work.

### Explicit snapshots and deletions

Native packages should declare which work units are complete snapshots, including explicitly empty snapshots.

- With **Use imported work**, an incoming complete snapshot replaces the conflicting local unit, including intentional deletions.
- With **Keep existing work**, the local conflicting unit stays unchanged.
- A work unit absent from the package does not imply deletion.

Manifest scope must distinguish absence, explicitly empty work, and content unsupported by a package version. Otherwise an older or partial package could accidentally erase destination data.

### Validation is separate from conflict policy

“Use imported work” must not bypass validation or integrity constraints.

For example, replacing shared reference calibration must not silently change unrelated destination tasks. Unsafe replacements should be blocked and explained rather than introducing a complex conflict editor.

For the first version, validate the eligible import and its required dependencies, then apply it atomically. Unresolved or ambiguous media, unsupported eligible content, incompatible dependencies, or invalid assignment identity/scope block the import with an explanation. Revalidate at apply time; failures must leave destination annotation state unchanged. Partial-import planning is not required.

### Safe defaults and repeat imports

- Proposed default: **Keep existing work**.
- Preserve portable entity identities across the admin → annotator → same admin project exchange, mapping destination-local media and vocabulary identities as needed.
- Unsupported identity collisions should fail clearly; general reconciliation across unrelated projects is not required.
- Reimporting unchanged native data should not create duplicates.
- Assignment identity and stored scope are sufficient for first-version return tracking; a general synchronization history is not required.
- Keep predictions, pending work, and human-confirmed annotations distinguishable.
- Never silently drop unsupported annotation types or provenance.

## Proposed package layout

Illustrative only; filenames and serialization choices are not yet fixed:

```text
project_annotations.zip
├── manifest.json
├── data/
│   ├── media.jsonl
│   ├── species_observations.jsonl
│   ├── distance_tasks.jsonl
│   ├── model_predictions.jsonl
│   └── ...
├── vocabulary/
│   ├── species.json
│   └── tags.json
├── assets/
│   └── references/...
└── tables/
    ├── species_observations.csv
    ├── distance_samples.csv
    └── model_predictions.csv
```

The manifest should identify the format/schema version, source project, exported content scope, content coverage, and optional assignment/return metadata. For assignments, distinguish the carried content scope from the return write scope, including stage and Distance target species.

Structured data preserves editable state, stable IDs, relationships, provenance, and review state. Analysis tables are generated from the same snapshot.

Table conventions should be consistent across domains: UTF-8, stable non-translated column names, documented delimiter and decimal conventions, units, timestamp semantics, booleans, missing values, and identifier handling.

## Proposed UI

Replace the crowded Model Import page with a simple annotation transfer entry point:

```text
Annotations / Import & Export

[Export package]    [Import package]

Import external data…
```

Package export:

1. Select whole project or project subset.
2. Show included content and dependencies, and explain that videos remain external.
3. Export one ZIP.

Package import:

1. Choose ZIP.
2. Match videos / media roots.
3. For a return, validate its originating assignment and show its eligible write scope separately from carried context.
4. Select the global conflict policy and preview its effect, including any blocking validation errors.
5. Confirm import and show the outcome.

Work-package creation adds assignment information to the same export machinery: assigned videos and annotator, Species or Distance stage, and target species for Distance. Return export reuses the stored assignment scope. The primary assignment flow is Species distribution and collection, followed by Distance distribution and collection.

Exact page naming and placement of work-package creation controls remain open.

## External and legacy formats

Keep external model CSVs, historic spreadsheets, existing AI Distance packages, and legacy app exports supported through a secondary external-data import route or compatibility adapters.

Native package users should not need column mapping. External data may still require format detection, column mapping, species mapping, and media matching.

External producers can adopt the standard package contract over time; this is not a prerequisite for the redesign.

## Deliberate limitations

- The first conflict model is replacement, not collaborative merging.
- If admin and annotator independently edit the same eligible work unit, one whole version wins.
- Within the assigned return write scope, unchanged older work can conflict with newer admin work. **Use imported work** may restore the older version. Carried context outside that write scope is not applied.
- Use non-overlapping assignments within each stage rather than attempting to merge concurrent edits.
- Baseline-aware return merging, per-record conflict resolution, a merge editor, peer-to-peer exchanges, and general synchronization histories are out of scope for the first version.
- Arbitrary identity reconciliation across unrelated projects and dependency-consistent partial imports are not first-version requirements.
- A package is not a complete project backup while videos or other recovery requirements remain external.

## Implementation direction

Define the package contract first, then build the simplified UI around it. Reuse domain-specific validation and persistence rules behind shared package read/write, preview, and apply infrastructure.

Core acceptance criterion:

> Export a supported project selection, import it into a project with matching media, and recover its annotation state, relationships, dependencies, and provenance. Import it again without creating duplicates.

Additional checks should focus on the actual staged workflow:

- Species assignment delivery, completion, and return to the admin project.
- Distance assignment delivery and return for selected species after Species results have been collected.
- Remembered assignment scope and validation of return identity.
- Preservation of admin Species state and unrelated Distance tasks when accepting a Distance return.
- Both global conflict policies, complete-snapshot deletions within eligible scope, and repeat-import safety.
- Required reference/provenance dependencies without unrelated annotation leakage.
- Atomic failure on unmatched media, invalid content, or unsafe shared dependencies.

Reuse existing domain models and validation where possible. Build the lossless Species snapshot path and extend Distance transfer behind shared package read/write, preview, and transactional apply infrastructure. Add only the assignment persistence needed to remember scope and recognize returns; do not build a general collaboration or synchronization subsystem.

Current manual Distance transfer does not support AI-linked tasks/references. That gap must be closed before claiming universal annotation coverage; unsupported content must be rejected explicitly, not omitted.

## Relationship to earlier plans

- [Manual Distance export/import](./distance-manual-export-import.md) documents the existing specialised transfer feature.
- [Distance annotator tasks and work packages](./distance-work-packages-plan.md) contains earlier Distance-specific package plans, including per-task conflict choices.
- [Reference-based Distance annotation](./reference-based-distance-annotation.md) documents the broader Distance workflow.

This discussion changes the planned transfer direction to **one cross-domain package format and one global conflict choice per import**. It does not change Distance task identity or annotation rules, nor does it claim the existing specialised implementations have already been replaced. Older plans should be reconciled when implementation is scoped.
