from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from review_app.backend.video_tools import find_video_tool, video_tool_env

TRANSCODE_CACHE_VERSION = 2


@dataclass(frozen=True)
class TranscodedVideoValidation:
    valid: bool
    error: str | None = None


@dataclass(frozen=True)
class TranscodedCacheStatus:
    exists: bool
    current: bool
    validation: TranscodedVideoValidation | None = None

    @property
    def usable(self) -> bool:
        return self.current and self.validation is not None and self.validation.valid


@dataclass(frozen=True)
class TranscodeResult:
    success: bool
    error: str | None = None
    new_path: str | None = None


def transcoded_cache_filename(video_id: str) -> str:
    safe_id = video_id.replace("/", "_").replace("\\", "_").replace(":", "_")
    return f"{safe_id}.v{TRANSCODE_CACHE_VERSION}.mp4"


def inspect_transcoded_cache(
    path: str | Path | None, *, validate_contents: bool = True
) -> TranscodedCacheStatus:
    """Inspect cache presence and version, optionally probing browser compatibility."""
    if not path:
        return TranscodedCacheStatus(exists=False, current=False)
    candidate = Path(path)
    if not candidate.exists():
        return TranscodedCacheStatus(exists=False, current=False)
    if not candidate.name.endswith(f".v{TRANSCODE_CACHE_VERSION}.mp4"):
        return TranscodedCacheStatus(exists=True, current=False)
    if not validate_contents:
        return TranscodedCacheStatus(exists=True, current=True)
    return TranscodedCacheStatus(
        exists=True,
        current=True,
        validation=validate_transcoded_video(candidate),
    )


def validate_transcoded_video(path: str | Path) -> TranscodedVideoValidation:
    """Validate browser compatibility, caching the probe result by file identity."""
    candidate = Path(path)
    try:
        stat = candidate.stat()
    except OSError as exc:
        return TranscodedVideoValidation(False, f"cannot read transcoded file: {exc}")
    return _validate_transcoded_video_cached(str(candidate), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=512)
def _validate_transcoded_video_cached(
    path: str, _mtime_ns: int, _size: int
) -> TranscodedVideoValidation:
    ffprobe = find_video_tool("ffprobe")
    if ffprobe is None:
        return TranscodedVideoValidation(False, "ffprobe executable not found")
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:stream=codec_type,codec_name,profile,pix_fmt,duration",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=video_tool_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return TranscodedVideoValidation(False, f"ffprobe failed: {exc}")
    if result.returncode != 0:
        return TranscodedVideoValidation(
            False, result.stderr.strip()[:200] or "ffprobe returned a non-zero exit code"
        )
    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        formats = {
            value.strip().lower()
            for value in data.get("format", {}).get("format_name", "").split(",")
        }
        duration = float(data.get("format", {}).get("duration") or 0)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return TranscodedVideoValidation(False, f"invalid ffprobe output: {exc}")

    if "mp4" not in formats:
        return TranscodedVideoValidation(False, "output is not an MP4 container")
    if duration <= 0:
        return TranscodedVideoValidation(False, "output has no positive duration")
    if len(video_streams) != 1:
        return TranscodedVideoValidation(False, "output must contain exactly one video stream")
    video = video_streams[0]
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        return TranscodedVideoValidation(False, "output video must be H.264 yuv420p")
    if any(
        stream.get("codec_name") != "aac" or stream.get("profile") != "LC"
        for stream in audio_streams
    ):
        return TranscodedVideoValidation(False, "output audio must be AAC-LC")
    return TranscodedVideoValidation(True)
