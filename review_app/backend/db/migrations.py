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


def _migration_v9(conn) -> None:
    """Add species_collections / species_collection_members tables and Project.collection_id."""
    tables = {
        r[0]
        for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }
    if "species_collections" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE species_collections (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    is_custom BOOLEAN NOT NULL DEFAULT 0
                )
                """
            )
        )
    if "species_collection_members" not in tables:
        conn.execute(
            text(
                """
                CREATE TABLE species_collection_members (
                    collection_id TEXT NOT NULL REFERENCES species_collections(id),
                    species_id TEXT NOT NULL REFERENCES species(id),
                    PRIMARY KEY (collection_id, species_id)
                )
                """
            )
        )
    proj_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(projects)")).fetchall()}
    if "collection_id" not in proj_cols:
        conn.execute(
            text(
                "ALTER TABLE projects ADD COLUMN collection_id TEXT REFERENCES species_collections(id)"
            )
        )


def _migration_v11(conn) -> None:
    """Replace inline UNIQUE(video_id, model_name, annotation_type) with an expression index
    that includes value_text so multiple object_detection rows per video are allowed.
    SQLite cannot drop inline table constraints, so we recreate the table."""
    conn.execute(text("DROP INDEX IF EXISTS uq_model_ann_identity"))
    conn.execute(
        text("""
            CREATE TABLE model_annotations_new (
                id TEXT PRIMARY KEY,
                project_id TEXT REFERENCES projects(id),
                video_id TEXT NOT NULL REFERENCES videos(video_id),
                annotation_type TEXT NOT NULL,
                model_name TEXT NOT NULL,
                value_text TEXT,
                value_num REAL,
                probability REAL,
                t_start_sec REAL,
                t_end_sec REAL,
                updated_at TEXT NOT NULL
            )
        """)
    )
    conn.execute(
        text("""
            INSERT INTO model_annotations_new
            SELECT id, project_id, video_id, annotation_type, model_name,
                   value_text, value_num, probability, t_start_sec, t_end_sec, updated_at
            FROM model_annotations
        """)
    )
    conn.execute(text("DROP TABLE model_annotations"))
    conn.execute(text("ALTER TABLE model_annotations_new RENAME TO model_annotations"))
    conn.execute(
        text(
            "CREATE UNIQUE INDEX uq_model_ann_identity "
            "ON model_annotations(video_id, model_name, annotation_type, COALESCE(value_text, ''))"
        )
    )
    conn.execute(
        text("CREATE INDEX idx_model_annotations_project_id ON model_annotations(project_id)")
    )
    conn.execute(
        text("CREATE INDEX idx_model_annotations_video_id ON model_annotations(video_id)")
    )
    conn.execute(
        text(
            "CREATE INDEX idx_model_annotations_annotation_type ON model_annotations(annotation_type)"
        )
    )
    conn.execute(
        text("CREATE INDEX idx_model_annotations_model_name ON model_annotations(model_name)")
    )


def _migration_v12(conn) -> None:
    """Seed additional built-in tags. Idempotent."""
    import uuid as _uuid

    new_builtin_tags = [
        ("nice_video", "Nice Video", "Belle vidéo", "amber", "thumb_up"),
        ("funny", "Funny", "Drôle", "purple", "sentiment_very_satisfied"),
        ("incorrect_time", "Incorrect Time", "Heure incorrecte", "orange", "schedule"),
        ("incorrect_date", "Incorrect Date", "Date incorrecte", "orange", "event_busy"),
        ("rain", "Rain", "Pluie", "blue", "water_drop"),
        ("wind", "Wind", "Vent", "teal", "air"),
        ("blurry", "Blurry", "Flou", "grey", "blur_on"),
        ("backlight", "Backlight", "Contre-jour", "yellow", "wb_sunny"),
        ("injured", "Injured", "Animal blessé", "red", "healing"),
        ("disease", "Disease", "Animal malade", "deep-orange", "sick"),
        ("reference_video", "Reference Video", "Vidéo de référence", "indigo", "bookmark"),
        ("cam_too_high", "Cam Too High", "Cam trop haute", "brown", "arrow_upward"),
        ("cam_too_low", "Cam Too Low", "Cam trop basse", "brown", "arrow_downward"),
        (
            "cam_displaced_by_animal",
            "Cam Displaced by Animal",
            "Cam déplacée par animal",
            "brown",
            "pets",
        ),
        (
            "cam_displaced_by_human",
            "Cam Displaced by Human",
            "Cam déplacée par humain",
            "brown",
            "person",
        ),
        ("cam_on_ground", "Cam on Ground", "Cam au sol", "brown", "landscape"),
        ("different_view_angle", "Different View Angle", "Angle de vue différent", "cyan", "360"),
        ("branch_leaf", "Branch/Leaf", "Branche/Feuille", "green", "eco"),
        ("vegetation_growth", "Vegetation Growth", "Croissance végétation", "green", "grass"),
    ]
    for key, name_en, name_fr, color, icon in new_builtin_tags:
        existing = conn.execute(text("SELECT id FROM tags WHERE key = :k"), {"k": key}).fetchone()
        if existing is None:
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


def _migration_v15(conn) -> None:
    """Convert custom tags that shadow built-in keys into proper built-ins. Idempotent."""
    builtin_tags = [
        ("fire", "Fire", "Feu", "deep-orange", "local_fire_department"),
        ("nice_video", "Nice Video", "Belle vidéo", "amber", "thumb_up"),
        ("funny", "Funny", "Drôle", "purple", "sentiment_very_satisfied"),
        ("incorrect_time", "Incorrect Time", "Heure incorrecte", "orange", "schedule"),
        ("incorrect_date", "Incorrect Date", "Date incorrecte", "orange", "event_busy"),
        ("rain", "Rain", "Pluie", "blue", "water_drop"),
        ("wind", "Wind", "Vent", "teal", "air"),
        ("blurry", "Blurry", "Flou", "grey", "blur_on"),
        ("backlight", "Backlight", "Contre-jour", "yellow", "wb_sunny"),
        ("injured", "Injured", "Animal blessé", "red", "healing"),
        ("disease", "Disease", "Animal malade", "deep-orange", "sick"),
        ("reference_video", "Reference Video", "Vidéo de référence", "indigo", "bookmark"),
        ("cam_too_high", "Cam Too High", "Cam trop haute", "brown", "arrow_upward"),
        ("cam_too_low", "Cam Too Low", "Cam trop basse", "brown", "arrow_downward"),
        (
            "cam_displaced_by_animal",
            "Cam Displaced by Animal",
            "Cam déplacée par animal",
            "brown",
            "pets",
        ),
        (
            "cam_displaced_by_human",
            "Cam Displaced by Human",
            "Cam déplacée par humain",
            "brown",
            "person",
        ),
        ("cam_on_ground", "Cam on Ground", "Cam au sol", "brown", "landscape"),
        ("different_view_angle", "Different View Angle", "Angle de vue différent", "cyan", "360"),
        ("branch_leaf", "Branch/Leaf", "Branche/Feuille", "green", "eco"),
        ("vegetation_growth", "Vegetation Growth", "Croissance végétation", "green", "grass"),
    ]
    for key, name_en, name_fr, color, icon in builtin_tags:
        conn.execute(
            text(
                "UPDATE tags SET name_en = :name_en, name_fr = :name_fr, color = :color, "
                "icon = :icon, is_custom = 0 "
                "WHERE key = :key AND is_custom = 1"
            ),
            {"key": key, "name_en": name_en, "name_fr": name_fr, "color": color, "icon": icon},
        )


def _migration_v17(conn) -> None:
    """Idempotent re-run of v11: ensure model_annotations uses a named expression index
    on (video_id, model_name, annotation_type, COALESCE(value_text, '')) instead of the
    old inline UNIQUE(video_id, model_name, annotation_type). DBs where v11 ran correctly
    already have the named index and skip the table recreation."""
    has_expr_index = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='index' AND name='uq_model_ann_identity'")
    ).fetchone()
    if has_expr_index:
        return
    conn.execute(text("DROP INDEX IF EXISTS uq_model_ann_identity"))
    conn.execute(
        text("""
            CREATE TABLE model_annotations_new (
                id TEXT PRIMARY KEY,
                project_id TEXT REFERENCES projects(id),
                video_id TEXT NOT NULL REFERENCES videos(video_id),
                annotation_type TEXT NOT NULL,
                model_name TEXT NOT NULL,
                value_text TEXT,
                value_num REAL,
                probability REAL,
                t_start_sec REAL,
                t_end_sec REAL,
                updated_at TEXT NOT NULL
            )
        """)
    )
    conn.execute(
        text("""
            INSERT INTO model_annotations_new
            SELECT id, project_id, video_id, annotation_type, model_name,
                   value_text, value_num, probability, t_start_sec, t_end_sec, updated_at
            FROM model_annotations
        """)
    )
    conn.execute(text("DROP TABLE model_annotations"))
    conn.execute(text("ALTER TABLE model_annotations_new RENAME TO model_annotations"))
    conn.execute(
        text(
            "CREATE UNIQUE INDEX uq_model_ann_identity "
            "ON model_annotations(video_id, model_name, annotation_type, COALESCE(value_text, ''))"
        )
    )
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_model_annotations_project_id ON model_annotations(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_model_annotations_video_id ON model_annotations(video_id)",
        "CREATE INDEX IF NOT EXISTS idx_model_annotations_annotation_type ON model_annotations(annotation_type)",
        "CREATE INDEX IF NOT EXISTS idx_model_annotations_model_name ON model_annotations(model_name)",
        "CREATE INDEX IF NOT EXISTS idx_model_ann_type_text_video ON model_annotations(annotation_type, value_text, video_id)",
        "CREATE INDEX IF NOT EXISTS idx_model_ann_blank_probe ON model_annotations(annotation_type, video_id, probability)",
    ]:
        conn.execute(text(idx_sql))


def _migration_v18(conn) -> None:
    """Convert behavior_id FK on individual_observations to observation_tags junction table.
    Drops species_behaviors and project_species_behaviors.  Idempotent."""
    tables = {
        r[0]
        for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }

    if "observation_tags" not in tables:
        conn.execute(
            text("""
                CREATE TABLE observation_tags (
                    video_id TEXT NOT NULL REFERENCES videos(video_id),
                    observation_id INTEGER NOT NULL,
                    behavior_id TEXT NOT NULL REFERENCES behaviors(id),
                    PRIMARY KEY (video_id, observation_id, behavior_id)
                )
            """)
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_observation_tags_video"
                " ON observation_tags(video_id, behavior_id)"
            )
        )

    io_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(individual_observations)")).fetchall()}
    if "behavior_id" in io_cols:
        # Migrate: copy non-"does_not_react" behavior_id rows to observation_tags
        does_not_react_id = conn.execute(
            text("SELECT id FROM behaviors WHERE key = 'does_not_react'")
        ).scalar()
        if does_not_react_id:
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO observation_tags (video_id, observation_id, behavior_id)
                    SELECT video_id, id, behavior_id
                    FROM individual_observations
                    WHERE behavior_id IS NOT NULL AND behavior_id != :dnr
                """),
                {"dnr": does_not_react_id},
            )
        else:
            conn.execute(
                text("""
                    INSERT OR IGNORE INTO observation_tags (video_id, observation_id, behavior_id)
                    SELECT video_id, id, behavior_id
                    FROM individual_observations
                    WHERE behavior_id IS NOT NULL
                """)
            )

        # Recreate individual_observations without behavior_id column
        conn.execute(
            text("""
                CREATE TABLE individual_observations_new (
                    video_id TEXT NOT NULL REFERENCES videos(video_id),
                    id INTEGER NOT NULL,
                    project_id TEXT REFERENCES projects(id),
                    species_id TEXT REFERENCES species(id),
                    count INTEGER,
                    start_sec REAL NOT NULL DEFAULT 0.0,
                    end_sec REAL,
                    labeled_by TEXT,
                    labeled_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (video_id, id)
                )
            """)
        )
        conn.execute(
            text("""
                INSERT INTO individual_observations_new
                SELECT video_id, id, project_id, species_id, count, start_sec, end_sec,
                       labeled_by, labeled_at, updated_at
                FROM individual_observations
            """)
        )
        conn.execute(text("DROP TABLE individual_observations"))
        conn.execute(
            text("ALTER TABLE individual_observations_new RENAME TO individual_observations")
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_individual_video_species"
                " ON individual_observations(video_id, species_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_individual_video_time"
                " ON individual_observations(video_id, start_sec)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_individual_project"
                " ON individual_observations(project_id)"
            )
        )

    # Drop old per-species behavior junction tables
    if "species_behaviors" in tables:
        conn.execute(text("DROP TABLE species_behaviors"))
    if "project_species_behaviors" in tables:
        conn.execute(text("DROP TABLE project_species_behaviors"))

    # Remove the does_not_react built-in behavior — empty tags now means normal
    conn.execute(text("DELETE FROM behaviors WHERE key = 'does_not_react' AND is_custom = 0"))


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
    (9, _migration_v9),
    (
        # v10 adds the expression index to the existing table (inline UNIQUE constraint stays).
        # v11 supersedes this by doing a full table recreate to drop the inline constraint.
        # v10 must remain here for DBs that already applied it.
        10,
        lambda conn: [
            conn.execute(text("DROP INDEX IF EXISTS uq_model_ann_identity")),
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_model_ann_identity "
                    "ON model_annotations(video_id, model_name, annotation_type, COALESCE(value_text, ''))"
                )
            ),
        ],
    ),
    (11, _migration_v11),
    (12, _migration_v12),
    (
        13,
        lambda conn: [
            conn.execute(
                text(
                    "DELETE FROM video_tags WHERE tag_id IN "
                    "(SELECT id FROM tags WHERE key IN ('broken_metadata', 'nice_shot'))"
                )
            ),
            conn.execute(
                text(
                    "DELETE FROM tags WHERE key IN ('broken_metadata', 'nice_shot') AND is_custom = 0"
                )
            ),
        ],
    ),
    (
        14,
        lambda conn: (
            conn.execute(
                text("ALTER TABLE videos ADD COLUMN is_missing INTEGER NOT NULL DEFAULT 0")
            )
            if "is_missing"
            not in {r[1] for r in conn.execute(text("PRAGMA table_info(videos)")).fetchall()}
            else None
        ),
    ),
    (15, _migration_v15),
    (
        16,
        lambda conn: [
            conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS annotators (
                        name TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL
                    )
                """)
            ),
            conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS video_assignments (
                        video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
                        assigned_to TEXT NOT NULL REFERENCES annotators(name),
                        assigned_at TEXT NOT NULL
                    )
                """)
            ),
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_video_assignments_assigned_to "
                    "ON video_assignments(assigned_to)"
                )
            ),
        ],
    ),
    (17, _migration_v17),
    (18, _migration_v18),
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
