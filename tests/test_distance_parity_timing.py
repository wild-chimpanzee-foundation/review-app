"""Parity-only schedules preserve saved evidence when alignment is invalid."""

import pytest
from review_app.backend.distance_timing import sample_offsets


@pytest.mark.parametrize(
    "even,duration,expected",
    [
        (True, 0.01, [0]),
        (True, 4, [0, 2000]),
        (False, 1, []),
        (False, 1.001, [1000]),
        (False, 5, [1000, 3000]),
    ],
)
def test_parity_schedule_excludes_the_video_endpoint(even, duration, expected):
    assert (
        sample_offsets(None, duration, alignment_mode="simplified", opening_even_second=even)
        == expected
    )


def test_short_odd_clip_cannot_reset_saved_evidence(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    with dp.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE videos SET duration_sec=1 WHERE video_id=?", (video_id,))
    dp.align_distance_by_parity(video_id, True, "alice")
    before = dp.get_distance_review_state(video_id, "alice")
    with pytest.raises(ValueError, match="No even camera second"):
        dp.align_distance_by_parity(video_id, False, "alice", reset_tracks=True)
    assert dp.get_distance_review_state(video_id, "alice") == before
    assert dp.list_distance_individuals(video_id)
