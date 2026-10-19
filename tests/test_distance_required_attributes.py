"""Explicit Unknown assessments and the Finish video attribute guard."""

from datetime import datetime

import pytest
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.mark.parametrize(
    "age,sex,missing",
    [(None, None, "age, sex"), (None, "Unknown", "age"), ("Unknown", None, "sex")],
)
def test_finish_requires_attributes_on_every_individual(distance_data, age, sex, missing):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.set_distance_individual_attributes(
        data["individual"]["id"], age="Adult", sex="Female", tag_ids=[]
    )
    second = dp.create_distance_individual(
        video_id, data["species_id"], 0, anchor=data["anchor"], age=age, sex=sex
    )
    # Unfinished attributes on another video must not block this video.
    dp.create_distance_individual(data["videos"][1], data["species_id"], 0, anchor=data["anchor"])
    for individual in dp.list_distance_individuals(video_id):
        for offset in dp.get_distance_review_state(video_id, "alice")["offsets"]:
            dp.save_distance_sample(
                individual["id"], offset, skip_reason="individual absent", labeled_by="alice"
            )
    before = dp.get_distance_review_state(video_id, "alice")
    with pytest.raises(ValueError, match=rf"#2 \({missing}\)"):
        dp.finish_distance_video(video_id, "alice")
    assert dp.get_distance_review_state(video_id, "alice") == before
    assert before["review"]["completed_at"] is None

    dp.set_distance_individual_attributes(second["id"], age="Unknown", sex="Unknown", tag_ids=[])
    dp.finish_distance_video(video_id, "alice")
    after = dp.get_distance_review_state(video_id, "alice")
    assert after["review"]["completed_at"] is not None
    assert after["samples"] == before["samples"]
    # Clearing a required attribute reopens the video without losing sample coverage.
    dp.set_distance_individual_attributes(second["id"], age=None, sex="Unknown", tag_ids=[])
    assert dp.get_distance_review_state(video_id, "alice")["review"]["completed_at"] is None


def test_unknown_age_creation_update_and_reopen(distance_data):
    data, dp = distance_data, distance_data["dp"]
    created = dp.create_distance_individual(
        data["videos"][0],
        data["species_id"],
        0,
        anchor=data["anchor"],
        age="Unknown",
        sex="Unknown",
    )
    assert created["age"] == "Unknown"
    assert data["individual"]["age"] is None
    dp.set_distance_individual_attributes(
        data["individual"]["id"], age="Unknown", sex="Unknown", tag_ids=[]
    )
    reopened = LocalDataProvider()
    try:
        rows = reopened.list_distance_individuals(data["videos"][0])
        assert all(row["age"] == "Unknown" and row["sex"] == "Unknown" for row in rows)
    finally:
        reopened.engine.dispose()
