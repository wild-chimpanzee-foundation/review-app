"""Camera-clock sampling."""

import math
from datetime import datetime, timedelta

SAMPLE_STOP_TOLERANCE_MS = 250


def sample_frame_time(
    offset_ms: int, frame_rate: float | None, duration_sec: float | None = None
) -> float:
    """Approximate the next frame from stream metadata without decoding the clip."""
    if offset_ms == 0:
        return 0.0
    boundary = offset_ms / 1000
    if frame_rate is not None and math.isfinite(frame_rate) and frame_rate > 0:
        target = max(boundary, math.ceil(boundary * frame_rate - 1e-9) / frame_rate)
    else:
        # A short forward margin keeps annotation usable when FPS is unavailable.
        target = boundary + 0.04
    if duration_sec is not None and boundary < duration_sec <= target:
        target = boundary + (duration_sec - boundary) / 2
    return target


def sample_offsets(
    recorded_start: datetime | None,
    duration_sec: float,
    *,
    clock_anchor_offset_ms: int | None = None,
    opening_even_second: bool = False,
    alignment_mode: str = "exact",
) -> list[int]:
    """Even camera seconds within [0, duration), including fractional start times."""
    if alignment_mode not in ("exact", "simplified"):
        raise ValueError("Choose exact or simplified alignment")
    if duration_sec is None or not math.isfinite(duration_sec) or duration_sec <= 0:
        raise ValueError("A known positive video duration is required")
    if not isinstance(opening_even_second, bool):
        raise ValueError("Check whether the opening timestamp is even")
    if alignment_mode == "simplified":
        if recorded_start is not None or clock_anchor_offset_ms is not None:
            raise ValueError("Simplified alignment has no camera timestamp or anchor frame")
        return list(
            range(0 if opening_even_second else 1000, math.ceil(duration_sec * 1000), 2000)
        )
    if not isinstance(recorded_start, datetime) or recorded_start.tzinfo is not None:
        raise ValueError("Use the camera's local recording time without a timezone")
    if recorded_start.microsecond % 1000:
        raise ValueError("Recording time precision must not exceed milliseconds")
    if clock_anchor_offset_ms is not None:
        if (
            isinstance(clock_anchor_offset_ms, bool)
            or not isinstance(clock_anchor_offset_ms, int)
            or not 0 <= clock_anchor_offset_ms < duration_sec * 1000
        ):
            raise ValueError("Select an anchor frame within the video")
        anchor_time = recorded_start + timedelta(milliseconds=clock_anchor_offset_ms)
        if anchor_time.second % 2 or anchor_time.microsecond:
            raise ValueError("The anchor timestamp must be an even whole second")
        offsets = list(range(clock_anchor_offset_ms, math.ceil(duration_sec * 1000), 2000))
        if opening_even_second and clock_anchor_offset_ms:
            offsets.insert(0, 0)
        return offsets
    phase = (recorded_start.second % 2) * 1000 + recorded_start.microsecond // 1000
    first = (-phase) % 2000
    return list(range(first, math.ceil(duration_sec * 1000), 2000))


def sample_camera_time(review: dict, offset_ms: int) -> datetime | None:
    """Label the opening exception with its visible even second, then the anchor grid."""
    if review.get("recorded_start") is None:
        return None
    time = review["recorded_start"] + timedelta(milliseconds=offset_ms)
    anchor = review.get("clock_anchor_offset_ms")
    if offset_ms == 0 and anchor and review.get("opening_even_second"):
        return review["recorded_start"] + timedelta(milliseconds=anchor, seconds=-2)
    return time
