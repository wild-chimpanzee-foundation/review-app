# Video Annotation Review App

A desktop app for manually reviewing camera trap footage and correcting AI model annotations. Built for small wildlife research teams.

## What it does

- **Import model results** — load AI species/behavior/blank predictions from CSV
- **Review videos** — step through footage with inline annotation controls, video player with brightness/contrast adjustment, and keyboard shortcuts
- **Correct and label** — confirm, override, or add species and behavior annotations per video
- **Track progress** — overview dashboard showing annotation coverage, species distributions, and per-camera stats
- **Export** — save reviewed annotations as CSV

Supports multiple projects, English/French UI, dark mode, and configurable confidence thresholds.

## Requirements

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) installed
- `ffmpeg` on PATH (for video transcoding)

## Run from source

```bash
uv run python review_app/app/entry_point.py --dev
```

`--dev` enables hot reload. Omit it to run in native window mode (non-Linux).

## Build standalone executable

```bash
# Clean previous builds
rm -rf dist build

# Build
uv run pyinstaller video_annotation.spec
```

Executable: `dist/VideoAnnotation/VideoAnnotation`

### Cross-platform builds via GitHub Actions

Push a version tag to trigger automated builds for Linux, Windows, and macOS:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Executables appear in the **Releases** page after ~5–10 minutes.

## Data storage

Config and database are stored in platform-specific user directories:

| Platform | Path |
|----------|------|
| Linux | `~/.local/share/VideoAnnotation/` |
| macOS | `~/Library/Application Support/VideoAnnotation/` |
| Windows | `%LOCALAPPDATA%\VideoAnnotation\` |

The SQLite database (`review_data.db`) and `config.yaml` are both written there — nothing is stored next to the executable.

## Database Management

The app includes a built-in backup and restore system found under **Settings → Database Management**.

- **Automatic Backups** — Backups are created automatically on application startup, shutdown, and before risky operations (database reset, project deletion, or restoration).
- **Manual Backups** — Trigger a backup at any time and download the `.db` file directly.
- **Restoration** — Restore the database from a list of local backups. A safety backup of the current state is always created before restoration.
- **Retention** — The app keeps the last 5 backups plus one daily milestone for each of the last 7 days.

Backups are stored in the `backups/` subdirectory of the data folder.

## Model import CSV format

The app expects a CSV with one row per annotation:

```
video_uid,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec
CAM01/VIDEO_001.mp4,species,species_model_a,deer,,0.92,0,12.0
CAM01/VIDEO_001.mp4,behavior,behavior_model_a,reacts_to_camera,,0.83,0,12.0
CAM01/VIDEO_002.mp4,blank_non_blank,blank_model,blank,,0.98,0,
```

A template is available in the Import page. Long-format CSVs are auto-detected; wide-format CSVs (one column per model) can be mapped interactively.

## Species and behavior configuration

By default all species and behaviors from the bundled CSVs (`review_app/data/species.csv` and `review_app/data/behaviors.csv`) are available app-wide. Per-project overrides can be configured in **Settings → Advanced → Project Species & Behaviors**.

### Per-project species list

Select which species are available for annotation in a given project. When a project has a species list configured, only those species appear in the annotation dropdowns and filters. Other projects are unaffected.

### Per-project behaviors

For each species in the project, you can override which behavior options appear. If no override is set for a species, the global behaviors from `behaviors.csv` apply.

### Custom species and behaviors

Add one-off species or behaviors via the **+ Add** buttons. Custom entries are marked `is_custom = 1` in the database and are never overwritten by the bundled CSVs on restart.

### Bulk import via CSV

Upload a CSV to replace the project's species list or behavior assignments:

**Species CSV** — semicolon-separated, requires a `scientific_name` column:

```
scientific_name;name_en;name_fr;group_en;group_fr;iucn
Capreolus capreolus;Roe deer;Chevreuil;Deer;Cervidés;LC
```

**Behaviors CSV** — semicolon-separated, requires `scientific_name` and `key` columns. Use `*` as `scientific_name` to apply a behavior to every species in the project:

```
scientific_name;key;name_en;name_fr
*;does_not_react;Does not react;Ne réagit pas
*;reacts_to_camera;Reacts to camera;Réagit à la caméra
Capreolus capreolus;grazing;Grazing;Pâturage
```

Rows with a specific `scientific_name` are applied on top of any `*` rows for that species only.

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Submit and go to next |
| `N` / `P` | Next / previous video |
| `B` | Mark as blank |
| `M` | Flag for review later |
| `Space` | Play / pause |
| `← →` | Seek |
| `S` / `D` | Speed up / down |
| `[` / `]` | Brightness |
| `{` / `}` | Contrast |
