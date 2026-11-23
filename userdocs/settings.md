# Settings

The **Settings** page collects everything that configures the app and the active project. The most-used controls are at the top; less-frequent and destructive operations live under **Advanced Settings**.

![The top of the Settings page: preferences, project name and video directory](img/settings.jpg)

## Preferences

- **Language** — switch the interface between English and French (reloads the app).
- **Dark mode** — toggle the light/dark theme.

## Project

Shown when a project is active:

- **Project name** — rename the active project.
- **Collection** — group the project under a named collection, or leave it uncategorized.

## Video directory

Shows the folder on disk that the active project scans for videos.

- **Sync videos** — re-scan the folder. New videos are registered; videos no longer on disk are flagged. A progress dialog reports how many were scanned, added, and removed.
- **Delete missing videos** — if videos in the database no longer exist on disk, a count appears here with a button to remove those records (confirmation required).

## Advanced Settings

An expandable section grouping the deeper configuration:

### Project species

Enable the species relevant to your project from the global catalog, or add custom ones. Only enabled species appear in the annotation controls. This is the main thing to set up before reviewing — see [Getting started](getting-started.md).

### Tags

Manage the tags that can be applied to videos. Built-in tags (such as `fire`, `nice_shot`, `broken_metadata`) are always available; you can add custom tags here.

### Blank detection

Three confidence-threshold sliders (0.0–1.0) that control how AI predictions are displayed:

- **Blank threshold** — how confident the model must be to treat a video as blank.
- **Species threshold** — minimum probability for a species prediction to be shown.
- **Object-detection threshold** — minimum probability for an object detection to be shown.

Click **Save** to apply.

### Assignments

Work allocation and handoff live in **Import / Export → Assignments**, not
Settings. The home screen is a compact list of prepared assignments. Select a row
to save a delivery folder or inspect its frozen scope. Technical
identifiers and precise timestamps are under **Technical details**.

1. Choose **Assign work** and the review stage. Add annotator names here if needed.
   - **Species**: distribute work automatically or inspect and adjust recipients.
     Existing allocations are loaded exactly, even when a camera is split between
     people. Per-recipient summaries show camera and video counts, known footage
     hours, and assigned camera/chunk labels; saved-assignment details use the frozen
     work, not a later redistribution. Automatic distribution selects unlabelled videos with a camera.
     Inspect the initial allocation to include annotated, missing or cameraless
     videos manually. Preparation always uses the displayed allocation; there is
     no all-project-videos shortcut or separate Apply allocation step.
   - **Distance**: choose a confirmed target species and its recipient. Matching
     videos load automatically and start selected. Inspect or narrow the work
     through the row's options button. Add a row for another target species; each species produces a
     separate assignment and working project. Manual and AI-linked tasks are
     supported, independently of Species allocation. Calibration/reference
     dependencies are collected automatically. Optional camera-local start/end
     times use 24-hour format: start included, end excluded, overnight windows
     supported. Unknown times are excluded only when a window is set. Changing
     filters clears and automatically reloads selection.
2. Choose **Review assignments**, check work grouped by annotator, then **Prepare
   assignments**. This freezes each assignment. Species allocation is applied
   during preparation. Rows are prepared individually: successful rows stay saved
   if another fails, and retrying skips successful rows. Allocation edits before
   preparation remain a draft; later redistribution does not change saved ZIPs.
3. Select a prepared assignment and **Save delivery folder**. Enter a folder path
   on the computer running the app, outside the project footage folders. The new
   folder contains **assignment.zip**. Optionally enable **Include footage** to
   copy assigned and required reference videos alongside it, in separate media-root
   subfolders. Leave footage off if the recipient can access the original folders.
   Select multiple assignments to save their delivery folders together. During
   copying, a progress bar counts attempted files, including missing files and
   failures. Results show one compact entry per recipient; expand it for paths
   and technical details.
   Check missing/failed counts;
   retry with a different output folder rather than overwrite an earlier delivery.
4. The recipient uses **Open package** in the project picker, uploads the ZIP,
   and locates each media-root subfolder to create a fresh project. **Included in
   this package** summarizes vocabulary, predictions, existing human work and any
   Distance tasks/reference dependencies. Confirm the local annotator name for
   the packaged recipient; your current login is suggested when available. This
   changes local assignment filtering, not the package recipient or historical
   attribution. Footage is not embedded in the ZIP. Opening syncs/probes only listed work and reference footage,
   without discovering unrelated files. A failed sync can be retried without
   creating another project.
5. The recipient uses **Download return progress** in **Assignments** (also linked
   from Overview). Returns include all saved in-scope state, even untouched,
   unfinished or empty work. Reviewing can continue. Both stages remain editable
   locally; unrelated stages/species do not enter the return.
6. In the origin project, choose **Accept returned work**. Upload the return and
   inspect the policy-specific preview before accepting. **Use imported** replaces
   whole differing Species video states or Distance task states, including
   deletions and admin corrections. **Keep existing** leaves differing local work
   untouched. Species also offers **Append**: add new observations, update matching
   UUIDs (including their attributes), keep observations absent from the return,
   merge tags, and preserve existing video-level labels on conflict. Append can
   overwrite newer edits to a matching observation, but never removes absent
   observations. Distance has no append option: its alignment, individuals and
   samples must stay consistent as one task. Changing the file invalidates
   confirmation; changing policy refreshes the preview. Destination changes
   require a new preview.

Video-detail lists support search, column sorting and relevant camera, footage,
role or return-action filters. Distance inspection filters do not change selected
work outside the filter; use **Select/Deselect matching videos** explicitly.

**Remove assignment** hides it and disables further delivery after confirmation.
It does not delete videos, annotations, working projects or files already saved.
Future returns remain acceptable: the original snapshot is retained internally for
validation, so removal does not free that snapshot's database storage.

For Distance returns, changed Species context warns but does not prevent accepting
saved-scope tasks. Acceptance does not restore or modify Species labels. Existing
admin reference calibration is reused by reference video and distance; substitutions
show the video, distance, and returned/admin playback positions, and keep the
admin mask. Returned sample distances are preserved. New distances can share a
frame timestamp without replacing other calibration.

Legacy CSV batch import now lives under **Import / Export → Add data → Existing annotations**. It remains separate and appends annotations immediately on upload without an assignment preview. Use assignment packages for editable manual or AI-linked Distance work. **Open package** also detects
legacy setup bundles and explains that they create independent projects without
assignment returns.

Assignments show up on the [Dashboard](dashboard.md#assignment-summary) and as an annotator filter on the Species screen.

### Users / annotators

Lists everyone who has logged in as an annotator, with their annotation counts. You can delete an annotator who has no annotations — annotators with existing annotations (and your own current name) can't be deleted.

### Database management

- **Backup** — download a `.db` snapshot of the whole database.
- **Restore** — replace the current database from a previous backup (a safety backup is taken first).
- **Delete model annotations** — remove all imported AI predictions while keeping your manual annotations.
- **Reset database** — wipe everything and start fresh (a backup is taken first). Destructive — confirmation required.

## Diagnostics

At the bottom of the page:

- **Download log** — save `app.log` to send when reporting a problem.
- **Check for updates** — compare your version against the latest release and, if newer, offer a download link (a backup is taken before updating).
