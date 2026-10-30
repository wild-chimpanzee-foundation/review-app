# Manual Distance Annotation Export and Import

**Status:** Planning notes; not implemented. Establish a manual annotation
export/import round trip before building annotator work-package generation.
The file layout and encoding below are proposals, not a frozen format.

Related: [current Distance workflow](./reference-based-distance-annotation.md)
and [Distance work packages](./distance-work-packages-plan.md).

## Scope and delivery order

Start with exporting and importing saved manual Distance annotations. Keep this
separate from recipient management, package generation, AI input packages, and
baseline-aware package-return reconciliation. Ordinary Species Review labels
must not be modified as a side effect of importing Distance annotations.

Distinguish two uses:

- **Analysis export:** a flat table of measurements is useful on its own.
- **Editable round trip:** restore tasks, timing, individual identity, references,
  visibility skips, relationships, and completion, not just numeric distances.

A results-only table must not be presented as a lossless editable backup. The
current annotated-sample model requires a calibrated reference frame, not only a
numeric distance.

## Species, video, date, and time selection

**Requested:** Per-species Distance export must allow selecting any video from
any day showing the selected species between a start time and an end time.
Selection must not depend on ordinary annotator assignments or be restricted to
the currently open day/video. Support selecting videos across days and previewing
the selected set before export.

Keep the following semantics explicit before implementation:

- Does the start/end range mean camera time of day across selected dates, an
  absolute date/time interval, or a playback interval within each video?
- Does a matching interval select whole videos/tasks, or only the sample rows
  inside that interval? A filtered analysis export and a full editable task export
  need not have the same rules. Never treat omitted samples as deletions or absence.
- Does "showing the species" mean confirmed Species Review presence, manual
  Distance evidence of presence during the interval, or both? A species-task label
  alone does not establish visibility at a particular sample; an explicit absent
  task may still have positive source Species Review labels.
- For camera-time filtering, define interval boundaries, overlap handling, and
  overnight ranges. Camera time can be unknown even when playback alignment is
  valid. Report such videos explicitly; do not silently use file timestamps as
  verified camera time.

## Proposed data layout

A flat sample table is the foundation: one row per saved individual/sample.
Repeating individual attributes across rows is acceptable. Reject contradictory
values for the same individual on import instead of choosing a row silently.

Candidate sample fields:

- `video_path`: resolvable relative path, not a source-machine absolute path.
- `task_id`: stable species-specific task identity.
- `individual_id`: stable UUID, distinct from the displayed individual number.
- `species`: scientific name, with an explicit mapping if local vocabulary IDs differ.
- `offset_ms`: integer sample key.
- `playback_time_s`: exact playback position, preserving fractional precision.
- `distance_m`: numeric distance, empty for visibility skips.
- `skip_reason`: distinguish `individual absent` from `midpoint not visible`.
- `behaviors`: behavior keys, with a specified list encoding.
- `sex`, `age`: individual attributes, including explicit Unknown.
- `carried_by_individual_id`: within-task infant/carrier relationships.
- `annotator`, `labeled_at`: evidence attribution and annotation time.

Do not use an ambiguous single `timestamp` field. Playback position, camera
wall-clock time, and annotation creation time have different meanings. Unknown
camera time stays empty rather than being fabricated.

For editable round trips, also preserve identifying position/bounding box,
individual numbers/tags, reference selection, and the vocabulary definitions
needed to interpret the exported keys.

**Proposed simple archive:**

```text
distance-annotations.zip
  tasks.tsv
  samples.tsv
  references.tsv
```

- `tasks.tsv`: task/video/species identity, source species/count context,
  alignment and sampling schedule, completion/absence outcome and attribution.
  Explicit absence needs a task record because it has no individual/sample rows.
  An unfinished task must not become completed merely because rows were exported.
- `samples.tsv`: sample evidence and enough individual data to restore the editor.
  Include an explicit reference link for measured samples. Row omission means
  unreviewed/not exported, never implicitly absent.
- `references.tsv`: stable reference identities, calibrated distances, exact frame
  positions, relative media paths, and calibration context required by evidence.
  Masks or other non-tabular assets may need additional files if present.

Footage remains separate. The exact file split, list encoding, and treatment of
reference assets are still to be specified. Do not silently overwrite shared
calibration used by other tasks. A first manual importer should preview explicit
per-task replace-or-skip behavior, validate dependencies, and apply accepted tasks
atomically; detailed package baseline merging is a later concern.

## Delimiters, decimals, and spreadsheet locales

**Problem raised:** English, German, and French spreadsheet defaults disagree
about comma/semicolon field delimiters and decimal punctuation. The decimal
separator is generally `.` or `,`; `;` is commonly a field delimiter, not a decimal
separator. File-extension conventions alone do not resolve this.

**Recommendation:** Use a canonical UTF-8 **tab-separated** format (`.tsv`), with:

- Literal tabs between fields and a header row with stable machine-readable names.
- Decimal point `.` for numeric values, irrespective of application/UI language.
- No thousands separators and no locale-dependent numeric formatting.
- Empty fields for missing values; distinguish these from zero and explicit Unknown.
- ISO-formatted dates and 24-hour times. Preserve naive camera-local time as such;
  do not append a fabricated timezone. Preserve real timezone information where known.
- Standard delimited-text quoting for tabs, quotes, or newlines inside a field;
  use a proper parser, not string splitting. Define structured-list encoding separately.
- UUIDs/paths treated as text, and enough numeric precision to retain source-frame timing.
- Import tolerance for a UTF-8 BOM and common line endings.

Tabs eliminate the field-delimiter versus decimal-comma collision, but **do not
force Excel/LibreOffice to interpret decimal points correctly**. Recommend using
Text/CSV Import, selecting tab as the delimiter and a decimal-point numeric locale
(e.g. English/US), with identifiers explicitly imported as text. Double-clicking a
TSV is not a reliable cross-locale workflow. Quoting a decimal does not reliably
control spreadsheet numeric interpretation either.

The app should always read its own canonical files without locale guessing. If
support for externally edited, decimal-comma files is added, provide an explicit
import setting/preview and normalize numeric columns only. Do not globally replace
commas or guess ambiguous numbers such as `1,234`.

An optional XLSX analysis export with typed numeric cells could improve spreadsheet
convenience later. It is not necessary for the initial machine round trip and
should not silently become a second import contract.
