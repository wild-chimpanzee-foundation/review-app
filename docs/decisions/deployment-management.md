# Project deployment management

## Implemented scope

New projects infer deployments from their footage folders. The setup preview
lists reference folders that need attention and keeps the full folder groups
in a collapsed section. **Create project and review reference folders** syncs
the footage and opens the filtered folder review. **Create project and review
later** goes to Overview, where **Review reference folders** opens that same
queue. The app rechecks the preview before saving the project and its inferred
groups.

A deployment is a particular camera placement and recording period. Use the
folder grouping for the placement even when the same physical camera appears
under several recording periods. The app records project-scoped deployment
identities and uses them for Distance review, calibration references, and
assignment handoff. See the [domain glossary](../../GLOSSARY.md).

## Supported folder layouts

| Layout | Ordinary footage | Reference footage |
| --- | --- | --- |
| Category first | `videos/cam_a/clip.mp4` | `ref/cam_a/calibration.mp4` |
| Deployment first | `cam_a/clip.mp4` or `cam_a/videos/clip.mp4` | `cam_a/ref/calibration.mp4` |
| Marked reference folder | `cam_a/clip.mp4` | `cam_a_VR/calibration.mp4` |

`video` and `videos` are ordinary category names. `ref`, `reference`, and
`references` are reference category names in a recognized layout. A bare
`ref` folder also identifies reference footage when its deployment is unknown.
Folder names with a separate leading or trailing `VR`, `V_R`, `ref`,
`reference`, or `references` marker match by normalized deployment name.
Normalization folds case and runs of spaces, hyphens, and underscores.
Matching stays within the import root and its folder hierarchy. Folders such
as `round_1/cam_a` and `round_2/cam_a` create separate deployments.

Ordinary footage outside these layouts groups by containing directory. An
ambiguous or unmatched reference folder retains its own group for inspection.
Review shared references and shortened names before linking them to an ordinary
deployment.

## Management

Open **Overview → Manage deployments**. The folder view lists unresolved
videos, references without ordinary footage, and individual clip exceptions.
Folder review plays the first clip from the source folder beside the first
ordinary clip from the selected deployment. The dropdown lists suggested
deployments first and highlights camera and transect mismatches below the
preview. Choosing a deployment previews the assignment automatically.
Confirm a folder link to assign directly contained current and future clips,
or select clips and confirm an assignment for that subset.
Descendants get their own inference or folder links. Explicit clip assignments
override folder links and survive rescans. Inspect a clip to play it or convert
it for browser playback from the same dialog.

Create deployment records or edit metadata in the same view. The deployment ID
is stable after creation. Display names and physical camera identifiers are
metadata. Location and CRS support is deferred.

Reassignment previews are revalidated inside the write transaction. Saved
Distance evidence, calibrated references, and frozen handoff bindings block
changes that would invalidate their scope. Assignment archives retain their
existing version and package-opening behavior.

## Manual verification

1. Create projects with each folder layout. Compare the preview with the
   registered deployment IDs and reference tags after sync.
2. Review a shared reference folder, link one folder, and split another by
   assigning selected clips. Confirm that Distance review offers the reference
   clips alongside the corresponding ordinary footage.
3. Rescan with a new file in a linked folder and another in a subfolder. Check
   the direct file inherits the link, the subfolder remains independent, and
   explicit clip assignments survive.
4. Try confirming a stale preview and moving a clip with saved Distance work
   or a frozen handoff. Verify the operation leaves saved work and assignments
   intact.

## Read-only Phase 5 path-matching audit

The Phase 5 snapshot had 21,358 ordinary clips and 152 reference clips in one
root. The existing detector reproduced 109 ordinary folder groups and all
stored reference tags. It associated 128 references with ordinary footage,
leaving 24 for inspection. Shared reference folders, shortened labels such as
`VR-Cam_453`, and differing dates warrant manual review before assignment.
