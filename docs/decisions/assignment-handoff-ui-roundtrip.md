# Try a Species or Distance assignment round trip

Species and Distance handoff and saved-assignment footage copying are available.
The Assignments workspace delivers work only through saved handoffs; the old
live-distribution bundle/copy controls were removed.
Legacy CSV annotations can be uploaded in **Accept returned work** or batched
under **Existing annotations**. **Append CSV to project** adds annotations for
matching videos across the active project, including videos outside saved
assignments. CSV intake has no assignment impact preview.
Legacy setup import is still available when creating a project.

## Start the app

From the repository root:

```bash
.venv/bin/python -m review_app.app.entry_point --dev
```

Open <http://127.0.0.1:8000>. If an older app process is already running, restart it
with the current code. Use a small test project or a copy of your real project:
**Use imported replaces complete existing Species video states or Distance task states.**

## 1. Prepare in the origin project

1. Open **Import / Export → Assignments** and choose **Assign work**.
   Select **Species** or **Distance** under **Assignment stage**. Add annotator
   names here if necessary.
2. Choose who reviews which work:
   - **Species**: distribute automatically or inspect and adjust the allocation.
     To include labelled, missing or cameraless videos, inspect and adjust the
     initial allocation manually. Automatic distribution uses unlabelled videos
     with cameras. There is no all-project shortcut or separate Apply step.
   - **Distance**: choose a confirmed target species and recipient. Matching
     videos load automatically and start selected. The row's options button
     exposes sortable/searchable video details, camera/footage filters and time controls. Add a row for another target species and recipient; each species
     produces its own assignment. No Species distribution is required. AI-linked
     and manual tasks can be selected together. Optional camera-local start/end
     times retain their existing boundary/unknown-time rules. Changing filters
     clears and automatically reloads the selection.
3. Choose **Review assignments**, inspect work grouped by annotator, then **Prepare
   assignments**. Rows are prepared individually; successful rows remain saved
   if another fails. Retrying skips successes. Species allocation is applied
   during preparation.
4. Select a saved assignment to see its frozen camera/video counts and footage
   hours, then **Save delivery folder**. Planning and review also show recipient
   workload and camera/chunk labels. For a single new assignment, details open
   automatically. UUIDs and precise timestamps are under **Technical details**,
   not in the compact home list. The saved setup file is `assignment.zip`; return
   download filenames include the recipient and assignment UUID.
5. Keep track of this origin project's name. Preparing again creates a new
   assignment; saving another delivery of an existing assignment keeps its original snapshot.
   Changing allocation must not change a saved assignment.

Footage is not inside the ZIP. For same-machine testing, keep the videos where
they are: both projects can point at the same footage folders.

To test delivery, choose **Save delivery folder** under the saved assignment.
Enter an output folder on the machine running the app, outside project footage.
The new `assignment-<UUID>` folder contains `assignment.zip`. Enable **Include
footage** to also copy the saved work and dependency files into that folder.
Without footage, the recipient locates the original folders when opening the ZIP.
Watch the attempted/total file progress bar during copying, then check
copied/missing/failed counts and the displayed root-folder mapping. Send the
generated folder to the recipient.
Use each root-ID subfolder when locating footage below. If copying is partial,
inspect the errors and retry with a different output folder: existing delivery
folders are deliberately never overwritten. Try a multi-root project with the
same relative filename in two roots and verify both copies remain separate.

Video tables support text search and sortable column headings. Distance filters
only inspect the list; they do not silently deselect hidden videos. Use the
Select/Deselect matching videos actions to change selection explicitly.

To test removal, first save/open an assignment, then **Remove assignment** in
the origin details and confirm. It disappears and further delivery is blocked.
Existing files and work remain, and a return from that working project must still
preview and accept normally, including repeated returns.

## 2. Open in a fresh working project

1. Open the project picker and click **Open package**. On another instance,
   **New project → Open package** offers the same format-detecting upload after
   initial setup. Assignment packages open linked working projects; older bundles
   show a warning and create independent projects without assignment returns.
2. Upload **assignment.zip** from the delivery folder, not a return ZIP. Check
   **Included in this package** for vocabulary, predictions, existing human work,
   and Distance task/reference dependencies. Legacy bundles get their own compact
   component summary and independent-project warning.
3. Choose a recognizable working project name, such as `Alice roundtrip`. Confirm
   the local annotator name for the packaged recipient (the current login is
   suggested). This changes local Assigned to me filtering, not the portable
   recipient or attribution of existing work.
4. For each root, enter the local folder containing the displayed relative paths.
   For example, if the example is `cam_a/clip.mp4`, enter the folder above
   `cam_a`, not `cam_a` itself. Paths refer to the machine running the app, not a
   remote browser's filesystem. Multiple roots need distinct local folders.
5. Click **Open assignment**. It checks the footage folders first. If files are
   missing, no project is created yet: edit the folder paths above and click
   **Open assignment** again. Changing a path clears the previous warning.
6. To proceed without all footage, explicitly click **Open with missing footage**.
   Otherwise, finding all listed files opens the project directly. Opening creates
   one fresh project, then syncs/probes its listed footage before activating it
   and continuing. Check that Overview's Unprobed count is zero for accessible
   footage; failed probes appear as Invalid, and missing files remain unprobed.
   If sync fails, use **Retry footage sync**; this reuses the same project rather
   than creating another. Opening does not import into the current project or
   recursively discover unrelated videos. Missing videos remain represented and
   can be located in Settings and synced later.

## 3. Annotate and return

1. In the working project, open the delegated review stage and change/save one
   video's work. Editing a carried annotation is a useful conflict-policy test.
   For Distance, select the target species normally, verify timing, edit/confirm
   a sample and check carried AI evidence and reference footage. Both stages
   remain editable; unrelated local work does not enter the assignment return.
2. Open **Assignments**. The working assignment shows its stage, target species
   (for Distance), recipient and video count. Coordinator preparation and return
   acceptance actions are not shown; ordinary annotation remains unrestricted.
3. Click **Download return progress** to save **return-….zip**.

All assigned saved states are returned, including untouched, unfinished, and
empty videos/tasks. Species returns exclude Distance work; Distance returns
include only the assigned target-species tasks, not Species edits or other
species' tasks. You can keep annotating and download another return later.

## 4. Preview and accept in the origin project

1. Switch back to the **origin project** using the project picker.
2. Open **Assignments → Accept returned work**.
3. Upload **return-….zip**.
4. Leave the default **Use imported** selected and click **Preview return**.
   Check the returned stage and target species. New work is added; identical
   work is unchanged; differing complete videos/tasks are replaced. Nothing has
   been written yet.
5. To preserve differing work already in the origin, select **Keep existing**.
   The preview refreshes automatically and those returned units remain unapplied.
   Read the compact summary and removal warnings;
   click **Show per-video details** to expand the action/count table.
6. For Species, try **Append**. It adds new observation UUIDs and updates matching
   observations, including attributes, but retains observations absent from the
   return. Local-only tags remain; existing video-level labels win conflicts.
   Matching UUID updates can overwrite newer local observation edits. Check the
   appended/updated count, composed-state detail counts and warning. Distance
   does not offer Append.
7. For Distance, check reference-substitution details when the returned
   calibration differs: reference video, distance, and returned/admin playback
   positions. Acceptance reuses admin position and mask for an existing
   video/distance pair, while preserving returned sample distances. Changed
   Species context warns but does not block saved-scope work or restore labels.
8. Click **Accept previewed return**, then reopen the delegated Review stage and
   verify the change. Other stages/species and unrelated videos stay untouched.

Changing the file clears confirmation. Changing the policy clears the old
confirmation and automatically produces a new preview before acceptance is enabled.
If destination annotations or relevant dependencies change after preview,
acceptance rejects the stale preview; preview again before accepting.

## Useful follow-up checks

- Upload the same return again after accepting it without intervening edits:
  replacement or append should report unchanged composed work. Confirmation is
  disabled if there is nothing to add, replace or append.
- Make another working-project edit and return again: **Keep existing** can skip
  later progress already present in the origin; **Use imported** replaces it.
  Species **Append** updates matching UUIDs without deleting local-only observations.
- Clear an annotated working video and return it: **Use imported** should warn
  about removals, even when that returned video is unfinished.
- Add an unrelated video under a located folder, then sync the working project:
  it must not be discovered or expand the saved assignment.

## Distance-specific follow-up checks

- Include AI and manual tasks, pending suggestions, and an untouched task in one
  handout. Verify the carried work, alignment, progress, and references after opening.
- Check that listed reference footage is probed on opening (not left Unprobed),
  and that missing reference footage can be located and rechecked by syncing.
- Try a time window crossing midnight; check the unknown-time count. Clear both
  times and reload to include unknown-time tasks. Select a subset before preparing.
- After preparation, change/remove Species labels in both projects. Return and
  accept Distance work: saved scope remains eligible, the warning appears, and
  admin Species labels are not restored.
- Move a working-project calibration position and return changed work. Preview
  should show reuse of the admin position/mask at the same video/distance. Switch
  to Keep existing: substitutions for skipped work disappear. Switch back to
  Use imported: preview refreshes and shows them again.
- Add a new reference distance at a timestamp used by another distance. Acceptance
  should keep both calibrations; returned sample distances remain their saved values.
- Change admin calibration after preview: acceptance must reject the stale preview.
- Clear a Distance task and return: removal warnings must be visible before
  replacement; explicit absence remains distinct from an empty unfinished task.
- Editable Distance work uses assignment packages. The legacy manual snapshot
  export and restore controls have been removed; the manual analysis TSV remains.

The UI interactions are covered by `tests/test_assignment_handoff_ui.py`,
`tests/test_assignment_distance.py`, and `tests/test_open_package_ui.py`. This
checklist is for human verification with real video playback and downloads;
automated UI tests simulate interactions without launching a browser.
