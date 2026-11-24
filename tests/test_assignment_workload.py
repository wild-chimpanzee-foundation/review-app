"""Assigned-footage workload summaries use only known, frozen metadata."""

from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import Deployment, Video
from review_app.backend.provider.assignment_handoff import summarize_workload


def test_workload_counts_only_known_cameras_and_durations():
    workload = summarize_workload(
        [
            {"camera_id": "cam-a", "duration_sec": 3600},
            {"camera_id": "cam-a", "duration_sec": None},
            {"camera_id": None, "duration_sec": 1800},
        ]
    )
    assert workload == {
        "video_count": 3,
        "camera_ids": ["cam-a"],
        "camera_count": 1,
        "hours": 1.5,
        "duration_count": 2,
    }


def test_saved_workload_stays_frozen_after_redistribution(provider_with_project):
    dp, project, _ = provider_with_project
    video_ids = dp.get_assignment_video_ids(project.id)
    with dp.Session.begin() as session:
        cameras = [
            Deployment(project_id=project.id, deployment_id=label)
            for label in ("camera-a", "camera-b")
        ]
        session.add_all(cameras)
        session.flush()
        first, second = (session.get(Video, video_id) for video_id in video_ids)
        first.deployment_id, first.duration_sec = cameras[0].id, 3600
        second.deployment_id, second.duration_sec = cameras[1].id, None
    assignment = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(video_ids)))

    with dp.Session.begin() as session:
        first = session.get(Video, video_ids[0])
        first.deployment_id, first.duration_sec = None, 7200
    dp.add_annotator("bob")
    dp.apply_chunk_assignment(
        project.id,
        {"bob": ["all"]},
        [{"chunk_id": "all", "video_ids": video_ids}],
    )

    (binding,) = dp.list_assignments(project.id)
    assert binding["id"] == assignment.id
    assert binding["camera_ids"] == ["camera-a", "camera-b"]
    assert binding["hours"] == 1.0
    assert binding["duration_count"] == 1
    media = dp.get_assignment_media(project.id, assignment.id)
    assert [row["duration_sec"] for row in media] == [3600, None]
    assert all(row["assigned"] for row in media)
