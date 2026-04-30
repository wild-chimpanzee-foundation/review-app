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

## Model import CSV format

The app expects a CSV with one row per annotation:

```
video_uid,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec
CAM01/VIDEO_001.mp4,species,species_model_a,deer,,0.92,0,12.0
CAM01/VIDEO_001.mp4,behavior,behavior_model_a,reacts_to_camera,,0.83,0,12.0
CAM01/VIDEO_002.mp4,blank_non_blank,blank_model,blank,,0.98,0,
```

A template is available in the Import page. Long-format CSVs are auto-detected; wide-format CSVs (one column per model) can be mapped interactively.

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
