from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

VIDEO_TOOLS = ("ffmpeg", "ffprobe")
_HOMEBREW_BIN_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))


@dataclass(frozen=True)
class VideoToolsStatus:
    paths: dict[str, str | None]

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(tool for tool in VIDEO_TOOLS if self.paths.get(tool) is None)

    @property
    def available(self) -> bool:
        return not self.missing


def video_tool_env() -> dict[str, str]:
    """Return an environment safe for video-tool subprocesses in packaged builds."""
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        if sys.platform.startswith("linux"):
            original = env.get("LD_LIBRARY_PATH_ORIG", "")
            if original:
                env["LD_LIBRARY_PATH"] = original
            else:
                env.pop("LD_LIBRARY_PATH", None)
        elif sys.platform == "darwin":
            path_parts = env.get("PATH", "").split(":")
            for bin_dir in _HOMEBREW_BIN_DIRS:
                value = str(bin_dir)
                if value not in path_parts:
                    path_parts.insert(0, value)
            env["PATH"] = ":".join(path_parts)
    return env


def find_video_tool(tool: str) -> str | None:
    """Resolve ffmpeg or ffprobe, including packaged macOS/Homebrew installs."""
    if tool not in VIDEO_TOOLS:
        raise ValueError(f"Unsupported video tool: {tool}")
    found = shutil.which(tool)
    if found:
        return found
    if sys.platform == "darwin":
        for bin_dir in _HOMEBREW_BIN_DIRS:
            candidate = bin_dir / tool
            if candidate.is_file():
                return str(candidate)
    return None


def check_video_tools() -> VideoToolsStatus:
    """Check that both required executables exist and can be launched."""
    paths: dict[str, str | None] = {}
    for tool in VIDEO_TOOLS:
        path = find_video_tool(tool)
        if path:
            try:
                result = subprocess.run(
                    [path, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=video_tool_env(),
                )
                if result.returncode != 0:
                    logger.warning(
                        "%s health check failed with exit code %d", tool, result.returncode
                    )
                    path = None
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("%s health check failed: %s", tool, exc)
                path = None
        paths[tool] = path
    status = VideoToolsStatus(paths)
    if status.available:
        logger.info(
            "Video tools available: ffmpeg=%s, ffprobe=%s",
            status.paths["ffmpeg"],
            status.paths["ffprobe"],
        )
    else:
        logger.warning("Required video tools unavailable: %s", ", ".join(status.missing))
    return status
