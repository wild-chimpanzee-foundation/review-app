❯ let's think about again how we handl config and the db location/recreation on different platforms. whats the flow like from a users perspective on closing/relaunching the tool etc

● Let me look at the current config and setup wizard code to understand the existing flow.

  Searched for 1 pattern, read 4 files (ctrl+o to expand)

● Now I have a complete picture. Let me summarize the current state and its problems:

  ---
  Current flow

  Where things live:

- Config: get_app_dir() / "config.yaml" — next to the executable (frozen) or project root (dev)
- DB: same dir as config, review_data.db

  Launch flow:

  1. First launch — no config.yaml → main page shows "no config" card → user navigates to /setup → wizard runs → config written, DB created, videos synced → redirected to /overview
  2. Subsequent launches — config exists → LocalDataProvider initialized at startup → main page lands on the welcome/overview screen

  ---
  Problems

  1. Platform write-permissions are wrong

  get_app_dir() returns the directory containing the executable. On:

- macOS: that's inside .app/Contents/MacOS/ — a read-only bundle. Config/DB writes will fail silently or crash.
- Windows: often C:\Program Files\... — needs admin rights.
- Linux: typically fine if installed in home dir.

  The fix is platformdirs (already in most Python envs): platformdirs.user_data_dir("VideoAnnotation") gives ~/.local/share/VideoAnnotation / ~/Library/Application Support/VideoAnnotation /
  %APPDATA%\VideoAnnotation.

  1. db_dir in config.yaml is ignored

  config.yaml has a db_dir key but get_default_db_path() always uses get_app_dir(). The setting does nothing.

  1. DB-missing-but-config-exists is unhandled

  If a user accidentally deletes the DB (or the app can't find it), LocalDataProvider just creates a fresh empty one. No warning, all annotations silently gone. The user would just see "no
  videos" and have to re-sync, not realizing their work was lost.

  1. The setup wizard can be re-run, but it's not clear when to

  The "reconfigure" button in the error card deletes config.yaml — but it's somewhat buried and requires an error state to even be shown.

  ---
  What the ideal flow would look like

  First launch:
  → Wizard → sets video_dir + annotator → config written to user data dir → DB created there → sync → app

  Every subsequent launch:
  → Config found → DB found at expected path → straight to app, no friction

  Config exists but DB missing:
  → Warn the user explicitly: "Your database was not found at <path>. Your annotations may be lost. [Re-sync from scratch] [Choose different DB location]"

  Config exists but video_dir doesn't exist:
  → Warn: "Your video directory is no longer accessible. [Update path] [Continue anyway]"

  ---
  The two most impactful fixes, in priority order: (1) move config+DB to platform user-data dirs (correctness, especially for macOS/Windows), and (2) detect a missing DB explicitly and warn
  before silently recreating it. Want me to tackle either or both?

✻ Cogitated for 43s

❯ what should happen if we want to annotate a new video_dir, a new batch of videos? i think we should create a new db, correct? or should we reflect that all in one db and allow multiple
  video dirs

● Your instinct is right — a new DB per batch is cleaner for this domain. Here's the tradeoff:

  One DB per batch (project model):

- Maps to how field researchers think: each deployment/survey is a self-contained unit
- Easy to archive and share: one folder with the DB + videos
- "Syncing" has a clear scope — the videos in that dir
- No filename collision risk across different camera deployments

  Single DB, multiple video dirs:

- You could query across all surveys ("total lion sightings this year")
- But the DB schema would need a batch/survey concept everywhere
- Syncing becomes ambiguous — sync which dir?
- Much harder to hand off one survey's data to a collaborator

  The missing piece with a per-batch model is the app currently has no way to switch between projects — you'd have to re-run setup and delete/replace the DB each time. That's the real gap.
  What you'd want is a "projects" concept: the app opens to a project picker, each project has a name + video_dir + its own DB path, and you can create/open/switch between them.

  That's a meaningful UI addition though. A simpler intermediate step: just let the setup wizard create a new project (picking a name and destination for the DB), without the full
  project-switcher UI yet — users can at least set things up cleanly even if switching requires going back through setup.

  Which direction feels right for your use case — do you have users who need to work on multiple batches simultaneously, or is it typically one-at-a-time?

❯ yeah, projects are probably a concept we need to implement. where would we store the different user projects?

● The cleanest split, given that videos typically live on external drives:

  Project registry — a single projects.json in the platform user-config dir:

- Linux: ~/.config/VideoAnnotation/projects.json
- macOS: ~/Library/Application Support/VideoAnnotation/projects.json
- Windows: %APPDATA%\VideoAnnotation\projects.json

  Each entry: {id, name, video_dir, db_path, created_at, last_opened}

  Each project's DB — default to user-data dir alongside the registry, e.g. ~/.local/share/VideoAnnotation/projects/<id>.db, but allow a custom path per project. This matters because:

- Videos on a removable drive might be read-only, or disappear between sessions
- The DB lives in stable, backed-up user home space by default
- A power user can still put the DB next to the videos if they want portability

  On startup: app reads projects.json, shows a project picker. If the file doesn't exist yet → first-time setup creates it and runs the wizard for the first project.

  The platformdirs package handles all the OS-specific paths cleanly — it's already the standard approach (used by pip, black, etc.) and avoids the current problem of writing next to the
  executable.

  One question worth deciding now: should switching projects restart the app (simpler, avoids any in-memory state leaking between projects) or swap state in-place (smoother UX but more
  complex)? Given the NiceGUI architecture, a restart/reload is probably much simpler.

❯ yeah, restart is fine. okay let's go, implement all of this
