"""Deleting reference mappings explicitly clears only their saved sample evidence."""

from datetime import datetime

import pytest
from review_app.backend.db.models import DistanceSampleBehavior


def test_delete_unused_mapping_and_reconfigure(distance_data):
    dp, frame = distance_data["dp"], distance_data["frame"]
    assert dp.count_reference_frame_samples(frame["id"]) == 0
    assert dp.delete_reference_frame(frame["id"]) == 0
    assert dp.list_reference_frames(frame["reference_video_id"]) == []
    replacement = dp.configure_reference_distance(frame["reference_video_id"], 3500, 5)
    assert replacement["id"] != frame["id"]
    assert replacement["distance_m"] == 5
    with pytest.raises(ValueError, match="Reference frame not found"):
        dp.delete_reference_frame("missing", clear_samples=True)


def test_used_mapping_requires_confirmation(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.save_distance_sample(
        data["individual"]["id"], 1000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    before = dp.get_distance_review_state(video_id, "alice")
    assert dp.count_reference_frame_samples(data["frame"]["id"]) == 1
    with pytest.raises(ValueError, match="Confirm deletion"):
        dp.delete_reference_frame(data["frame"]["id"])
    assert dp.get_distance_review_state(video_id, "alice") == before
    assert dp.list_reference_frames(data["reference"]["id"]) == [data["frame"]]


def test_delete_mapping_clears_shared_evidence_and_reopens_affected_reviews(distance_data):
    data, dp = distance_data, distance_data["dp"]
    frame_id = data["frame"]["id"]
    other = dp.add_reference_frame(data["reference"]["id"], 1000, 6)
    affected_individual_ids = set()
    for index, video_id in enumerate(data["videos"]):
        dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
        individual = (
            data["individual"]
            if index == 0
            else dp.create_distance_individual(
                video_id, data["species_id"], 500, anchor=data["anchor"], labeled_by="alice"
            )
        )
        dp.set_distance_individual_attributes(
            individual["id"], age="Adult", sex="Unknown", tag_ids=[], labeled_by="alice"
        )
        affected_individual_ids.add(individual["id"])
        dp.apply_distance_to_end(
            individual["id"], 1000, reference_frame_id=other["id"], labeled_by="alice"
        )
        saved = dp.save_distance_sample(
            individual["id"],
            1000,
            reference_frame_id=frame_id,
            behavior_ids=[data["behavior_id"]],
            create_infant=index == 0,
            labeled_by="alice",
        )
        if index == 0:
            affected_individual_ids.add(saved["infant_id"])
            dp.apply_distance_to_end(
                saved["infant_id"], 3000, reference_frame_id=other["id"], labeled_by="alice"
            )
        dp.finish_distance_video(video_id, "alice")
    before = {vid: dp.get_distance_review_state(vid, "alice") for vid in data["videos"]}
    deleted_ids = {
        sample["id"]
        for state in before.values()
        for sample in state["samples"]
        if sample["reference_frame_id"] == frame_id
    }
    assert len(deleted_ids) == 3  # Adult and carried infant, plus an adult in another video.
    assert dp.count_reference_frame_samples(frame_id) == 3
    assert dp.count_reference_frame_samples(other["id"]) == sum(
        sample["reference_frame_id"] == other["id"]
        for state in before.values()
        for sample in state["samples"]
    )
    assert dp.delete_reference_frame(frame_id, clear_samples=True) == 3
    for video_id in data["videos"]:
        after = dp.get_distance_review_state(video_id, "alice")
        assert after["samples"] == [
            sample for sample in before[video_id]["samples"] if sample["id"] not in deleted_ids
        ]
        assert after["review"]["completed_at"] is None
        assert after["review"]["completed_by"] is None
        for key, value in before[video_id]["review"].items():
            if key not in {"completed_at", "completed_by"}:
                assert after["review"][key] == value
        individuals = dp.list_distance_individuals(video_id)
        assert {row["id"] for row in individuals}.issubset(affected_individual_ids)
        assert all(row["review_status"] == "in_progress" for row in individuals)
        assert all(row["completed_at"] is None for row in individuals)
    with dp.Session() as session:
        assert (
            session.query(DistanceSampleBehavior)
            .filter(DistanceSampleBehavior.sample_id.in_(deleted_ids))
            .count()
            == 0
        )
    assert dp.list_reference_frames(data["reference"]["id"]) == [other]
    assert dp.list_reference_videos(data["project"].id, "cam_x") == [data["reference"]]
