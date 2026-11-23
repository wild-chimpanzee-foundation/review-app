"""Eligibility and camera-local time rules for selecting Distance tasks."""

from datetime import time

from review_app.backend.db.models import IndividualObservation, Video


def time_window(start, end):
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise ValueError("Provide both start and end times, or neither")
    try:
        start, end = time.fromisoformat(start), time.fromisoformat(end)
    except (ValueError, TypeError) as exc:
        raise ValueError("Use 24-hour start/end times, such as 18:00 and 20:00") from exc
    if start.tzinfo or end.tzinfo or start == end:
        raise ValueError("Time-of-day boundaries must be different local times")
    return start, end


def manual_presence(session, video_id, species_id):
    return (
        session.query(IndividualObservation.video_id)
        .filter(
            IndividualObservation.video_id == video_id,
            IndividualObservation.species_id == species_id,
            (IndividualObservation.count.is_(None)) | (IndividualObservation.count > 0),
        )
        .correlate(Video)
        .exists()
    )


def in_time_window(recorded_start, window):
    start, end = window
    value = recorded_start.time()
    return start <= value < end if start < end else value >= start or value < end
