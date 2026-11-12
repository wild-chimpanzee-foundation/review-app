# Manual Distance Annotation Export and Import

**Status:** Implemented initial manual-only round trip and analysis export under
**Model Import → Manual Distance**. Assignment handoff remains a separate, unimplemented workflow.

Related: [current Distance workflow](./reference-based-distance-annotation.md)
and [assignment handoff](./assignment-handoff.md).

## Scope and delivery order

Start with exporting and importing saved manual Distance annotations. Keep this
separate from recipient management, package generation, AI input packages, and
assignment returns. Ordinary Species Review labels
are never modified as a side effect of importing Distance annotations.

Two outputs are available:

- **Analysis TSV:** one row per saved sample, including visibility skips. It is
  not an editable backup; empty/explicitly absent tasks have no sample rows.
- **Editable ZIP:** restores whole tasks, timing, individual identity, references,
  visibility skips, relationships, completion/history, and resume state.

Version 1 deliberately rejects AI-linked tasks, individuals, samples, and selected
references rather than dropping their provenance. Imported AI schedules and
rejected-AI history are also outside this manual format. AI input ZIPs, ordinary
project bundles, and future work-package returns are different formats.

## Confirmed species and time selection

Per-species export selects videos across all days using a recurring camera-local
**time-of-day window**, for example **18:00–20:00 each day**. Match the video's
**start time only**; overlap alone does not qualify. A video starting inside the
window is included in full even if it ends outside it. This is not an absolute
date/time interval or a playback interval within a video.

**Showing the species** means a manually confirmed Species Review label for the
selected species, with positive or unspecified count. Unconfirmed predictions,
a Distance task label, or Distance samples alone do not establish eligibility.
An explicit Distance absence outcome may coexist with a positive source Species
Review label and is preserved as such.

Selection is independent of ordinary annotator assignments and the open day/video.
Preview the set, then select which matching tasks to download. AI-linked tasks are
shown with an error and are not preselected.

Initial boundary rules:

- Start inclusive, end exclusive: `[18:00, 20:00)`.
- End earlier than start means an overnight window, e.g. `[22:00, 02:00)`.
- Both fields empty means all times; one missing boundary or equal boundaries
  is rejected. Use 24-hour time inputs.
- Use the task's verified `recorded_start`, not file creation metadata. Alignment
  may be valid while absolute camera time is unknown. Such videos are listed
  explicitly as excluded from a time-filtered export; an unfiltered export can
  still include them. No file timestamp is silently promoted to camera time.
- Editable exports always retain whole tasks. Omitted samples never imply absence
  or deletion; only an explicit whole-task replace action removes current evidence.

## Version 1 archive

```text
distance-annotations.zip
  manifest.json
  tasks.tsv
  individuals.tsv
  samples.tsv
  references.tsv
  progress.tsv
  species.tsv
  behaviors.tsv
  tags.tsv
```

The manifest identifies `review-app.distance-manual`, integer version `1`, the
source project ID, and media-root IDs. It contains no absolute source-machine paths.

- `tasks.tsv`: stable task ID, root-relative video path, camera, duration, scientific
  species name, original source count, alignment/precise anchor positions, schedule,
  current completion/absence and history, attribution. An empty or unfinished task
  does not become completed just because it was exported.
- `individuals.tsv`: stable UUID and task ID, individual number, age/sex (including
  explicit Unknown), first-seen positions, identifying box, initial carrier link,
  individual tags with attribution, and individual completion.
- `samples.tsv`: stable UUID, individual ID, integer `offset_ms` sample key, saved
  annotated/skipped state, calibrated reference ID and distance or visibility-skip
  reason, sample-specific carrier, behavior keys and attribution.
- `references.tsv`: selected reference identities, calibrated fractional distances,
  precise frame positions, camera/root-relative media path, and inline mask context
  where present. Includes dependencies used by the exported samples, not a complete
  backup of every unused calibration in the project.
- `progress.tsv`: task/annotator bookmark, active individual and update time.
- Vocabulary tables: definitions for the used scientific species names, behavior
  keys and individual-tag keys. Local vocabulary IDs are mapped by name/key.

Playback alignment and precise anchor/first-seen/reference positions are retained
as stored. A sample's integer schedule key is not a claim about an independently
saved inspected playback frame. Camera time, playback position, and annotation
creation time remain distinct; unknown camera time stays empty.

Footage remains separate. Media paths are relative to stable source root IDs;
most-specific registered roots take precedence. On import, match those roots to
local project folders. Matching root IDs are reused; a single source/destination
root can map automatically. Ambiguous multi-root mappings require an explicit
choice. Traversal/absolute media paths are rejected and no footage is extracted.

## Restore semantics and safety

Upload, map roots, inspect the per-task preview, and explicitly choose **Replace
task** or **Skip**. The default is skip. Videos must already be imported/synced in
the destination project; camera/deployment and duration must match. Missing or
incompatible dependencies are reported per task before restoration.

- Replace restores that task's complete evidence and corresponding completion
  atomically. It removes current individuals, samples and bookmarks for that task,
  not another species task for the same video. This is not a fine-grained merge.
- Existing canonical destination task/vocabulary identities are reused where
  appropriate; otherwise source task/entity IDs are retained. An entity ID already
  owned by another task/project is a conflict, not permission to overwrite it.
- Shared references must match the exported frame position, distance, mask and
  deployment. Conflicting calibration blocks that task; it is never silently
  overwritten, even when replacing task evidence. Other independent tasks can
  still import.
- Validate graph ownership, schedules, boxes/attributes, carrier dependencies,
  reference distances, vocabulary, explicit absence and completion consistency.
- Revalidate under a database writer reservation and apply all accepted tasks in
  one transaction. A failed write rolls back the accepted batch, including newly
  installed dependencies. Exports read a consistent database snapshot.
- Reimporting the same snapshot does not duplicate evidence or completion history.
- Skips leave source data untouched. The app does not retain skipped payloads or
  archive the upload. The UI lists imported/skipped task IDs; keep the original ZIP
  to retry. Imports do not update ordinary labels, assignments or video metadata.

This initial manual restore is separate from the assignment-handoff contract.

## Delimiters, decimals and spreadsheet locales

The canonical format is UTF-8 **tab-separated** text with stable header names,
decimal point `.`, no thousands separators, ISO dates/times, and empty fields for
missing values. Zero and explicit Unknown remain distinct. Lists/objects use JSON
inside properly quoted TSV cells; boolean columns use `0`/`1`. The reader tolerates
a UTF-8 BOM and common line endings, but does not guess delimiters or decimal locales.
Archives are limited to 50 MiB compressed and 100 MiB uncompressed.

Tabs avoid comma-versus-decimal-comma delimiter collisions, but do not force Excel
or LibreOffice to interpret decimal points correctly. Use Text/CSV Import with tab
as delimiter, a decimal-point numeric locale, and identifiers imported as text.
Double-clicking a TSV is not a reliable cross-locale workflow. Do not globally
replace commas or guess ambiguous numbers such as `1,234`.

An optional typed XLSX analysis export and externally edited decimal-comma imports
remain deferred. The editable archive is an application snapshot, not a supported
spreadsheet-editing or whole-database-backup contract.
