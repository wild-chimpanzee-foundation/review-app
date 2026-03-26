# Review App

Standalone Streamlit review dashboard that reads/writes to the Postgres V2 schema.

## Requirements

- A running Postgres instance with the V2 tables
- `DATABASE_URL` set for SQLAlchemy/psycopg

Example:

```bash
export DATABASE_URL="postgres://admin:password@localhost:5432/video_db?sslmode=disable"
export REVIEW_APP_USER_EMAIL="reviewer@local"
```

## Run

```bash
cd review-app
uv run streamlit run review_app/frontend/1_Pipeline_Overview.py
```

## Notes

- Videos remain local. The app resolves local files from `user_video_locations.local_path`.
- If a video has no local mapping for the current user, the player shows metadata only.
