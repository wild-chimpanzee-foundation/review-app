# Getting Started

## Installing

The app is distributed as a standalone executable for Linux, Windows, and macOS — no Python install required. Download the build for your platform from the project's [Releases page](https://github.com/wild-chimpanzee-foundation/review-app/releases) and run it.

The app requires **ffmpeg** to be installed on your system for video playback. If it isn't found, the setup wizard shows install instructions for your OS:

- macOS: `brew install ffmpeg`
- Windows: via winget, or a manual download
- Linux: `apt install ffmpeg` (or your distro's package manager)

## First run

On first launch, no database exists yet, so the app opens directly into a **setup wizard**:

1. Choose your language (English or French) — this can be changed later in Settings.
2. Choose **Fresh start** to begin a new database, or **Restore from backup** to load a previously exported `.db` backup file.

## Projects

A **project** is a name paired with a folder of camera-trap videos on disk. The app scans that folder recursively (supported formats: mp4, avi, mov, mkv, webm, wmv, flv) and registers every video it finds. You can have multiple projects in one database and switch between them from the header dropdown.

There are two ways to create a project:

- **Manual** — enter a project name and the path to your video folder, optionally assign it to a collection, then sync.
- **Bundle import** — upload a `.zip` project bundle (prepared by a colleague) containing a species list, tags, AI annotations, and/or metadata, point it at your local video folder, and the app imports everything in one step. This is the recommended way to distribute a project across multiple annotators.

!!! note
    Cameras don't need to be configured separately — the camera ID is inferred automatically from your folder structure during sync.

## Initial setup

Once a project exists, the main thing to configure before reviewing is the **species list**:

- Go to **Settings → Advanced Settings → Project species** and enable the species relevant to your project from the global catalog, or add custom ones.

You may also want to tune the **confidence thresholds** used to decide how AI predictions are displayed (blank-detection, species, and object-detection thresholds) under **Settings → Advanced Settings → Blank detection**.

Next: [Importing model results](importing.md)
