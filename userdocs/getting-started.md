# Getting Started

This page walks through everything from installing the app to reviewing your first video.

## Installing

The app is distributed as a standalone executable for Linux, Windows, and macOS — no Python install required. Download the build for your platform from the project's [Releases page](https://github.com/wild-chimpanzee-foundation/review-app/releases) and run it.

There's nothing to install beyond the executable itself: the database, your settings, and automatic backups are all stored in a per-user data folder the app manages for you.

### ffmpeg

The app needs **ffmpeg** for video playback, thumbnails, and reading video metadata. It's a free, one-time install and isn't bundled with the app, so you'll need to add it once. The setup wizard checks for it and shows **Installed** when it's ready.

#### Windows

1. **Open a terminal (PowerShell).** Click the **Start** menu, type `powershell`, and click **Windows PowerShell**. (Or press **Win + R**, type `powershell`, and press Enter.)
2. **Install ffmpeg** by running:

    ```powershell
    winget install ffmpeg
    ```

3. **Close the terminal and open a new one**, then confirm it worked:

    ```powershell
    ffmpeg -version
    ```

    If you see a version banner, you're done.

#### macOS / Linux

- **macOS:** `brew install ffmpeg` (install [Homebrew](https://brew.sh) first if needed)
- **Linux:** `sudo apt install ffmpeg` (or your distribution's package manager)

If you install ffmpeg while the setup wizard is already open, **restart the app** so it re-checks, then continue once the status reads **Installed**.

## First run

On a fresh install there's no database yet, so the app guides you through a short one-time setup before you reach the main interface.

### 1. Tell the app who you are

The first screen asks for your name. Every annotation you make is tagged with this name, which is what lets a team split work and track who reviewed what.

![The "Who are you?" screen, where you select or type your annotator name](img/login.jpg)

Pick your name from the dropdown if it's already there, or type a new one and press **Enter**. You can also switch the interface language (English / Français) from the toggle in the top-right — this choice is remembered and can be changed later in Settings. Click **Continue**.

!!! note
    This name identifies you as an *annotator*; it isn't a password-protected account. On a shared machine, each person simply selects their own name. You can switch names later from the account menu in the top-right of the header.

Each time the app starts, choose your annotator name. If you already have projects,
the project picker opens next. Use the project name in the header to switch later.

### 2. Welcome & system check

Next, the setup wizard opens. The first step confirms your language and checks that ffmpeg is available.

![The setup wizard welcome step with the ffmpeg status check](img/onboarding-welcome.jpg)

Once ffmpeg shows **Installed**, click **Next**.

### 3. Fresh start or restore

The wizard then asks how you'd like to begin:

![Choosing between starting fresh and restoring a backup](img/onboarding-start-choice.jpg)

- **Start Fresh** — create a brand-new database and set up your first project. Choose this the first time you use the app.
- **Restore a Backup** — load a previously exported `.db` backup file. Use this to move your work to a new computer, or to recover after reinstalling. You'll be shown any backups the app already has, and can also upload a `.db` file.

### 4. Create your first project

Choosing **Start Fresh** brings you to project creation. A **project** is a name paired with a folder of camera-trap videos on disk (more on projects [below](#what-a-project-is)). There are two tabs:

**Set up manually** — point the app at a folder of videos:

![Creating a project manually: name, optional collection, and video folder](img/onboarding-new-project.jpg)

1. **Project name** — a short label, e.g. `PSS 2024`.
2. **Species Collection** *(optional)* — pre-populate the project's species list from a saved collection. Leave as *No collection* if you're unsure; you can set up species later.
3. **Video Directory** — the full path to the folder containing your videos. The app scans it recursively.
4. **Deployment inventory** — setup lists the reference folders that need review. Expand **Footage groups by folder** to see all inferred groups.
5. Click **Create project**. If there are reference folders to review, choose **Create project and review reference folders** to open the review as soon as footage syncs, or **Create project and review later** to go to Overview. The app registers the footage and reads its basic metadata during sync.

Use **Review reference folders** on Overview to open the unmatched reference queue. **Manage deployments** also lets you review other folders, add deployments, edit metadata, and inspect footage. The main table has one row per directly media-containing folder, with footage counts, unresolved videos, references without ordinary footage, and individual exceptions. Disable **Needs attention only** to see all folders. Assignment changes have a preview and confirmation step; moves that would invalidate saved Distance work, calibrated references or frozen assignments are blocked without partial changes.

Choose **Review folders…** to open the first matching folder, or select a row to start there. The comparison opens the folder’s first clip on the left and the first ordinary clip from the top suggestion on the right. Use the deployment dropdown to compare other camera views. Check suggestions against your field records. Open **Folder and deployment details** to add a deployment or edit metadata.

For a whole-folder correction, choose a deployment with no clips selected. Review the automatic preview and click **Apply folder link & next**. For a shared reference folder, use the left dropdown to compare other clips and open **Assign individual clips** to select those that belong together. Choose their deployment and click **Apply clip assignments** after checking the preview. Repeat for each group, then click **Next folder**.

The app recognizes these folder layouts:

| Ordinary footage | Reference footage |
| --- | --- |
| `videos/cam_a/clip.mp4` | `ref/cam_a/calibration.mp4` |
| `cam_a/clip.mp4` or `cam_a/videos/clip.mp4` | `cam_a/ref/calibration.mp4` |
| `cam_a/clip.mp4` | `cam_a_VR/calibration.mp4` |

Paired categories also recognize `video`, `reference`, and `references`. An explicit folder link applies to files directly in that folder, including files discovered later. Subfolders keep their own grouping. Individual video assignments take precedence and survive rescans. **Use folder assignment** removes an individual exception. Footage in separate roots requires an explicit assignment to share a deployment. Ordinary footage and its reference clips must share a deployment for Distance review.

**Open package** — if a colleague prepared an assignment or legacy setup bundle for you:

![Earlier bundle-opening layout; the current entry is Open package](img/onboarding-bundle.jpg)

Upload the setup `.zip`, not a return ZIP. **Included in this package** summarizes
its vocabulary, predictions, existing annotations and, for Distance assignments,
task/reference dependencies. Confirm the local annotator name and locate each
footage root, preserving the displayed relative subfolders. Assignment packages
create linked working projects and sync only listed footage; use **Download return
progress** after reviewing. The packaged recipient and historical attribution
stay unchanged when you map the recipient to your local name.

Older setup bundles still open as independent projects: choose a video directory
and project name, then confirm any annotator-name mapping. These projects scan
the directory normally and do not have assignment returns. Use prepared
[assignments](settings.md#assignments) for new delegated work. **Open package** is
also available in the project picker after initial setup.

After the sync finishes, the wizard offers to jump straight to **Importing model results** or to open the **Dashboard**.

## What a project is

A project has a **name + one or more video folders**. Ordinary projects scan those folders recursively and register the videos they find; assignment working projects check only their packaged footage list. Supported formats: `mp4`, `avi`, `mov`, `mkv`, `webm`, `wmv`, `flv`, `m4v`.

You can keep multiple projects in one database and switch between them from the dropdown at the left of the header. To add more projects later, use the project switcher or **Settings**.

!!! note
    Cameras don't need to be configured separately — each video's **camera ID is inferred automatically from your folder structure** during the sync. Organising footage as `.../CAM01/clip.mp4`, `.../CAM02/clip.mp4` is enough for the per-camera stats and work distribution to work.

If you add or remove files on disk later, re-run the scan from **Settings → Video directory → Sync videos** to pick up the changes.

## Initial setup

Once a project exists, the main thing to configure before reviewing is the **species list** — only enabled species appear in the annotation controls:

- Go to **Settings → Advanced Settings → Project species** and enable the species relevant to your project from the global catalog, or add custom ones. (If you imported a bundle or chose a species collection, this may already be populated.)

You may also want to tune the **confidence thresholds** that decide how AI predictions are displayed — blank-detection, species, and object-detection thresholds — under **Settings → Advanced Settings → Blank detection**. See the [Settings](settings.md) page for the full reference.

## Your first review

The first time you open the **Species** screen, a short guided tour points out the video player, the queue, the filters, and the annotation controls.

![The first-run guided tour on the Species screen](img/onboarding-tour.jpg)

Step through it with **Next**, or click **Skip tour** to dismiss it — either way it won't show again. From here you're ready to start annotating; see [Species](reviewing.md) for the full walkthrough.

Next: [Dashboard](dashboard.md)
