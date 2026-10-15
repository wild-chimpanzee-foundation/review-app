from datetime import datetime

import pytest
from review_app.backend.db.models import DistanceVideoReview, Video


@pytest.mark.parametrize("status", [None, "not_started", "in_progress", "completed"])
def test_missing_video_is_excluded_from_distance_queue_without_losing_evidence(
    distance_data, status
):
    data = distance_data
    dp, project = data["dp"], data["project"]
    video_id = data["videos"][0]

    if status in {"in_progress", "completed"}:
        with dp.Session.begin() as session:
            session.add(
                DistanceVideoReview(
                    video_id=video_id,
                    recorded_start=datetime(2026, 10, 12),
                    verified_by="alice",
                    completed_by="alice" if status == "completed" else None,
                    completed_at=datetime(2026, 10, 12) if status == "completed" else None,
                )
            )

    filters = {"species_id": data["species_id"], "camera_id": "cam_x", "status": status}
    assert video_id in {
        row["video_id"] for row in dp.get_distance_video_queue(project.id, **filters)
    }
    with dp.Session.begin() as session:
        session.get(Video, video_id).is_missing = True

    assert video_id not in dp.get_video_queue({}, active_project_id=project.id)
    assert all(
        row["video_id"] != video_id for row in dp.get_distance_video_queue(project.id, **filters)
    )
    # Queue filtering must not delete or rewrite saved distance evidence.
    assert dp.list_distance_individuals(video_id)[0]["id"] == data["individual"]["id"]

    with dp.Session.begin() as session:
        session.get(Video, video_id).is_missing = False
    assert video_id in {row["video_id"] for row in dp.get_distance_video_queue(project.id)}
