from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Each entry is (version: int, sql: str | list[str] | Callable[[conn], None]).
# Use a callable for migrations that need conditional logic (e.g. idempotent DDL).
# Versions must be contiguous starting at 1. Never modify or remove published entries.
# Distance's private development migrations were consolidated into v23 before release.


def _add_column_if_missing(conn, table: str, column: str, decl: str) -> None:
    """ALTER TABLE ... ADD COLUMN, skipped if the column already exists. Idempotent."""
    cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {decl}"))


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

    io_cols = {
        r[1] for r in conn.execute(text("PRAGMA table_info(individual_observations)")).fetchall()
    }
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


def _migration_v22(conn) -> None:
    """Add and backfill the durable cross-database identity for observations."""
    import uuid as _uuid

    _add_column_if_missing(conn, "individual_observations", "observation_uuid", "TEXT")
    missing = conn.execute(
        text(
            "SELECT video_id, id FROM individual_observations "
            "WHERE observation_uuid IS NULL OR TRIM(observation_uuid) = ''"
        )
    ).fetchall()
    if missing:
        conn.execute(
            text(
                "UPDATE individual_observations SET observation_uuid = :observation_uuid "
                "WHERE video_id = :video_id AND id = :id"
            ),
            [
                {
                    "video_id": video_id,
                    "id": observation_id,
                    "observation_uuid": str(_uuid.uuid4()),
                }
                for video_id, observation_id in missing
            ],
        )
    # SQLite cannot make an added column NOT NULL after the fact. Recreate the
    # table so upgraded databases have the same invariant as fresh databases.
    conn.execute(
        text("""
            CREATE TABLE individual_observations_new (
                video_id TEXT NOT NULL REFERENCES videos(video_id),
                id INTEGER NOT NULL,
                observation_uuid TEXT NOT NULL,
                project_id TEXT REFERENCES projects(id),
                species_id TEXT REFERENCES species(id),
                count INTEGER,
                start_sec REAL NOT NULL DEFAULT 0.0,
                end_sec REAL,
                labeled_by TEXT,
                labeled_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (video_id, id),
                UNIQUE (video_id, observation_uuid)
            )
        """)
    )
    conn.execute(
        text("""
            INSERT INTO individual_observations_new
                (video_id, id, observation_uuid, project_id, species_id, count,
                 start_sec, end_sec, labeled_by, labeled_at, updated_at)
            SELECT video_id, id, observation_uuid, project_id, species_id, count,
                   start_sec, end_sec, labeled_by, labeled_at, updated_at
            FROM individual_observations
        """)
    )
    conn.execute(text("DROP TABLE individual_observations"))
    conn.execute(text("ALTER TABLE individual_observations_new RENAME TO individual_observations"))
    for idx_sql in [
        "CREATE INDEX idx_individual_video_species ON individual_observations(video_id, species_id)",
        "CREATE INDEX idx_individual_video_time ON individual_observations(video_id, start_sec)",
        "CREATE INDEX ix_individual_observations_project_id ON individual_observations(project_id)",
        "CREATE INDEX ix_individual_observations_species_id ON individual_observations(species_id)",
    ]:
        conn.execute(text(idx_sql))


def _migration_v23(conn) -> None:
    """Add the final Distance schema to a published v22 database."""
    for sql in DISTANCE_SCHEMA_V23:
        conn.execute(text(sql))
    _add_column_if_missing(conn, "tags", "individual_eligible", "BOOLEAN NOT NULL DEFAULT 0")
    conn.execute(
        text(
            "UPDATE tags SET individual_eligible = 1 "
            "WHERE is_custom = 1 OR key IN ('injured', 'disease')"
        )
    )
    # Legacy custom tags describing import/video metadata are not animal attributes.
    conn.execute(
        text(
            "UPDATE tags SET individual_eligible = 0 "
            "WHERE lower(trim(key)) IN ('comments', 'import_tags')"
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
            _add_column_if_missing(conn, "videos", "latitude", "REAL"),
            _add_column_if_missing(conn, "videos", "longitude", "REAL"),
        ],
    ),
    (
        7,
        lambda conn: _migration_v7(conn),
    ),
    (
        8,
        lambda conn: [
            _add_column_if_missing(conn, "individual_observations", "count", "INTEGER"),
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
        lambda conn: _add_column_if_missing(
            conn, "videos", "is_missing", "INTEGER NOT NULL DEFAULT 0"
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
    (
        19,
        lambda conn: _add_column_if_missing(conn, "species", "inaturalist_url", "TEXT"),
    ),
    (
        # Queue queries now filter model_annotations by project_id; backfill rows
        # imported before the column existed so they don't vanish from the filters.
        20,
        """
        UPDATE model_annotations
        SET project_id = (
            SELECT project_id FROM videos
            WHERE videos.video_id = model_annotations.video_id
        )
        WHERE project_id IS NULL
        """,
    ),
    (
        # Canonicalize stored paths to forward slashes so DBs/bundles created on
        # Windows match correctly when opened on macOS/Linux (and vice versa).
        21,
        [
            "UPDATE videos SET video_path = REPLACE(video_path, '\\', '/')"
            " WHERE video_path LIKE '%\\%'",
            "UPDATE project_dirs SET path = REPLACE(path, '\\', '/') WHERE path LIKE '%\\%'",
        ],
    ),
    (22, _migration_v22),
    (23, _migration_v23),
    (24, lambda conn: _migration_v24(conn)),
]

# Frozen upgrade DDL. Keep independent of evolving ORM metadata.
DISTANCE_SCHEMA_V23 = [
    """
    CREATE TABLE IF NOT EXISTS distance_imports (
        id VARCHAR NOT NULL,
        project_id VARCHAR NOT NULL,
        content_hash VARCHAR NOT NULL,
        manifest JSON NOT NULL,
        payload JSON NOT NULL,
        imported_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        UNIQUE (project_id, content_hash),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_distance_imports_project_id ON distance_imports (project_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_videos (
        id VARCHAR NOT NULL,
        project_id VARCHAR NOT NULL,
        camera_id VARCHAR NOT NULL,
        video_path VARCHAR NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_reference_video_path_project UNIQUE (project_id, video_path),
        CONSTRAINT ck_reference_camera CHECK (length(trim(camera_id)) > 0),
        CONSTRAINT ck_reference_path CHECK (length(trim(video_path)) > 0),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reference_deployment ON reference_videos (project_id, camera_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS distance_individuals (
        id VARCHAR NOT NULL,
        video_id VARCHAR NOT NULL,
        import_id VARCHAR,
        ai_original JSON,
        individual_number INTEGER NOT NULL,
        species_id VARCHAR NOT NULL,
        age VARCHAR,
        sex VARCHAR,
        first_seen_offset_ms INTEGER NOT NULL,
        first_seen_playback_ms FLOAT,
        initial_carrier_individual_id VARCHAR,
        anchor_x FLOAT,
        anchor_y FLOAT,
        anchor_width FLOAT,
        anchor_height FLOAT,
        labeled_by VARCHAR,
        labeled_at DATETIME,
        updated_at DATETIME NOT NULL,
        review_status VARCHAR DEFAULT 'not_started' NOT NULL,
        completed_by VARCHAR,
        completed_at DATETIME,
        PRIMARY KEY (id),
        CONSTRAINT uq_distance_individual_number UNIQUE (video_id, individual_number),
        CONSTRAINT ck_distance_individual_number CHECK (individual_number > 0),
        CONSTRAINT ck_distance_first_seen CHECK (first_seen_offset_ms >= 0),
        CONSTRAINT ck_distance_age CHECK (age IS NULL OR age IN ('Adult', 'Adolescent', 'Juvenile', 'Infant', 'Unknown')),
        CONSTRAINT ck_distance_sex CHECK (sex IS NULL OR sex IN ('Male', 'Female', 'Unknown')),
        CONSTRAINT ck_distance_anchor CHECK ((anchor_x IS NULL AND anchor_y IS NULL AND anchor_width IS NULL AND anchor_height IS NULL) OR (anchor_x IS NOT NULL AND anchor_y IS NOT NULL AND anchor_x BETWEEN 0 AND 1 AND anchor_y BETWEEN 0 AND 1 AND anchor_width IS NOT NULL AND anchor_height IS NOT NULL AND anchor_width > 0 AND anchor_height > 0 AND anchor_x + anchor_width <= 1 AND anchor_y + anchor_height <= 1)),
        FOREIGN KEY(video_id) REFERENCES videos (video_id) ON DELETE CASCADE,
        FOREIGN KEY(import_id) REFERENCES distance_imports (id) ON DELETE SET NULL,
        FOREIGN KEY(species_id) REFERENCES species (id),
        FOREIGN KEY(initial_carrier_individual_id) REFERENCES distance_individuals (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS distance_video_reviews (
        video_id VARCHAR NOT NULL,
        recorded_start DATETIME,
        alignment_mode VARCHAR DEFAULT 'exact' NOT NULL,
        imported_sample_times_s JSON,
        import_id VARCHAR,
        started_at DATETIME,
        clock_anchor_offset_ms INTEGER,
        clock_anchor_playback_ms FLOAT,
        opening_even_second BOOLEAN DEFAULT '0' NOT NULL,
        verified_by VARCHAR NOT NULL,
        verified_at DATETIME NOT NULL,
        completed_by VARCHAR,
        completed_at DATETIME,
        PRIMARY KEY (video_id),
        FOREIGN KEY(video_id) REFERENCES videos (video_id) ON DELETE CASCADE,
        FOREIGN KEY(import_id) REFERENCES distance_imports (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_frames (
        id VARCHAR NOT NULL,
        reference_video_id VARCHAR NOT NULL,
        offset_ms INTEGER NOT NULL,
        playback_offset_ms FLOAT,
        distance_m FLOAT NOT NULL,
        mask JSON,
        import_id VARCHAR,
        PRIMARY KEY (id),
        CONSTRAINT uq_reference_frame_offset UNIQUE (reference_video_id, offset_ms),
        CONSTRAINT ck_reference_frame_offset CHECK (offset_ms >= 0),
        CONSTRAINT ck_reference_frame_playback CHECK (playback_offset_ms >= 0),
        CONSTRAINT ck_reference_frame_distance CHECK (distance_m > 0),
        FOREIGN KEY(reference_video_id) REFERENCES reference_videos (id) ON DELETE CASCADE,
        FOREIGN KEY(import_id) REFERENCES distance_imports (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS distance_individual_tags (
        individual_id VARCHAR NOT NULL,
        tag_id VARCHAR NOT NULL,
        tagged_by VARCHAR,
        tagged_at DATETIME NOT NULL,
        PRIMARY KEY (individual_id, tag_id),
        FOREIGN KEY(individual_id) REFERENCES distance_individuals (id) ON DELETE CASCADE,
        FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS distance_review_progress (
        video_id VARCHAR NOT NULL,
        annotator_name VARCHAR NOT NULL,
        active_individual_id VARCHAR NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (video_id, annotator_name),
        FOREIGN KEY(video_id) REFERENCES videos (video_id) ON DELETE CASCADE,
        FOREIGN KEY(active_individual_id) REFERENCES distance_individuals (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS distance_samples (
        id VARCHAR NOT NULL,
        individual_id VARCHAR NOT NULL,
        offset_ms INTEGER NOT NULL,
        ai_original JSON,
        status VARCHAR NOT NULL,
        skip_reason VARCHAR,
        reference_frame_id VARCHAR,
        distance_m FLOAT,
        carried_by_individual_id VARCHAR,
        labeled_by VARCHAR,
        labeled_at DATETIME,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_distance_sample_offset UNIQUE (individual_id, offset_ms),
        CONSTRAINT ck_distance_sample_offset CHECK (offset_ms >= 0),
        CONSTRAINT ck_distance_sample_state CHECK ((status = 'pending' AND reference_frame_id IS NULL AND distance_m IS NULL AND skip_reason IS NULL AND labeled_at IS NULL AND labeled_by IS NULL AND carried_by_individual_id IS NULL) OR (status = 'annotated' AND reference_frame_id IS NOT NULL AND distance_m IS NOT NULL AND distance_m > 0 AND skip_reason IS NULL) OR (status = 'skipped' AND skip_reason IS NOT NULL AND skip_reason IN ('individual absent', 'midpoint not visible') AND reference_frame_id IS NULL AND distance_m IS NULL)),
        CONSTRAINT ck_distance_carrier CHECK (carried_by_individual_id IS NULL OR carried_by_individual_id != individual_id),
        FOREIGN KEY(individual_id) REFERENCES distance_individuals (id) ON DELETE CASCADE,
        FOREIGN KEY(reference_frame_id) REFERENCES reference_frames (id),
        FOREIGN KEY(carried_by_individual_id) REFERENCES distance_individuals (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS distance_sample_behaviors (
        sample_id VARCHAR NOT NULL,
        behavior_id VARCHAR NOT NULL,
        PRIMARY KEY (sample_id, behavior_id),
        FOREIGN KEY(sample_id) REFERENCES distance_samples (id) ON DELETE CASCADE,
        FOREIGN KEY(behavior_id) REFERENCES behaviors (id)
    )
    """,
]


def _migration_v24(conn) -> None:
    """Discard unreleased Distance evidence, preserving references and ordinary Review."""
    for table in (
        "distance_sample_behaviors",
        "distance_individual_tags",
        "distance_samples",
        "distance_review_progress",
        "distance_individuals",
        "distance_video_reviews",
    ):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
    # Frozen task schema; create_all may already have created this empty table.
    conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS distance_review_tasks (
            id VARCHAR PRIMARY KEY NOT NULL,
            video_id VARCHAR NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
            species_id VARCHAR NOT NULL REFERENCES species(id),
            source_count INTEGER NOT NULL,
            completion_outcome VARCHAR,
            completion_history JSON NOT NULL,
            rejected_ai JSON NOT NULL,
            recorded_start DATETIME,
            alignment_mode VARCHAR DEFAULT 'exact' NOT NULL,
            imported_sample_times_s JSON,
            import_id VARCHAR REFERENCES distance_imports(id) ON DELETE SET NULL,
            started_at DATETIME,
            clock_anchor_offset_ms INTEGER,
            clock_anchor_playback_ms FLOAT,
            opening_even_second BOOLEAN DEFAULT '0' NOT NULL,
            verified_by VARCHAR,
            verified_at DATETIME,
            completed_by VARCHAR,
            completed_at DATETIME,
            CONSTRAINT uq_distance_task UNIQUE (video_id, species_id)
        )
    """)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_distance_review_tasks_video_id ON distance_review_tasks(video_id)"
    )
    # Reuse only frozen v23 DDL, never evolving ORM metadata.
    for ddl in DISTANCE_SCHEMA_V23:
        if "CREATE TABLE IF NOT EXISTS distance_individuals (" in ddl:
            ddl = ddl.replace(
                "video_id VARCHAR NOT NULL,",
                "video_id VARCHAR NOT NULL, task_id VARCHAR NOT NULL "
                "REFERENCES distance_review_tasks(id) ON DELETE CASCADE,",
            ).replace(
                "UNIQUE (video_id, individual_number)", "UNIQUE (task_id, individual_number)"
            )
        elif "CREATE TABLE IF NOT EXISTS distance_review_progress (" in ddl:
            ddl = ddl.replace(
                "video_id VARCHAR NOT NULL,",
                "task_id VARCHAR NOT NULL REFERENCES distance_review_tasks(id) ON DELETE CASCADE,",
            ).replace(
                "PRIMARY KEY (video_id, annotator_name)", "PRIMARY KEY (task_id, annotator_name)"
            )
            ddl = ddl.replace(
                "FOREIGN KEY(video_id) REFERENCES videos (video_id) ON DELETE CASCADE,",
                "",
            )
        elif not any(
            f"CREATE TABLE IF NOT EXISTS {table} (" in ddl
            for table in (
                "distance_samples",
                "distance_individual_tags",
                "distance_sample_behaviors",
            )
        ):
            continue
        conn.exec_driver_sql(ddl)
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_distance_individuals_task_id ON distance_individuals(task_id)"
    )


def check_schema_version(conn) -> None:
    """Reject newer databases without writing anything, including startup DDL."""
    if not conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_schema_version'"
    ).first():
        return
    row = conn.exec_driver_sql("SELECT version FROM _schema_version").first()
    if row and row[0] > len(MIGRATIONS):
        raise ValueError(
            f"Database schema v{row[0]} is newer than this application's v{len(MIGRATIONS)}. "
            "Open it with a compatible application version."
        )


def _apply_migrations(conn) -> bool:
    check_schema_version(conn)
    conn.execute(text("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER)"))
    row = conn.execute(text("SELECT version FROM _schema_version")).fetchone()
    if row is None:
        logger.info("Fresh database — stamping schema version %d", len(MIGRATIONS))
        conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
        return False
    current = row[0]
    pending = [v for v, _ in MIGRATIONS if v > current]
    if pending:
        logger.info("Running %d migration(s): v%d → v%d", len(pending), current, len(MIGRATIONS))
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
    return bool(pending)


def run_migrations(engine) -> None:
    with engine.connect() as conn:
        # Rebuilding a referenced SQLite table with foreign keys enabled would
        # cascade-delete its children. Disable enforcement before the transaction,
        # check the resulting relationships before commit, then restore it.
        foreign_keys = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            with conn.begin():
                # sqlite3's legacy transaction mode does not begin on DDL.
                # Explicitly begin so table rebuilds roll back as a whole.
                conn.exec_driver_sql("BEGIN")
                if _apply_migrations(conn) and foreign_keys:
                    violations = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise ValueError("Database migration would leave invalid foreign keys")
        finally:
            conn.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
            conn.commit()
