"""Guard against drift between models.py (fresh installs) and migrations.py (upgrades).

The schema is defined twice: ``Base.metadata.create_all()`` builds it for fresh
databases, while the MIGRATIONS list evolves existing ones. ``schema_snapshot.sql``
is a committed snapshot of the fresh schema at the version it was generated.
These tests load the snapshot, run any pending migrations on it, and compare the
result structurally (columns, primary keys, foreign keys, indexes, uniqueness)
against a fresh create_all() database.

If a test here fails after you added a migration, the migration produces a schema
that differs from models.py — fix one of the two. After they agree, refresh the
snapshot with: uv run python scripts/dump_schema_snapshot.py
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from review_app.backend.db.migrations import run_migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine

SNAPSHOT = Path(__file__).parent / "fixtures" / "schema_snapshot.sql"

REGEN_HINT = (
    "Migrated schema differs from models.py create_all() schema. "
    "Fix the migration or the model so they agree, then regenerate the snapshot "
    "with: uv run python scripts/dump_schema_snapshot.py"
)


def _affinity(decl: str | None) -> str:
    """SQLite column type affinity (https://www.sqlite.org/datatype3.html#determination_of_column_affinity)."""
    d = (decl or "").upper()
    if "INT" in d:
        return "INTEGER"
    if "CHAR" in d or "CLOB" in d or "TEXT" in d:
        return "TEXT"
    if not d or "BLOB" in d:
        return "BLOB"
    if "REAL" in d or "FLOA" in d or "DOUB" in d:
        return "REAL"
    return "NUMERIC"


def _norm_default(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().strip("'\"")


def _norm_sql(sql: str) -> str:
    """Normalize a CREATE INDEX statement for comparison: case- and whitespace-insensitive."""
    return re.sub(r"\s+", "", sql.replace('"', "").lower())


def _table_schema(con: sqlite3.Connection, table: str) -> dict:
    columns = {}
    for _cid, name, decl, notnull, default, pk in con.execute(f"PRAGMA table_info({table})"):
        columns[name] = {
            "affinity": _affinity(decl),
            "notnull": bool(notnull),
            "default": _norm_default(default),
            "pk": pk,
        }
    fks = {
        (row[3], row[2], row[4])  # (from_column, referenced_table, referenced_column)
        for row in con.execute(f"PRAGMA foreign_key_list({table})")
    }
    indexes = set()
    for _seq, name, unique, origin, _partial in con.execute(f"PRAGMA index_list({table})"):
        cols = tuple(
            r[2] if r[2] is not None else "<expr>"
            for r in con.execute(f"PRAGMA index_info({name})")
        )
        # Autoindexes (inline UNIQUE / PK) have generated names; compare them by shape only.
        indexes.add((origin, bool(unique), cols, name if origin == "c" else None))
    return {"columns": columns, "fks": fks, "indexes": indexes}


def _schema(db_path: Path) -> dict:
    con = sqlite3.connect(db_path)
    try:
        tables = sorted(
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        result = {t: _table_schema(con, t) for t in tables}
        # Named index DDL (expression indexes are invisible to index_info, so compare SQL too)
        result["__named_index_sql__"] = {
            r[0]: _norm_sql(r[1])
            for r in con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
        }
        return result
    finally:
        con.close()


def _build_fresh(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    run_migrations(engine)
    engine.dispose()


def _build_from_snapshot(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SNAPSHOT.read_text())
    finally:
        con.close()
    engine = create_engine(f"sqlite:///{db_path}")
    run_migrations(engine)
    engine.dispose()


def test_snapshot_plus_migrations_matches_create_all(tmp_path):
    fresh_db = tmp_path / "fresh.db"
    migrated_db = tmp_path / "migrated.db"
    _build_fresh(fresh_db)
    _build_from_snapshot(migrated_db)

    fresh = _schema(fresh_db)
    migrated = _schema(migrated_db)

    assert sorted(fresh.keys()) == sorted(migrated.keys()), REGEN_HINT
    for table in fresh:
        assert fresh[table] == migrated[table], f"Schema mismatch in {table!r}. {REGEN_HINT}"


def test_snapshot_version_is_not_ahead_of_migrations(tmp_path):
    """A snapshot stamped newer than MIGRATIONS means it was generated against code
    that no longer exists — regenerate it."""
    from review_app.backend.db.migrations import MIGRATIONS

    match = re.search(r"INSERT INTO _schema_version VALUES \((\d+)\);", SNAPSHOT.read_text())
    assert match, "snapshot is missing its _schema_version stamp"
    assert int(match.group(1)) <= len(MIGRATIONS), REGEN_HINT
