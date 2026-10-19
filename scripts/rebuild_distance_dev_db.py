"""Rebuild a private v29 development DB as v26, preserving ordinary Review exactly.

Stop the app and back up the source first. This command never installs the result
or changes the source. Run from the repository root:

    uv run python -m scripts.rebuild_distance_dev_db --source backup.db --output rebuilt.db

The default is a read-only report. Add --execute to create a new output database.
Prepared references and all Distance annotations/progress are intentionally reset;
imported video records, reference tags, vocabulary, and ordinary annotations survive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from review_app.backend.db.migrations import MIGRATIONS, run_migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine

SOURCE_VERSION = 29
TARGET_VERSION = 26
DISTANCE_TABLES = frozenset(
    {
        "reference_videos",
        "reference_frames",
        "distance_individuals",
        "distance_samples",
        "distance_individual_tags",
        "distance_sample_behaviors",
        "distance_video_reviews",
        "distance_review_progress",
    }
)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _check_integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Database integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Database contains invalid foreign keys")


def _validate_source(conn: sqlite3.Connection) -> None:
    # This is intentionally a one-off converter, not a second migration framework.
    if len(MIGRATIONS) != TARGET_VERSION:
        raise ValueError(f"This converter requires the v{TARGET_VERSION} application")
    tables = {
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    expected = set(Base.metadata.tables) | {"_schema_version"}
    if tables != expected:
        raise ValueError(
            f"Unexpected source tables: missing={sorted(expected - tables)}, "
            f"extra={sorted(tables - expected)}"
        )
    for name in sorted(expected):
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({_quote(name)})")}
        wanted = (
            {"version"} if name == "_schema_version" else set(Base.metadata.tables[name].c.keys())
        )
        if name == "distance_video_reviews":
            wanted -= {"alignment_mode"}
        if columns != wanted:
            raise ValueError(f"Unexpected source columns in {name}: {sorted(columns)}")
    if conn.execute("SELECT version FROM _schema_version").fetchall() != [(SOURCE_VERSION,)]:
        raise ValueError(f"Only the original private v{SOURCE_VERSION} database is supported")
    _check_integrity(conn)


def _rows(conn: sqlite3.Connection, table):
    columns = ", ".join(_quote(column.name) for column in table.columns)
    order = ", ".join(_quote(column.name) for column in table.primary_key)
    return conn.execute(f"SELECT {columns} FROM {_quote(table.name)} ORDER BY {order}")


def _fingerprint(conn: sqlite3.Connection, table) -> dict:
    digest = hashlib.sha256()
    count = 0
    for row in _rows(conn, table):
        # SQLite returns only scalar values; repr preserves their types, bytes,
        # timestamp strings, and embedded newlines without lossy normalization.
        digest.update(repr(tuple(row)).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def _copy_retained(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    for table in Base.metadata.sorted_tables:
        if table.name in DISTANCE_TABLES:
            continue
        columns = ", ".join(_quote(column.name) for column in table.columns)
        parameters = ", ".join("?" for _ in table.columns)
        target.executemany(
            f"INSERT INTO {_quote(table.name)} ({columns}) VALUES ({parameters})",
            _rows(source, table),
        )


def rebuild_database(source: Path, output: Path, *, execute: bool = False) -> dict:
    """Report or create a checked replacement; never overwrite a source/output file."""
    source, output = source.resolve(), output.resolve()
    if source == output:
        raise ValueError("Source and output must be different files")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        # Pin one snapshot for schema checks, reporting, copying and comparison.
        src.execute("BEGIN")
        _validate_source(src)
        retained = {
            table.name: _fingerprint(src, table)
            for table in Base.metadata.sorted_tables
            if table.name not in DISTANCE_TABLES
        }
        report = {
            "source": str(source),
            "output": str(output),
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "executed": False,
            "retained": retained,
            "discarded": {
                name: src.execute(f"SELECT COUNT(*) FROM {_quote(name)}").fetchone()[0]
                for name in sorted(DISTANCE_TABLES)
            },
        }
        if not execute:
            return report

        # Reserve exclusively; another file must never be truncated or replaced.
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            engine = create_engine(f"sqlite:///{output}")
            try:
                Base.metadata.create_all(engine)
                run_migrations(engine)
            finally:
                engine.dispose()
            with closing(sqlite3.connect(output)) as dst:
                dst.execute("PRAGMA foreign_keys=ON")
                with dst:
                    _copy_retained(src, dst)
                    for table in Base.metadata.sorted_tables:
                        if table.name in DISTANCE_TABLES:
                            if dst.execute(
                                f"SELECT COUNT(*) FROM {_quote(table.name)}"
                            ).fetchone()[0]:
                                raise ValueError(f"Distance table is not empty: {table.name}")
                        elif _fingerprint(dst, table) != retained[table.name]:
                            raise ValueError(f"Retained data differs in {table.name}")
                    _check_integrity(dst)
            report["executed"] = True
            return report
        except BaseException:
            output.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(output) + suffix).unlink(missing_ok=True)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Original v29 backup")
    parser.add_argument(
        "--output", required=True, type=Path, help="New v26 database (must not exist)"
    )
    parser.add_argument("--execute", action="store_true", help="Create output; default is dry run")
    args = parser.parse_args()
    try:
        report = rebuild_database(args.source, args.output, execute=args.execute)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Rebuild failed: {exc}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
