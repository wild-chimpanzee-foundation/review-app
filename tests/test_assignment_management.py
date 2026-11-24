"""Removal preserves return authority; local delivery saves the same original ZIP."""

from pathlib import Path

import pytest
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import HandoffAssignment, Video
from review_app.backend.provider.local_data_provider import LocalDataProvider


def test_remove_hides_delivery_but_accepts_repeated_returns(provider_with_project):
    dp, project, root = provider_with_project
    original_id = dp.get_assignment_video_ids(project.id)[0]
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", (original_id,)))
    archive = dp.export_assignment(saved.id)
    opened = dp.open_assignment(archive, "Working", {root.id: root.path})
    local_id = dp.get_assignment_video_ids(opened.project_id)[0]
    for count in (3, 4):
        dp.update_manual_review(
            local_id, [{"species": "deer", "count": count}], active_project_id=opened.project_id
        )
        returned = dp.export_assignment_return(opened.project_id)
        dp.remove_assignment(project.id, saved.id)
        assert dp.list_assignments(project.id) == []
        preview = dp.preview_assignment_return(project.id, returned)
        dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
        assert dp.get_video_detail(original_id)["manual_selections"][0]["count"] == count
    assert len(dp.get_assignment_video_ids(project.id)) == 2
    assert len(dp.list_assignments(opened.project_id)) == 1
    with dp.Session() as session:
        binding = session.get(HandoffAssignment, saved.id)
        assert binding.setup_archive == archive and binding.removed_at is not None
    with pytest.raises(ValueError, match="removed"):
        dp.export_assignment(saved.id)
    with pytest.raises(ValueError):
        dp.remove_assignment("another-project", saved.id)
    with pytest.raises(ValueError):
        dp.remove_assignment(opened.project_id, opened.id)


def test_save_delivery_zip_and_optional_saved_scope_footage(provider_with_project, tmp_path):
    dp, project, root = provider_with_project
    video_id = dp.get_assignment_video_ids(project.id)[0]
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", (video_id,)))
    archive = dp.export_assignment(saved.id)
    zip_only = dp.save_assignment_delivery(project.id, saved.id, tmp_path / "zip-only")
    assert (Path(zip_only.path) / "assignment.zip").read_bytes() == archive
    assert not list(Path(zip_only.path).rglob("*.mp4"))
    assert zip_only.media_roots == {} and zip_only.copied_count == 0
    complete = dp.save_assignment_delivery(
        project.id, saved.id, tmp_path / "complete", include_footage=True
    )
    assert (Path(complete.path) / "assignment.zip").read_bytes() == archive
    assert complete.copied_count == 1
    assert len(list(Path(complete.path).rglob("*.mp4"))) == 1
    opened = dp.open_assignment(archive, "Delivered", complete.media_roots)
    assert not opened.missing_video_ids
    with pytest.raises(FileExistsError):
        dp.save_assignment_delivery(project.id, saved.id, tmp_path / "zip-only")
    with pytest.raises(ValueError, match="outside"):
        dp.save_assignment_delivery(project.id, saved.id, root.path)
    with pytest.raises(ValueError):
        dp.save_assignment_delivery("another-project", saved.id, tmp_path / "wrong")
    dp.remove_assignment(project.id, saved.id)
    with pytest.raises(ValueError):
        dp.save_assignment_delivery(project.id, saved.id, tmp_path / "removed")
    with pytest.raises(ValueError):
        dp.copy_assignment_footage(saved.id, tmp_path / "removed-copy")
    assert (Path(complete.path) / "assignment.zip").is_file()  # Existing deliveries stay intact.


def test_delivery_reports_missing_footage_but_keeps_zip(provider_with_project, tmp_path):
    dp, project, root = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    with dp.Session() as session:
        missing_path = Path(session.get(Video, ids[0]).video_path)
    missing_path.unlink()
    result = dp.save_assignment_delivery(
        project.id, saved.id, tmp_path / "partial", include_footage=True
    )
    assert result.missing_video_ids == (ids[0],)
    assert result.copied_count == 1
    assert (Path(result.path) / "assignment.zip").read_bytes() == dp.export_assignment(saved.id)


def test_reopening_keeps_saved_snapshot_and_removal_state(provider_with_project):
    dp, project, _ = provider_with_project
    saved = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    archive = dp.export_assignment(saved.id)
    dp.engine.dispose()
    dp = LocalDataProvider()
    assert dp.export_assignment(saved.id) == archive
    assert dp.list_assignments(project.id)[0]["id"] == saved.id
    dp.remove_assignment(project.id, saved.id)
    assert not dp.list_assignments(project.id)
