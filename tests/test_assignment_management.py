"""Removal preserves return authority; local delivery saves the same original ZIP."""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from review_app.backend.annotation_package.archive import decode_archive, encode_archive
from review_app.backend.annotation_package.naming import package_stem
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import HandoffAssignment, Video
from review_app.backend.provider.local_data_provider import LocalDataProvider


def test_package_names_use_frozen_preparation_time_and_return_export_time():
    assignment = {
        "assignment_reference": "015",
        "project_name": "../Forêt/Lope",
        "recipient": "Élodie Smith",
        "stage": "distance",
        "target_species": "Loxodonta cyclotis",
        "created_at": "2026-11-25T15:30:12+01:00",
    }
    base = "Forêt_Lope-distance-Loxodonta_cyclotis-Élodie_Smith-015"
    assert package_stem(assignment) == f"{base}-setup-20261125-143012Z"
    exported = datetime(2026, 11, 27, 9, 15, 22, tzinfo=timezone.utc)
    assert package_stem(assignment, exported_at=exported) == f"{base}-return-20261127-091522Z"


def test_legacy_assignment_gets_same_reference_on_both_sides(provider_with_project, tmp_path):
    dp, project, root = provider_with_project
    video_id = dp.get_assignment_video_ids(project.id)[0]
    request = AssignmentRequest("alice", (video_id,))
    saved = dp.prepare_assignment(project.id, request)
    manifest, context, work = decode_archive(dp.export_assignment(saved.id))
    assert manifest.pop("assignment_number") == 1
    legacy = encode_archive(manifest, context, work)
    with dp.Session.begin() as session:
        row = session.get(HandoffAssignment, saved.id)
        row.scope = {key: row.scope[key] for key in ("work_keys", "roots", "media")}
        row.setup_archive = legacy

    reference = dp.inspect_assignment(legacy)["assignment_reference"]
    assert reference.startswith("L")
    (setup,) = dp.save_assignment_packages(project.id, [saved.id], tmp_path)
    assert reference in setup.name
    assert saved.assignment_id not in setup.name
    opened = dp.open_assignment(legacy, "Local name", {root.id: root.path})
    working = dp.list_assignments(opened.project_id)[0]
    assert working["project_name"] == context["project_name"]
    assert working["assignment_reference"] == reference
    dp.remove_assignment(project.id, saved.id)
    returned = dp.export_assignment_return(opened.project_id)
    assert dp.preview_assignment_return(project.id, returned).assignment_reference == reference
    next_assignment = dp.prepare_assignment(project.id, request)
    assert (
        dp.inspect_assignment(dp.export_assignment(next_assignment.id))["assignment_reference"]
        == "002"
    )


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
        if count == 3:
            # Earlier releases can open the ZIP but omit the display number in returns.
            manifest, context, work = decode_archive(returned)
            assert manifest.pop("assignment_number") == 1
            returned = encode_archive(manifest, context, work)
        dp.remove_assignment(project.id, saved.id)
        assert dp.list_assignments(project.id) == []
        preview = dp.preview_assignment_return(project.id, returned)
        assert preview.assignment_reference == "001"
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
    (standalone,) = dp.save_assignment_packages(project.id, [saved.id], tmp_path / "packages")
    zip_only = dp.save_assignment_delivery(project.id, saved.id, tmp_path / "zip-only")
    assert Path(zip_only.path).name == standalone.stem
    assert (
        (Path(zip_only.path) / standalone.name).read_bytes() == standalone.read_bytes() == archive
    )
    assert not list(Path(zip_only.path).rglob("*.mp4"))
    assert zip_only.media_roots == {} and zip_only.copied_count == 0
    complete = dp.save_assignment_delivery(
        project.id, saved.id, tmp_path / "complete", include_footage=True
    )
    assert Path(complete.path).name == standalone.stem
    assert (Path(complete.path) / standalone.name).read_bytes() == archive
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
    assert (Path(complete.path) / standalone.name).is_file()


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
    path = Path(result.path)
    assert (path / f"{path.name}.zip").read_bytes() == dp.export_assignment(saved.id)


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
