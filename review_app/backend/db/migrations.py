from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)

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

    # ── 1. Recreate species table with surrogate id ──────────────────────────
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

    # ── 2. Swap species_new → species ─────────────────────────────────────────
    if "species_new" in _tables():
        conn.execute(text("DROP TABLE species"))
        conn.execute(text("ALTER TABLE species_new RENAME TO species"))

    # ── 3. Drop old junction table — species_behaviors is repopulated from the bundled CSV on startup
    if "species_behavior" in _tables():
        conn.execute(text("DROP TABLE species_behavior"))

    # ── 4. Add FK columns to individual_observations ─────────────────────────
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

    # ── 5. Backfill FK columns from old string columns ────────────────────────
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


def _migration_v7(conn) -> None:
    """Create tags and video_tags tables and seed built-in tags. Idempotent."""
    tables = {
        r[0]
        for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }
    if "tags" not in tables:
        conn.execute(
            text("""
                CREATE TABLE tags (
                    id TEXT PRIMARY KEY,
                    key TEXT UNIQUE NOT NULL,
                    name_en TEXT NOT NULL,
                    name_fr TEXT,
                    color TEXT,
                    icon TEXT,
                    is_custom BOOLEAN NOT NULL DEFAULT 0
                )
            """)
        )
    if "video_tags" not in tables:
        conn.execute(
            text("""
                CREATE TABLE video_tags (
                    video_id TEXT NOT NULL REFERENCES videos(video_id),
                    tag_id TEXT NOT NULL REFERENCES tags(id),
                    tagged_by TEXT,
                    tagged_at TEXT NOT NULL,
                    PRIMARY KEY (video_id, tag_id)
                )
            """)
        )
    builtin_tags = [
        ("fire", "Fire", "Feu", "deep-orange", "local_fire_department"),
        ("nice_shot", "Nice Shot", "Belle image", "amber", "star"),
        ("broken_metadata", "Broken Metadata", "Métadonnées corrompues", "red", "report_problem"),
    ]
    for key, name_en, name_fr, color, icon in builtin_tags:
        existing = conn.execute(text("SELECT id FROM tags WHERE key = :k"), {"k": key}).fetchone()
        if existing is None:
            import uuid as _uuid

            conn.execute(
                text(
                    "INSERT INTO tags (id, key, name_en, name_fr, color, icon, is_custom) "
                    "VALUES (:id, :key, :name_en, :name_fr, :color, :icon, 0)"
                ),
                {
                    "id": str(_uuid.uuid4()),
                    "key": key,
                    "name_en": name_en,
                    "name_fr": name_fr,
                    "color": color,
                    "icon": icon,
                },
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
    (
        5,
        [
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_project_dir ON project_dirs(project_id, path)",
            "CREATE INDEX IF NOT EXISTS idx_videos_is_web_safe ON videos(is_web_safe)",
        ],
    ),
    (
        6,
        lambda conn: [
            conn.execute(text("ALTER TABLE videos ADD COLUMN latitude REAL"))
            if "latitude"
            not in {r[1] for r in conn.execute(text("PRAGMA table_info(videos)")).fetchall()}
            else None,
            conn.execute(text("ALTER TABLE videos ADD COLUMN longitude REAL"))
            if "longitude"
            not in {r[1] for r in conn.execute(text("PRAGMA table_info(videos)")).fetchall()}
            else None,
        ],
    ),
    (
        7,
        lambda conn: _migration_v7(conn),
    ),
    (
        8,
        lambda conn: [
            conn.execute(text("ALTER TABLE individual_observations ADD COLUMN count INTEGER"))
            if "count"
            not in {
                r[1]
                for r in conn.execute(
                    text("PRAGMA table_info(individual_observations)")
                ).fetchall()
            }
            else None,
            conn.execute(text("UPDATE individual_observations SET count = 1 WHERE count IS NULL")),
        ],
    ),
]


def run_migrations(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER)"))
        row = conn.execute(text("SELECT version FROM _schema_version")).fetchone()
        if row is None:
            logger.info("Fresh database — stamping schema version %d", len(MIGRATIONS))
            conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
            return
        current = row[0]
        pending = [v for v, _ in MIGRATIONS if v > current]
        if pending:
            logger.info(
                "Running %d migration(s): v%d → v%d", len(pending), current, len(MIGRATIONS)
            )
        for version, migration in MIGRATIONS:
            if version > current:
                logger.debug("Applying migration v%d", version)
                if callable(migration):
                    migration(conn)
                else:
                    stmts = migration if isinstance(migration, list) else [migration]
                    for stmt in stmts:
                        conn.execute(text(stmt))
        conn.execute(text("DELETE FROM _schema_version"))
        conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
