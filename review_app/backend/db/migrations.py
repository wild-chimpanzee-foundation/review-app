from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from review_app.backend.db.models import Base

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


# Frozen DDL for tables introduced by v23. Future schema changes append a migration.
SCHEMA_V23 = (
    """
    CREATE TABLE IF NOT EXISTS deployments (
        id VARCHAR NOT NULL,
        project_id VARCHAR NOT NULL,
        deployment_id VARCHAR NOT NULL,
        name VARCHAR,
        camera_id VARCHAR,
        installation_date DATE,
        retrieval_date DATE,
        transect_id VARCHAR,
        habitat VARCHAR,
        notes VARCHAR,
        PRIMARY KEY (id),
        CONSTRAINT uq_deployment_project_key UNIQUE (project_id, deployment_id),
        CONSTRAINT ck_deployment_key CHECK (length(trim(deployment_id)) > 0),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_deployments_project_id ON deployments (project_id)",
    """
    CREATE TABLE IF NOT EXISTS handoff_assignments (
        id VARCHAR NOT NULL,
        assignment_id VARCHAR NOT NULL,
        project_id VARCHAR NOT NULL,
        role VARCHAR NOT NULL,
        source_project_id VARCHAR NOT NULL,
        recipient VARCHAR NOT NULL,
        stage VARCHAR NOT NULL,
        target_species VARCHAR,
        created_at DATETIME NOT NULL,
        scope JSON NOT NULL,
        removed_at DATETIME,
        setup_archive BLOB NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_handoff_project_assignment UNIQUE (project_id, assignment_id),
        CONSTRAINT ck_handoff_role CHECK (role IN ('origin', 'working')),
        CONSTRAINT ck_handoff_recipient CHECK (length(trim(recipient)) > 0),
        CONSTRAINT ck_handoff_stage CHECK ((stage = 'species' AND target_species IS NULL)
            OR (stage = 'distance' AND target_species IS NOT NULL
            AND length(trim(target_species)) > 0)),
        CONSTRAINT ck_handoff_origin CHECK (role != 'origin' OR source_project_id = project_id),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_handoff_working_project ON handoff_assignments (project_id) WHERE role = 'working'",
    "CREATE INDEX IF NOT EXISTS ix_handoff_assignments_project_id ON handoff_assignments (project_id)",
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
    "CREATE INDEX IF NOT EXISTS ix_distance_imports_project_id ON distance_imports (project_id)",
    """
    CREATE TABLE IF NOT EXISTS deployment_folder_links (
        id VARCHAR NOT NULL,
        project_dir_id VARCHAR NOT NULL,
        folder_path VARCHAR NOT NULL,
        deployment_id VARCHAR,
        source VARCHAR DEFAULT 'manual' NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_deployment_folder UNIQUE (project_dir_id, folder_path),
        FOREIGN KEY(project_dir_id) REFERENCES project_dirs (id) ON DELETE CASCADE,
        FOREIGN KEY(deployment_id) REFERENCES deployments (id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_deployment_folder_links_project_dir_id ON deployment_folder_links (project_dir_id)",
    """
    CREATE TABLE IF NOT EXISTS deployment_folder_reviews (
        project_dir_id VARCHAR NOT NULL,
        folder_path VARCHAR NOT NULL,
        note VARCHAR,
        available_candidates JSON NOT NULL,
        PRIMARY KEY (project_dir_id, folder_path),
        FOREIGN KEY(project_dir_id) REFERENCES project_dirs (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS handoff_identity_maps (
        handoff_id VARCHAR NOT NULL,
        kind VARCHAR NOT NULL,
        portable_id VARCHAR NOT NULL,
        local_id VARCHAR NOT NULL,
        PRIMARY KEY (handoff_id, kind, portable_id),
        CONSTRAINT uq_handoff_local_identity UNIQUE (handoff_id, kind, local_id),
        CONSTRAINT ck_handoff_identity CHECK (length(trim(kind)) > 0
            AND length(trim(portable_id)) > 0 AND length(trim(local_id)) > 0),
        FOREIGN KEY(handoff_id) REFERENCES handoff_assignments (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_videos (
        id VARCHAR NOT NULL,
        project_id VARCHAR NOT NULL,
        camera_id VARCHAR NOT NULL,
        deployment_id VARCHAR,
        video_path VARCHAR NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_reference_video_path_project UNIQUE (project_id, video_path),
        CONSTRAINT ck_reference_camera CHECK (length(trim(camera_id)) > 0),
        CONSTRAINT ck_reference_path CHECK (length(trim(video_path)) > 0),
        FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE,
        FOREIGN KEY(deployment_id) REFERENCES deployments (id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_reference_videos_deployment_id ON reference_videos (deployment_id)",
    "CREATE INDEX IF NOT EXISTS idx_reference_deployment ON reference_videos (project_id, camera_id)",
    """
    CREATE TABLE IF NOT EXISTS reference_frames (
        id VARCHAR NOT NULL,
        reference_video_id VARCHAR NOT NULL,
        offset_ms INTEGER NOT NULL,
        playback_offset_ms FLOAT,
        distance_m FLOAT NOT NULL,
        mask JSON,
        import_id VARCHAR,
        retired BOOLEAN DEFAULT '0' NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_reference_frame_position UNIQUE (reference_video_id, distance_m, offset_ms),
        CONSTRAINT ck_reference_frame_offset CHECK (offset_ms >= 0),
        CONSTRAINT ck_reference_frame_playback CHECK (playback_offset_ms >= 0),
        CONSTRAINT ck_reference_frame_distance CHECK (distance_m > 0),
        FOREIGN KEY(reference_video_id) REFERENCES reference_videos (id) ON DELETE CASCADE,
        FOREIGN KEY(import_id) REFERENCES distance_imports (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS distance_review_tasks (
        id VARCHAR NOT NULL,
        video_id VARCHAR NOT NULL,
        species_id VARCHAR NOT NULL,
        source_count INTEGER NOT NULL,
        completion_outcome VARCHAR,
        completion_history JSON NOT NULL,
        rejected_ai JSON NOT NULL,
        recorded_start DATETIME,
        alignment_mode VARCHAR DEFAULT 'exact' NOT NULL,
        imported_sample_times_s JSON,
        import_id VARCHAR,
        started_at DATETIME,
        clock_anchor_offset_ms INTEGER,
        clock_anchor_playback_ms FLOAT,
        opening_even_second BOOLEAN DEFAULT '0' NOT NULL,
        verified_by VARCHAR,
        verified_at DATETIME,
        completed_by VARCHAR,
        completed_at DATETIME,
        PRIMARY KEY (id),
        CONSTRAINT uq_distance_task UNIQUE (video_id, species_id),
        FOREIGN KEY(video_id) REFERENCES videos (video_id) ON DELETE CASCADE,
        FOREIGN KEY(species_id) REFERENCES species (id),
        FOREIGN KEY(import_id) REFERENCES distance_imports (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_distance_review_tasks_video_id ON distance_review_tasks (video_id)",
    """
    CREATE TABLE IF NOT EXISTS distance_individuals (
        id VARCHAR NOT NULL,
        video_id VARCHAR NOT NULL,
        import_id VARCHAR,
        task_id VARCHAR NOT NULL,
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
        CONSTRAINT uq_distance_individual_number UNIQUE (task_id, individual_number),
        CONSTRAINT ck_distance_individual_number CHECK (individual_number > 0),
        CONSTRAINT ck_distance_first_seen CHECK (first_seen_offset_ms >= 0),
        CONSTRAINT ck_distance_age CHECK (age IS NULL OR age IN ('Adult', 'Adolescent', 'Juvenile', 'Infant', 'Unknown')),
        CONSTRAINT ck_distance_sex CHECK (sex IS NULL OR sex IN ('Male', 'Female', 'Unknown')),
        CONSTRAINT ck_distance_anchor CHECK ((anchor_x IS NULL AND anchor_y IS NULL
            AND anchor_width IS NULL AND anchor_height IS NULL) OR (anchor_x IS NOT NULL
            AND anchor_y IS NOT NULL AND anchor_x BETWEEN 0 AND 1 AND anchor_y BETWEEN 0 AND 1
            AND anchor_width IS NOT NULL AND anchor_height IS NOT NULL AND anchor_width > 0
            AND anchor_height > 0 AND anchor_x + anchor_width <= 1
            AND anchor_y + anchor_height <= 1)),
        FOREIGN KEY(video_id) REFERENCES videos (video_id) ON DELETE CASCADE,
        FOREIGN KEY(import_id) REFERENCES distance_imports (id) ON DELETE SET NULL,
        FOREIGN KEY(task_id) REFERENCES distance_review_tasks (id) ON DELETE CASCADE,
        FOREIGN KEY(species_id) REFERENCES species (id),
        FOREIGN KEY(initial_carrier_individual_id) REFERENCES distance_individuals (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_distance_individuals_task_id ON distance_individuals (task_id)",
    """
    CREATE TABLE IF NOT EXISTS distance_review_progress (
        task_id VARCHAR NOT NULL,
        annotator_name VARCHAR NOT NULL,
        active_individual_id VARCHAR NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (task_id, annotator_name),
        FOREIGN KEY(task_id) REFERENCES distance_review_tasks (id) ON DELETE CASCADE,
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
        CONSTRAINT ck_distance_sample_state CHECK ((status = 'pending'
            AND reference_frame_id IS NULL AND distance_m IS NULL AND skip_reason IS NULL
            AND labeled_at IS NULL AND labeled_by IS NULL AND carried_by_individual_id IS NULL)
            OR (status = 'annotated' AND skip_reason IS NULL AND distance_m IS NOT NULL
            AND ((distance_m = 0 AND reference_frame_id IS NULL) OR (distance_m > 0
            AND reference_frame_id IS NOT NULL))) OR (status = 'skipped'
            AND skip_reason IS NOT NULL
            AND skip_reason IN ('individual absent', 'midpoint not visible')
            AND reference_frame_id IS NULL AND distance_m IS NULL)),
        CONSTRAINT ck_distance_carrier CHECK (carried_by_individual_id IS NULL OR carried_by_individual_id != individual_id),
        FOREIGN KEY(individual_id) REFERENCES distance_individuals (id) ON DELETE CASCADE,
        FOREIGN KEY(reference_frame_id) REFERENCES reference_frames (id),
        FOREIGN KEY(carried_by_individual_id) REFERENCES distance_individuals (id)
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
    CREATE TABLE IF NOT EXISTS distance_sample_behaviors (
        sample_id VARCHAR NOT NULL,
        behavior_id VARCHAR NOT NULL,
        PRIMARY KEY (sample_id, behavior_id),
        FOREIGN KEY(sample_id) REFERENCES distance_samples (id) ON DELETE CASCADE,
        FOREIGN KEY(behavior_id) REFERENCES behaviors (id)
    )
    """,
)


def _repair_species_observations(conn: Connection) -> None:
    """Reconcile Species records with their videos and enforce behavior links."""
    ownership = conn.exec_driver_sql("""
        UPDATE individual_observations AS io
        SET project_id = (SELECT v.project_id FROM videos AS v WHERE v.video_id = io.video_id)
        WHERE EXISTS (
            SELECT 1 FROM videos AS v
            WHERE v.video_id = io.video_id AND v.project_id IS NOT io.project_id
        )
    """).rowcount
    labels = conn.exec_driver_sql("""
        UPDATE video_labels AS vl SET is_blank = 0
        WHERE vl.is_blank = 1 AND EXISTS (
            SELECT 1 FROM individual_observations AS io WHERE io.video_id = vl.video_id
        )
    """).rowcount
    orphans = conn.exec_driver_sql("""
        DELETE FROM observation_tags AS ot
        WHERE NOT EXISTS (
            SELECT 1 FROM individual_observations AS io
            WHERE io.video_id = ot.video_id AND io.id = ot.observation_id
        )
    """).rowcount
    conn.exec_driver_sql("""
        CREATE TABLE observation_tags_new (
            video_id VARCHAR NOT NULL,
            observation_id INTEGER NOT NULL,
            behavior_id VARCHAR NOT NULL REFERENCES behaviors(id),
            PRIMARY KEY (video_id, observation_id, behavior_id),
            FOREIGN KEY (video_id, observation_id)
                REFERENCES individual_observations(video_id, id) ON DELETE CASCADE
        )
    """)
    conn.exec_driver_sql("""
        INSERT INTO observation_tags_new (video_id, observation_id, behavior_id)
        SELECT video_id, observation_id, behavior_id FROM observation_tags
    """)
    conn.exec_driver_sql("DROP TABLE observation_tags")
    conn.exec_driver_sql("ALTER TABLE observation_tags_new RENAME TO observation_tags")
    conn.exec_driver_sql("""
        CREATE INDEX idx_observation_tags_video ON observation_tags (video_id, behavior_id)
    """)
    if ownership or labels or orphans:
        logger.warning(
            "Species upgrade reconciled %d observation projects, cleared %d blank labels,"
            " and removed %d behavior links without an observation",
            ownership,
            labels,
            orphans,
        )


def _migration_v23(conn) -> None:
    """Add the final Distance schema to a published v22 database."""
    for sql in SCHEMA_V23:
        conn.execute(text(sql))
    _add_column_if_missing(conn, "videos", "inferred_camera_id", "VARCHAR")
    _add_column_if_missing(conn, "videos", "deployment_id", "VARCHAR REFERENCES deployments(id)")
    _add_column_if_missing(
        conn, "videos", "deployment_assignment", "VARCHAR NOT NULL DEFAULT 'legacy'"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_videos_deployment_id ON videos (deployment_id)"
    )
    conn.exec_driver_sql(
        "UPDATE videos SET inferred_camera_id = camera_id WHERE inferred_camera_id IS NULL"
    )
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
    _repair_species_observations(conn)


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
]


class SchemaVersionError(ValueError):
    pass


class UnsupportedSchemaError(ValueError):
    pass


def check_schema_version(conn: Connection, maximum: int | None = None) -> int | None:
    """Read the version stamp and reject unsupported versions without writing."""
    if not conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_schema_version'"
    ).first():
        return None
    if "version" not in _columns(conn, "_schema_version"):
        raise UnsupportedSchemaError("Invalid Review App schema version table")
    rows = conn.exec_driver_sql("SELECT version FROM _schema_version LIMIT 2").all()
    if not rows:
        return None
    if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] < 0:
        raise UnsupportedSchemaError("Invalid Review App schema version stamp")
    version = rows[0][0]
    limit = len(MIGRATIONS) if maximum is None else min(maximum, len(MIGRATIONS))
    if version > limit:
        raise SchemaVersionError(
            f"Database schema v{version} is newer than this application's v{limit}. "
            "Open it with a compatible application version."
        )
    return version


def _table_info(conn: Connection, table: str):
    # Table names here come from the app schema, never from the candidate database.
    return conn.exec_driver_sql(f'PRAGMA table_info("{table}")').all()


def _columns(conn: Connection, table: str) -> set[str]:
    return {row[1] for row in _table_info(conn, table)}


def _has_current_schema(conn: Connection) -> bool:
    tables = set(inspect(conn).get_table_names())
    for table in Base.metadata.tables.values():
        if table.name not in tables:
            return False
        fields = {row[1]: row[5] for row in _table_info(conn, table.name)}
        if not set(table.columns.keys()) <= fields.keys():
            return False
        if {name for name, pk in fields.items() if pk} != set(table.primary_key.columns.keys()):
            return False
    return True


def _existing_version(conn: Connection, stamped: int | None) -> int:
    core = {
        "projects": {"id", "name"},
        "project_dirs": {"project_id", "path"},
        "videos": {"video_id", "video_path", "project_id"},
        "video_labels": {"video_id", "is_blank"},
        "individual_observations": {"video_id", "id", "project_id", "start_sec"},
        "model_annotations": {"video_id", "id", "model_name", "annotation_type"},
        "species": {"scientific_name", "name_en"},
    }
    if not all(columns <= _columns(conn, table) for table, columns in core.items()):
        raise UnsupportedSchemaError("Database does not have a supported Review App schema")
    if stamped is not None:
        return stamped
    if _has_current_schema(conn):
        return len(MIGRATIONS)

    # The project-based schema in v1.0.0-beta6 predates surrogate species and
    # behavior IDs. Its full upgrade starts at migration 4.
    legacy_tables = {*core, "app_settings", "species_behavior"}
    tables = set(inspect(conn).get_table_names()) - {"_schema_version"}
    observations = _columns(conn, "individual_observations")
    if (
        tables == legacy_tables
        and {"species", "behavior"} <= observations
        and "id" not in _columns(conn, "species")
        and "review_later" in _columns(conn, "video_labels")
    ):
        return 3
    raise UnsupportedSchemaError("Unrecognized Review App schema without a version stamp")


def _apply_migrations(conn: Connection, *, assumed_version: int | None = None) -> bool:
    stamped = check_schema_version(conn)
    current = stamped if stamped is not None else assumed_version
    if stamped == len(MIGRATIONS):
        return False
    conn.execute(text("CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER)"))
    if current is None:
        logger.info("Fresh database — stamping schema version %d", len(MIGRATIONS))
        conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": len(MIGRATIONS)})
        return False
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


def _run_migration_transaction(
    engine: Engine, apply: Callable[[Connection], bool], *, validate_foreign_keys: bool = False
) -> bool:
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
                changed = apply(conn)
                if changed and (foreign_keys or validate_foreign_keys):
                    violations = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise ValueError("Database migration would leave invalid foreign keys")
        finally:
            conn.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")
            conn.commit()
        return changed


def run_migrations(engine: Engine) -> None:
    """Run migrations on a database whose initial schema has already been selected."""
    _run_migration_transaction(engine, _apply_migrations)


def initialize_database(engine: Engine) -> None:
    """Create and version a new database in a single transaction."""

    def initialize(conn: Connection) -> bool:
        if inspect(conn).get_table_names():
            raise UnsupportedSchemaError("Cannot initialize a database that already has tables")
        Base.metadata.create_all(conn)
        return _apply_migrations(conn)

    _run_migration_transaction(engine, initialize)


def upgrade_database(engine: Engine, *, maximum_version: int | None = None) -> bool:
    """Recognize an existing database and upgrade it without creating unrelated tables."""

    def upgrade(conn: Connection) -> bool:
        stamped = check_schema_version(conn, maximum_version)
        version = _existing_version(conn, stamped)
        project_species = []
        if version < 4:
            # These tables were previously installed by startup create_all()
            # before v4. Retain that prerequisite for old project databases.
            for name in ("behaviors", "project_species"):
                Base.metadata.tables[name].create(conn, checkfirst=True)
            if "project_id" in _columns(conn, "species"):
                project_species = conn.exec_driver_sql("""
                    SELECT project_id, scientific_name FROM species WHERE project_id IS NOT NULL
                """).all()
            if "behavior" in _columns(conn, "individual_observations"):
                conn.exec_driver_sql("""
                    INSERT OR IGNORE INTO behaviors (id, key, name_en, is_custom)
                    SELECT lower(hex(randomblob(16))), behavior, behavior, 1
                    FROM (SELECT DISTINCT behavior FROM individual_observations
                          WHERE behavior IS NOT NULL AND trim(behavior) <> '')
                """)
        _apply_migrations(conn, assumed_version=version)
        if project_species:
            conn.exec_driver_sql(
                """
                INSERT OR IGNORE INTO project_species (project_id, species_id)
                SELECT ?, id FROM species WHERE scientific_name = ?
                """,
                [tuple(row) for row in project_species],
            )
        if not _has_current_schema(conn):
            raise UnsupportedSchemaError("Review App schema is incomplete after migration")
        return stamped != len(MIGRATIONS)

    return _run_migration_transaction(engine, upgrade, validate_foreign_keys=True)
