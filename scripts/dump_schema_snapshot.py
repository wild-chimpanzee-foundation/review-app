"""Regenerate tests/fixtures/schema_snapshot.sql from the current models + migrations.

The snapshot is the schema of a fresh database (create_all + version stamp).
tests/test_schema_parity.py loads it, applies any pending migrations, and checks
the result matches a fresh create_all() database. Regenerate with:

    uv run python scripts/dump_schema_snapshot.py
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from review_app.backend.db.migrations import MIGRATIONS, run_migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine

SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "schema_snapshot.sql"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "snapshot.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        run_migrations(engine)
        engine.dispose()

        con = sqlite3.connect(db_path)
        try:
            rows = con.execute(
                "SELECT sql FROM sqlite_master"
                " WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                " ORDER BY type DESC, name"
            ).fetchall()
        finally:
            con.close()

    statements = [row[0].strip() + ";" for row in rows]
    statements.append(f"INSERT INTO _schema_version VALUES ({len(MIGRATIONS)});")
    header = (
        "-- Schema snapshot used by tests/test_schema_parity.py.\n"
        "-- Generated from models.py create_all() at schema version "
        f"{len(MIGRATIONS)}.\n"
        "-- Regenerate with: uv run python scripts/dump_schema_snapshot.py\n\n"
    )
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(header + "\n\n".join(statements) + "\n")
    print(f"Wrote {SNAPSHOT_PATH} (schema version {len(MIGRATIONS)})")


if __name__ == "__main__":
    main()
