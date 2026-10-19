from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd
from sqlalchemy import literal, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from review_app.app.config import VIDEO_EXTENSIONS
from review_app.backend.camera_layout import camera_id_for_path, is_reference_video_path
from review_app.backend.db.models import ProjectDir, Tag, Video, VideoTag
from review_app.backend.errors import VideoError
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.base import ProviderBase
from review_app.backend.transcoding import (
    TranscodeResult,
    inspect_transcoded_cache,
    transcoded_cache_filename,
    validate_transcoded_video,
)
from review_app.backend.video_tools import find_video_tool, video_tool_env

logger = logging.getLogger(__name__)

_FFPROBE_MAX_WORKERS: int = int(os.getenv("FFPROBE_MAX_WORKERS", "16"))
_FFPROBE_TIMEOUT_SEC: int = int(os.getenv("FFPROBE_TIMEOUT_SEC", "10"))


class ProbeResult(NamedTuple):
    """Outcome of probing a single video file with ffprobe."""

    duration_sec: float | None
    is_valid: bool
    is_web_safe: bool
    error: str | None
    created_at: datetime | None
    latitude: float | None
    longitude: float | None


def _parse_iso6709(s: str) -> tuple[float, float] | None:
    """Parse an ISO 6709 location string like '+37.4060-122.0782/' into (lat, lon)."""
    m = re.match(r"^([+-]\d+\.?\d*)([+-]\d+\.?\d*)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _probe_video(path: Path) -> ProbeResult:
    """Run ffprobe on *path* and return a :class:`ProbeResult`."""
    ffprobe = find_video_tool("ffprobe")
    if ffprobe is None:
        logger.error("ffprobe not found on PATH — video probing unavailable")
        return ProbeResult(None, False, False, "ffprobe executable not found", None, None, None)

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:format_tags:stream=duration,codec_name,codec_type",
        "-of",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT_SEC,
            env=video_tool_env(),
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe timed out after %ds on %s", _FFPROBE_TIMEOUT_SEC, path)
        return ProbeResult(
            None,
            False,
            False,
            f"ffprobe timed out after {_FFPROBE_TIMEOUT_SEC}s",
            None,
            None,
            None,
        )
    except OSError as exc:
        logger.error("ffprobe OS error on %s: %s", path, exc)
        return ProbeResult(None, False, False, f"ffprobe OS error: {exc}", None, None, None)

    if result.returncode != 0:
        stderr_text = result.stderr.strip()
        logger.warning("ffprobe non-zero exit for %s: %s", path, stderr_text[:200])
        return ProbeResult(
            None,
            False,
            False,
            stderr_text[:200] or "ffprobe returned non-zero exit code",
            None,
            None,
            None,
        )

    try:
        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        raw_duration = fmt.get("duration")
        if raw_duration is None:
            streams = data.get("streams", [])
            if streams:
                raw_duration = streams[0].get("duration")

        format_name = fmt.get("format_name", "").lower()
        streams = data.get("streams", [])
        video_codec = next(
            (s.get("codec_name", "") for s in streams if s.get("codec_type") == "video"), ""
        ).lower()

        safe_formats = {"mp4", "mov", "webm", "ogg"}
        safe_codecs = {"h264", "vp8", "vp9", "av1", "theora"}
        formats = {f.strip() for f in format_name.split(",")}
        is_web_safe = bool(formats & safe_formats) and video_codec in safe_codecs

        tags = fmt.get("tags", {})

        created_at: datetime | None = None
        for tag_key in ("creation_time", "com.apple.quicktime.creationdate"):
            raw_ts = tags.get(tag_key)
            if raw_ts:
                try:
                    ts = raw_ts.rstrip("Z")
                    if "+" not in ts and "T" in ts:
                        ts += "+00:00"
                    created_at = datetime.fromisoformat(ts)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    pass

        latitude: float | None = None
        longitude: float | None = None
        for tag_key in ("location", "com.apple.quicktime.location.ISO6709"):
            raw_loc = tags.get(tag_key)
            if raw_loc:
                parsed = _parse_iso6709(raw_loc)
                if parsed:
                    latitude, longitude = parsed
                    break

    except (json.JSONDecodeError, ValueError, TypeError):
        logger.warning("ffprobe returned unparseable JSON for %s", path)
        return ProbeResult(
            None, False, False, "ffprobe returned unparseable JSON", None, None, None
        )

    return ProbeResult(raw_duration, True, is_web_safe, None, created_at, latitude, longitude)


def _probe_frame_rate(path: Path, *, timeout_sec: float | None = None) -> float | None:
    """Read the video stream's frame rate for frame-by-frame preparation controls."""
    ffprobe = find_video_tool("ffprobe")
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate,r_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT_SEC if timeout_sec is None else timeout_sec,
            env=video_tool_env(),
        )
        if result.returncode:
            return None
        stream = next(iter(json.loads(result.stdout).get("streams", [])), {})
        for key in ("avg_frame_rate", "r_frame_rate"):
            try:
                rate = float(Fraction(stream.get(key, "0/1")))
            except (ValueError, TypeError, ZeroDivisionError):
                continue
            if math.isfinite(rate) and rate > 0:
                return rate
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        logger.warning("Could not read reference frame rate for %s", path)
    return None


def _probe_many(
    paths: list[Path],
    max_workers: int = _FFPROBE_MAX_WORKERS,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[Path, ProbeResult]:
    if not paths:
        return {}

    results: dict[Path, ProbeResult] = {}
    total = len(paths)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(paths))) as pool:
        future_to_path = {pool.submit(_probe_video, p): p for p in paths}
        for i, future in enumerate(as_completed(future_to_path)):
            path = future_to_path[future]
            try:
                results[path] = future.result()
            except Exception as exc:  # pragma: no cover
                logger.error("Unexpected error probing %s: %s", path, exc)
                results[path] = ProbeResult(None, False, False, str(exc), None, None, None)
            if progress_callback:
                progress_callback(i + 1, total, path.name)

    return results


class VideoMixin(ProviderBase):
    """Video scanning, probing, and transcoding. Requires self.engine, self.Session."""

    def _scan_videos(
        self, video_dir: Path | None = None, active_project_id: str | None = None
    ) -> pd.DataFrame:
        if video_dir is not None:
            scan_dirs = [video_dir]
        elif active_project_id:
            with self.Session() as s:
                scan_dirs = [
                    Path(normalize_path_str(d.path))
                    for d in s.query(ProjectDir)
                    .filter_by(project_id=active_project_id)
                    .order_by(ProjectDir.sort_order)
                    .all()
                ]
        else:
            scan_dirs = []

        rows: list[dict[str, Any]] = []
        for scan_dir in scan_dirs:
            if not scan_dir.exists():
                continue
            for p in sorted(scan_dir.rglob("*")):
                if p.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                # Skip hidden files/dirs (e.g. macOS ._* resource forks, .Trashes)
                if any(part.startswith(".") for part in p.relative_to(scan_dir).parts):
                    continue
                camera_id = camera_id_for_path(p, scan_dir)
                rows.append(
                    {
                        "video_path": normalize_path_str(str(p)),
                        "camera_id": camera_id,
                        "is_reference_video": is_reference_video_path(p, scan_dir),
                    }
                )

        if not rows:
            return pd.DataFrame(columns=["video_path", "camera_id", "is_reference_video"])
        return pd.DataFrame(rows)

    def _sync_videos_table(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        video_dir: Path | None = None,
        active_project_id: str | None = None,
    ) -> dict[str, int]:
        scanned = self._scan_videos(video_dir, active_project_id)
        now = self._utcnow_dt()

        with self.Session() as session:
            if scanned.empty:
                session.commit()
                return {"scanned": 0, "added": 0, "updated": 0, "removed": 0}

            # Deduplicate by (video_path, project_id)
            existing = {
                row[0]: row
                for row in session.execute(
                    select(
                        Video.video_path,
                        Video.video_id,
                        Video.is_valid,
                        Video.duration_sec,
                        Video.validation_error,
                    ).where(Video.project_id == active_project_id)
                ).fetchall()
            }

            new_rows = scanned[~scanned["video_path"].isin(existing)]
            existing_df = scanned[scanned["video_path"].isin(existing)]
            scanned_paths = set(scanned["video_path"])
            orphaned_paths = [p for p in existing if p not in scanned_paths]

            total_scanned = len(scanned)

            n_existing = len(existing_df)

            if progress_callback:
                progress_callback(0, total_scanned, "")

            probe_results: dict[Path, tuple] = {}
            if not new_rows.empty:

                def _offset_callback(current, total, filename):
                    if progress_callback:
                        progress_callback(n_existing + current, total_scanned, filename)

                probe_results = _probe_many(
                    [Path(r) for r in new_rows["video_path"]],
                    progress_callback=_offset_callback,
                )

            if not new_rows.empty:
                insert_rows = []
                empty = ProbeResult(None, None, None, None, None, None, None)
                for _, row in new_rows.iterrows():
                    probe = ProbeResult._make(probe_results.get(Path(row["video_path"]), empty))
                    insert_rows.append(
                        {
                            "video_id": str(uuid.uuid4()),
                            "project_id": active_project_id,
                            "video_path": row["video_path"],
                            "camera_id": row["camera_id"],
                            "created_at": probe.created_at,
                            "last_seen_at": now,
                            "duration_sec": probe.duration_sec,
                            "is_valid": probe.is_valid,
                            "is_web_safe": probe.is_web_safe,
                            "validation_error": probe.error,
                            "latitude": probe.latitude,
                            "longitude": probe.longitude,
                        }
                    )
                session.execute(Video.__table__.insert(), insert_rows)

            if not existing_df.empty:
                session.execute(
                    text("""
                        UPDATE videos
                        SET camera_id    = :camera_id,
                            last_seen_at = :last_seen_at,
                            is_missing   = 0
                        WHERE video_path = :video_path
                          AND project_id IS :project_id
                    """),
                    [
                        {
                            "video_path": row["video_path"],
                            "camera_id": row["camera_id"],
                            "last_seen_at": now,
                            "project_id": active_project_id,
                        }
                        for _, row in existing_df.iterrows()
                    ],
                )
                if progress_callback:
                    progress_callback(n_existing, total_scanned, "")

            reference_paths = scanned.loc[scanned["is_reference_video"], "video_path"].tolist()
            if reference_paths:
                session.execute(
                    sqlite_insert(VideoTag)
                    .from_select(
                        ["video_id", "tag_id", "tagged_at"],
                        select(Video.video_id, Tag.id, literal(now))
                        .select_from(Video)
                        .join(Tag, Tag.key == "reference_video")
                        .where(
                            Video.project_id == active_project_id,
                            Video.video_path.in_(reference_paths),
                        ),
                    )
                    .on_conflict_do_nothing(index_elements=["video_id", "tag_id"])
                )

            if orphaned_paths:
                session.execute(
                    text(
                        "UPDATE videos SET is_missing = 1"
                        " WHERE video_path = :p AND project_id IS :pid"
                    ),
                    [{"p": p, "pid": active_project_id} for p in orphaned_paths],
                )

            session.commit()

        if progress_callback:
            progress_callback(total_scanned, total_scanned, "")

        result = {
            "scanned": len(scanned),
            "added": len(new_rows),
            "updated": len(existing_df),
            "removed": len(orphaned_paths),
        }
        logger.info(
            "Video sync complete: scanned=%d added=%d updated=%d removed=%d (project=%s)",
            result["scanned"],
            result["added"],
            result["updated"],
            result["removed"],
            active_project_id,
        )
        return result

    @staticmethod
    def _apply_probe_result(video: Video, probe) -> None:
        """Copy a ProbeResult onto a Video row. Location/timestamp fields are only
        filled in when still empty, so a re-probe never clobbers existing metadata."""
        probe = ProbeResult._make(probe)
        video.duration_sec = probe.duration_sec
        video.is_valid = probe.is_valid
        video.is_web_safe = probe.is_web_safe
        video.validation_error = probe.error
        if probe.created_at is not None and video.created_at is None:
            video.created_at = probe.created_at
        if probe.latitude is not None and video.latitude is None:
            video.latitude = probe.latitude
        if probe.longitude is not None and video.longitude is None:
            video.longitude = probe.longitude

    def reprobe_video(self, video_id: str) -> None:
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise VideoError(
                    user_message_key="video_error_not_found",
                    detail=f"Unknown video_id: {video_id!r}",
                )
            self._apply_probe_result(
                video, _probe_video(Path(normalize_path_str(video.video_path)))
            )
            self._reconcile_distance_video_completion(session, video_id)
            session.commit()

    def reprobe_invalid_videos(self) -> dict[str, Any]:
        with self.Session() as session:
            invalid_videos = (
                session.query(Video)
                .filter((Video.is_valid == False) | (Video.is_valid.is_(None)))  # noqa: E712
                .all()
            )
            if not invalid_videos:
                return {"re_probed": 0, "now_valid": 0, "still_invalid": 0}

            path_map: dict[Path, str] = {
                Path(normalize_path_str(v.video_path)): v.video_id for v in invalid_videos
            }
            probe_results = _probe_many(list(path_map.keys()))

            now_valid = 0
            still_invalid = 0
            for path, probe in probe_results.items():
                video = session.get(Video, path_map[path])
                if video is None:
                    continue
                self._apply_probe_result(video, probe)
                self._reconcile_distance_video_completion(session, video.video_id)
                if video.is_valid:
                    now_valid += 1
                else:
                    still_invalid += 1
            session.commit()

        return {
            "re_probed": len(invalid_videos),
            "now_valid": now_valid,
            "still_invalid": still_invalid,
        }

    def transcode_video(self, video_id: str) -> TranscodeResult:
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise VideoError(
                    user_message_key="video_error_not_found",
                    detail=f"Unknown video_id: {video_id!r}",
                )

            input_path = Path(normalize_path_str(video.video_path))
            if not input_path.exists():
                video.is_missing = True
                session.commit()
                return TranscodeResult(False, error=f"File not found on disk: {input_path}")

            from review_app.app.config import get_user_data_dir

            tmp_dir = get_user_data_dir() / "transcoded_cache"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            sidecar_path = tmp_dir / transcoded_cache_filename(video_id)

            cache_status = inspect_transcoded_cache(sidecar_path)
            if cache_status.exists:
                if cache_status.usable:
                    video.transcoded_path = str(sidecar_path)
                    session.commit()
                    return TranscodeResult(True, new_path=str(sidecar_path))
                logger.warning(
                    "Replacing invalid or unverifiable transcode cache for video %s: %s",
                    video_id,
                    cache_status.validation.error if cache_status.validation else "outdated cache",
                )

            ffmpeg = find_video_tool("ffmpeg")
            if ffmpeg is None:
                logger.error("Cannot transcode video %s: ffmpeg executable not found", video_id)
                return TranscodeResult(False, error="ffmpeg executable not found")

            work_path = tmp_dir / f".{sidecar_path.stem}.{uuid.uuid4().hex}.part.mp4"
            cmd = [
                ffmpeg,
                "-y",
                "-nostdin",
                "-i",
                str(input_path),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-vf",
                "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-profile:a",
                "aac_low",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(work_path),
            ]

            logger.info("Transcoding %s -> %s", input_path, sidecar_path)
            try:
                subprocess.run(
                    cmd, capture_output=True, text=True, check=True, env=video_tool_env()
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                work_path.unlink(missing_ok=True)
                detail = (
                    exc.stderr[:500]
                    if isinstance(exc, subprocess.CalledProcessError)
                    else str(exc)
                )
                logger.error("ffmpeg failed for %s: %s", input_path, detail)
                return TranscodeResult(False, error=f"ffmpeg failed: {detail}")

            validation = validate_transcoded_video(work_path)
            if not validation.valid:
                work_path.unlink(missing_ok=True)
                logger.error(
                    "Transcoded output validation failed for video %s: %s",
                    video_id,
                    validation.error,
                )
                return TranscodeResult(False, error=validation.error)

            work_path.replace(sidecar_path)

            video.transcoded_path = str(sidecar_path)
            session.commit()

            logger.info("Transcode complete: %s", sidecar_path)
            return TranscodeResult(True, new_path=str(sidecar_path))

    def count_missing_videos(self, project_id: str) -> int:
        with self.engine.connect() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM videos WHERE project_id = :pid AND is_missing = 1"),
                {"pid": project_id},
            )
            return result.scalar() or 0

    def delete_missing_videos(self, project_id: str) -> int:
        with self.Session() as session:
            count = session.query(Video).filter_by(project_id=project_id, is_missing=True).count()
            self._cascade_delete_videos(
                session, (Video.project_id == project_id) & Video.is_missing.is_(True)
            )
            session.commit()
        return count


def cleanup_orphaned_transcoded_files(engine: Any, cache_dir: Path) -> int:
    """Delete .mp4 files in cache_dir that are not referenced by any Video.transcoded_path."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT transcoded_path FROM videos WHERE transcoded_path IS NOT NULL")
            )
            known = {row[0] for row in rows}
        removed = 0
        for f in cache_dir.glob("*.mp4"):
            if str(f) not in known:
                logger.info("Removing orphaned transcoded cache file: %s", f)
                f.unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.info("Removed %d orphaned transcoded cache file(s)", removed)
        return removed
    except Exception:
        logger.warning("Failed to clean up orphaned transcoded cache files", exc_info=True)
        return 0
