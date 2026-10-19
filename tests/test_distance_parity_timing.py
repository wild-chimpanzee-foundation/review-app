"""Parity-only schedules and migration of existing exact alignment evidence."""

from datetime import datetime

import pytest
from review_app.backend.db import migrations
from review_app.backend.db.models import Base
from review_app.backend.distance_timing import sample_offsets


@pytest.mark.parametrize(
    "even,duration,expected",
    [
        (True, 0.01, [0]),
        (True, 4, [0, 2000]),
        (False, 1, []),
        (False, 1.001, [1000]),
        (False, 5, [1000, 3000]),
    ],
)
def test_parity_schedule_excludes_the_video_endpoint(even, duration, expected):
    assert (
        sample_offsets(None, duration, alignment_mode="simplified", opening_even_second=even)
        == expected
    )


def test_short_odd_clip_cannot_reset_saved_evidence(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    with dp.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE videos SET duration_sec=1 WHERE video_id=?", (video_id,))
    dp.align_distance_by_parity(video_id, True, "alice")
    before = dp.get_distance_review_state(video_id, "alice")
    with pytest.raises(ValueError, match="No even camera second"):
        dp.align_distance_by_parity(video_id, False, "alice", reset_tracks=True)
    assert dp.get_distance_review_state(video_id, "alice") == before
    assert dp.list_distance_individuals(video_id)


@pytest.mark.parametrize("fail_first", [False, True])
def test_v24_upgrade_preserves_clock_samples_progress_and_completion(
    distance_data, monkeypatch, fail_first
):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.calibrate_distance_clock(video_id, datetime(2026, 10, 12, 16, 2, 50), 920.125, "alice")
    dp.apply_distance_to_end(
        data["individual"]["id"], 920, skip_reason="individual absent", labeled_by="alice"
    )
    dp.set_distance_active_individual(video_id, "alice", data["individual"]["id"])
    dp.finish_distance_video(video_id, "alice")
    with dp.engine.begin() as conn:
        expected = {
            table.name: conn.execute(table.select().order_by(*table.primary_key.columns)).all()
            for table in Base.metadata.sorted_tables
        }
        # Recreate the actual previous table, not just its version stamp.
        old_ddl = next(
            sql
            for sql in migrations.DISTANCE_SCHEMA_V23
            if "CREATE TABLE IF NOT EXISTS distance_video_reviews (" in sql
        )
        conn.exec_driver_sql(old_ddl.replace("distance_video_reviews (", "reviews_v24 ("))
        columns = ", ".join(
            column.name
            for column in Base.metadata.tables["distance_video_reviews"].columns
            if column.name != "alignment_mode"
        )
        conn.exec_driver_sql(
            f"INSERT INTO reviews_v24 ({columns}) SELECT {columns} FROM distance_video_reviews"
        )
        conn.exec_driver_sql("DROP TABLE distance_video_reviews")
        conn.exec_driver_sql("ALTER TABLE reviews_v24 RENAME TO distance_video_reviews")
        conn.exec_driver_sql("UPDATE _schema_version SET version=24")
    if fail_first:
        with dp.engine.connect() as conn:
            before = list(conn.connection.driver_connection.iterdump())

        def fail_after_rebuild(conn):
            migrations._migration_v25(conn)
            raise RuntimeError("abort parity migration")

        with monkeypatch.context() as patch:
            patch.setattr(
                migrations,
                "MIGRATIONS",
                [
                    (v, fail_after_rebuild if v == 25 else migration)
                    for v, migration in migrations.MIGRATIONS
                ],
            )
            with pytest.raises(RuntimeError, match="abort parity migration"):
                migrations.run_migrations(dp.engine)
        with dp.engine.connect() as conn:
            assert list(conn.connection.driver_connection.iterdump()) == before
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    migrations.run_migrations(dp.engine)
    migrations.run_migrations(dp.engine)
    with dp.engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            assert (
                conn.execute(table.select().order_by(*table.primary_key.columns)).all()
                == (expected[table.name])
            )
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        columns = {
            row[1]: row
            for row in conn.exec_driver_sql("PRAGMA table_info(distance_video_reviews)")
        }
        assert columns["recorded_start"][3] == 0
        assert columns["alignment_mode"][3] == 1
    # The upgraded table can now actually persist an unknown camera time.
    dp.align_distance_by_parity(video_id, False, "bob", reset_tracks=True)
    assert dp.get_distance_review_state(video_id, "bob")["review"]["recorded_start"] is None
