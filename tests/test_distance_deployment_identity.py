"""Regression coverage for equal camera labels under distinct ProjectDir roots."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from review_app.backend.camera_layout import camera_id_for_path
from review_app.backend.db.models import Video
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
                        camera_id="cam_1",
                        video_path=str(animal_path),
                        duration_sec=10,
                    ),
                    Video(
                        video_id=reference_id,
                        project_id=project.id,
                        camera_id="cam_1",
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
                        camera_id=camera_id,
                        video_path=str(animal_path),
                        duration_sec=10,
                    ),
                    Video(
                        video_id=source_id,
                        project_id=project.id,
                        camera_id=camera_id_for_path(reference_path, root),
                        video_path=str(reference_path),
                        duration_sec=10,
                    ),
                ]
            )
        dp.set_video_tags(source_id, ["reference_video"])
        expected[camera] = {"animal": animal_id, "source": source_id}

    for camera in ("cam_1", "cam_2"):
        found = dp.get_distance_reference_videos(expected[camera]["animal"])
        assert [row["source_video_id"] for row in found] == [expected[camera]["source"]]


def test_reference_discovery_does_not_mix_distinct_project_roots(tmp_db, mock_probe):
    dp, _project, ids, _species_id = _multi_root_deployment(tmp_db, mock_probe)

    references_b = dp.get_distance_reference_videos(ids["B"]["animal"])

    assert [row["source_video_id"] for row in references_b] == [ids["B"]["source"]]


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
