# Review App

Standalone Streamlit review dashboard that runs in local-only mode with sqlite.

## Requirements

- Python 3.12+
- `uv` installed
- A valid `config.yaml` at repo root, or set `LOCAL_CONFIG_YAML` to an alternate config file

Optional `.env` values:

```bash
REVIEW_APP_USER_EMAIL="reviewer@local"
LOCAL_CONFIG_YAML="./config.docker.yaml"
```

## Run

```bash
uv run streamlit run review_app/frontend/1_Pipeline_Overview.py
```

## Run with Docker (recommended)

```bash
docker compose up --build
```

- App URL: `http://localhost:8501`
- Docker uses `config.docker.yaml` (`LOCAL_CONFIG_YAML=/app/config.docker.yaml`).
- Persisted sqlite data is stored in `./_local_db` on the host.

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
