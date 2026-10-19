"""Infer camera deployments without treating clip categories as cameras."""

from collections.abc import Sequence
from pathlib import Path

from review_app.backend.path_matching import normalize_path_str


def _paired_deployment(path: Path, scan_dir: Path) -> Path | None:
    parent = path.parent
    deployment = parent.parent
    if (
        parent.name in {"videos", "references"}
        and deployment.is_relative_to(scan_dir)
        and (deployment / "videos").is_dir()
        and (deployment / "references").is_dir()
    ):
        return deployment
    return None


def is_reference_video_path(path: Path, scan_dir: Path) -> bool:
    """Recognize references only inside an explicit paired deployment layout."""
    return path.parent.name == "references" and _paired_deployment(path, scan_dir) is not None


def camera_id_for_path(path: Path, scan_dir: Path) -> str:
    # A lone folder called "videos" or "references" can be an ordinary camera.
    parent = _paired_deployment(path, scan_dir) or path.parent
    relative = parent.relative_to(scan_dir)
    if relative != Path("."):
        return normalize_path_str(str(relative))
    if (scan_dir / "videos").is_dir() and (scan_dir / "references").is_dir():
        return scan_dir.name
    return "default"


def deployment_key(
    project_id: str | None,
    camera_id: str | None,
    video_path: str,
    roots: Sequence[tuple[str, str]],
) -> tuple[str, str, str] | None:
    """Match deployments by stable import-root ID as well as the camera label.

    Most-specific registered roots take precedence. A single-root project can
    still use explicitly registered external/relative reference files. With
    multiple roots such a path is ambiguous and must not match by camera alone.
    No filesystem access is needed, so relocating a registered root preserves
    identity and missing media does not change its scope.
    """
    if not project_id or not camera_id or not camera_id.strip():
        return None
    path = Path(normalize_path_str(video_path))
    candidates = sorted(
        ((root_id, Path(normalize_path_str(root_path))) for root_id, root_path in roots),
        key=lambda root: len(root[1].parts),
        reverse=True,
    )
    for root_id, root_path in candidates:
        if path.is_relative_to(root_path):
            return project_id, root_id, camera_id.strip()
    if len(candidates) == 1:
        return project_id, candidates[0][0], camera_id.strip()
    return None
