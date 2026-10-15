"""Infer camera deployments without treating clip categories as cameras."""

from collections.abc import Sequence
from pathlib import Path

from review_app.backend.path_matching import normalize_path_str


def camera_id_for_path(path: Path, scan_dir: Path) -> str:
    parent = path.parent
    # Only collapse the explicit paired layout. A lone folder called "videos"
    # or "references" can still be an ordinary camera folder.
    if parent.name in {"videos", "references"}:
        deployment = parent.parent
        if (
            deployment.is_relative_to(scan_dir)
            and (deployment / "videos").is_dir()
            and (deployment / "references").is_dir()
        ):
            parent = deployment
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
