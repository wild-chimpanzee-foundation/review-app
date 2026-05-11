from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select, text

from review_app.app.config import VIDEO_EXTENSIONS
from review_app.backend.db.models import ProjectDir, Video
from review_app.backend.errors import VideoError
from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)

_FFPROBE_MAX_WORKERS: int = int(os.getenv("FFPROBE_MAX_WORKERS", "16"))
_FFPROBE_TIMEOUT_SEC: int = int(os.getenv("FFPROBE_TIMEOUT_SEC", "10"))


def _subprocess_env() -> dict[str, str]:
    """Return an environment safe for subprocesses when running frozen.

    PyInstaller prepends _internal/ to LD_LIBRARY_PATH so its bundled libs
    are found by Python. Subprocesses (ffprobe, ffmpeg) inherit this and can
    crash when the bundled libs conflict with system libs they link against.
    Restoring the original value fixes that.
    """
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        if sys.platform.startswith("linux"):
            orig = env.get("LD_LIBRARY_PATH_ORIG", "")
            if orig:
                env["LD_LIBRARY_PATH"] = orig
            else:
                env.pop("LD_LIBRARY_PATH", None)
        elif sys.platform == "darwin":
            # Homebrew directories are stripped from PATH in frozen bundles
            path_parts = env.get("PATH", "").split(":")
            for brew_bin in ("/opt/homebrew/bin", "/usr/local/bin"):
                if brew_bin not in path_parts:
                    path_parts.insert(0, brew_bin)
            env["PATH"] = ":".join(path_parts)
    return env


def _find_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def _parse_iso6709(s: str) -> tuple[float, float] | None:
    """Parse an ISO 6709 location string like '+37.4060-122.0782/' into (lat, lon)."""
    m = re.match(r"^([+-]\d+\.?\d*)([+-]\d+\.?\d*)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _probe_video(
    path: Path,
) -> tuple[float | None, bool, bool, str | None, datetime | None, float | None, float | None]:
    """
    Run ffprobe on *path* and return
    ``(duration_sec, is_valid, is_web_safe, error_message, created_at, latitude, longitude)``.
    """
    ffprobe = _find_ffprobe()
    if ffprobe is None:
        logger.error("ffprobe not found on PATH — video probing unavailable")
        return None, False, False, "ffprobe executable not found", None, None, None

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
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe timed out after %ds on %s", _FFPROBE_TIMEOUT_SEC, path)
        return (
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
        return None, False, False, f"ffprobe OS error: {exc}", None, None, None

    if result.returncode != 0:
        stderr_text = result.stderr.strip()
        logger.warning("ffprobe non-zero exit for %s: %s", path, stderr_text[:200])
        return (
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
        return None, False, False, "ffprobe returned unparseable JSON", None, None, None

    return raw_duration, True, is_web_safe, None, created_at, latitude, longitude


def _probe_many(
    paths: list[Path],
    max_workers: int = _FFPROBE_MAX_WORKERS,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[
    Path, tuple[float | None, bool, bool, str | None, datetime | None, float | None, float | None]
]:
    if not paths:
        return {}

    results: dict[Path, tuple[float | None, bool, bool, str | None]] = {}
    total = len(paths)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(paths))) as pool:
        future_to_path = {pool.submit(_probe_video, p): p for p in paths}
        for i, future in enumerate(as_completed(future_to_path)):
            path = future_to_path[future]
            try:
                results[path] = future.result()
            except Exception as exc:  # pragma: no cover
                logger.error("Unexpected error probing %s: %s", path, exc)
                results[path] = (None, False, False, str(exc), None, None, None)
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
                    Path(d.path)
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
                camera_id = p.parent.name if p.parent != scan_dir else "default"
                rows.append({"video_path": str(p), "camera_id": camera_id})

        if not rows:
            return pd.DataFrame(columns=["video_path", "camera_id"])
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
                return {"scanned": 0, "added": 0, "updated": 0}

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

            total_scanned = len(scanned)
            probe_results: dict[Path, tuple] = {}
            if not new_rows.empty:
                n_new = len(new_rows)
                n_existing = total_scanned - n_new

                def _offset_callback(current, total, filename):
                    if progress_callback:
                        progress_callback(n_existing + current, total_scanned, filename)

                probe_results = _probe_many(
                    [Path(r) for r in new_rows["video_path"]],
                    progress_callback=_offset_callback,
                )

            if not new_rows.empty:
                insert_rows = []
                for _, row in new_rows.iterrows():
                    probe = probe_results.get(
                        Path(row["video_path"]), (None, None, None, None, None, None, None)
                    )
                    (
                        duration_sec,
                        is_valid,
                        is_web_safe,
                        validation_error,
                        ffprobe_created_at,
                        latitude,
                        longitude,
                    ) = probe
                    insert_rows.append(
                        {
                            "video_id": str(uuid.uuid4()),
                            "project_id": active_project_id,
                            "video_path": row["video_path"],
                            "camera_id": row["camera_id"],
                            "created_at": ffprobe_created_at,
                            "last_seen_at": now,
                            "duration_sec": duration_sec,
                            "is_valid": is_valid,
                            "is_web_safe": is_web_safe,
                            "validation_error": validation_error,
                            "latitude": latitude,
                            "longitude": longitude,
                        }
                    )
                session.execute(Video.__table__.insert(), insert_rows)

            if not existing_df.empty:
                session.execute(
                    text("""
                        UPDATE videos
                        SET camera_id    = :camera_id,
                            last_seen_at = :last_seen_at
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

            session.commit()

        if progress_callback:
            progress_callback(total_scanned, total_scanned, "")

        result = {
            "scanned": len(scanned),
            "added": len(new_rows),
            "updated": len(existing_df),
        }
        logger.info(
            "Video sync complete: scanned=%d added=%d updated=%d (project=%s)",
            result["scanned"],
            result["added"],
            result["updated"],
            active_project_id,
        )
        return result

    def reprobe_video(self, video_id: str) -> None:
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise VideoError(
                    user_message_key="video_error_not_found",
                    detail=f"Unknown video_id: {video_id!r}",
                )
            duration, is_valid, is_web_safe, validation_error, created_at, latitude, longitude = (
                _probe_video(Path(video.video_path))
            )
            video.duration_sec = duration
            video.is_valid = is_valid
            video.is_web_safe = is_web_safe
            video.validation_error = validation_error
            if created_at is not None and video.created_at is None:
                video.created_at = created_at
            if latitude is not None and video.latitude is None:
                video.latitude = latitude
            if longitude is not None and video.longitude is None:
                video.longitude = longitude
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

            path_map: dict[Path, str] = {Path(v.video_path): v.video_id for v in invalid_videos}
            probe_results = _probe_many(list(path_map.keys()))

            now_valid = 0
            still_invalid = 0
            for path, (
                duration,
                is_valid,
                is_web_safe,
                validation_error,
                created_at,
                latitude,
                longitude,
            ) in probe_results.items():
                vid_id = path_map[path]
                video = session.get(Video, vid_id)
                if video is None:
                    continue
                video.duration_sec = duration
                video.is_valid = is_valid
                video.is_web_safe = is_web_safe
                video.validation_error = validation_error
                if created_at is not None and video.created_at is None:
                    video.created_at = created_at
                if latitude is not None and video.latitude is None:
                    video.latitude = latitude
                if longitude is not None and video.longitude is None:
                    video.longitude = longitude
                if is_valid:
                    now_valid += 1
                else:
                    still_invalid += 1
            session.commit()

        return {
            "re_probed": len(invalid_videos),
            "now_valid": now_valid,
            "still_invalid": still_invalid,
        }

    def transcode_video(self, video_id: str) -> dict[str, Any]:
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise VideoError(
                    user_message_key="video_error_not_found",
                    detail=f"Unknown video_id: {video_id!r}",
                )

            input_path = Path(video.video_path)
            if not input_path.exists():
                raise VideoError(
                    user_message_key="video_error_file_not_found",
                    detail=f"Video file not found: {input_path}",
                )

            from review_app.app.config import get_user_data_dir

            tmp_dir = get_user_data_dir() / "transcoded_cache"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = video_id.replace("/", "_").replace("\\", "_").replace(":", "_")
            sidecar_path = tmp_dir / f"{safe_name}.mp4"

            if sidecar_path.exists():
                video.transcoded_path = str(sidecar_path)
                session.commit()
                return {"success": True, "new_path": str(sidecar_path)}

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(sidecar_path),
            ]

            logger.info("Transcoding %s -> %s", input_path, sidecar_path)
            try:
                subprocess.run(
                    cmd, capture_output=True, text=True, check=True, env=_subprocess_env()
                )
            except subprocess.CalledProcessError as exc:
                if sidecar_path.exists():
                    sidecar_path.unlink()
                logger.error("ffmpeg failed for %s: %s", input_path, exc.stderr[:500])
                return {"success": False, "error": f"ffmpeg failed: {exc.stderr}"}

            video.transcoded_path = str(sidecar_path)
            video.is_web_safe = True
            session.commit()

            logger.info("Transcode complete: %s", sidecar_path)
            return {"success": True, "new_path": str(sidecar_path)}
