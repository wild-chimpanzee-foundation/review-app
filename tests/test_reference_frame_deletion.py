"""Retired references preserve measurements, carrying links, and review completion."""

from datetime import datetime

import pytest
from review_app.backend.db.models import DistanceSampleBehavior


def test_delete_unused_mapping_and_reconfigure(distance_data):
    dp, frame = distance_data["dp"], distance_data["frame"]
    assert dp.count_reference_frame_samples(frame["id"]) == 0
    assert dp.delete_reference_frame(frame["id"]) == 0
    assert dp.list_reference_frames(frame["reference_video_id"]) == []
    replacement = dp.configure_reference_distance(frame["reference_video_id"], 3500, 5)
    assert replacement["id"] == frame["id"]
    assert replacement["distance_m"] == 5
    with pytest.raises(ValueError, match="Reference frame not found"):
        dp.delete_reference_frame("missing")


def test_delete_used_reference_without_alternative_keeps_saved_distance(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    frame_id = data["frame"]["id"]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.save_distance_sample(
        data["individual"]["id"], 1000, reference_frame_id=frame_id, labeled_by="alice"
    )
    other = dp.add_reference_frame(data["reference"]["id"], 1000, 6)
    before = dp.get_distance_review_state(video_id, "alice")
    dp.update_reference_frame_position(frame_id, 3600)
    assert dp.get_distance_review_state(video_id, "alice") == before
    assert dp.delete_reference_frame(frame_id) == 1
    after = dp.get_distance_review_state(video_id, "alice")
    assert after["samples"] == [{**before["samples"][0], "display_reference_frame_id": None}]
    assert dp.list_reference_frames(data["reference"]["id"]) == [other]
    with pytest.raises(ValueError, match="Select a prepared reference frame"):
        dp.save_distance_sample(
            data["individual"]["id"], 3000, reference_frame_id=frame_id, labeled_by="alice"
        )


def test_delete_mapping_preserves_completed_reviews_and_shows_alternative(distance_data):
    data, dp = distance_data, distance_data["dp"]
    frame_id = data["frame"]["id"]
    other = dp.add_reference_frame(data["reference"]["id"], 1000, 6)
    alternative = dp.add_reference_frame(data["reference"]["id"], 2000, 5)
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
    affected_sample_ids = {
        sample["id"]
        for state in before.values()
        for sample in state["samples"]
        if sample["reference_frame_id"] == frame_id
    }
    assert (
        len(affected_sample_ids) == 3
    )  # Adult and carried infant, plus an adult in another video.
    assert dp.count_reference_frame_samples(frame_id) == 3
    assert dp.count_reference_frame_samples(other["id"]) == sum(
        sample["reference_frame_id"] == other["id"]
        for state in before.values()
        for sample in state["samples"]
    )
    assert dp.delete_reference_frame(frame_id) == 3
    for video_id in data["videos"]:
        after = dp.get_distance_review_state(video_id, "alice")
        assert after["samples"] == [
            {**sample, "display_reference_frame_id": alternative["id"]}
            if sample["id"] in affected_sample_ids
            else sample
            for sample in before[video_id]["samples"]
        ]
        assert after["review"] == before[video_id]["review"]
        assert after["review"]["completed_at"] is not None
        individuals = dp.list_distance_individuals(video_id)
        assert {row["id"] for row in individuals}.issubset(affected_individual_ids)
        assert all(row["review_status"] == "completed" for row in individuals)
    with dp.Session() as session:
        assert (
            session.query(DistanceSampleBehavior)
            .filter(DistanceSampleBehavior.sample_id.in_(affected_sample_ids))
            .count()
            == 3
        )
    assert dp.list_reference_frames(data["reference"]["id"]) == [other, alternative]
    assert dp.list_reference_videos(data["project"].id, "cam_x") == [data["reference"]]
