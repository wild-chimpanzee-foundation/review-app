"""Full-video AI draft schedules keep exact source frames."""

import pytest
from review_app.backend.distance_timing import infer_imported_schedule


@pytest.mark.parametrize(
    "tracks,duration,expected",
    [
        (
            [[1.250000001, 2.250000001, 4.250000001]],
            7,
            [0, 1.250000001, 2.250000001, 3.250000001, 4.250000001, 5.250000001, 6.250000001],
        ),
        (
            [[0, 1.25, 2.25, 4.208333333, 5.25]],
            8,
            [0, 1.25, 2.25, 3.25, 4.208333333, 5.25, 6.25, 7.25],
        ),
        ([[0, 0.5, 1.5, 2.5]], 5, [0, 0.5, 1.5, 2.5, 3.5, 4.5]),
        (
            [[1.25, 2.25, 3.25], [1.75, 2.75, 3.75]],
            5,
            [0, 1.25, 1.75, 2.25, 2.75, 3.25, 3.75, 4.25],
        ),
    ],
)
def test_inferred_cadence_fills_gaps_and_boundaries_without_snapping(tracks, duration, expected):
    schedule, cadence = infer_imported_schedule(tracks, duration)
    assert cadence == 1
    assert schedule == expected
    assert {time for track in tracks for time in track}.issubset(schedule)


def test_single_prediction_cannot_silently_invent_cadence():
    with pytest.raises(ValueError, match="Cannot infer AI cadence"):
        infer_imported_schedule([[1.25]], 10)
