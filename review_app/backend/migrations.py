from __future__ import annotations

from sqlalchemy import text

# Add tuples of (version: int, sql: str) to apply schema changes to existing DBs.
# Versions must be contiguous starting at 1. Never modify or remove existing entries.
MIGRATIONS: list[tuple[int, str]] = []


def run_migrations(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER)"))
        row = conn.execute(text("SELECT version FROM _schema_version")).fetchone()
        current = row[0] if row else 0
        for version, sql in MIGRATIONS:
            if version > current:
                conn.execute(text(sql))
        conn.execute(text("DELETE FROM _schema_version"))
        conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
