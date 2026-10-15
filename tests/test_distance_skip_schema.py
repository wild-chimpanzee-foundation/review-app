"""Carried skips on fresh databases and the published v22 upgrade path."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from review_app.backend.db import migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError

STAMP = datetime(2026, 6, 1, 12, 34, 56, 123456)


def _contents(conn):
    """Compare all persisted fields, including evidence, junctions and completion."""
    return {
        table: sorted(conn.exec_driver_sql(f'SELECT * FROM "{table}"').all(), key=repr)
        for table in Base.metadata.tables
    }


def _sample(**changes):
    return {
        "id": "new-skip",
        "individual_id": "infant",
        "offset_ms": 2000,
        "status": "skipped",
        "skip_reason": "individual absent",
        "reference_frame_id": None,
        "distance_m": None,
        "carried_by_individual_id": "parent",
        "labeled_by": "bob",
        "labeled_at": STAMP,
        "updated_at": STAMP,
        **changes,
    }


def _insert(conn, table, **values):
    conn.execute(Base.metadata.tables[table].insert(), values)


def _make_database(path, schema):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    if schema != "fresh":
        with sqlite3.connect(path) as conn:
            conn.executescript(
                Path(__file__).with_name("fixtures").joinpath("schema_snapshot.sql").read_text()
            )
    if schema != "upgraded":
        Base.metadata.create_all(engine)
    migrations.run_migrations(engine)
    with engine.begin() as conn:
        _insert(conn, "projects", id="project", name="Project", created_at=STAMP)
        _insert(
            conn,
            "videos",
            video_id="video",
            project_id="project",
            video_path="video.mp4",
            last_seen_at=STAMP,
        )
        _insert(conn, "species", id="species", scientific_name="Species")
        _insert(conn, "behaviors", id="behavior", key="feeding", name_en="Feeding")
        _insert(
            conn,
            "reference_videos",
            id="reference-video",
            project_id="project",
            camera_id="camera",
            video_path="reference.mp4",
        )
        _insert(
            conn,
            "reference_frames",
            id="frame",
            reference_video_id="reference-video",
            offset_ms=100,
            playback_offset_ms=100.25,
            distance_m=5.5,
        )
        for number, individual in enumerate(("parent", "infant", "independent"), 1):
            _insert(
                conn,
                "distance_individuals",
                id=individual,
                video_id="video",
                individual_number=number,
                species_id="species",
                age="Infant" if individual == "infant" else "Adult",
                first_seen_offset_ms=0,
                labeled_by="alice",
                labeled_at=STAMP,
                updated_at=STAMP,
                review_status="complete",
                completed_by="alice",
                completed_at=STAMP,
            )
        _insert(
            conn,
            "distance_video_reviews",
            video_id="video",
            recorded_start=STAMP,
            verified_by="alice",
            verified_at=STAMP,
            clock_anchor_offset_ms=0,
            clock_anchor_playback_ms=0.25,
            opening_even_second=True,
            completed_by="alice",
            completed_at=STAMP,
        )
        _insert(
            conn,
            "distance_review_progress",
            video_id="video",
            annotator_name="alice",
            active_individual_id="infant",
            updated_at=STAMP,
        )
        for individual in ("parent", "infant"):
            _insert(
                conn,
                "distance_samples",
                **_sample(
                    id=f"{individual}-annotation",
                    individual_id=individual,
                    offset_ms=0,
                    status="annotated",
                    skip_reason=None,
                    reference_frame_id="frame",
                    distance_m=5.5,
                    carried_by_individual_id="parent" if individual == "infant" else None,
                    labeled_by="alice",
                ),
            )
        _insert(
            conn,
            "distance_samples",
            **_sample(
                id="independent-skip",
                individual_id="independent",
                offset_ms=0,
                carried_by_individual_id=None,
            ),
        )
        for sample_id in ("parent-annotation", "infant-annotation", "independent-skip"):
            _insert(conn, "distance_sample_behaviors", sample_id=sample_id, behavior_id="behavior")
        _insert(conn, "distance_samples", **_sample(id="existing-carried-skip", offset_ms=1000))
    return engine


@pytest.fixture(params=["fresh", "upgraded", "precreated"])
def distance_db(tmp_path, request):
    engine = _make_database(tmp_path / "distance.db", request.param)
    try:
        with engine.connect() as conn:
            before = _contents(conn)
        migrations.run_migrations(engine)
        migrations.run_migrations(engine)
        with engine.connect() as conn:
            assert _contents(conn) == before
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert conn.exec_driver_sql("SELECT version FROM _schema_version").scalar() == len(
                migrations.MIGRATIONS
            )
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("reason", ["individual absent", "midpoint not visible"])
def test_carried_skip_preserves_carrier_and_attribution(distance_db, reason):
    with distance_db.begin() as conn:
        _insert(conn, "distance_samples", **_sample(skip_reason=reason))
        assert conn.exec_driver_sql(
            "SELECT status, skip_reason, reference_frame_id, distance_m, "
            "carried_by_individual_id, labeled_by FROM distance_samples WHERE id='new-skip'"
        ).one() == ("skipped", reason, None, None, "parent", "bob")
        # Updating an existing annotated infant into a carried skip is valid too.
        conn.exec_driver_sql(
            "UPDATE distance_samples SET status='skipped', skip_reason=?, "
            "reference_frame_id=NULL, distance_m=NULL WHERE id='infant-annotation'",
            (reason,),
        )
        assert (
            conn.exec_driver_sql(
                "SELECT carried_by_individual_id FROM distance_samples WHERE id='infant-annotation'"
            ).scalar()
            == "parent"
        )
        assert (
            conn.exec_driver_sql(
                "SELECT behavior_id FROM distance_sample_behaviors WHERE sample_id='infant-annotation'"
            ).scalar()
            == "behavior"
        )


@pytest.mark.parametrize(
    "changes, constraint",
    [
        ({"skip_reason": None}, "ck_distance_sample_state"),
        ({"skip_reason": "unknown"}, "ck_distance_sample_state"),
        ({"skip_reason": ""}, "ck_distance_sample_state"),
        ({"reference_frame_id": "frame"}, "ck_distance_sample_state"),
        ({"distance_m": 5.5}, "ck_distance_sample_state"),
        ({"status": "pending"}, "ck_distance_sample_state"),
        ({"status": None}, "NOT NULL"),
        ({"status": "annotated", "skip_reason": None}, "ck_distance_sample_state"),
        (
            {"status": "annotated", "skip_reason": None, "distance_m": 5.5},
            "ck_distance_sample_state",
        ),
        (
            {"status": "annotated", "skip_reason": None, "reference_frame_id": "frame"},
            "ck_distance_sample_state",
        ),
        (
            {"status": "annotated", "reference_frame_id": "frame", "distance_m": 5.5},
            "ck_distance_sample_state",
        ),
        *[
            (
                {
                    "status": "annotated",
                    "skip_reason": None,
                    "reference_frame_id": "frame",
                    "distance_m": distance,
                },
                "ck_distance_sample_state",
            )
            for distance in (0, -1)
        ],
        ({"carried_by_individual_id": "infant"}, "ck_distance_carrier"),
        (
            {
                "status": "annotated",
                "skip_reason": None,
                "reference_frame_id": "frame",
                "distance_m": 5.5,
                "carried_by_individual_id": "infant",
            },
            "ck_distance_carrier",
        ),
        ({"offset_ms": -1}, "ck_distance_sample_offset"),
        ({"offset_ms": 0}, "UNIQUE"),
        ({"individual_id": "missing"}, "FOREIGN KEY"),
        ({"carried_by_individual_id": "missing"}, "FOREIGN KEY"),
        (
            {
                "status": "annotated",
                "skip_reason": None,
                "reference_frame_id": "missing",
                "distance_m": 5.5,
            },
            "FOREIGN KEY",
        ),
    ],
)
def test_invalid_samples_are_still_rejected(distance_db, changes, constraint):
    with pytest.raises(IntegrityError, match=constraint), distance_db.begin() as conn:
        _insert(conn, "distance_samples", **_sample(**changes))


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM reference_frames WHERE id='frame'",
        "DELETE FROM reference_videos WHERE id='reference-video'",
        "DELETE FROM distance_individuals WHERE id='parent'",
        "DELETE FROM behaviors WHERE id='behavior'",
        "INSERT INTO distance_sample_behaviors VALUES ('missing', 'behavior')",
        "INSERT INTO distance_sample_behaviors VALUES ('infant-annotation', 'missing')",
    ],
)
def test_referenced_evidence_carriers_and_junctions_are_protected(distance_db, sql):
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), distance_db.begin() as conn:
        conn.exec_driver_sql(sql)


def test_carried_skip_alone_protects_its_carrier(distance_db):
    with distance_db.begin() as conn:
        _insert(conn, "distance_samples", **_sample(carried_by_individual_id="independent"))
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), distance_db.begin() as conn:
        conn.exec_driver_sql("DELETE FROM distance_individuals WHERE id='independent'")
    with distance_db.connect() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT carried_by_individual_id FROM distance_samples WHERE id='new-skip'"
            ).scalar()
            == "independent"
        )
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM distance_sample_behaviors WHERE sample_id='independent-skip'"
            ).scalar()
            == 1
        )


def test_sample_and_individual_deletion_still_cascade_to_behaviors(distance_db):
    with distance_db.begin() as conn:
        conn.exec_driver_sql("DELETE FROM distance_samples WHERE id='independent-skip'")
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM distance_sample_behaviors WHERE sample_id='independent-skip'"
            ).scalar()
            == 0
        )
        conn.exec_driver_sql("DELETE FROM distance_individuals WHERE id='infant'")
        assert (
            conn.exec_driver_sql(
                "SELECT count(*) FROM distance_samples WHERE individual_id='infant'"
            ).scalar()
            == 0
        )
        assert conn.exec_driver_sql("SELECT count(*) FROM distance_review_progress").scalar() == 0
        assert conn.exec_driver_sql("SELECT * FROM distance_sample_behaviors").all() == [
            ("parent-annotation", "behavior")
        ]
        conn.exec_driver_sql("DELETE FROM videos WHERE video_id='video'")
        assert conn.exec_driver_sql("SELECT count(*) FROM distance_samples").scalar() == 0
        assert conn.exec_driver_sql("SELECT count(*) FROM distance_sample_behaviors").scalar() == 0
        assert conn.exec_driver_sql("SELECT count(*) FROM distance_video_reviews").scalar() == 0
        assert conn.exec_driver_sql("SELECT count(*) FROM reference_frames").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
