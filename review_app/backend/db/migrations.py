from __future__ import annotations

from typing import Callable

from sqlalchemy import text

# Each entry is (version: int, sql: str | list[str] | Callable[[conn], None]).
# Use a callable for migrations that need conditional logic (e.g. idempotent DDL).
# Versions must be contiguous starting at 1. Never modify or remove existing entries.


def _migration_v4(conn) -> None:
    """Migrate to surrogate-ID species/behaviors schema. Idempotent — safe to re-run."""

    def _tables() -> set[str]:
        return {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }

    def _columns(table: str) -> set[str]:
        return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}

    # ── 1. Populate behaviors from all historic behavior strings ─────────────
    # behaviors table was created empty by create_all; source tables may still exist.
    sources = []
    if "species_behavior" in _tables():
        sources.append(
            "SELECT DISTINCT behavior AS b FROM species_behavior WHERE behavior IS NOT NULL"
        )
    if "behavior" in _columns("individual_observations"):
        sources.append(
            "SELECT DISTINCT behavior AS b FROM individual_observations"
            " WHERE behavior IS NOT NULL AND TRIM(behavior) <> ''"
        )
    if sources:
        conn.execute(
            text(
                f"""
                INSERT OR IGNORE INTO behaviors (id, key, name_en)
                SELECT lower(hex(randomblob(16))), b, b FROM ({" UNION ".join(sources)})
                """
            )
        )

    # ── 2. Recreate species table with surrogate id ──────────────────────────
    tables = _tables()
    if "species_new" not in tables and "id" not in _columns("species"):
        conn.execute(
            text(
                """
                CREATE TABLE species_new (
                    id TEXT PRIMARY KEY,
                    scientific_name TEXT UNIQUE NOT NULL,
                    name_en TEXT, name_fr TEXT, group_en TEXT, group_fr TEXT, iucn TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO species_new (id, scientific_name, name_en, name_fr, group_en, group_fr, iucn)
                SELECT lower(hex(randomblob(16))), scientific_name, name_en, name_fr,
                       group_en, group_fr, iucn
                FROM species
                """
            )
        )
    elif "species_new" not in tables:
        # id column already exists on species (partial previous run); nothing to do here.
        pass

    # ── 3. Populate species_behaviors from old junction table ─────────────────
    if "species_behavior" in _tables():
        src = "species_new" if "species_new" in _tables() else "species"
        conn.execute(
            text(
                f"""
                INSERT OR IGNORE INTO species_behaviors (species_id, behavior_id)
                SELECT sn.id, b.id
                FROM species_behavior sb
                JOIN {src} sn ON sn.scientific_name = sb.scientific_name
                JOIN behaviors b ON b.key = sb.behavior
                """
            )
        )
        conn.execute(text("DROP TABLE species_behavior"))

    # ── 4. Swap species_new → species ─────────────────────────────────────────
    if "species_new" in _tables():
        conn.execute(text("DROP TABLE species"))
        conn.execute(text("ALTER TABLE species_new RENAME TO species"))

    # ── 5. Add FK columns to individual_observations ─────────────────────────
    io_cols = _columns("individual_observations")
    if "species_id" not in io_cols:
        conn.execute(
            text(
                "ALTER TABLE individual_observations ADD COLUMN species_id TEXT REFERENCES species(id)"
            )
        )

    species_cols = _columns("species")
    if "is_custom" not in species_cols:
        conn.execute(text("ALTER TABLE species ADD COLUMN is_custom BOOLEAN NOT NULL DEFAULT 0"))

    behavior_cols = _columns("behaviors")
    if "is_custom" not in behavior_cols:
        conn.execute(text("ALTER TABLE behaviors ADD COLUMN is_custom BOOLEAN NOT NULL DEFAULT 0"))
    if "behavior_id" not in io_cols:
        conn.execute(
            text(
                "ALTER TABLE individual_observations ADD COLUMN behavior_id TEXT REFERENCES behaviors(id)"
            )
        )

    # ── 6. Backfill FK columns from old string columns ────────────────────────
    io_cols = _columns("individual_observations")
    if "species" in io_cols:
        conn.execute(
            text(
                """
                UPDATE individual_observations
                SET species_id = (SELECT id FROM species WHERE scientific_name = individual_observations.species)
                WHERE species_id IS NULL
                """
            )
        )
    if "behavior" in io_cols:
        conn.execute(
            text(
                """
                UPDATE individual_observations
                SET behavior_id = (SELECT id FROM behaviors WHERE key = individual_observations.behavior)
                WHERE behavior_id IS NULL
                """
            )
        )


MIGRATIONS: list[tuple[int, str | list[str] | Callable]] = [
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
    (4, _migration_v4),
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
        for version, migration in MIGRATIONS:
            if version > current:
                if callable(migration):
                    migration(conn)
                else:
                    stmts = migration if isinstance(migration, list) else [migration]
                    for stmt in stmts:
                        conn.execute(text(stmt))
        conn.execute(text("DELETE FROM _schema_version"))
        conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
