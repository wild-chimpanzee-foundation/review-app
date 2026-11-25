"""Regression coverage for equal camera labels under distinct ProjectDir roots."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from review_app.backend.camera_layout import camera_id_for_path
from review_app.backend.db.models import ReferenceVideo, Video
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import text


def _multi_root_deployment(tmp_db, mock_probe):
    dp = LocalDataProvider()
    roots = [tmp_db["root"] / name for name in ("deployment_A", "deployment_B")]
    project = dp.create_project("Two deployments", str(roots[0]))
    dp.add_project_dir(project.id, str(roots[1]))
    ids = {}
    for name, root in zip(("A", "B"), roots, strict=True):
        for folder in ("cam_1/videos", "cam_1/references"):
            (root / folder).mkdir(parents=True)
        animal_path = root / "cam_1/videos/animal.mp4"
        reference_path = root / "cam_1/references/reference.mp4"
        animal_path.touch()
        reference_path.touch()
        animal_id, reference_id = str(uuid.uuid4()), str(uuid.uuid4())
        with dp.Session.begin() as session:
            session.add_all(
                [
                    Video(
                        video_id=animal_id,
                        project_id=project.id,
                        folder_camera_label="cam_1",
                        video_path=str(animal_path),
                        duration_sec=10,
                    ),
                    Video(
                        video_id=reference_id,
                        project_id=project.id,
                        folder_camera_label="cam_1",
                        video_path=str(reference_path),
                        duration_sec=10,
                    ),
                ]
            )
        dp.set_video_tags(reference_id, ["reference_video"])
        ids[name] = {"animal": animal_id, "source": reference_id}
    with dp.engine.connect() as conn:
        species_id = conn.execute(
            text("SELECT id FROM species WHERE scientific_name='deer'")
        ).scalar_one()
    for deployment in ids.values():
        dp.update_manual_review(deployment["animal"], [{"species": "deer"}], is_blank=False)
    dp.initialize_project_deployments(project.id)
    return dp, project, ids, species_id


def test_root_aware_scope_keeps_cameras_separate_within_one_root(tmp_db, mock_probe):
    dp = LocalDataProvider()
    root = tmp_db["root"] / "one-import-root"
    project = dp.create_project("Two cameras", str(root))
    expected = {}
    for camera in ("cam_1", "cam_2"):
        for folder in ("videos", "references"):
            (root / camera / folder).mkdir(parents=True)
        animal_path = root / camera / "videos/animal.mp4"
        reference_path = root / camera / "references/reference.mp4"
        animal_path.touch()
        reference_path.touch()
        camera_id = camera_id_for_path(animal_path, root)
        assert camera_id == camera
        animal_id, source_id = str(uuid.uuid4()), str(uuid.uuid4())
        with dp.Session.begin() as session:
            session.add_all(
                [
                    Video(
                        video_id=animal_id,
                        project_id=project.id,
                        folder_camera_label=camera_id,
                        video_path=str(animal_path),
                        duration_sec=10,
                    ),
                    Video(
                        video_id=source_id,
                        project_id=project.id,
                        folder_camera_label=camera_id_for_path(reference_path, root),
                        video_path=str(reference_path),
                        duration_sec=10,
                    ),
                ]
            )
        dp.set_video_tags(source_id, ["reference_video"])
        expected[camera] = {"animal": animal_id, "source": source_id}

    dp.initialize_project_deployments(project.id)
    for camera in ("cam_1", "cam_2"):
        found = dp.get_distance_reference_videos(expected[camera]["animal"])
        assert [row["source_video_id"] for row in found] == [expected[camera]["source"]]


def test_reference_discovery_does_not_mix_distinct_project_roots(tmp_db, mock_probe):
    dp, _project, ids, _species_id = _multi_root_deployment(tmp_db, mock_probe)

    references_b = dp.get_distance_reference_videos(ids["B"]["animal"])

    assert [row["source_video_id"] for row in references_b] == [ids["B"]["source"]]


@pytest.mark.parametrize("second_root", [False, True])
def test_first_scan_resolves_external_calibration_only_with_one_root(
    tmp_db, mock_probe, second_root
):
    dp = LocalDataProvider()
    root = tmp_db["root"] / "imported"
    animal = root / "cam_a" / "animal.mp4"
    animal.parent.mkdir(parents=True)
    animal.touch()
    external = tmp_db["root"] / "calibration.mp4"
    external.touch()
    project = dp.create_project("External calibration", str(root))
    if second_root:
        other = tmp_db["root"] / "other"
        other.mkdir()
        other_root = dp.add_project_dir(project.id, str(other))
    reference = dp.register_reference_video(project.id, "cam_a", str(external))
    frame = dp.add_reference_frame(reference["id"], 0, 5.0)
    assert reference["deployment_id"] is None

    dp.sync_videos(active_project_id=project.id)
    (video_id,) = dp.get_video_queue({}, active_project_id=project.id)
    with dp.Session() as session:
        deployment_id = session.get(Video, video_id).deployment_id
        species_id = session.execute(
            text("SELECT id FROM species WHERE scientific_name='deer'")
        ).scalar_one()
    (saved_reference,) = dp.list_reference_videos(project.id, "cam_a")
    assert saved_reference["id"] == reference["id"]
    assert saved_reference["deployment_id"] == (None if second_root else deployment_id)
    dp.update_manual_review(video_id, [{"species": "deer"}], is_blank=False)
    individual = dp.create_distance_individual(
        video_id, species_id, 0, anchor={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
    )
    dp.verify_distance_recording_time(video_id, datetime(2024, 1, 1), "alice")
    if second_root:
        with pytest.raises(ValueError, match="match the video's project and deployment"):
            dp.save_distance_sample(
                individual["id"], 0, reference_frame_id=frame["id"], labeled_by="alice"
            )
        assert dp.list_reference_videos(project.id, "cam_a")[0]["deployment_id"] is None
        dp.remove_project_dir(other_root.id)

    sample = dp.save_distance_sample(
        individual["id"], 0, reference_frame_id=frame["id"], labeled_by="alice"
    )
    assert sample["reference_frame_id"] == frame["id"]
    assert dp.list_reference_videos(project.id, "cam_a")[0]["deployment_id"] == deployment_id


@pytest.mark.parametrize("bulk", [False, True])
def test_sample_rejects_reference_from_another_project_root(tmp_db, mock_probe, bulk):
    dp, _project, ids, species_id = _multi_root_deployment(tmp_db, mock_probe)
    references_a = dp.get_distance_reference_videos(ids["A"]["animal"])
    reference_a = references_a[0]
    frame_a = dp.add_reference_frame(reference_a["id"], 0, 5.0)
    individual_b = dp.create_distance_individual(
        ids["B"]["animal"],
        species_id,
        0,
        anchor={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
    )
    dp.verify_distance_recording_time(ids["B"]["animal"], datetime(2024, 1, 1), "alice")

    try:
        save = dp.apply_distance_to_end if bulk else dp.save_distance_sample
        save(individual_b["id"], 0, reference_frame_id=frame_a["id"], labeled_by="alice")
    except ValueError as exc:
        assert "deployment" in str(exc).lower() or "reference" in str(exc).lower()
    else:
        raise AssertionError("A reference from deployment A was accepted for deployment B")
    assert dp.get_distance_review_state(ids["B"]["animal"], "alice")["samples"] == []


def test_relocation_retains_root_scope_and_prepared_reference_ids(tmp_db, mock_probe):
    dp, project, ids, _ = _multi_root_deployment(tmp_db, mock_probe)
    original = dp.get_distance_reference_videos(ids["A"]["animal"])[0]
    frame = dp.add_reference_frame(original["id"], 0, 5)
    directory = next(
        row for row in dp.get_project_dirs(project.id) if row.path.endswith("deployment_A")
    )
    dp.update_project_dir(directory.id, str(tmp_db["root"] / "relocated_A"))
    references_a = dp.get_distance_reference_videos(ids["A"]["animal"])
    references_b = dp.get_distance_reference_videos(ids["B"]["animal"])
    assert [row["id"] for row in references_a] == [original["id"]]
    assert [row["source_video_id"] for row in references_b] == [ids["B"]["source"]]
    assert dp.list_reference_frames(original["id"]) == [frame]


def test_handoff_includes_pending_calibration_from_the_video_root(tmp_db, mock_probe):
    from review_app.backend.annotation_package.archive import decode_archive
    from review_app.backend.provider.assignment_handoff import AssignmentRequest

    dp, project, ids, _ = _multi_root_deployment(tmp_db, mock_probe)
    path = tmp_db["root"] / "deployment_A" / "unscanned-reference.mp4"
    path.touch()
    reference = dp.register_reference_video(project.id, "cam_1", str(path))
    frame = dp.add_reference_frame(reference["id"], 1000, 5.5)
    with dp.Session.begin() as session:
        row = session.get(ReferenceVideo, reference["id"])
        row.deployment_id = None
        row.unresolved_camera_label = "cam_1"

    prepared = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", (ids["A"]["animal"],), "distance", "deer")
    )
    _, context, _ = decode_archive(dp.export_assignment(prepared.id))
    assert frame["id"] in {row["id"] for row in context["distance"]["frames"]}


def test_cross_root_deployment_round_trips_reference_evidence(tmp_db, mock_probe):
    from review_app.backend.annotation_package.archive import decode_archive, encode_archive
    from review_app.backend.provider.assignment_handoff import AssignmentRequest

    dp, project, ids, species_id = _multi_root_deployment(tmp_db, mock_probe)
    animal = ids["A"]["animal"]
    across_roots = ids["B"]["source"]
    original_scope = {row["video_id"]: row for row in dp.get_distance_video_queue(project.id)}
    assert {row["camera_id"] for row in original_scope.values()} == {"cam_1", "cam_1 [2]"}
    destination = original_scope[animal]["deployment_id"]
    other_animal = ids["B"]["animal"]
    dp.apply_deployment_assignment(
        dp.preview_deployment_assignment(
            project.id, [other_animal], original_scope[other_animal]["deployment_id"]
        )
    )
    dp.apply_deployment_assignment(
        dp.preview_deployment_assignment(project.id, [across_roots], destination)
    )
    dp.sync_videos(active_project_id=project.id)
    source = next(
        row
        for row in dp.get_distance_reference_videos(animal)
        if row["source_video_id"] == across_roots
    )
    assert source["deployment_id"] == destination
    frame = dp.add_reference_frame(source["id"], 1000, 5.5, playback_offset_ms=1000.25)
    individual = dp.create_distance_individual(
        animal,
        species_id,
        0,
        anchor={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
    )
    dp.verify_distance_recording_time(animal, datetime(2024, 1, 1), "alice")
    dp.save_distance_sample(
        individual["id"], 0, reference_frame_id=frame["id"], labeled_by="alice"
    )
    task = original_scope[animal]["task_id"]
    assert frame["id"].encode() in dp.export_distance_measurements(project.id, [task])
    with pytest.raises(ValueError, match="deployment"):
        dp.export_distance_annotations(project.id, [task])

    prepared = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", (animal, other_animal), "distance", "deer")
    )
    package = dp.export_assignment(prepared.id)
    manifest, context, work = decode_archive(package)
    legacy_context = {key: value for key, value in context.items() if key != "deployments"}
    with pytest.raises(ValueError, match="another deployment"):
        encode_archive({**manifest, "version": 1}, legacy_context, work)
    opened = dp.open_assignment(
        package, "Working copy", {row.id: row.path for row in dp.get_project_dirs(project.id)}
    )
    working_task = next(
        row
        for row in dp.get_distance_video_queue(opened.project_id)
        if row["camera_id"] == original_scope[animal]["camera_id"]
    )
    working_individual = dp.list_distance_individuals(working_task["task_id"])[0]
    working_reference = next(
        row
        for row in dp.get_distance_reference_videos(working_task["video_id"])
        if "deployment_B" in row["video_path"]
    )
    working_frame = dp.list_reference_frames(working_reference["id"])[0]
    dp.save_distance_sample(
        working_individual["id"], 2000, reference_frame_id=working_frame["id"], labeled_by="alice"
    )
    returned = dp.export_assignment_return(opened.project_id)
    preview = dp.preview_assignment_return(project.id, returned)
    assert preview.totals["replaced"] == 1

    ret_manifest, ret_context, ret_work = decode_archive(returned)
    reference_id = next(
        row["reference_video_id"]
        for row in ret_context["distance"]["frames"]
        if row["id"] == frame["id"]
    )
    next(row for row in ret_context["distance"]["references"] if row["id"] == reference_id)[
        "video_id"
    ] = other_animal
    forged = encode_archive(ret_manifest, ret_context, ret_work)
    with pytest.raises(ValueError, match="another deployment"):
        dp.preview_assignment_return(project.id, forged)
    with pytest.raises(ValueError, match="another deployment"):
        dp.accept_assignment_return(project.id, forged, "use_imported", preview.token)

    dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    samples = dp.get_distance_review_state(task, "alice")["samples"]
    assert {
        (row["offset_ms"], row["reference_frame_id"], row["distance_m"]) for row in samples
    } == {(0, frame["id"], 5.5), (2000, frame["id"], 5.5)}
    assert dp.list_reference_frames(source["id"]) == [frame]
