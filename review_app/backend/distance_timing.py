"""Camera-clock sampling."""

import math
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

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


def imported_sample_positions(times_s: list[float], duration_sec: float) -> dict[int, float]:
    """Retain authoritative source-frame positions behind integer-ms sample IDs."""
    if not isinstance(times_s, list) or not times_s:
        raise ValueError("An imported schedule must contain at least one timestamp")
    positions = {}
    previous = -1.0
    for time in times_s:
        if (
            isinstance(time, bool)
            or not isinstance(time, (int, float))
            or not math.isfinite(time)
            or not 0 <= time < duration_sec
            or time <= previous
        ):
            raise ValueError("Imported timestamps must be sorted, unique and within the video")
        precise = time * 1000
        if not math.isfinite(precise) or precise >= 2**63 - 1:
            raise ValueError("Imported timestamp exceeds the supported playback range")
        offset = math.floor(precise + 0.5)
        if offset in positions:
            raise ValueError("Distinct imported timestamps must not share a millisecond sample ID")
        positions[offset] = precise
        previous = time
    return positions


def infer_imported_schedule(
    tracks: list[list[float]], duration_sec: float
) -> tuple[list[float], float]:
    """Extend the modal AI cadence without moving any supplied prediction frame.

    Half-interval matching treats frame-quantization jitter as an existing stop,
    rather than inventing a nearby Absent stop. The opening frame is always included.
    Cadence is inferred within tracks, not from interleaved animals' timestamps.
    """
    if duration_sec is None or not math.isfinite(duration_sec) or duration_sec <= 0:
        raise ValueError("A known positive video duration is required")
    times = sorted({time for track in tracks for time in track})
    imported_sample_positions(times, duration_sec)
    intervals = defaultdict(list)
    for track in tracks:
        ordered = sorted(set(track))
        for previous, current in zip(ordered, ordered[1:]):
            delta = current - previous
            intervals[round(delta, 6)].append(delta)
    if not intervals:
        raise ValueError(
            "Cannot infer AI cadence: a video needs at least two predictions on one track"
        )
    interval_key = min(intervals, key=lambda key: (-len(intervals[key]), key))
    cadence = round(median(intervals[interval_key]), 9)
    if cadence < 0.001 or duration_sec / cadence > 100_000:
        raise ValueError("Inferred AI cadence would create too many review samples")
    phases = defaultdict(list)
    for time in times:
        if time == 0:
            continue
        phase = time % cadence
        key = round(phase, 6)
        if math.isclose(key, cadence, rel_tol=0, abs_tol=0.000001):
            key, phase = 0, 0
        phases[key].append(phase)
    phase_key = min(phases, key=lambda key: (-len(phases[key]), key))
    phase = median(phases[phase_key])
    original = sorted({0.0, *times})
    result = set(original)
    for step in range(math.ceil(duration_sec / cadence) + 1):
        candidate = round(phase + step * cadence, 9)
        if candidate >= duration_sec:
            break
        index = bisect_left(original, candidate)
        neighbors = original[max(0, index - 1) : index + 1]
        if all(abs(time - candidate) >= cadence / 2 - 1e-9 for time in neighbors):
            result.add(candidate)
    schedule = sorted(result)
    imported_sample_positions(schedule, duration_sec)
    return schedule, cadence


def sample_offsets(
    recorded_start: datetime | None,
    duration_sec: float,
    *,
    clock_anchor_offset_ms: int | None = None,
    opening_even_second: bool = False,
    alignment_mode: str = "exact",
    clock_anchor_playback_ms: float | None = None,
    imported_sample_times_s: list[float] | None = None,
) -> list[int]:
    """Manual clock grids or authoritative imported source frames within [0, duration)."""
    if alignment_mode not in ("exact", "simplified", "imported"):
        raise ValueError("Choose exact, simplified or imported alignment")
    if duration_sec is None or not math.isfinite(duration_sec) or duration_sec <= 0:
        raise ValueError("A known positive video duration is required")
    if alignment_mode == "imported":
        return list(imported_sample_positions(imported_sample_times_s, duration_sec))
    if not isinstance(opening_even_second, bool):
        raise ValueError("Check whether the opening timestamp is even")
    if alignment_mode == "simplified":
        if clock_anchor_offset_ms is None:
            # Keep existing parity-only reviews readable without reinterpreting evidence.
            if recorded_start is not None or clock_anchor_playback_ms is not None:
                raise ValueError("Select an anchor frame before setting clock alignment")
            return list(
                range(0 if opening_even_second else 1000, math.ceil(duration_sec * 1000), 2000)
            )
        if (
            isinstance(clock_anchor_offset_ms, bool)
            or not isinstance(clock_anchor_offset_ms, int)
            or not 0 <= clock_anchor_offset_ms < duration_sec * 1000
            or isinstance(clock_anchor_playback_ms, bool)
            or not isinstance(clock_anchor_playback_ms, (int, float))
            or not math.isfinite(clock_anchor_playback_ms)
            or not 0 <= clock_anchor_playback_ms < duration_sec * 1000
            or math.floor(clock_anchor_playback_ms) != clock_anchor_offset_ms
        ):
            raise ValueError("Select an anchor frame within the video")
        if opening_even_second:
            raise ValueError("Anchored simplified alignment uses only its two-second grid")
        if recorded_start is not None:
            # Validate an explicitly confirmed/corrected time against the even anchor.
            sample_offsets(
                recorded_start, duration_sec, clock_anchor_offset_ms=clock_anchor_offset_ms
            )
        phase = clock_anchor_playback_ms - clock_anchor_offset_ms
        return list(
            range(
                clock_anchor_offset_ms % 2000,
                math.ceil(duration_sec * 1000 - phase),
                2000,
            )
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
