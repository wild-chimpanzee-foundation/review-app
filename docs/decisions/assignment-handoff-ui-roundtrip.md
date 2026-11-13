# Try a Species assignment round trip

This is the first M3 UI slice. Species handoff is available; Distance handoff and
saved-assignment footage copying are not yet wired. Legacy bundle/CSV/copy controls
remain independent and do not use the frozen assignment selection.

## Start the app

From the repository root:

```bash
.venv/bin/python -m review_app.app.entry_point --dev
```

Open <http://127.0.0.1:8000>. If an older app process is already running, restart it
with the current code. Use a small test project or a copy of your real project:
**Use imported replaces existing Species work.**

## 1. Prepare in the origin project

1. Open **Distribution** and find **Species assignment handoff**.
2. Select a registered recipient, for example `alice`. If needed, add the
   annotator in Work Distribution below first.
3. For a quick smoke test, choose **All videos in this project**, ideally in a
   small test project. This includes labeled and known-missing videos.
   Alternatively, apply the normal distribution below first, then choose
   **Applied distribution for this recipient**. Unsaved distribution edits do not
   affect the prepared package.
4. Click **Prepare Species assignment**, then **Download saved assignment**.
   Setup and return ZIP filenames include the recipient's name and assignment UUID.
5. Keep track of this origin project's name. Preparing another package creates
   another assignment; redownloading an existing one keeps its original snapshot.

Footage is not inside the ZIP. For same-machine testing, keep the videos where
they are: both projects can point at the same footage folders.

## 2. Open in a fresh working project

1. Open the project picker and click **Open assignment**. On another instance,
   **New project → Open assignment** offers the same form after initial setup.
2. Upload the saved **assignment-….zip**, not a return or legacy bundle ZIP.
3. Choose a recognizable working project name, such as `Alice roundtrip`.
4. For each root, enter the local folder containing the displayed relative paths.
   For example, if the example is `cam_a/clip.mp4`, enter the folder above
   `cam_a`, not `cam_a` itself. Paths refer to the machine running the app, not a
   remote browser's filesystem. Multiple roots need distinct local folders.
5. Click **Open assignment**. It creates and activates a fresh project; it does
   not import into the current project or recursively discover unrelated videos.
6. If footage is missing, read the warning and click **Continue to project**.
   The videos remain represented. Locate their folders in Settings and sync later.

## 3. Annotate and return

1. In the working project, open Species Review and change/save one video's work.
   Editing a carried annotation is a useful way to test the conflict policy.
2. Open **Distribution**. The saved binding shows the recipient, assignment UUID,
   and video count.
3. Click **Download return progress** to save **return-….zip**.

All assigned saved Species states are returned, including untouched, unfinished,
and empty videos. Local Distance work does not enter this Species return. You can
keep annotating and download another return later.

## 4. Preview and accept in the origin project

1. Switch back to the **origin project** using the project picker.
2. Open **Distribution → Preview and accept returned Species work**.
3. Upload **return-….zip**.
4. Leave **Keep existing** selected and click **Preview return**. New work is
   added; identical work is unchanged; differing existing videos are left
   unapplied. Nothing has been written yet.
5. To accept a changed carried annotation, select **Use imported**. The preview
   refreshes automatically. Read the compact summary and removal warnings;
   click **Show per-video details** to expand the action/count table.
6. Click **Accept previewed return**, then reopen Species Review and verify the
   change. Distance state and unrelated videos should be untouched.

Changing the file clears confirmation. Changing the policy clears the old
confirmation and automatically produces a new preview before acceptance is enabled.
If destination annotations or relevant dependencies change after preview,
acceptance rejects the stale preview; preview again before accepting.

## Useful follow-up checks

- Upload the same return again: both policies should report identical work with
  no changes. Confirmation is disabled if there is nothing to add or replace.
- Make another working-project edit and return again: **Keep existing** can skip
  later progress already present in the origin; **Use imported** replaces it.
- Clear an annotated working video and return it: **Use imported** should warn
  about removals, even when that returned video is unfinished.
- Add an unrelated video under a located folder, then sync the working project:
  it must not be discovered or expand the saved assignment.

The UI interactions are covered by `tests/test_assignment_handoff_ui.py`; this
checklist is for human verification with real video playback and downloads.
