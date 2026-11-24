"""Upgrade supported historical schemas without altering review evidence."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from review_app.backend.db import migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def legacy_database(tmp_path):
    path = tmp_path / "ordinary-v22.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            Path(__file__).with_name("fixtures").joinpath("schema_snapshot.sql").read_text()
        )
        conn.executescript("""
            INSERT INTO projects (id, name, created_at) VALUES ('project', 'Keep', '2024-01-01');
            INSERT INTO videos (video_id, project_id, video_path, camera_id, last_seen_at)
                VALUES ('video', 'project', 'animal.mp4', 'legacy-camera', '2024-01-01');
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
            table: conn.exec_driver_sql(f'SELECT * FROM "{table}"').mappings().all()
            for table in ("projects", "videos", "individual_observations", "video_tags")
        }
    if precreate_tables:
        Base.metadata.create_all(engine)
    assert migrations.upgrade_database(engine)
    assert not migrations.upgrade_database(engine)
    with engine.connect() as conn:
        for table, rows in before.items():
            after = conn.exec_driver_sql(f'SELECT * FROM "{table}"').mappings().all()
            expected = [dict(row) for row in rows]
            for row in expected:
                if table == "videos":
                    row["folder_camera_label"] = row.pop("camera_id")
                elif table in {"individual_observations", "model_annotations"}:
                    del row["project_id"]
            assert [{key: row[key] for key in expected[0]} for row in after] == expected
        assert conn.exec_driver_sql("""
            SELECT folder_camera_label, deployment_id, deployment_assignment
            FROM videos
        """).one() == ("legacy-camera", None, "legacy")
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
                (id, task_id, individual_number, age, sex,
                 first_seen_offset_ms, first_seen_playback_ms, updated_at)
            VALUES ('animal', 'task', 1, 'Unknown', 'Unknown',
                    1250, 1250.000001, '2026-01-01')
        """)
        conn.exec_driver_sql("""
            INSERT INTO distance_individuals
                (id, task_id, individual_number, age,
                 first_seen_offset_ms, initial_carrier_individual_id, updated_at)
            VALUES ('infant', 'task', 2, 'Infant',
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


@pytest.mark.parametrize("upgraded", [False, True])
def test_deployment_foreign_keys_are_project_scoped(legacy_database, tmp_path, upgraded):
    engine = legacy_database if upgraded else create_engine(f"sqlite:///{tmp_path / 'new.db'}")
    if upgraded:
        migrations.upgrade_database(engine)
    else:
        migrations.initialize_database(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.exec_driver_sql("""
            INSERT OR IGNORE INTO projects (id, name, created_at) VALUES
                ('project', 'Project', '2026-01-01'), ('other', 'Other', '2026-01-01')
        """)
        conn.exec_driver_sql("""
            INSERT INTO deployments (id, project_id, deployment_id)
            VALUES ('deployment', 'other', 'Camera')
        """)
        for statement in (
            "INSERT INTO videos (video_id, project_id, deployment_id, video_path, last_seen_at) "
            "VALUES ('wrong-project', 'project', 'deployment', 'wrong.mp4', '2026-01-01')",
            "INSERT INTO videos (video_id, deployment_id, video_path, last_seen_at) "
            "VALUES ('projectless', 'deployment', 'wrong.mp4', '2026-01-01')",
            "INSERT INTO reference_videos (id, project_id, deployment_id, video_path) "
            "VALUES ('wrong-reference', 'project', 'deployment', 'wrong.mp4')",
        ):
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql(statement)
        conn.exec_driver_sql("""
            INSERT INTO reference_videos (id, project_id, deployment_id, video_path)
            VALUES ('reference', 'other', 'deployment', 'right.mp4')
        """)
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    if not upgraded:
        engine.dispose()


def test_rejects_unknown_layout_with_current_version_stamp(legacy_database):
    migrations.upgrade_database(legacy_database)
    with legacy_database.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE videos ADD COLUMN camera_id VARCHAR")
    with pytest.raises(migrations.UnsupportedSchemaError, match="incompatible layout"):
        migrations.upgrade_database(legacy_database)


@pytest.fixture
def previous_v23_database(tmp_path):
    path = tmp_path / "pre-normalization-v23.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            Path(__file__)
            .with_name("fixtures")
            .joinpath("schema_pre_normalization_v23.sql")
            .read_text()
        )
        conn.executescript("""
            PRAGMA foreign_keys=ON;
            INSERT INTO projects (id, name, created_at) VALUES ('project', 'Keep', '2024-01-01');
            INSERT INTO deployments (id, project_id, deployment_id, notes)
                VALUES ('deployment', 'project', 'public-label', 'keep deployment metadata');
            INSERT INTO videos
                (video_id, project_id, video_path, camera_id, inferred_camera_id, deployment_id,
                 deployment_assignment, last_seen_at)
                VALUES ('video', 'project', 'animal.mp4', 'public-label', 'folder-label',
                        'deployment', 'manual', '2024-01-01'),
                       ('reference-source', 'project', 'refs/prepared.mp4', 'public-label',
                        'folder-label', 'deployment', 'folder', '2024-01-01');
            INSERT INTO species (id, scientific_name, is_custom) VALUES ('species', 'deer', 0);
            INSERT INTO individual_observations
                (video_id, id, observation_uuid, project_id, species_id, count, start_sec, updated_at)
                VALUES ('video', 1, 'observation', 'project', 'species', 3, 0, '2024-01-01');
            INSERT INTO model_annotations
                (id, project_id, video_id, annotation_type, model_name, value_text, updated_at)
                VALUES ('prediction', 'project', 'video', 'species', 'model', 'deer', '2024-01-01');
            INSERT INTO reference_videos (id, project_id, camera_id, deployment_id, video_path)
                VALUES ('external', 'project', 'public-label', NULL, 'calibration.mp4'),
                       ('linked', 'project', 'public-label', 'deployment', 'tagged.mp4'),
                       ('pending', 'project', 'unresolved-label', NULL, 'other.mp4'),
                       ('source', 'project', 'folder-label', NULL, 'refs/prepared.mp4');
            INSERT INTO reference_frames (id, reference_video_id, offset_ms, distance_m)
                VALUES ('frame', 'external', 1000, 10.0),
                       ('suggested-frame', 'source', 1000, 20.0);
            INSERT INTO distance_review_tasks
                (id, video_id, species_id, source_count, completion_history, rejected_ai)
                VALUES ('task', 'video', 'species', 1, '[]', '[]');
            INSERT INTO distance_individuals
                (id, task_id, video_id, species_id, individual_number, age,
                 first_seen_offset_ms, initial_carrier_individual_id, updated_at)
                VALUES ('animal', 'task', 'video', 'species', 1, 'Adult', 0, NULL, '2024-01-01'),
                       ('infant', 'task', 'video', 'species', 2, 'Infant', 0, 'animal', '2024-01-01');
            INSERT INTO distance_samples
                (id, individual_id, offset_ms, status, reference_frame_id, distance_m,
                 carried_by_individual_id, updated_at)
                VALUES ('sample', 'infant', 1000, 'annotated', 'frame', 10.0, 'animal', '2024-01-01');
            INSERT INTO distance_samples (id, individual_id, offset_ms, status, ai_original, updated_at)
                VALUES ('suggestion', 'animal', 2000, 'pending',
                        '{"reference_frame_id":"suggested-frame"}', '2024-01-01');
            INSERT INTO distance_review_progress
                (task_id, annotator_name, active_individual_id, updated_at)
                VALUES ('task', 'alice', 'infant', '2024-01-01');
        """)
    engine = create_engine(f"sqlite:///{path}")
    try:
        yield engine
    finally:
        engine.dispose()


def test_previous_v23_upgrade_preserves_deployments_and_evidence(previous_v23_database):
    engine = previous_v23_database
    assert migrations.upgrade_database(engine)
    assert not migrations.upgrade_database(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("""
            SELECT folder_camera_label, deployment_id, deployment_assignment FROM videos
            WHERE video_id = 'video'
        """).one() == ("folder-label", "deployment", "manual")
        assert conn.exec_driver_sql("SELECT id, notes FROM deployments").one() == (
            "deployment",
            "keep deployment metadata",
        )
        assert conn.exec_driver_sql("""
            SELECT id, deployment_id, unresolved_camera_label FROM reference_videos ORDER BY id
        """).all() == [
            ("external", "deployment", None),
            ("linked", "deployment", None),
            ("pending", None, "unresolved-label"),
            ("source", "deployment", None),
        ]
        assert conn.exec_driver_sql("""
            SELECT dt.id, di.id, dt.video_id, dt.species_id, di.initial_carrier_individual_id,
                   ds.id, ds.distance_m, ds.carried_by_individual_id, rf.id, rf.reference_video_id
            FROM distance_samples ds JOIN distance_individuals di ON di.id = ds.individual_id
            JOIN distance_review_tasks dt ON dt.id = di.task_id
            JOIN reference_frames rf ON rf.id = ds.reference_frame_id
        """).one() == (
            "task",
            "infant",
            "video",
            "species",
            "animal",
            "sample",
            10.0,
            "animal",
            "frame",
            "external",
        )
        assert (
            conn.exec_driver_sql(
                "SELECT active_individual_id FROM distance_review_progress"
            ).scalar_one()
            == "infant"
        )
        assert (
            conn.exec_driver_sql(
                "SELECT observation_uuid FROM individual_observations"
            ).scalar_one()
            == "observation"
        )
        assert (
            conn.exec_driver_sql("SELECT id FROM model_annotations").scalar_one() == "prediction"
        )
        assert conn.exec_driver_sql("""
            SELECT json_extract(ai_original, '$.reference_frame_id') FROM distance_samples
            WHERE id = 'suggestion'
        """).scalar_one() == "suggested-frame"
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_legacy_v23_upgrade_rolls_back_cross_project_link(previous_v23_database):
    engine = previous_v23_database
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO projects (id, name, created_at) VALUES ('other', 'Other', '2024-01-01')"
        )
        conn.exec_driver_sql("UPDATE deployments SET project_id = 'other'")
        before = list(conn.connection.driver_connection.iterdump())
    with pytest.raises(ValueError, match="invalid foreign keys"):
        migrations.upgrade_database(engine)
    with engine.connect() as conn:
        assert list(conn.connection.driver_connection.iterdump()) == before


def test_current_schema_requires_composite_deployment_fk(legacy_database):
    engine = legacy_database
    migrations.upgrade_database(engine)
    with engine.begin() as conn:
        ddl = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'reference_videos'"
        ).scalar_one()
        conn.exec_driver_sql("DROP TABLE reference_videos")
        conn.exec_driver_sql(
            ddl.replace(
                ",\n        FOREIGN KEY(project_id, deployment_id) REFERENCES deployments (project_id, id)",
                "",
            )
        )
    with pytest.raises(migrations.UnsupportedSchemaError, match="incompatible layout"):
        migrations.upgrade_database(engine)
