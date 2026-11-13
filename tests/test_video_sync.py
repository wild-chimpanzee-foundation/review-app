"""Sync probes discovered media and existing unknown media, not already checked rows."""

from pathlib import Path

import pytest
from review_app.backend.db.models import Video
from review_app.backend.provider.video import ProbeResult


@pytest.mark.parametrize("valid", [True, False])
def test_sync_probes_new_and_existing_unknown_media_with_monotonic_progress(
    provider_with_project, monkeypatch, valid
):
    dp, project, root = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    with dp.Session.begin() as session:
        unknown = session.get(Video, ids[0])
        unknown.is_valid = None
        unknown.is_web_safe = None
        unknown.latitude = 1.5
        unknown.longitude = 2.5
        saved_created_at = unknown.created_at
        unknown_path = Path(unknown.video_path)
        checked = session.get(Video, ids[1])
        checked_path = Path(checked.video_path)
    new_path = Path(root.path) / "cam_x/new.mp4"
    new_path.touch()
    calls = []
    progress = []

    def probe_many(paths, progress_callback=None):
        calls.extend(paths)
        for count, path in enumerate(paths, 1):
            if progress_callback:
                progress_callback(count, len(paths), path.name)
        return {
            path: ProbeResult(20.0, valid, valid, None if valid else "Invalid video", None, 3, 4)
            for path in paths
        }

    monkeypatch.setattr("review_app.backend.provider.video._probe_many", probe_many)
    result = dp.sync_videos(
        active_project_id=project.id,
        progress_callback=lambda done, total, _: progress.append((done, total)),
    )
    assert set(calls) == {unknown_path, new_path}
    assert checked_path not in calls
    assert result == {"scanned": 3, "added": 1, "updated": 2, "removed": 0}
    assert progress[0] == (0, 3) and progress[-1] == (3, 3)
    assert all(total == 3 and 0 <= done <= total for done, total in progress)
    assert [done for done, _ in progress] == sorted(done for done, _ in progress)
    with dp.Session() as session:
        unknown = session.get(Video, ids[0])
        assert unknown.is_valid is valid
        assert unknown.is_web_safe is valid
        assert unknown.validation_error == (None if valid else "Invalid video")
        assert unknown.duration_sec == 20.0
        assert unknown.created_at == saved_created_at
        assert (unknown.latitude, unknown.longitude) == (1.5, 2.5)
        assert session.get(Video, ids[1]).duration_sec == 10.0
    calls.clear()
    dp.sync_videos(active_project_id=project.id)
    assert calls == []  # Known invalid rows use explicit re-probing, not every sync.
