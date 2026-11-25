"""Footage delivery follows saved scope and preserves multi-root identity."""

from pathlib import Path

import pytest
from review_app.backend.annotation_package import delivery
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import Video


def test_copy_saved_scope_multi_root_and_open(provider_with_project, tmp_path):
    dp, project, root = provider_with_project
    second = tmp_path / "second"
    (second / "cam_x").mkdir(parents=True)
    (second / "cam_x/a.mp4").write_bytes(b"second root")
    dp.add_project_dir(project.id, str(second))
    dp.sync_videos(video_dir=second, active_project_id=project.id)
    with dp.Session() as session:
        ids = [v.video_id for v in session.query(Video).filter_by(project_id=project.id)]
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    package = dp.export_assignment(saved.id)
    dp.clear_all_assignments(project.id)
    (Path(root.path) / "unrelated.mp4").write_bytes(b"not assigned")
    dp.sync_videos(video_dir=Path(root.path), active_project_id=project.id)
    progress = []
    result = dp.copy_assignment_footage(
        saved.id, tmp_path / "delivery", lambda done, total: progress.append((done, total))
    )
    assert result.copied_count == len(ids)
    assert result.missing_video_ids == ()
    assert result.failures == {}
    assert progress == [(n, len(ids)) for n in range(1, len(ids) + 1)]
    assert len(list(Path(result.path).rglob("a.mp4"))) == 2
    assert {Path(path).name for path in result.media_roots.values()} == {
        "Footage-01",
        "Footage-02",
    }
    assert not list(Path(result.path).rglob("unrelated.mp4"))
    assert dp.export_assignment(saved.id) == package
    opened = dp.open_assignment(package, "Delivered", result.media_roots)
    assert opened.video_count == len(ids)
    assert opened.missing_video_ids == ()
    with pytest.raises(FileExistsError):
        dp.copy_assignment_footage(saved.id, tmp_path / "delivery")


def test_copy_uses_relocated_sources_with_original_destinations(provider_with_project, tmp_path):
    dp, project, root = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    moved = tmp_path / "relocated"
    Path(root.path).rename(moved)
    dp.update_project_dir(root.id, str(moved))
    result = dp.copy_assignment_footage(saved.id, tmp_path / "delivery")
    assert result.copied_count == len(ids)
    assert result.missing_video_ids == ()
    assert Path(result.media_roots[root.id], "cam_x/a.mp4").is_file()


def test_copy_reports_missing_and_failures(provider_with_project, tmp_path, monkeypatch):
    dp, project, root = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    with dp.Session() as session:
        missing = Path(session.get(Video, ids[0]).video_path)
        interrupted = Path(session.get(Video, ids[1]).video_path)
    original = missing.read_bytes()
    missing.unlink()

    def fail(source, target):
        Path(target).write_bytes(b"partial copy")
        raise PermissionError("copy denied")

    with monkeypatch.context() as patch:
        patch.setattr(delivery.shutil, "copy2", fail)
        result = dp.copy_assignment_footage(saved.id, tmp_path / "delivery")
    assert result.copied_count == 0
    assert result.missing_video_ids == (ids[0],)
    assert result.failures == {ids[1]: "copy denied"}
    assert result.problem_paths == {
        ids[0]: f"Footage-01/{missing.relative_to(root.path).as_posix()}",
        ids[1]: f"Footage-01/{interrupted.relative_to(root.path).as_posix()}",
    }
    assert all(Path(path).is_dir() for path in result.media_roots.values())
    assert not [path for path in Path(result.path).rglob("*") if path.is_file()]

    package = dp.export_assignment(saved.id)
    opened = dp.open_assignment(package, "Interrupted delivery", result.media_roots)
    assert set(opened.missing_video_ids) == set(ids)

    missing.write_bytes(original)
    retry = dp.copy_assignment_footage(saved.id, tmp_path / "retry")
    assert retry.copied_count == len(ids)
    assert retry.failures == {}
    target = Path(retry.media_roots[root.id]) / interrupted.relative_to(root.path)
    assert target.read_bytes() == interrupted.read_bytes()
    reopened = dp.open_assignment(package, "Retried delivery", retry.media_roots)
    assert reopened.missing_video_ids == ()


def test_copy_rejects_scanned_output_and_escaped_source(provider_with_project, tmp_path):
    dp, project, root = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    with pytest.raises(ValueError, match="outside"):
        dp.copy_assignment_footage(saved.id, Path(root.path) / "exports")
    with dp.Session.begin() as session:
        session.get(Video, ids[0]).video_path = str(tmp_path / "outside.mp4")
    with pytest.raises(ValueError, match="escapes"):
        dp.copy_assignment_footage(saved.id, tmp_path / "delivery")
    assert not (tmp_path / "delivery").exists()
