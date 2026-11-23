"""Exercise real inventory/scan/reassignment/reference/package flows, without UI tests."""

from datetime import datetime
from pathlib import Path

import pytest
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import Species, Video
from review_app.backend.deployments import DeploymentInputError
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.fixture
def managed(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    for relative in (
        "animals/a.mp4",
        "B/videos/b.mp4",
        "B/references/ref.mp4",
        "shared/ref.mp4",
        "misc/other.mp4",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    dp = LocalDataProvider()
    project = dp.create_project("Managed", str(root))
    target = dp.save_deployment(project.id, {"deployment_id": "A", "name": "Display A"})
    directory = dp.get_project_dirs(project.id)[0]
    for folder, deployment_id in (("animals", target), ("shared", None), ("misc", None)):
        assign(
            dp,
            project.id,
            (),
            deployment_id,
            action="folder",
            folder={"root_id": directory.id, "path": folder},
        )
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        videos = {
            Path(v.video_path).relative_to(root).as_posix(): v.video_id
            for v in session.query(Video).filter_by(project_id=project.id)
        }
    dp.set_video_tags(videos["shared/ref.mp4"], ["reference_video"])
    inventory = {row["deployment_id"]: row["id"] for row in dp.get_project_deployments(project.id)}
    return dp, project, root, videos, inventory


def row(dp, video_id):
    with dp.Session() as session:
        video = session.get(Video, video_id)
        return (
            video.camera_id,
            video.deployment_id,
            video.deployment_assignment,
            video.inferred_camera_id,
        )


def assign(dp, project_id, videos, target, **kwargs):
    preview = dp.preview_deployment_assignment(project_id, videos, target, **kwargs)
    return dp.apply_deployment_assignment(preview)


def test_folder_links_and_inference_drive_grouping_and_unresolved_distance(managed):
    dp, project, root, videos, inventory = managed
    assert row(dp, videos["animals/a.mp4"])[:2] == ("A", inventory["A"])
    assert row(dp, videos["B/videos/b.mp4"])[:2] == ("B", inventory["B"])
    assert row(dp, videos["B/references/ref.mp4"])[:2] == ("B", inventory["B"])
    assert row(dp, videos["shared/ref.mp4"])[0:2] == (None, None)
    assert row(dp, videos["animals/a.mp4"])[3] == "animals"
    assert dp.get_video_detail(videos["animals/a.mp4"])["camera_id"] == "A"
    assert dp.get_queue_filter_options(project.id)["camera_values"] == ["A", "B"]
    assert {
        v["source_video_id"] for v in dp.get_distance_reference_videos(videos["B/videos/b.mp4"])
    } == {videos["B/references/ref.mp4"]}
    with pytest.raises(ValueError, match="Resolve"):
        dp.get_distance_reference_videos(videos["misc/other.mp4"])
    summary = dp.get_deployment_summary(project.id)
    assert summary == {
        "deployments": 2,
        "references": 2,
        "unresolved": 2,
        "unmatched_references": 0,
    }


def test_manual_reference_reassignment_reconciles_prepared_identity_and_survives_scan(managed):
    dp, project, root, videos, inventory = managed
    # A durable but not yet calibrated record must be reconciled, not replaced.
    prepared = dp.register_reference_video(project.id, "old", str(root / "shared/ref.mp4"))
    assign(dp, project.id, [videos["shared/ref.mp4"]], inventory["A"])
    discovered = dp.get_distance_reference_videos(videos["animals/a.mp4"])
    assert [r["id"] for r in discovered] == [prepared["id"]]
    assert discovered[0]["camera_id"] == "A"
    assert discovered[0]["deployment_id"] == inventory["A"]
    dp.sync_videos(active_project_id=project.id)
    assert row(dp, videos["shared/ref.mp4"])[0:3] == ("A", inventory["A"], "manual")
    assert [r["id"] for r in dp.get_distance_reference_videos(videos["animals/a.mp4"])] == [
        prepared["id"]
    ]


def test_folder_link_direct_future_files_video_exceptions_and_reset(managed):
    dp, project, root, videos, inventory = managed
    directory = dp.get_project_dirs(project.id)[0]
    assign(dp, project.id, [videos["animals/a.mp4"]], inventory["B"])
    preview = dp.preview_deployment_assignment(
        project.id,
        target_id=inventory["A"],
        action="folder",
        folder={"root_id": directory.id, "path": "animals"},
    )
    assert preview["rows"][0]["preserved"]
    dp.apply_deployment_assignment(preview)
    (root / "animals/new.mp4").touch()
    nested = root / "animals/sub/inside.mp4"
    nested.parent.mkdir()
    nested.touch()
    dp.sync_videos(active_project_id=project.id)
    management = {r["filename"]: r for r in dp.get_deployment_management(project.id)["videos"]}
    assert management["a.mp4"]["deployment"] == "B"
    assert management["new.mp4"]["deployment"] == "A"
    assert management["inside.mp4"]["deployment"] == "animals/sub"
    assign(dp, project.id, [videos["animals/a.mp4"]], None, action="reset")
    assert row(dp, videos["animals/a.mp4"])[0:3] == ("A", inventory["A"], "folder")


def test_explicit_unresolved_video_and_folder_are_not_undone_by_scan(managed):
    dp, project, root, videos, inventory = managed
    assign(dp, project.id, [videos["B/videos/b.mp4"]], None)
    dp.sync_videos(active_project_id=project.id)
    assert row(dp, videos["B/videos/b.mp4"])[0:3] == (None, None, "manual")
    directory = dp.get_project_dirs(project.id)[0]
    preview = dp.preview_deployment_assignment(
        project.id, action="folder", folder={"root_id": directory.id, "path": "B/references"}
    )
    dp.apply_deployment_assignment(preview)
    (root / "B/references/later.mp4").touch()
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        assert {
            v.camera_id
            for v in session.query(Video).filter(Video.video_path.like("%B/references/%"))
        } == {None}


def test_folder_preview_rejects_new_files_but_video_selection_does_not_include_them(managed):
    dp, project, root, videos, inventory = managed
    directory = dp.get_project_dirs(project.id)[0]
    folder = dp.preview_deployment_assignment(
        project.id,
        target_id=inventory["B"],
        action="folder",
        folder={"root_id": directory.id, "path": "animals"},
    )
    video = dp.preview_deployment_assignment(project.id, [videos["animals/a.mp4"]], inventory["B"])
    (root / "animals/new.mp4").touch()
    dp.sync_videos(active_project_id=project.id)
    with pytest.raises(DeploymentInputError, match="stale"):
        dp.apply_deployment_assignment(folder)
    dp.apply_deployment_assignment(video)
    assert row(dp, videos["animals/a.mp4"])[0] == "B"
    with dp.Session() as session:
        assert (
            session.query(Video)
            .filter_by(video_path=str(root / "animals/new.mp4"))
            .one()
            .camera_id
            == "A"
        )


def test_cross_project_targets_and_changed_selection_rejected_atomically(managed):
    dp, project, root, videos, inventory = managed
    other = dp.create_project("Other", str(root))
    foreign = dp.save_deployment(other.id, {"deployment_id": "Other"})
    with pytest.raises(DeploymentInputError, match="target"):
        dp.preview_deployment_assignment(project.id, [videos["animals/a.mp4"]], foreign)
    with pytest.raises(DeploymentInputError, match="selection"):
        dp.preview_deployment_assignment(
            project.id, [videos["animals/a.mp4"], "missing"], inventory["B"]
        )
    preview = dp.preview_deployment_assignment(
        project.id, [videos["animals/a.mp4"]], inventory["B"]
    )
    assign(dp, project.id, [videos["animals/a.mp4"]], None)
    with pytest.raises(DeploymentInputError, match="stale"):
        dp.apply_deployment_assignment(preview)


def test_reference_and_animal_moves_block_saved_distance_work(managed):
    dp, project, root, videos, inventory = managed
    animal = videos["animals/a.mp4"]
    reference = videos["shared/ref.mp4"]
    assign(dp, project.id, [reference], inventory["A"])
    ref = dp.get_distance_reference_videos(animal)[0]
    frame = dp.add_reference_frame(ref["id"], 0, 5)
    dp.update_manual_review(animal, [{"species": "deer"}], is_blank=False)
    with dp.Session() as session:
        species_id = session.query(Species.id).filter_by(scientific_name="deer").scalar()
    individual = dp.create_distance_individual(
        animal, species_id, 0, anchor={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
    )
    dp.verify_distance_recording_time(animal, datetime(2026, 1, 1), "alice")
    dp.save_distance_sample(
        individual["id"], 0, reference_frame_id=frame["id"], labeled_by="alice"
    )
    for selected in ([animal], [reference], [videos["misc/other.mp4"], reference]):
        preview = dp.preview_deployment_assignment(project.id, selected, inventory["B"])
        assert preview["blocked"]
        with pytest.raises(DeploymentInputError, match="blocked"):
            dp.apply_deployment_assignment(preview)
    assert row(dp, animal)[0] == row(dp, reference)[0] == "A"
    assert row(dp, videos["misc/other.mp4"])[1] is None
    assert dp.list_reference_frames(ref["id"]) == [frame]


def test_frozen_species_assignment_blocks_changes_but_not_metadata(managed):
    dp, project, root, videos, inventory = managed
    animal = videos["animals/a.mp4"]
    dp.prepare_assignment(project.id, AssignmentRequest("alice", (animal,)))
    preview = dp.preview_deployment_assignment(project.id, [animal], inventory["B"])
    assert preview["rows"][0]["blocked"] == "deployment_block_handoff"
    with pytest.raises(DeploymentInputError):
        dp.apply_deployment_assignment(preview)
    existing = next(r for r in dp.get_project_deployments(project.id) if r["id"] == inventory["A"])
    dp.save_deployment(
        project.id, existing | {"name": "Renamed display"}, deployment_id=existing["id"]
    )
    assert row(dp, animal)[0:2] == ("A", inventory["A"])


def test_inventory_edit_create_validation_and_display_names_not_used_for_matching(managed):
    dp, project, root, videos, inventory = managed
    new_id = dp.save_deployment(
        project.id, {"deployment_id": "C", "name": "misc", "habitat": "Forest"}
    )
    dp.sync_videos(active_project_id=project.id)
    assert row(dp, videos["misc/other.mp4"])[1] is None
    assert (
        next(r for r in dp.get_project_deployments(project.id) if r["id"] == new_id)["habitat"]
        == "Forest"
    )
    with pytest.raises(DeploymentInputError, match="immutable"):
        dp.save_deployment(project.id, {"deployment_id": "D"}, deployment_id=new_id)
    with pytest.raises(DeploymentInputError, match="date"):
        dp.save_deployment(project.id, {"deployment_id": "D", "installation_date": "bad"})


def test_assignment_package_preserves_effective_assignment_on_open_and_rescan(managed):
    dp, project, root, videos, inventory = managed
    animal = videos["misc/other.mp4"]
    assign(dp, project.id, [animal], inventory["A"])
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", (animal,)))
    data = dp.export_assignment(prepared.id)
    # Existing v1 fields carry the effective label; reconstruct local pinned scope.
    from review_app.backend.annotation_package.archive import decode_archive

    manifest, context, work = decode_archive(data)
    assert manifest["version"] == 1
    assert context["media"][0]["camera_id"] == "A"
    opened = dp.open_assignment(data, "Opened", {dp.get_project_dirs(project.id)[0].id: root})
    local = dp.get_assignment_video_ids(opened.project_id)[0]
    assert row(dp, local)[0] == "A"
    dp.sync_videos(active_project_id=opened.project_id)
    assert row(dp, local)[0] == "A"
    assert dp.get_project_deployments(opened.project_id)


def test_missing_media_can_be_assigned_and_root_relocation_preserves_links(managed, tmp_path):
    dp, project, root, videos, inventory = managed
    (root / "shared/ref.mp4").unlink()
    assign(dp, project.id, [videos["shared/ref.mp4"]], inventory["A"])
    directory = dp.get_project_dirs(project.id)[0]
    new_root = tmp_path / "relocated"
    root.rename(new_root)
    dp.update_project_dir(directory.id, str(new_root))
    dp.sync_videos(active_project_id=project.id)
    assert row(dp, videos["animals/a.mp4"])[0:2] == ("A", inventory["A"])
    assert row(dp, videos["shared/ref.mp4"])[0:3] == ("A", inventory["A"], "manual")


def test_metadata_csv_restores_effective_identity_and_pins_it_on_rescan(managed):
    import pandas as pd

    dp, project, root, videos, inventory = managed
    result = dp.import_video_metadata_csv(
        pd.DataFrame([{"video_path": str(root / "misc/other.mp4"), "camera_id": "A"}]), project.id
    )
    assert result["updated"] == 1
    assert row(dp, videos["misc/other.mp4"])[0:3] == ("A", inventory["A"], "manual")
    dp.sync_videos(active_project_id=project.id)
    assert row(dp, videos["misc/other.mp4"])[0] == "A"


def test_metadata_reimport_preserves_frozen_identity_and_folder_provenance(tmp_db, mock_probe):
    import pandas as pd

    root = tmp_db["video_dir"]
    clip = root / "A" / "animal.mp4"
    clip.parent.mkdir()
    clip.touch()
    dp = LocalDataProvider()
    project = dp.create_project("Metadata", str(root))
    dp.save_deployment(project.id, {"deployment_id": "B", "name": "A"})
    dp.sync_videos(active_project_id=project.id)
    video = dp.get_deployment_management(project.id)["videos"][0]
    deployments = {d["deployment_id"]: d["id"] for d in dp.get_project_deployments(project.id)}
    dp.prepare_assignment(project.id, AssignmentRequest("alice", (video["video_id"],)))

    metadata = pd.DataFrame([{"video_path": str(clip), "camera_id": "A"}])
    dp.import_video_metadata_csv(metadata, project.id)
    assert row(dp, video["video_id"])[:3] == ("A", deployments["A"], "folder")
    metadata["camera_id"] = "B"
    with pytest.raises(DeploymentInputError, match="deployment_error_blocked"):
        dp.import_video_metadata_csv(metadata, project.id)
    assert row(dp, video["video_id"])[:3] == ("A", deployments["A"], "folder")


def test_explicit_cross_root_reference_link_uses_identity_not_root_or_label(managed, tmp_path):
    dp, project, root, videos, inventory = managed
    other_root = tmp_path / "other-root"
    other_root.mkdir()
    (other_root / "external.mp4").touch()
    dp.add_project_dir(project.id, str(other_root))
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        source_id = (
            session.query(Video.video_id)
            .filter_by(video_path=str(other_root / "external.mp4"))
            .scalar()
        )
    dp.set_video_tags(source_id, ["reference_video"])
    assign(dp, project.id, [source_id], inventory["A"])
    assert [
        r["source_video_id"] for r in dp.get_distance_reference_videos(videos["animals/a.mp4"])
    ] == [source_id]
    # The existing archive contract is root-scoped. Refuse unsupported output
    # rather than flattening this explicit cross-root identity into a v1 label.
    with pytest.raises(ValueError, match="multiple import roots"):
        dp.prepare_assignment(
            project.id, AssignmentRequest("alice", (videos["animals/a.mp4"], source_id))
        )


def test_late_calibration_invalidates_preview_without_partial_assignment(managed):
    dp, project, root, videos, inventory = managed
    source = videos["shared/ref.mp4"]
    assign(dp, project.id, [source], inventory["A"])
    reference = dp.get_distance_reference_videos(videos["animals/a.mp4"])[0]
    preview = dp.preview_deployment_assignment(
        project.id, [source, videos["misc/other.mp4"]], inventory["B"]
    )
    assert not preview["blocked"]
    dp.add_reference_frame(reference["id"], 0, 5)
    with pytest.raises(DeploymentInputError, match="stale"):
        dp.apply_deployment_assignment(preview)
    assert row(dp, source)[0] == "A"
    assert row(dp, videos["misc/other.mp4"])[1] is None


def test_apply_failure_rolls_back_all_selected_videos(managed, monkeypatch):
    from review_app.backend.provider import deployments

    dp, project, root, videos, inventory = managed
    selected = [videos["shared/ref.mp4"], videos["misc/other.mp4"]]
    preview = dp.preview_deployment_assignment(project.id, selected, inventory["A"])
    original = deployments._set_assignment
    called = []

    def fail_late(*args, **kwargs):
        original(*args, **kwargs)
        called.append(True)
        if len(called) == 2:
            raise RuntimeError("late failure")

    monkeypatch.setattr(deployments, "_set_assignment", fail_late)
    with pytest.raises(RuntimeError, match="late failure"):
        dp.apply_deployment_assignment(preview)
    assert all(row(dp, video)[1] is None for video in selected)


def test_pending_folder_link_applies_when_directory_arrives(managed):
    dp, project, root, videos, inventory = managed
    directory = dp.get_project_dirs(project.id)[0]
    preview = dp.preview_deployment_assignment(
        project.id,
        target_id=inventory["A"],
        action="folder",
        folder={"root_id": directory.id, "path": "later"},
    )
    assert preview["rows"] == []
    dp.apply_deployment_assignment(preview)
    (root / "later").mkdir()
    (root / "later/new.mp4").touch()
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        assert (
            session.query(Video)
            .filter_by(video_path=str(root / "later/new.mp4"))
            .one()
            .deployment_id
            == inventory["A"]
        )
