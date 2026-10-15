import uuid
from datetime import datetime
from pathlib import Path

import pytest
from review_app.backend.db.models import Video
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def _paths(dp, pid):
    with dp.Session() as s:
        return sorted(v.video_path for v in s.query(Video).filter_by(project_id=pid).all())


def test_update_project_dir_rewrites_video_paths(provider_with_project):
    dp, project, d = provider_with_project
    old = d.path
    before = _paths(dp, project.id)
    assert all(p.startswith(old) for p in before)

    new_dir = str(Path(old).parent / "moved_videos")
    updated = dp.update_project_dir(d.id, new_dir)
    assert updated is not None
    assert updated.path == new_dir

    after = _paths(dp, project.id)
    assert all(p.startswith(new_dir + "/") for p in after)
    # filenames/subpaths preserved
    assert [Path(p).name for p in before] == [Path(p).name for p in after]
    # dir record updated
    assert dp.get_project_dirs(project.id)[0].path == new_dir


def test_update_project_dir_unknown_id_returns_none(provider_with_project):
    dp, project, _ = provider_with_project
    assert dp.update_project_dir("nope", "/tmp/x") is None


def test_update_project_dir_matches_backslash_stored_paths(provider_with_project):
    """Legacy rows stored with Windows backslashes (e.g. a pre-migration or
    cross-OS-imported DB) must still be found and rewritten, not silently skipped."""
    dp, project, d = provider_with_project
    old = d.path
    win_old = old.replace("/", "\\")
    with dp.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE videos SET video_path = REPLACE(video_path, '/', '\\')"
                " WHERE project_id = :pid"
            ),
            {"pid": project.id},
        )
        conn.execute(
            text("UPDATE project_dirs SET path = :p WHERE id = :id"),
            {"p": win_old, "id": d.id},
        )

    new_dir = str(Path(old).parent / "moved_videos")
    updated = dp.update_project_dir(d.id, new_dir)
    assert updated is not None
    after = _paths(dp, project.id)
    assert after, "no video rows found — backslash paths were not matched"
    assert all(p.startswith(new_dir + "/") for p in after)


def test_remove_project_dir_matches_backslash_stored_paths(provider_with_project):
    dp, project, d = provider_with_project
    with dp.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE videos SET video_path = REPLACE(video_path, '/', '\\')"
                " WHERE project_id = :pid"
            ),
            {"pid": project.id},
        )
        conn.execute(
            text("UPDATE project_dirs SET path = REPLACE(path, '/', '\\') WHERE id = :id"),
            {"id": d.id},
        )

    dp.remove_project_dir(d.id)
    assert _paths(dp, project.id) == []


def test_relocation_preserves_reference_identity_frames_and_samples(distance_data, tmp_path):
    data = distance_data
    dp, project, directory = data["dp"], data["project"], data["directory"]
    old_path = str(Path(directory.path) / "cam_x" / "patrol.mp4")
    source_id = str(uuid.uuid4())
    with dp.Session.begin() as session:
        session.add(
            Video(
                video_id=source_id,
                project_id=project.id,
                camera_id="cam_x",
                video_path=old_path,
                duration_sec=10,
                is_missing=False,
            )
        )
    dp.set_video_tags(source_id, ["reference_video"])
    reference = dp.get_distance_reference_videos(data["videos"][0])[0]
    frame = dp.add_reference_frame(reference["id"], 333, 4, playback_offset_ms=333.334)
    dp.verify_distance_recording_time(data["videos"][0], datetime(2024, 1, 1), "alice")
    sample = dp.save_distance_sample(
        data["individual"]["id"], 0, reference_frame_id=frame["id"], labeled_by="alice"
    )
    moved = tmp_path / "moved"
    dp.update_project_dir(directory.id, str(moved))
    references = dp.get_distance_reference_videos(data["videos"][0])
    assert len(references) == 1
    assert references[0]["id"] == reference["id"]
    assert references[0]["video_path"] == str(moved / "cam_x" / "patrol.mp4")
    assert references[0]["is_tagged"]
    assert dp.list_reference_frames(reference["id"]) == [frame]
    assert (
        dp.get_distance_review_state(data["videos"][0], "alice")["samples"][0]["id"]
        == sample["id"]
    )


def test_relocation_updates_only_root_derived_deployment_ids(distance_data, tmp_path):
    data = distance_data
    dp, project, directory = data["dp"], data["project"], data["directory"]
    old_root = Path(directory.path)
    new_root = tmp_path / "renamed-deployment"
    (new_root / "videos").mkdir(parents=True)
    (new_root / "references").mkdir()
    with dp.Session.begin() as session:
        video = session.get(Video, data["videos"][0])
        video.video_path = str(old_root / "videos" / "animal.mp4")
        video.camera_id = old_root.name
    reference = dp.register_reference_video(
        project.id, old_root.name, str(old_root / "references" / "patrol.mp4")
    )
    explicit = dp.register_reference_video(
        project.id, "explicit-camera", str(old_root / "references" / "explicit.mp4")
    )
    dp.update_project_dir(directory.id, str(new_root))
    with dp.Session() as session:
        assert session.get(Video, data["videos"][0]).camera_id == new_root.name
    assert dp.list_reference_videos(project.id, new_root.name)[0]["id"] == reference["id"]
    assert dp.list_reference_videos(project.id, "explicit-camera")[0]["id"] == explicit["id"]


def test_reference_path_collision_rolls_back_entire_relocation(distance_data, tmp_path):
    data = distance_data
    dp, project, directory = data["dp"], data["project"], data["directory"]
    old_paths = _paths(dp, project.id)
    relative = Path("cam_x") / "patrol.mp4"
    original = dp.register_reference_video(
        project.id, "cam_x", str(Path(directory.path) / relative)
    )
    target = tmp_path / "moved"
    dp.register_reference_video(project.id, "cam_x", str(target / relative))
    with pytest.raises(IntegrityError):
        dp.update_project_dir(directory.id, str(target))
    assert _paths(dp, project.id) == old_paths
    assert dp.get_project_dirs(project.id)[0].path == directory.path
    assert (
        next(
            row
            for row in dp.list_reference_videos(project.id, "cam_x")
            if row["id"] == original["id"]
        )["video_path"]
        == original["video_path"]
    )
