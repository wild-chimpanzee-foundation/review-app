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


@pytest.mark.parametrize("fail_first", [False, True])
def test_v26_preserves_distance_evidence_and_allows_unknown_age(
    legacy_database, monkeypatch, fail_first
):
    engine = legacy_database
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:25])
        migrations.run_migrations(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            INSERT INTO distance_individuals
                (id, video_id, individual_number, species_id, age, sex,
                 first_seen_offset_ms, updated_at, initial_carrier_individual_id)
            VALUES ('adult', 'video', 1, 'species', 'Adult', 'Female', 0, '2026-01-01', NULL),
                   ('infant', 'video', 2, 'species', 'Infant', 'Unknown', 0, '2026-01-01', 'adult'),
                   ('unset', 'video', 3, 'species', NULL, NULL, 0, '2026-01-01', NULL)
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_samples
                (id, individual_id, offset_ms, status, skip_reason, updated_at)
            VALUES ('sample', 'adult', 0, 'skipped', 'individual absent', '2026-01-01')
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_review_progress
                (video_id, annotator_name, active_individual_id, updated_at)
            VALUES ('video', 'alice', 'infant', '2026-01-01')
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_video_reviews
                (video_id, recorded_start, verified_by, verified_at, completed_by, completed_at)
            VALUES ('video', '2026-01-01', 'alice', '2026-01-01', 'alice', '2026-01-01')
        """)
        tables = ("distance_individuals", "distance_samples", "distance_review_progress")
        before = {table: conn.exec_driver_sql(f"SELECT * FROM {table}").all() for table in tables}
    if fail_first:
        with engine.connect() as conn:
            dump = list(conn.connection.driver_connection.iterdump())

        def fail_after_rebuild(conn):
            migrations._migration_v26(conn)
            raise RuntimeError("abort age migration")

        with monkeypatch.context() as patch:
            patch.setattr(
                migrations,
                "MIGRATIONS",
                [(v, fail_after_rebuild if v == 26 else m) for v, m in migrations.MIGRATIONS],
            )
            with pytest.raises(RuntimeError, match="abort age migration"):
                migrations.run_migrations(engine)
        with engine.connect() as conn:
            assert list(conn.connection.driver_connection.iterdump()) == dump
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    migrations.run_migrations(engine)
    migrations.run_migrations(engine)
    with engine.begin() as conn:
        for table in tables:
            assert conn.exec_driver_sql(f"SELECT * FROM {table}").all() == before[table]
        assert conn.exec_driver_sql(
            "SELECT completed_by, completed_at FROM distance_video_reviews"
        ).one() == (None, None)
        conn.exec_driver_sql("UPDATE distance_individuals SET age='Unknown' WHERE id='unset'")
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
