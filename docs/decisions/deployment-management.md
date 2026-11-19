# Project deployment management

**Status:** Implemented without location support. Inventory editing, folder links,
manual footage assignment, unresolved-footage handling and resolved Distance/reference
identity are included. Location/CRS design below remains deferred.

Related: [domain language](../../GLOSSARY.md),
[reference discovery and deployment identity](reference-based-distance-annotation.md#references-and-deployment-identity),
[assignment handoff](assignment-handoff.md), and
[database migration rules](../../CONTRIBUTING.md#database-migrations).

## Implemented scope

The initial inventory-only slice was extended at the operator's request so manual
verification can exercise the actual footage and reference workflow:

- Standard new-project CSV upload, template, validation and preview of inventory, folder matches, unresolved footage and empty deployments.
  Only `deployment_id` is required; optional fields are `folder_path`, `name`,
  `camera_id`, `installation_date`, `retrieval_date`, `transect_id`, `habitat`,
  and `notes`. Unknown columns, including coordinates/CRS, are reported and ignored.
- Advanced folder inference using the existing conservative layout detector.
- Atomic creation of project, root and inventory (migration v28).
- Durable video and prepared-reference deployment identities, assignment provenance,
  retained folder-inferred labels, and root-relative folder links (migration v29).
- **Overview → Manage deployments** is folder-first: one summary per directly
  media-containing folder, with unresolved/unmatched-reference counts and individual
  exceptions. A step-through review dialog offers edit-distance suggestions, reasons,
  a searchable inventory picker, metadata edits, and inspection and selection of any
  clip in the folder. Folder links and selected-clip corrections use the same
  confirmation flow. The advanced video tools allow selections across folders.
- Fuzzy suggestions are advisory only. Transect/camera hints, zero-padding differences
  and shortened reference names help ranking; competing or approximate suggestions
  never become automatic assignment rules. Confirmed folder corrections create exact,
  root-scoped links. Shared folders can be split by selecting and assigning clips
  in the same panel. Skip affects the current review session and Back retains
  confirmed writes.
- A folder link affects directly contained current/future files, not descendants.
  Manual video exceptions win and survive rescans. Missing folders can be linked
  before their footage arrives. CSV paths become initial folder links. Unconfirmed
  matches are recomputed on rescan, subject to evidence protection. Automatic
  matches cannot combine footage across roots.
- In CSV mode, normalized deployment IDs match entire directory components or
  separator-bounded prefixes before a suffix (for example, `48_cam086` matches
  `48_Cam086_814419_1116757_20260221`). Ambiguous candidates stay unresolved.
  Display names and camera-device
  metadata are never aliases. Legacy/inferred mode retains conservative inference.
- Assignment preview/confirmation is atomic and rejects stale previews. Saved
  Distance evidence, calibrated references, and frozen handoff bindings block
  effective identity movement; unrelated selected videos are not partially applied.
- Distance discovery, suggestions, saved-frame validation, AI import, transfer import,
  and exports use resolved identity. Unresolved footage cannot enter Distance review.
- Existing projects and package-opening callers require no CSV. Existing projects
  are materialized conservatively from their historical scope without discarding
  camera labels, saved evidence, prepared reference IDs or tags. Existing v1 archives
  reconstruct pinned local assignments from effective labels. Metadata reimports
  preserve unchanged assignments and protect saved work from identity changes.
- The archive version is unchanged. Since v1 carries root-scoped labels rather than
  shared deployment identities, exporting a single deployment spanning multiple roots
  is explicitly rejected instead of silently splitting it. Ordinary existing
  single-root and separate-root deployment packages remain supported.

No location fields, CRS controls, X/Y swap or map are included. Public deployment IDs
are immutable once created; use display metadata edits or a separate reassignment.
No deployment-management UI tests were added; manual UI validation belongs to the
operator. Backend tests cover parsing, migrations, resolution, evidence safety,
transaction rollback, rescans and package compatibility.

Manual verification:

1. Create a project from a CSV; exercise validation, missing-folder warnings and
   **Advanced options → Infer deployment inventory**.
2. Open **Manage deployments**, disable **Needs attention only** to see all footage,
   and add/edit deployment metadata.
3. Open **Review folders…**, select fuzzy suggestions or search the inventory, add
   or edit metadata, and confirm a grouped preview with **Apply folder link & next**.
   Exercise Skip/Back, ambiguous candidates and missing camera identifiers. Split a
   shared reference folder by inspecting and assigning selected clips. Check that the selected starting folder wraps through the full
   filtered queue and changing a target invalidates the preview. Then rescan/reopen
   to check persistence and reference matching.
4. Link a folder and add files both directly and in a child folder. Only direct
   files should inherit it. Explicit video exceptions should survive; resetting
   should use the current folder rule.
5. Try an unresolved assignment, stale preview and a move with saved Distance work
   or a frozen handoff. Blocked operations must apply nothing and retain evidence.
6. Check project isolation, English/French labels, and opening existing packages.

## Decision and rationale

Introduce a project-scoped deployment inventory. Standard project creation requires
a CSV in an app-defined format; an alternative under **Advanced options** creates
deployment records from the existing conservative folder inference. Both paths
populate the same deployments table and use the same subsequent management workflow.

This supersedes the earlier proposal to add only `videos.camera_id_override` and
identify deployments by project, import root, and effective label. Field deployment
sheets are available for most projects, giving deployments meaningful metadata and
existence independent of imported footage. A stable record also separates deployment
identity from folder labels and physical-camera identifiers. A physical-camera
registry is not required.

Accepted choices:

- Only `deployment_id` is required in the standard CSV. It is an opaque text
  identifier, not a prescribed camera/date/location naming formula. Never extract
  scientific metadata from its contents.
- All recognized metadata columns are optional; unrecognized columns are reported
  and discarded, not preserved for later inspection or export.
- CSV provides one optional `folder_path` per deployment row, accepting relative
  or absolute paths. Additional folder links can be added in the management UI;
  do not require multiline path cells, duplicate deployment rows, or a second CSV.
- Use explicit paths to establish directory links where supplied; otherwise fall
  back to a directory component matching a complete deployment ID, either exactly
  or as a separator-bounded prefix before a suffix. Thus `48_cam086` matches
  `48_Cam086_814419_1116757_20260221`, but `cam45` never matches `cam453`.
  Preserve existing conservative reference-layout recognition before comparison. Normalize
  case and runs of spaces/dashes/underscores without rewriting stored IDs or paths.
  Display names, filenames, physical-camera numbers, and arbitrary substrings are
  not fallback matching aliases.
- New automatic matching with competing deployment candidates remains unresolved;
  never silently choose a deepest, longest, or ID-versus-name winner. Existing
  confirmed directory links and explicit video assignments retain precedence.
- A deployment-linked folder assigns directly contained video files to that
  deployment, including files added later. Links do not inherit into subdirectories;
  each media-containing directory needs its own link.
- Explicit individual-video assignments override the directory link. Display these
  exceptions clearly and preserve them on rescan.
- The operator's actual workflow uses one import root per project. Current creation
  and Settings expose one directory, although the backend supports several. Do not
  add a multi-root setup workflow or burden normal setup with cross-root controls.
  Preserve existing database compatibility and avoid accidental cross-root merges;
  project-scoped deployment identity does not require exposing multi-root setup.
- A deployment belongs to a project, not exclusively to one import root. It may
  contain footage from multiple roots through explicit links.
- Identical folder labels in separate roots do not by themselves establish that
  the footage belongs to one deployment.
- Unmatched footage is imported as **Unresolved**, requires an administrator to
  match it to a deployment, and does not block project creation.
- Standard CSV import does not silently create folder-inferred deployments for
  unmatched footage. Folder-only inventory creation is an explicit advanced path.

## Domain and identity

A deployment is a particular camera placement and recording period. A camera may
appear in several deployments; its label or serial number is not deployment identity.
A deployment can exist without imported footage, including when footage is pending,
a camera was stolen, or a card failed.

Use a durable internal deployment identity with a unique project-local CSV
`deployment_id`. Distinguish the CSV identifier, optional display name, physical
camera identifier, and source folder paths. Changing a display name must not
silently reassign already linked footage or alter saved reference identities.

Video assignment points to a deployment record, or is absent when unresolved.
Retain folder-derived baseline information and assignment provenance separately.
A directory link applies only to directly contained videos; an explicit per-video
assignment takes precedence. Rescan must not overwrite explicit assignments or
declared deployment metadata.
Do not introduce the superseded string override as a second source of truth.

Import roots still establish media-path identity and disambiguate folder matches.
Root relocation preserves root identity. Identical labels in different roots remain
separate matching candidates until explicitly linked to a declared deployment.
Two explicitly linked videos may share a deployment despite having different roots.

The glossary describes this intended domain. Existing code still uses the older
root-scoped camera-label representation until migration is implemented.

## Standard CSV contract

The app owns the interchange format. Provide a downloadable template, documented
column names and examples, and row-specific validation feedback. Users convert
project-specific spreadsheets into this format; direct XLSX ingestion and arbitrary
column-mapping interfaces are not part of the first version.

### Required column

| Column | Meaning |
| --- | --- |
| `deployment_id` | Nonempty, unique identifier within the project; not merely a physical camera number |

Missing required headers, empty identifiers, and duplicate identifiers reject the
CSV. Do not silently keep the first or last duplicate row or merge their metadata.

### Optional columns

The following small metadata set describes the target design. The implemented
initial slice omits location entirely; `x`/`y` are currently ignored extra columns,
not recognized inputs. Matching/application semantics remain planned.

| Column | Meaning |
| --- | --- |
| `folder_path` | One optional explicit media-containing directory, relative or absolute; additional links can be added in the UI |
| `name` | Human-readable display name, separate from identity; not a matching alias |
| `camera_id` | Field camera identifier; may repeat across deployments |
| `installation_date` | Optional installation date in ISO `YYYY-MM-DD` format |
| `retrieval_date` | Optional retrieval date in ISO `YYYY-MM-DD` format |
| `x` | Longitude/easting in the selected source CRS; optional together with `y` |
| `y` | Latitude/northing in the selected source CRS; optional together with `x` |
| `transect_id` | Field transect identifier, retained as text |
| `habitat` | Habitat description |
| `notes` | Free-text deployment context |

Blank optional values mean unknown, not zero, an inferred date, or an empty-string
identity. Validate populated recognized columns and report bad values by row and
column.

Dates are date-only field records: either is independently optional; if both are
provided retrieval must not precede installation. Do not infer times, time zones,
or missing dates. A video's recorded timestamp falling outside the declared dates
must not by itself block deployment assignment or trigger clock correction.

There is one deployment location, not separate installation/retrieval coordinate
pairs. Use optional `x`/`y` columns with explicit longitude/easting first and
latitude/northing second convention, independent of the CRS's formal axis ordering.
Both coordinates must be populated together or both absent.

Select one source CRS for the entire CSV import in the UI, visibly defaulting to
WGS84 (EPSG:4326); no per-row CRS column is required. Never guess the CRS from
coordinate magnitudes. Provide a map preview that reprojects the original source
values when the CRS changes, plus an explicit **Swap X/Y** control for correcting
axis mistakes and visually verifying the result. Swapping must be reversible and
must never operate on already transformed WGS84 values.

Retain the original source coordinate pair and CRS alongside the derived WGS84
location for provenance and later correction. Persist the chosen axis interpretation
so later reprojection is reproducible. Update source interpretation and canonical
WGS84 atomically; only WGS84 drives normal map display and geographic queries.
Individual deployment location editing supports source CRS selection as well.
Invalid transformation handling and confirmation behavior remain to be settled.
Deployment location is distinct from embedded video coordinates; do not silently
overwrite video metadata.

Ignore unrecognized columns and list their names in the preview. Required-header
validation must still catch a misspelled `deployment_id`; ignoring extra columns is
not permission to guess intended header names or silently discard malformed known
values.

The initial format describes actual deployments, not unsuccessful installation
attempts. Do not import every row of a field installation sheet indiscriminately.
It is an inventory, not a lossless archive of the field sheet: serial numbers,
installation/retrieval times, separate retrieval coordinates, vegetation surveys,
and expected video counts are outside the first recognized metadata set.
No scientific requirement for dates or coordinates is implied by CSV acceptance;
future analysis workflows may impose their own explicit eligibility requirements.

### Project creation workflow

1. Choose the project name and footage directories.
2. Upload the deployment CSV in the standard path.
3. Validate all rows before writing a partial inventory.
4. Preview declared deployments, candidate footage matches, unresolved videos,
   empty deployments, and ignored columns.
5. Confirm explicit directory links and unambiguous ID-based matches. Competing
   automatic candidates remain unresolved. Standard creation selects one footage
   root; do not introduce a multi-root setup workflow.
6. Confirm project creation. Import unresolved footage with a visible attention
   count rather than inventing assignments or refusing to create the project.

The validation/import operations belong in the backend/provider, independent of
NiceGUI. Preview and apply must agree on the confirmed inventory and matching set;
revalidate ownership, matching ambiguity, and selection freshness on apply.

## Advanced folder-only creation

Expose **Infer deployments from folders** under **Advanced options**, rather than
as a silent fallback when a CSV is missing or invalid.

- Create durable deployment records using existing conservative layout inference.
- Mark those records as folder-inferred; leave unknown field metadata empty.
- Preserve distinct candidates in different roots, including repeated labels.
- Preserve the current reference-tag detector and paired-layout behavior.
- Do not guess camera numbers, dates, coordinates, spelling corrections, or fuzzy
  reference associations.
- Use the same deployment assignment, reference discovery, and management behavior
  as CSV-created projects after setup.

A declared CSV inventory and an inferred inventory are two creation paths, not two
independent deployment-assignment systems.

## Unresolved footage

Unresolved is an explicit state: a video has no deployment assignment. It is not a
synthetic deployment, a blank label, or a reference-only deployment.

- Allow the footage to be imported and project creation to finish.
- Show it prominently in Overview and **Manage deployments → Needs attention**.
- Require an administrator to choose a deployment; do not silently choose a fuzzy
  suggestion, assign by physical-camera number, or create a deployment for it in
  the standard CSV workflow.
- Keep footage visible for inspection and Species Review.
- Block operations that require a resolved deployment, including calibration
  reference matching and accepting deployment-dependent Distance evidence.
- A reference tag can identify reference footage without resolving its deployment.
- Distinguish unresolved references from references assigned to a deployment that
  currently has no ordinary footage. The latter may simply be incomplete imports.

Missing media must not prevent assignment from stored metadata. Where playback or
thumbnails are unavailable, state that visual inspection is unavailable.

## Management workflow

Enter from **Overview → Manage deployments**, not from Species Review. This is a
project-administration workflow, not a new authentication or permission system.

Overview shows inventory, reference, and unresolved-footage counts. Count deployments
by durable identity, including inventory records without videos; do not count labels
or source folders as deployment entities.

Use a dedicated project-scoped page:

- Default to **Needs attention**; also offer the complete deployment inventory.
- Show unresolved footage, reference-only deployments, and ambiguous matches with
  neutral explanations. Missing footage does not prove an assignment is wrong.
- Search/filter by deployment, import root, actual source folder, reference status,
  and assignment provenance.
- Keep source paths visible after reassignment. A source folder can contain videos
  assigned to multiple deployments and must display a mixed state honestly.
- Support individual video selection and source-folder bulk selection, including
  inspection of shared reference folders that contain multiple deployments.
- Resolve **Select all matching videos** to explicit existing video IDs across
  pagination, with a visible affected count. Never include subsequently imported
  files in an already confirmed selection.
- Reuse one assignment dialog for individual and bulk operations. Choose existing
  deployments in the same project, showing identity, metadata, and footage counts.
- Allow explicit cross-root links without changing source paths or root ownership.
- Preview changed, unchanged, and blocked videos before confirmation, and report
  the actual applied count.
- Keep reference tagging independent: reassignment never tags or untags footage.
- Allow administrators to add missing deployments and edit optional metadata using
  the same validation as CSV creation. A replacement CSV is not required for those
  changes. Display-name/metadata edits must not silently alter confirmed folder or
  video links. ID changes, deletion, and merging require separate decisions.

Confirmed folder links apply to current and future videos directly in those
folders, not to videos in subdirectories. Explicit per-video assignments take
precedence and survive rescan. Optional CSV folder paths establish explicit links;
otherwise scanning tries to match as-yet-unlinked footage using complete normalized
deployment IDs at the start of directory components, followed by a separator or the
end of the component, never display names or filenames. Preserve the
existing conservative paired/reference-layout recognition before comparison.
Competing automatic candidates remain unresolved; do not choose deepest or longest
matches. Add further directory links in the management UI when one CSV path does
not cover all footage.

A well-formed explicit path that does not currently exist is a preview warning,
not an inventory validation error. Retain the pending link and apply it when that
media directory arrives. Reject malformed/escaping paths and conflicting explicit
assignments of the same directory. Absolute-path canonicalization within the chosen
root remains to be finalized. Rescanning must not overwrite inventory metadata
or silently replace confirmed links. A deployment losing its last video remains
an inventory record, not an instruction to delete it or retarget its references.

## Consistency and safety

All deployment-sensitive consumers must adopt the canonical resolved assignment:
queues, filters, sorting/grouping, Overview/maps/stats, allocation, reference
registration/discovery, Distance evidence validation, AI imports, exports, metadata
bundles, and assignment delivery/open/return handling. Keep physical-camera labels
and folder-derived baselines available without confusing them with deployment IDs.

Prepared `ReferenceVideo` and frame identities remain durable. Reconcile their
assignment transactionally with source videos; do not duplicate prepared records,
replace frame IDs, discard calibration, or maintain a second independent manual
assignment system. Define an explicit migration for external references without a
source Video; do not infer cross-root identity from their camera labels.

Preview and apply revalidate project ownership, target existence, confirmed video
IDs, root/path membership, selection freshness, and Distance/handoff dependencies.
Apply the confirmed selection atomically; if any selected change is unsafe, apply
none and explain the blockers. An unchanged assignment is not evidence movement.

Check both directions: moving ordinary footage away from references used by its
samples, and moving a reference used by other footage. Preserve Species annotations,
Distance task identities, samples, calibration, completion, and assignment membership.
Conservatively block changes invalidating saved Distance evidence or frozen handoff
identity. Renaming a display name is not reassignment; reassigning selected footage
is not deployment merge or global rename.

Existing package versions carry camera labels, not this new deployment model. A
versioned compatibility strategy is a release gate, not an excuse to add unversioned
fields or silently reinterpret old packages. Rescanning an opened package must not
undo its resolved assignments.

## Implementation gates and sequencing

1. Finalize CSV value validation and absolute-path canonicalization within the
   selected root. One optional `folder_path` is accepted; fallback uses complete
   normalized deployment IDs as exact components or separator-bounded component
   prefixes after conservative layout recognition. Resolve identifier collision policy and legacy multiple-root
   compatibility without expanding the standard single-root workflow. Folder links apply only to directly contained current/future files;
   per-video explicit assignments override them. Define how automatic path matches
   establish links for separate `videos/` and `references/` media directories without
   introducing inherited folder rules.
2. Implement project-scoped deployment records, nullable video assignments,
   provenance, and shared read semantics. Append migrations; never edit published
   migrations. Test fresh construction and upgrades from the published v22 fixture
   and the current supported schema.
3. Migrate existing projects conservatively from their current root-scoped labels,
   preserving distinct roots, paths, video/reference/frame IDs, annotations, and
   evidence. Existing projects must not suddenly require a CSV to reopen. Resolve
   unestablished scope explicitly rather than guessing.
4. Implement backend CSV preview/apply, advanced folder inference, and the updated
   project-creation workflow, including CSV templates and English/French text.
5. Implement administration previews, atomic assignment, dependency checks,
   Overview summary, and the management page. Define safe reset/unassign semantics
   rather than inheriting the superseded override-only reset behavior implicitly.
6. Version and verify all relevant exports/imports and handoff delivery/open/rescan/
   return flows before exposing writes that those flows cannot preserve safely.

No physical-camera registry, arbitrary XLSX ingestion, automatic fuzzy assignment,
file/folder renaming, deployment merge, or general-purpose metadata spreadsheet is
required in the first version. Later CSV updates to an existing inventory need a
separate explicit update/conflict policy; creation import is not an upsert contract.

## Acceptance cases

1. Standard project creation requires a valid app-format CSV; advanced folder-only
   creation is an explicit alternative. Both produce the same deployment model.
2. A CSV containing only unique, populated `deployment_id` values is accepted.
   Optional metadata is not secretly required by matching or project creation.
3. Unknown columns are ignored and reported. Invalid required fields, duplicates,
   and malformed recognized values produce actionable validation errors, without
   partially creating the inventory.
4. Reusing a physical camera label across installation periods does not merge
   deployment rows.
5. Unmatched ordinary and reference videos remain visible and unresolved. An
   administrator assigns them; no synthetic fallback deployment is created.
6. Resolved reference-only and empty deployments remain distinct from unresolved
   footage and are not described as corrupt.
7. Two roots with identical labels are not silently combined. Explicitly linking
   their footage to one project deployment permits shared reference matching.
8. Splitting a shared reference folder changes only confirmed video IDs. Paths,
   tags, prepared frame identities, and unrelated videos are unchanged.
9. Rescan and root relocation preserve declared metadata, stable identities, and
   explicit assignments; changed media selections invalidate stale previews.
10. Reassignment that would invalidate Distance evidence or frozen handoff scope
    is blocked atomically, including reference moves affecting other videos.
11. Existing projects upgrade without requiring a CSV or conflating root-scoped
    labels. Supported package and metadata flows preserve resolved identity.
12. Counts, filters, allocation, exports, and reference validation agree on the
    project-scoped deployment model, and UI text is available in English/French.

## Motivating source data

The inspected `2026_Phase5_info_camera_complet_a_verifier.xlsx` contains 279 data
rows: 262 marked camera placed and 17 not placed. Its columns include camera labels,
installation/retrieval dates and coordinates, field context, and sometimes serial
numbers. Several camera labels recur on different installation dates. These facts
motivate independent deployment records, but are not permission to import every
spreadsheet row as a deployment or treat unverified field values as authoritative.
The app's standard CSV is deliberately independent of this workbook's column names.

### Read-only Phase 5 path-matching audit

A subsequent read-only snapshot of project `pss_p5_1` found 21,510 registered videos
in one root: 21,358 ordinary and 152 reference videos. There are 109 ordinary
inferred labels and 230 distinct directly media-containing directories overall.
The existing pure layout detector reproduced all stored labels and reference tags.
Of the references, 128 share a current inferred label with ordinary footage and
24 do not. These are existing layout associations, not CSV inventory matches.

At the time of that audit, no standardized deployment CSV was available. Simulations used case-insensitive path
components beneath the root, excluding filenames, without separator normalization:

| Candidate identifiers | Complete component matches, ordinary/reference | Delimiter-bounded matches, ordinary/reference |
| --- | --- | --- |
| 109 full ordinary deployment folder basenames | 21,358 / 16 | 21,358 / 119 |
| 261 distinct nonblank workbook camera labels | 0 / 0 | 20,142 / 127 |

Camera-label token matches are not necessarily unique deployment-row matches.
Several workbook camera labels repeat; `cam499` occurs in three installation rows.
Six ordinary footage camera tokens have no matching workbook camera label and
account for 1,216 ordinary videos. The workbook cannot simply use `Cam_ID` as a
unique deployment identifier.

An actual substring hazard: `cam45` matches 769 videos across labels containing
`cam452`, `cam453`, `cam454`, and `cam455`, whereas delimiter-bounded `cam45` matches
none. The workbook's camera-45 label is `cam045`; zero-padding removal is not an
accepted matching rule.

Existing layout inference is stronger than a literal component/token check for
references: it handles paired directories, boundary-delimited VR/ref markers,
normalized spacing/separators, and nested reference copies. A generic path matcher
must not inadvertently discard those associations. Conversely, shared `E5/VR/`
footage (16 videos), shortened `VR-Cam_453` labels, and mismatched dates demonstrate
why unresolved/manual assignment remains necessary. Dates in folder names cannot
safely be interpreted as installation dates.

### CSV prefix-matching follow-up

The operator subsequently supplied `deployments.csv` with 279 project-local IDs,
including `48_cam086`, and blank folder paths. Exact-component matching was too
strict for the corresponding `48_Cam086_814419_1116757_20260221` directory. CSV-mode
matching now accepts a case/separator-normalized ID at the start of a directory
component, followed by a separator or the end of that component. Coordinate/date
suffixes are not parsed as metadata. Competing candidates still stay unresolved;
explicit links and video exceptions retain precedence. Legacy/inferred matching
is unchanged.

A read-only replay of registered `pss_p5_1` paths against this CSV, using the actual
resolver in an isolated database, matched 19,650/21,358 ordinary videos and 123/152
reference videos. The requested `48_cam086` example resolves. The remaining 1,708
ordinary videos occupy nine directories: seven have IDs absent from the CSV, while
two use `07_Cam433`/`07_Cam536` against CSV IDs `7_cam433`/`7_cam536`. Zero-padding
removal and physical-camera-only guesses are not introduced by this fix.
