"""M4: video/distance identity on fresh databases and lossless v25 upgrades."""

import sqlite3
from pathlib import Path

import pytest
from review_app.backend.db import migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def v25_references(tmp_path, monkeypatch):
    path = tmp_path / "v25.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            Path(__file__).with_name("fixtures").joinpath("schema_snapshot.sql").read_text()
        )
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:25])
        migrations.run_migrations(engine)
    with engine.begin() as conn:
        for sql in (
            "INSERT INTO projects (id, name, created_at) VALUES ('p', 'Project', '2026-01-01')",
            "INSERT INTO videos (video_id, project_id, video_path, last_seen_at) VALUES ('v', 'p', 'animal.mp4', '2026-01-01')",
            "INSERT INTO species (id, scientific_name, is_custom) VALUES ('s', 'deer', 0)",
            "INSERT INTO behaviors (id, key, name_en, is_custom) VALUES ('behavior', 'walking', 'Walking', 0)",
            "INSERT INTO distance_imports (id, project_id, content_hash, manifest, payload, imported_at) VALUES ('i', 'p', 'hash', '{}', '{}', '2026-01-01')",
            "INSERT INTO reference_videos VALUES ('r', 'p', 'camera', 'reference.mp4')",
            # Lexical ID, not timestamp or insertion order, determines the keeper.
            "INSERT INTO reference_frames VALUES ('z', 'r', 1000, 1000.25, 5, '{\"old\":true}', 'i')",
            "INSERT INTO reference_frames VALUES ('a', 'r', 2000, 2000.25, 5, '{\"keep\":true}', 'i')",
            "INSERT INTO reference_frames VALUES ('b', 'r', 3000, NULL, 8, NULL, NULL)",
            "INSERT INTO distance_review_tasks (id, video_id, species_id, source_count, completion_history, rejected_ai, alignment_mode) VALUES ('task', 'v', 's', 1, '[]', '[]', 'exact')",
            "INSERT INTO distance_individuals (id, task_id, video_id, species_id, individual_number, first_seen_offset_ms, updated_at) VALUES ('animal', 'task', 'v', 's', 1, 0, '2026-01-01')",
            "INSERT INTO distance_samples (id, individual_id, offset_ms, status, reference_frame_id, distance_m, labeled_by, labeled_at, updated_at) VALUES ('sample-z', 'animal', 1000, 'annotated', 'z', 5, 'alice', '2026-01-01', '2026-01-01')",
            "INSERT INTO distance_samples (id, individual_id, offset_ms, status, reference_frame_id, distance_m, labeled_by, labeled_at, updated_at) VALUES ('sample-a', 'animal', 2000, 'annotated', 'a', 5, 'alice', '2026-01-01', '2026-01-01')",
            "INSERT INTO distance_samples (id, individual_id, offset_ms, status, reference_frame_id, distance_m, updated_at) VALUES ('sample-b', 'animal', 3000, 'annotated', 'b', 8, '2026-01-01')",
            "INSERT INTO distance_sample_behaviors VALUES ('sample-z', 'behavior')",
        ):
            conn.exec_driver_sql(sql)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("upgraded", [False, True])
def test_reference_identity_is_distance_not_timestamp(v25_references, tmp_path, upgraded):
    if upgraded:
        engine = v25_references
        migrations.run_migrations(engine)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
        Base.metadata.create_all(engine)
        migrations.run_migrations(engine)
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO projects (id, name, created_at) VALUES ('p', 'Fresh', '2026-01-01')"
            )
            conn.exec_driver_sql(
                "INSERT INTO reference_videos VALUES ('r', 'p', 'camera', 'reference.mp4')"
            )
            conn.exec_driver_sql(
                "INSERT INTO reference_frames (id, reference_video_id, offset_ms, distance_m) VALUES ('a', 'r', 2000, 5)"
            )
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO reference_frames (id, reference_video_id, offset_ms, distance_m) VALUES ('same-time', 'r', 2000, 6)"
            )
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO reference_frames (id, reference_video_id, offset_ms, distance_m) VALUES ('same-distance', 'r', 4000, 5)"
            )
    finally:
        if not upgraded:
            engine.dispose()


def test_upgrade_remaps_duplicate_frames_without_altering_sample_evidence(v25_references):
    engine = v25_references
    with engine.connect() as conn:
        samples = (
            conn.exec_driver_sql("SELECT * FROM distance_samples ORDER BY id").mappings().all()
        )
        before = {
            table: conn.exec_driver_sql(f'SELECT * FROM "{table}"').all()
            for table in (
                "videos",
                "reference_videos",
                "distance_imports",
                "distance_individuals",
                "distance_review_tasks",
                "distance_sample_behaviors",
            )
        }
        keeper = conn.exec_driver_sql("SELECT * FROM reference_frames WHERE id='a'").one()
    migrations.run_migrations(engine)
    migrations.run_migrations(engine)
    with engine.connect() as conn:
        after = conn.exec_driver_sql("SELECT * FROM distance_samples ORDER BY id").mappings().all()
        assert [dict(row) for row in after] == [
            {
                **row,
                "reference_frame_id": "a"
                if row["reference_frame_id"] == "z"
                else row["reference_frame_id"],
            }
            for row in samples
        ]
        assert conn.exec_driver_sql("SELECT * FROM reference_frames WHERE id='a'").one() == keeper
        assert conn.exec_driver_sql(
            "SELECT id FROM reference_frames ORDER BY id"
        ).scalars().all() == ["a", "b"]
        for table, rows in before.items():
            assert conn.exec_driver_sql(f'SELECT * FROM "{table}"').all() == rows
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("SELECT version FROM _schema_version").scalar() == len(
            migrations.MIGRATIONS
        )


def test_failed_reference_identity_upgrade_rolls_back(v25_references, monkeypatch):
    engine = v25_references
    with engine.connect() as conn:
        before = list(conn.connection.driver_connection.iterdump())

    def fail(conn):
        migrations._migration_v26(conn)
        raise RuntimeError("abort reference upgrade")

    with monkeypatch.context() as patch:
        patch.setattr(
            migrations,
            "MIGRATIONS",
            [(v, fail if v == 26 else m) for v, m in migrations.MIGRATIONS],
        )
        with pytest.raises(RuntimeError, match="abort reference upgrade"):
            migrations.run_migrations(engine)
    with engine.connect() as conn:
        assert list(conn.connection.driver_connection.iterdump()) == before
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    migrations.run_migrations(engine)
