from __future__ import annotations


def frame_time(offset_ms: int | float) -> str:
    minutes, seconds = divmod(offset_ms / 1000, 60)
    return f"{int(minutes):02d}:{seconds:06.3f}"


def reference_frame_offset(frame: dict) -> int | float:
    """Use precise positions for new frames, retaining legacy integer offsets."""
    playback = frame.get("playback_offset_ms")
    return frame["offset_ms"] if playback is None else playback
