from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select, text

from review_app.app.config import VIDEO_EXTENSIONS
from review_app.backend.models import ProjectDir, Video

_FFPROBE_MAX_WORKERS: int = int(os.getenv("FFPROBE_MAX_WORKERS", "16"))
_FFPROBE_TIMEOUT_SEC: int = int(os.getenv("FFPROBE_TIMEOUT_SEC", "10"))


def _subprocess_env() -> dict:
    """Return an environment safe for subprocesses when running frozen.

    PyInstaller prepends _internal/ to LD_LIBRARY_PATH so its bundled libs
    are found by Python. Subprocesses (ffprobe, ffmpeg) inherit this and can
    crash when the bundled libs conflict with system libs they link against.
    Restoring the original value fixes that.
    """
    env = os.environ.copy()
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        orig = env.get("LD_LIBRARY_PATH_ORIG", "")
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


def _find_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def _probe_video(path: Path) -> tuple[float | None, bool, bool, str | None]:
    """
    Run ffprobe on *path* and return ``(duration_sec, is_valid, is_web_safe, error_message)``.
    """
    ffprobe = _find_ffprobe()
    if ffprobe is None:
        return None, False, False, "ffprobe executable not found"

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=duration,codec_name,codec_type",
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
        return None, False, False, f"ffprobe timed out after {_FFPROBE_TIMEOUT_SEC}s"
    except OSError as exc:
        return None, False, False, f"ffprobe OS error: {exc}"

    if result.returncode != 0:
        stderr_text = result.stderr.strip()
        return None, False, False, stderr_text[:200] or "ffprobe returned non-zero exit code"

    try:
        data = json.loads(result.stdout)
        raw_duration = data.get("format", {}).get("duration")
        if raw_duration is None:
            streams = data.get("streams", [])
            if streams:
                raw_duration = streams[0].get("duration")

        format_name = data.get("format", {}).get("format_name", "").lower()
        streams = data.get("streams", [])
        video_codec = next(
            (s.get("codec_name", "") for s in streams if s.get("codec_type") == "video"), ""
        ).lower()

        safe_formats = {"mp4", "mov", "webm", "ogg"}
        safe_codecs = {"h264", "vp8", "vp9", "av1", "theora"}
        formats = {f.strip() for f in format_name.split(",")}
        is_web_safe = bool(formats & safe_formats) and video_codec in safe_codecs

    except (json.JSONDecodeError, ValueError, TypeError):
        return None, False, False, "ffprobe returned unparseable JSON"

    return raw_duration, True, is_web_safe, None


def _probe_many(
    paths: list[Path],
    max_workers: int = _FFPROBE_MAX_WORKERS,
    progress_callback: callable = None,
) -> dict[Path, tuple[float | None, bool, bool, str | None]]:
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
                results[path] = (None, False, False, str(exc))
            if progress_callback:
                progress_callback(i + 1, total, path.name)

    return results


class VideoMixin:
    """Video scanning, probing, and transcoding. Requires self.engine, self.Session."""

    def _scan_videos(self, video_dir: Path | None = None, active_project_id: str | None = None) -> pd.DataFrame:
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
                created_at = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                rows.append(
                    {
                        "video_path": str(p),
                        "camera_id": camera_id,
                        "created_at": created_at,
                    }
                )

        if not rows:
            return pd.DataFrame(columns=["video_path", "camera_id", "created_at"])
        return pd.DataFrame(rows)

    def _sync_videos_table(
        self,
        progress_callback=None,
        video_dir: Path | None = None,
        active_project_id: str | None = None,
    ) -> dict:
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
                session.execute(
                    Video.__table__.insert(),
                    [
                        {
                            "video_id": str(uuid.uuid4()),
                            "project_id": active_project_id,
                            "video_path": row["video_path"],
                            "camera_id": row["camera_id"],
                            "created_at": row["created_at"].to_pydatetime(),
                            "last_seen_at": now,
                            **dict(
                                zip(
                                    (
                                        "duration_sec",
                                        "is_valid",
                                        "is_web_safe",
                                        "validation_error",
                                    ),
                                    probe_results.get(
                                        Path(row["video_path"]), (None, None, None, None)
                                    ),
                                )
                            ),
                        }
                        for _, row in new_rows.iterrows()
                    ],
                )

            if not existing_df.empty:
                session.execute(
                    text("""
                        UPDATE videos
                        SET camera_id    = :camera_id,
                            created_at   = :created_at,
                            last_seen_at = :last_seen_at
                        WHERE video_path = :video_path
                          AND project_id IS :project_id
                    """),
                    [
                        {
                            "video_path": row["video_path"],
                            "camera_id": row["camera_id"],
                            "created_at": row["created_at"].to_pydatetime(),
                            "last_seen_at": now,
                            "project_id": active_project_id,
                        }
                        for _, row in existing_df.iterrows()
                    ],
                )

            session.commit()

        if progress_callback:
            progress_callback(total_scanned, total_scanned, "")

        return {
            "scanned": len(scanned),
            "added": len(new_rows),
            "updated": len(existing_df),
        }

    def reprobe_video(self, video_id: str) -> None:
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise ValueError(f"Unknown video_id: {video_id!r}")
            duration, is_valid, is_web_safe, validation_error = _probe_video(
                Path(video.video_path)
            )
            video.duration_sec = duration
            video.is_valid = is_valid
            video.is_web_safe = is_web_safe
            video.validation_error = validation_error
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
            for path, (duration, is_valid, is_web_safe, validation_error) in probe_results.items():
                vid_id = path_map[path]
                video = session.get(Video, vid_id)
                if video is None:
                    continue
                video.duration_sec = duration
                video.is_valid = is_valid
                video.is_web_safe = is_web_safe
                video.validation_error = validation_error
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
                raise ValueError(f"Unknown video_id: {video_id!r}")

            input_path = Path(video.video_path)
            if not input_path.exists():
                raise FileNotFoundError(f"Video file not found: {input_path}")

            tmp_dir = Path(tempfile.gettempdir()) / "video_review_transcoded"
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

            try:
                subprocess.run(
                    cmd, capture_output=True, text=True, check=True, env=_subprocess_env()
                )
            except subprocess.CalledProcessError as exc:
                if sidecar_path.exists():
                    sidecar_path.unlink()
                return {"success": False, "error": f"ffmpeg failed: {exc.stderr}"}

            video.transcoded_path = str(sidecar_path)
            video.is_web_safe = True
            session.commit()

            return {"success": True, "new_path": str(sidecar_path)}
