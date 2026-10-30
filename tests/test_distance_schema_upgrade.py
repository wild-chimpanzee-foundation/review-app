"""Upgrade the published v22 schema without altering ordinary review evidence."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from review_app.backend.db import migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine, event


@pytest.fixture
def legacy_database(tmp_path):
    path = tmp_path / "ordinary-v22.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            Path(__file__).with_name("fixtures").joinpath("schema_snapshot.sql").read_text()
        )
        conn.executescript("""
            INSERT INTO projects (id, name, created_at) VALUES ('project', 'Keep', '2024-01-01');
            INSERT INTO videos (video_id, project_id, video_path, last_seen_at)
                VALUES ('video', 'project', 'animal.mp4', '2024-01-01');
            INSERT INTO species (id, scientific_name, is_custom) VALUES ('species', 'deer', 0);
            INSERT INTO individual_observations
                (video_id, id, observation_uuid, project_id, species_id, count,
                 start_sec, labeled_by, updated_at)
                VALUES ('video', 1, 'durable-identity', 'project', 'species', 3, 0,
                        'alice', '2024-01-01');
            INSERT INTO tags (id, key, name_en, is_custom) VALUES ('tag', 'injured', 'Injured', 0);
            INSERT INTO video_tags VALUES ('video', 'tag', 'alice', '2024-01-01');
        """)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("precreate_tables", [False, True])
def test_upgrade_preserves_ordinary_records_and_adds_final_schema(
    legacy_database, precreate_tables
):
    engine = legacy_database
    with engine.connect() as conn:
        before = {
            table: conn.exec_driver_sql(f'SELECT * FROM "{table}"').all()
            for table in ("projects", "videos", "individual_observations", "video_tags")
        }
    if precreate_tables:
        Base.metadata.create_all(engine)
    migrations.run_migrations(engine)
    migrations.run_migrations(engine)
    with engine.connect() as conn:
        for table, rows in before.items():
            assert conn.exec_driver_sql(f'SELECT * FROM "{table}"').all() == rows
        assert conn.exec_driver_sql("SELECT individual_eligible FROM tags").scalar() == 1
        assert conn.exec_driver_sql("SELECT version FROM _schema_version").scalar() == len(
            migrations.MIGRATIONS
        )
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_failed_v23_upgrade_rolls_back_ddl_data_and_version(legacy_database, monkeypatch):
    engine = legacy_database
    with engine.connect() as conn:
        before = list(conn.connection.driver_connection.iterdump())

    def fail_after_upgrade(conn):
        migrations._migration_v23(conn)
        raise RuntimeError("abort upgrade")

    with monkeypatch.context() as patch:
        patch.setattr(
            migrations,
            "MIGRATIONS",
            [(v, fail_after_upgrade if v == 23 else m) for v, m in migrations.MIGRATIONS],
        )
        with pytest.raises(RuntimeError, match="abort upgrade"):
            migrations.run_migrations(engine)
    with engine.connect() as conn:
        assert list(conn.connection.driver_connection.iterdump()) == before
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    migrations.run_migrations(engine)


def test_task_transition_discards_only_legacy_distance_evidence(legacy_database, monkeypatch):
    engine = legacy_database
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:23])
        migrations.run_migrations(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO reference_videos VALUES ('ref', 'project', 'camera', 'ref.mp4')
        """)
        conn.exec_driver_sql("""
            INSERT INTO reference_frames (id, reference_video_id, offset_ms, playback_offset_ms, distance_m)
            VALUES ('frame', 'ref', 1000, 1000.5, 2.5)
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_individuals
                (id, video_id, individual_number, species_id, first_seen_offset_ms, updated_at)
            VALUES ('legacy', 'video', 1, 'species', 0, '2026-01-01')
        """)
        before = {
            table: conn.exec_driver_sql(f'SELECT * FROM "{table}"').all()
            for table in (
                "reference_videos",
                "reference_frames",
                "individual_observations",
                "video_tags",
            )
        }
    Base.metadata.create_all(engine)
    migrations.run_migrations(engine)
    with engine.connect() as conn:
        for table, rows in before.items():
            assert conn.exec_driver_sql(f'SELECT * FROM "{table}"').all() == rows
        assert conn.exec_driver_sql("SELECT count(*) FROM distance_individuals").scalar() == 0
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_v22_upgrade_creates_final_distance_schema_directly(legacy_database):
    engine = legacy_database
    migrations.run_migrations(engine)
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT version FROM _schema_version").scalar() == len(
            migrations.MIGRATIONS
        )
        tables = {
            row[0]
            for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "distance_imports" in tables
        assert not any(name.startswith("distance_ai_") for name in tables)
        conn.exec_driver_sql("""
            INSERT INTO distance_review_tasks
                (id, video_id, species_id, source_count, completion_history, rejected_ai, alignment_mode, imported_sample_times_s, verified_by, verified_at)
            VALUES ('task', 'video', 'species', 3, '[]', '[]', 'imported', '[1.250000001]', 'AI', '2026-01-01')
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_individuals
                (id, task_id, video_id, individual_number, species_id, age, sex,
                 first_seen_offset_ms, first_seen_playback_ms, updated_at)
            VALUES ('animal', 'task', 'video', 1, 'species', 'Unknown', 'Unknown',
                    1250, 1250.000001, '2026-01-01')
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_individuals
                (id, task_id, video_id, individual_number, species_id, age,
                 first_seen_offset_ms, initial_carrier_individual_id, updated_at)
            VALUES ('infant', 'task', 'video', 2, 'species', 'Infant',
                    1250, 'animal', '2026-01-01')
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_samples
                (id, individual_id, offset_ms, status, ai_original, updated_at)
            VALUES ('proposal', 'animal', 1250, 'pending', '{"time_s": 1.250000001}', '2026-01-01')
        """)
        assert conn.exec_driver_sql(
            "SELECT recorded_start, started_at FROM distance_review_tasks"
        ).one() == (None, None)
        assert conn.exec_driver_sql(
            "SELECT reference_frame_id, distance_m, labeled_by, labeled_at FROM distance_samples"
        ).one() == (None, None, None, None)
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
