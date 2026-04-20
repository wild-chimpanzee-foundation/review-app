# Review App

Video annotation review dashboard for manual classification of camera trap footage.

## Requirements

- Python 3.12+
- `uv` installed
- ffmpeg (for video processing)

## Run

```bash
uv run python review_app/app/entry_point.py --dev
```

## Building

### Local Build

```bash
# Clean previous builds
rm -rf dist build

# Build single-file executable
uv run pyinstaller video_annotation.spec
```

The executable will be at `dist/VideoAnnotation/VideoAnnotation`.

### GitHub Actions Release

Builds are automatically created when you push a version tag:

```bash
# Create a release
git tag v1.0.0
git push origin v1.0.0
```

This triggers builds for:

- Linux
- Windows
- macOS

After ~5-10 minutes, executables are available in the **Releases** page.

**View releases:** Repository → Releases

## Configuration

Config is stored in platform-specific directories:

- **Linux:** `~/.config/video_review_app/config.yaml`
- **macOS:** `~/Library/Application Support/video_review_app/config.yaml`
- **Windows:** `%APPDATA%\video_review_app\config.yaml`

Database is stored at:

- **Linux:** `~/.local/share/video_review_app/review_data.db`
- **macOS:** `~/Library/Application Support/video_review_app/review_data.db`
- **Windows:** `%LOCALAPPDATA%\video_review_app\review_data.db`

## Database & Migrations

- Sqlite DB path is `db_dir/<db_filename>` from config (`db_filename` defaults to `review_data.db`).
- The schema is created from scratch using SQLAlchemy table creation for:
  - `videos`
  - `video_labels`
  - `individual_observations`
  - `model_annotations`
- Optional destructive reset: set `recreate_db_on_start: true` in config or `REVIEW_APP_RECREATE_DB=1`.
- Model imports are upserts keyed by `(video_id, model_name, annotation_type)`; model versions are not stored.

## Notes

- Videos remain local and are discovered by recursively scanning `video_dir`.
- Keep config explicit while schema evolves: set `video_dir`, `db_dir`, `species`, `behaviors`.
- Optional queue ranking CSV can be configured with `priority_csv_path` and columns:
  - `video_id`
  - `annotation_importance_score`
