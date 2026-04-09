# SQLite Schema Reset Plan

## Goal

Keep local sqlite schema evolution simple during early development by recreating schema from scratch when needed.

## Migration Contract

- Schema is created by SQLAlchemy `create_all` for the current models.
- For breaking changes, delete the sqlite file and recreate from scratch.
- Optional startup reset can be enabled with `recreate_db_on_start: true` or `REVIEW_APP_RECREATE_DB=1`.
- Current schema tables:
  - `videos`
  - `video_labels`
  - `individual_observations`
  - `model_annotations`

## Model Import Constraints

- `model_annotations` enforces uniqueness on `(video_id, model_name, annotation_type)`.
- CSV imports use upsert-replace semantics for that key.
- Model versions are intentionally not stored in the schema.

## Rollback Strategy

- Back up `_local_db/review_data.db` before enabling reset or deleting manually.
- Prefer explicit reset windows because all prior annotation state in that file is removed.
