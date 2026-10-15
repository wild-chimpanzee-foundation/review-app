"""New tracked animals are absent before their identifying frame, not unreviewed."""

from datetime import datetime

import pytest
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.mark.parametrize("first_seen", [0, 500, 1000, 1040, 5000, 5040, 9040])
@pytest.mark.parametrize("opening_second", [48, 49])
def test_creation_marks_only_samples_before_first_seen_absent(
    distance_data, first_seen, opening_second
):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.verify_distance_recording_time(
        video_id, datetime(2026, 10, 12, 16, 2, opening_second), "alice"
    )
    before = dp.get_distance_review_state(video_id, "alice")
    row = dp.create_distance_individual(
        video_id,
        data["species_id"],
        first_seen,
        anchor=data["anchor"],
        labeled_by="bob",
    )
    state = dp.get_distance_review_state(video_id, "alice")
    samples = [sample for sample in state["samples"] if sample["individual_id"] == row["id"]]
    assert [sample["offset_ms"] for sample in samples] == [
        offset for offset in before["offsets"] if offset < first_seen
    ]
    for sample in samples:
        assert sample["status"] == "skipped"
        assert sample["skip_reason"] == "individual absent"
        assert sample["reference_frame_id"] is None
        assert sample["distance_m"] is None
        assert sample["carried_by_individual_id"] is None
        assert sample["behavior_ids"] == []
        assert sample["labeled_by"] == "bob"
        assert sample["labeled_at"] == row["labeled_at"].replace(tzinfo=None)
    assert [
        sample for sample in state["samples"] if sample["individual_id"] != row["id"]
    ] == before["samples"]
    expected_status = (
        "completed"
        if len(samples) == len(before["offsets"])
        else "in_progress"
        if samples
        else "not_started"
    )
    assert row["review_status"] == expected_status
    assert state["review"]["completed_at"] is None

    reopened = LocalDataProvider()
    try:
        assert reopened.get_distance_review_state(video_id, "alice")["samples"] == state["samples"]
    finally:
        reopened.engine.dispose()


def test_automatically_absent_sample_can_be_corrected(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    row = dp.create_distance_individual(
        video_id, data["species_id"], 5000, anchor=data["anchor"], labeled_by="alice"
    )
    dp.save_distance_sample(
        row["id"], 1000, reference_frame_id=data["frame"]["id"], labeled_by="bob"
    )
    samples = {
        sample["offset_ms"]: sample
        for sample in dp.get_distance_review_state(video_id, "alice")["samples"]
        if sample["individual_id"] == row["id"]
    }
    assert samples[1000]["status"] == "annotated"
    assert samples[1000]["labeled_by"] == "bob"
    assert samples[3000]["skip_reason"] == "individual absent"
    assert 5000 not in samples
