"""
Tests for the migration runner in review_app/backend/migrations.py.

Strategy: migrations only execute on *existing* databases (fresh DBs are
stamped at the current version without running any SQL).  To exercise the
runner we create a real engine, build the current schema with create_all(),
then wind the version stamp back to simulate an older database.
"""

from review_app.backend.db.migrations import (
    MIGRATIONS,
    _add_column_if_missing,
    _migration_v4,
    run_migrations,
)
from review_app.backend.db.models import Base
from sqlalchemy import create_engine, event, text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(path):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return engine


def _get_version(engine) -> int | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version FROM _schema_version")).fetchone()
        return row[0] if row else None


def _set_version(engine, v: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM _schema_version"))
        conn.execute(text("INSERT INTO _schema_version VALUES (:v)"), {"v": v})


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }


# ---------------------------------------------------------------------------
# Fresh database
# ---------------------------------------------------------------------------


def test_fresh_db_is_stamped_at_latest_version(tmp_path):
    engine = _make_engine(tmp_path / "test.db")
    run_migrations(engine)
    assert _get_version(engine) == len(MIGRATIONS)


def test_fresh_db_runs_no_migrations(tmp_path):
    """
    On a new database create_all() already builds the current schema.
    run_migrations() must only stamp the version, not execute any SQL migration.
    The v1 migration (ADD COLUMN review_later) would raise if actually run on
    the current schema because the column already exists.
    """
    engine = _make_engine(tmp_path / "test.db")
    # If any migration were executed on a fresh schema, the v1 ADD COLUMN
    # would raise "duplicate column name: review_later".  No exception = pass.
    run_migrations(engine)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_run_migrations_twice_is_safe(tmp_path):
    engine = _make_engine(tmp_path / "test.db")
    run_migrations(engine)
    run_migrations(engine)  # second call must not raise
    assert _get_version(engine) == len(MIGRATIONS)


def test_init_from_full_provider_twice_is_safe(tmp_db):
    """End-to-end: constructing LocalDataProvider twice on same DB doesn't crash."""
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    LocalDataProvider()
    dp2 = LocalDataProvider()
    assert dp2.has_videos_in_db(active_project_id=None) is False


# ---------------------------------------------------------------------------
# Pending migrations are applied
# ---------------------------------------------------------------------------


def test_migration_v3_creates_app_settings(tmp_path):
    """
    Simulate a DB at version 2 (no app_settings table).
    After running migrations it must exist at the latest version.
    """
    engine = _make_engine(tmp_path / "test.db")
    run_migrations(engine)  # stamp at latest initially

    # Drop app_settings and wind back to v2 to force v3 to re-run.
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS app_settings"))
    _set_version(engine, 2)

    run_migrations(engine)

    assert _get_version(engine) == len(MIGRATIONS)
    assert "app_settings" in _tables(engine)


def test_all_pending_migrations_advance_version(tmp_path):
    """
    Starting from version 2, the runner must advance to the latest version.
    """
    engine = _make_engine(tmp_path / "test.db")
    run_migrations(engine)
    _set_version(engine, 2)

    run_migrations(engine)

    assert _get_version(engine) == len(MIGRATIONS)


def test_already_at_latest_version_stays_unchanged(tmp_path):
    engine = _make_engine(tmp_path / "test.db")
    run_migrations(engine)
    version_before = _get_version(engine)

    run_migrations(engine)

    assert _get_version(engine) == version_before


# ---------------------------------------------------------------------------
# _migration_v4 against the real pre-v4 schema
# ---------------------------------------------------------------------------


def _make_pre_v4_engine(path):
    """Build the actual old schema that v4 users had: species without id,
    old species_behavior junction table, foreign_keys=ON."""
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with engine.begin() as conn:
        conn.execute(
            text("""
            CREATE TABLE species (
                scientific_name TEXT PRIMARY KEY,
                name_en TEXT, name_fr TEXT, group_en TEXT, group_fr TEXT, iucn TEXT
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE behaviors (
                id TEXT PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                name_en TEXT, name_fr TEXT,
                is_custom BOOLEAN NOT NULL DEFAULT 0
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE species_behavior (
                scientific_name TEXT NOT NULL,
                behavior TEXT NOT NULL,
                PRIMARY KEY (scientific_name, behavior)
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE species_behaviors (
                species_id TEXT NOT NULL REFERENCES species(id),
                behavior_id TEXT NOT NULL REFERENCES behaviors(id),
                PRIMARY KEY (species_id, behavior_id)
            )
        """)
        )
        conn.execute(
            text("""
            CREATE TABLE individual_observations (
                id TEXT PRIMARY KEY,
                video_id TEXT, project_id TEXT,
                species TEXT, behavior TEXT,
                labeled_by TEXT, start_sec REAL, end_sec REAL
            )
        """)
        )
        conn.execute(
            text("INSERT INTO species VALUES ('deer', 'Red Deer', NULL, NULL, NULL, NULL)")
        )
        conn.execute(text("INSERT INTO species VALUES ('fox', 'Red Fox', NULL, NULL, NULL, NULL)"))
        conn.execute(text("INSERT INTO species_behavior VALUES ('deer', 'grazing')"))
        conn.execute(text("INSERT INTO species_behavior VALUES ('fox', 'running')"))
    return engine


def test_migration_v4_on_real_old_schema(tmp_path):
    """v4 must succeed against the actual pre-v4 schema with FK enforcement on.
    This is the scenario that caused the FK mismatch bug."""
    engine = _make_pre_v4_engine(tmp_path / "old.db")

    with engine.begin() as conn:
        _migration_v4(conn)

    with engine.connect() as conn:
        names = {
            r[0] for r in conn.execute(text("SELECT scientific_name FROM species")).fetchall()
        }
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(species)")).fetchall()}

    assert {"deer", "fox"} == names
    assert "id" in cols
    assert "species_behavior" not in _tables(engine)


def test_migration_v4_backfills_observation_fks(tmp_path):
    """Existing observations with old string species/behavior columns get their FK ids filled."""
    engine = _make_pre_v4_engine(tmp_path / "old.db")

    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO behaviors (id, key, name_en) VALUES ('beh1', 'grazing', 'Grazing')
        """)
        )
        conn.execute(
            text("""
            INSERT INTO individual_observations (id, species, behavior)
            VALUES ('obs1', 'deer', 'grazing')
        """)
        )

    with engine.begin() as conn:
        _migration_v4(conn)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT species_id, behavior_id FROM individual_observations WHERE id='obs1'")
        ).fetchone()
        species_id = conn.execute(
            text("SELECT id FROM species WHERE scientific_name='deer'")
        ).scalar()

    assert row[0] == species_id
    assert row[1] == "beh1"


# ---------------------------------------------------------------------------
# _migration_v4 idempotency
# ---------------------------------------------------------------------------


def test_migration_v4_is_idempotent(tmp_path):
    """Calling _migration_v4 twice on the current schema must not raise."""
    engine = _make_engine(tmp_path / "test.db")
    with engine.begin() as conn:
        _migration_v4(conn)
        _migration_v4(conn)


def test_migration_v4_preserves_existing_species(tmp_path):
    """Species rows already in the DB must survive a v4 re-run."""
    from review_app.backend.db.models import Species

    engine = _make_engine(tmp_path / "test.db")

    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine)
    with Session() as s:
        s.add(Species(id="abc123", scientific_name="deer", name_en="Red Deer"))
        s.commit()

    with engine.begin() as conn:
        _migration_v4(conn)

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT scientific_name FROM species")).fetchall()
    names = {r[0] for r in rows}
    assert "deer" in names


# ---------------------------------------------------------------------------
# _add_column_if_missing helper and v19 (inaturalist_url)
# ---------------------------------------------------------------------------


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def test_add_column_if_missing_adds_and_skips(tmp_path):
    engine = _make_engine(tmp_path / "test.db")
    with engine.begin() as conn:
        _add_column_if_missing(conn, "videos", "extra_col", "TEXT")
        # Second call with the column present must be a no-op, not raise.
        _add_column_if_missing(conn, "videos", "extra_col", "TEXT")
    assert "extra_col" in _columns(engine, "videos")


def test_add_column_if_missing_with_default(tmp_path):
    engine = _make_engine(tmp_path / "test.db")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id TEXT PRIMARY KEY)"))
        conn.execute(text("INSERT INTO t VALUES ('x')"))
        _add_column_if_missing(conn, "t", "flag", "INTEGER NOT NULL DEFAULT 0")
        val = conn.execute(text("SELECT flag FROM t")).scalar()
    assert val == 0


def test_migration_v19_adds_inaturalist_url(tmp_path):
    """Simulate a v18 database (species without inaturalist_url) and re-run v19."""
    engine = _make_engine(tmp_path / "test.db")
    run_migrations(engine)

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE species DROP COLUMN inaturalist_url"))
    _set_version(engine, 18)

    run_migrations(engine)

    assert _get_version(engine) == len(MIGRATIONS)
    assert "inaturalist_url" in _columns(engine, "species")
