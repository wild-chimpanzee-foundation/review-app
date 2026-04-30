from __future__ import annotations

from sqlalchemy import text

# Add tuples of (version: int, sql: str) to apply schema changes to existing DBs.
# Versions must be contiguous starting at 1. Never modify or remove existing entries.
MIGRATIONS: list[tuple[int, str]] = [
    (1, "ALTER TABLE video_labels ADD COLUMN review_later INTEGER DEFAULT 0"),
    (
        2,
        """
        UPDATE individual_observations
        SET project_id = (
            SELECT project_id FROM videos
            WHERE videos.video_id = individual_observations.video_id
        )
        WHERE project_id IS NULL
        """,
    ),
    (3, "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)"),
]


def run_migrations(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER)"))
        row = conn.execute(text("SELECT version FROM _schema_version")).fetchone()
        if row is None:
            # Fresh DB — create_all already applied the latest schema; just stamp the version.
            conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
            return
        current = row[0]
        for version, sql in MIGRATIONS:
            if version > current:
                conn.execute(text(sql))
        conn.execute(text("DELETE FROM _schema_version"))
        conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
