"""Deleting videos must also remove their child rows (observations, tags, labels).

Regression: with foreign_keys=ON and no ON DELETE CASCADE in the schema, deleting a
video that has any annotation used to raise IntegrityError (remove_project_dir,
delete_missing_videos) or orphan observation_tags. All paths now cascade explicitly.
"""

from __future__ import annotations

from sqlalchemy import text


def _count(dp, table: str, video_id: str) -> int:
    with dp.engine.connect() as conn:
        return conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE video_id = :v"), {"v": video_id}
        ).scalar()


def _annotate(dp, project_id: str, video_id: str) -> None:
    dp.update_manual_review(
        video_id,
        [{"species": "deer", "tags": ["grazing"], "start_sec": 0.0}],
        is_blank=False,
        active_project_id=project_id,
    )


def _assert_no_orphans(dp, video_id: str) -> None:
    for table in ("individual_observations", "observation_tags", "video_labels"):
        assert _count(dp, table, video_id) == 0, f"orphaned rows left in {table}"
    with dp.engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_remove_project_dir_cascades(provider_with_project):
    dp, project, d = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    _annotate(dp, project.id, video_id)

    dp.remove_project_dir(d.id)

    assert _count(dp, "videos", video_id) == 0
    _assert_no_orphans(dp, video_id)


def test_delete_missing_videos_cascades(provider_with_project):
    dp, project, d = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    _annotate(dp, project.id, video_id)
    with dp.engine.begin() as conn:
        conn.execute(text("UPDATE videos SET is_missing=1 WHERE video_id=:v"), {"v": video_id})

    removed = dp.delete_missing_videos(project.id)

    assert removed == 1
    assert _count(dp, "videos", video_id) == 0
    _assert_no_orphans(dp, video_id)


def test_delete_project_cascades(provider_with_project):
    dp, project, d = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    _annotate(dp, project.id, video_id)

    dp.delete_project(project.id)

    assert _count(dp, "videos", video_id) == 0
    _assert_no_orphans(dp, video_id)
