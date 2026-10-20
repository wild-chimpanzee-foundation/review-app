"""Infer camera deployments without treating clip categories as cameras."""

from collections.abc import Sequence
from pathlib import Path

from review_app.backend.path_matching import normalize_path_str


def _paired_layout(path: Path, scan_dir: Path) -> tuple[Path, str] | None:
    # Support both cam_a/{videos,references}/clip and {videos,references}/cam_a/clip.
    for category in (path.parent, path.parent.parent):
        deployment = category.parent
        if (
            category.name in {"videos", "references"}
            and deployment.is_relative_to(scan_dir)
            and (deployment / "videos").is_dir()
            and (deployment / "references").is_dir()
        ):
            camera = deployment / path.parent.relative_to(category)
            return camera, category.name
    return None


def is_reference_video_path(path: Path, scan_dir: Path) -> bool:
    """Recognize references only inside an explicit paired deployment layout."""
    layout = _paired_layout(path, scan_dir)
    return layout is not None and layout[1] == "references"


def camera_id_for_path(path: Path, scan_dir: Path) -> str:
    # A lone folder called "videos" or "references" can be an ordinary camera.
    layout = _paired_layout(path, scan_dir)
    parent = layout[0] if layout else path.parent
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
