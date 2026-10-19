"""Timestamp-optional even-frame anchors schedule samples in both directions."""

from datetime import datetime, timedelta

import pytest
from review_app.backend.distance_timing import sample_camera_time, sample_offsets


@pytest.mark.parametrize("camera_time", [None, datetime(2026, 10, 12, 16, 2, 50)])
def test_midvideo_anchor_schedules_both_directions_and_reconfirms_without_reset(
    distance_data, camera_time
):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.align_distance_on_even_frame(video_id, 5120.125, "alice", camera_time=camera_time)
    state = dp.get_distance_review_state(video_id, "alice")
    assert state["offsets"] == [1120, 3120, 5120, 7120, 9120]
    review = state["review"]
    assert review["clock_anchor_playback_ms"] == 5120.125
    assert review["clock_anchor_offset_ms"] == 5120
    for offset in state["offsets"]:
        assert sample_camera_time(review, offset) == (
            camera_time + timedelta(milliseconds=offset - 5120) if camera_time else None
        )
    dp.apply_distance_to_end(
        data["individual"]["id"], 1120, skip_reason="individual absent", labeled_by="alice"
    )
    dp.set_distance_active_individual(video_id, "alice", data["individual"]["id"])
    dp.set_distance_individual_attributes(
        data["individual"]["id"], age="Unknown", sex="Unknown", tag_ids=[]
    )
    dp.finish_distance_video(video_id, "alice")
    before = dp.get_distance_review_state(video_id, "alice")
    dp.align_distance_on_even_frame(video_id, 5120.125, "bob", camera_time=camera_time)
    after = dp.get_distance_review_state(video_id, "alice")
    assert after["samples"] == before["samples"]
    assert after["active_individual_id"] == before["active_individual_id"]
    assert after["review"]["completed_at"] == before["review"]["completed_at"]


def test_optional_timestamp_confirmation_and_correction_preserve_evidence(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.align_distance_on_even_frame(video_id, 5120.125, "alice")
    dp.apply_distance_to_end(
        data["individual"]["id"], 1120, skip_reason="individual absent", labeled_by="alice"
    )
    dp.set_distance_individual_attributes(
        data["individual"]["id"], age="Unknown", sex="Unknown", tag_ids=[]
    )
    dp.finish_distance_video(video_id, "alice")
    before = dp.get_distance_review_state(video_id, "alice")
    for camera_time in (datetime(2026, 10, 12, 16, 2, 50), datetime(2026, 10, 12, 16, 2, 52)):
        dp.align_distance_on_even_frame(video_id, 5120.125, "bob", camera_time=camera_time)
        after = dp.get_distance_review_state(video_id, "alice")
        assert after["offsets"] == before["offsets"]
        assert after["samples"] == before["samples"]
        assert after["review"]["completed_at"] == before["review"]["completed_at"]
        assert sample_camera_time(after["review"], 5120) == camera_time


@pytest.mark.parametrize("offset", [5120.5, 6120.125])
def test_changing_selected_frame_requires_explicit_reset(distance_data, offset):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.align_distance_on_even_frame(video_id, 5120.125, "alice")
    before = dp.get_distance_review_state(video_id, "alice")
    with pytest.raises(ValueError, match="requires resetting"):
        dp.align_distance_on_even_frame(video_id, offset, "bob")
    assert dp.get_distance_review_state(video_id, "alice") == before
    dp.align_distance_on_even_frame(video_id, offset, "bob", reset_tracks=True)
    assert dp.list_distance_individuals(video_id) == []
    assert (
        dp.get_distance_review_state(video_id, "alice")["review"]["clock_anchor_playback_ms"]
        == offset
    )


@pytest.mark.parametrize("duration,expected", [(4.0001, [0, 2000]), (4.0002, [0, 2000, 4000])])
def test_precise_anchor_phase_excludes_samples_beyond_the_endpoint(duration, expected):
    assert (
        sample_offsets(
            None,
            duration,
            alignment_mode="simplified",
            clock_anchor_offset_ms=2000,
            clock_anchor_playback_ms=2000.125,
        )
        == expected
    )


@pytest.mark.parametrize(
    "offset,camera_time",
    [(float("nan"), None), (10000, None), (5000, datetime(2026, 10, 12, 16, 2, 51))],
)
def test_invalid_even_frame_alignment_does_not_reset_existing_work(
    distance_data, offset, camera_time
):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.align_distance_on_even_frame(video_id, 5120.125, "alice")
    before = dp.get_distance_review_state(video_id, "alice")
    with pytest.raises(ValueError):
        dp.align_distance_on_even_frame(
            video_id, offset, "bob", camera_time=camera_time, reset_tracks=True
        )
    assert dp.get_distance_review_state(video_id, "alice") == before
    assert dp.list_distance_individuals(video_id)
