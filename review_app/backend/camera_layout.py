"""Infer camera deployments without treating clip categories as cameras."""

import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from review_app.backend.path_matching import normalize_path_str

_VIDEO_CATEGORIES = {"video", "videos"}
_REFERENCE_CATEGORIES = {"ref", "reference", "references"}


def _children(folder: Path, cache: dict[Path, list[Path]]) -> list[Path]:
    if folder not in cache:
        cache[folder] = (
            [child for child in folder.iterdir() if child.is_dir()] if folder.is_dir() else []
        )
    return cache[folder]


def _has_direct_footage(folder: Path) -> bool:
    from review_app.app.config import VIDEO_EXTENSIONS

    return folder.is_dir() and any(
        not child.name.startswith(".")
        and child.suffix.lower() in VIDEO_EXTENSIONS
        and child.is_file()
        for child in folder.iterdir()
    )


def _paired_layout(
    path: Path, scan_dir: Path, directory_cache: dict[Path, list[Path]]
) -> tuple[Path, str] | None:
    for category in path.parents:
        parent = category.parent
        if not parent.is_relative_to(scan_dir):
            break
        name = category.name.casefold()
        if name not in _VIDEO_CATEGORIES | _REFERENCE_CATEGORIES:
            continue
        reference = name in _REFERENCE_CATEGORIES
        siblings = _children(parent, directory_cache)
        ordinary = [child for child in siblings if child.name.casefold() in _VIDEO_CATEGORIES]
        references = [
            child for child in siblings if child.name.casefold() in _REFERENCE_CATEGORIES
        ]
        relative = path.parent.relative_to(category)
        if len(ordinary) == len(references) == 1 and category in (ordinary[0], references[0]):
            # A nested category contains one deployment per matching child name.
            # A reference-only child keeps its own scope.
            if relative.parts:
                camera = relative.parts[0]
                if reference and not (ordinary[0] / camera).is_dir():
                    return category / camera, "references"
                return parent / camera, "references" if reference else "videos"
            return parent, "references" if reference else "videos"
        if (
            reference
            and not relative.parts
            and len(references) == 1
            and _has_direct_footage(parent)
        ):
            return parent, "references"
        if name == "ref" and category in references:
            return category / Path(*relative.parts[:1]), "references"
    if (
        path.parent == scan_dir
        and path.is_file()
        and any(
            child.name.casefold() in _REFERENCE_CATEGORIES
            for child in _children(scan_dir, directory_cache)
        )
    ):
        return scan_dir, "videos"
    return None


def _normalized_folder_name(name: str) -> str:
    """Comparison key only; never rewrite filesystem paths or stored camera IDs."""
    return re.sub(r"[\s_-]+", "_", name).strip("_").lower()


def _reference_deployment_name(name: str) -> str | None:
    """Strip one boundary-delimited reference marker, not embedded letters."""
    name = _normalized_folder_name(name)
    for marker in ("v_r", "vr", "ref", "reference", "references"):
        # Bare textual categories retain the existing paired-layout semantics.
        if name == marker and marker in {"v_r", "vr"}:
            return ""
        if name.startswith(f"{marker}_"):
            return name[len(marker) + 1 :]
        if name.endswith(f"_{marker}"):
            return name[: -len(marker) - 1]
    return None


def _marked_layout(
    path: Path, scan_dir: Path, directory_cache: dict[Path, list[Path]]
) -> tuple[Path, str] | None:
    # Outermost deployment wins: nested copies and processing folders must not
    # turn one reference deployment into several camera IDs. Never inspect a
    # folder outside the registered import root, or infer a role from the root.
    if not path.is_relative_to(scan_dir):
        return None
    folders = path.parent.relative_to(scan_dir).parts
    parent = scan_dir
    ordinary_layout: tuple[Path, str] | None = None
    ordinary_ancestors: list[Path] = []
    for name in folders:
        folder = parent / name
        reference_name = _reference_deployment_name(name)
        siblings = _children(parent, directory_cache)
        ordinary = [child for child in siblings if _reference_deployment_name(child.name) is None]
        if reference_name is not None:
            # Standalone V_R/Video categories use the same enclosing deployment.
            key = reference_name
            matches = [
                child
                for child in ordinary
                if (
                    _normalized_folder_name(child.name) == key
                    if key
                    else _normalized_folder_name(child.name) in {"video", "videos"}
                )
            ]
            if not matches and key:
                # Some exports put cam_a_VR under cam_a/Pantro instead of
                # beside cam_a. The enclosing name must still match exactly.
                matches = [
                    ancestor
                    for ancestor in ordinary_ancestors
                    if _normalized_folder_name(ancestor.name) == key
                ]
            if len(matches) == 1:
                return (matches[0] if key else parent), "references"
            # A clear marker still identifies reference footage, but unmatched
            # or colliding names must not be attached to an ordinary deployment.
            return folder, "references"

        key = _normalized_folder_name(name)
        matches = [child for child in ordinary if _normalized_folder_name(child.name) == key]
        if len(matches) == 1 and ordinary_layout is None:
            reference_names = [_reference_deployment_name(child.name) for child in siblings]
            if key in reference_names:
                ordinary_layout = folder, "videos"
            if key in {"video", "videos"} and "" in reference_names:
                video_categories = [
                    child
                    for child in ordinary
                    if _normalized_folder_name(child.name) in {"video", "videos"}
                ]
                if len(video_categories) == 1:
                    ordinary_layout = parent, "videos"
        ordinary_ancestors.append(folder)
        parent = folder
    return ordinary_layout


def video_layout_for_path(
    path: Path,
    scan_dir: Path,
    *,
    directory_cache: dict[Path, list[Path]] | None = None,
) -> tuple[str, bool]:
    """Shared camera/role inference; optional directory cache lives for one scan only."""
    # Explicit reference markers also apply inside a legacy videos/references layout.
    # A lone folder called "videos" or "references" can be an ordinary camera.
    cache = directory_cache if directory_cache is not None else {}
    layout = _marked_layout(path, scan_dir, cache) or _paired_layout(path, scan_dir, cache)
    parent = layout[0] if layout else path.parent
    relative = parent.relative_to(scan_dir)
    if relative != Path("."):
        camera_id = normalize_path_str(str(relative))
    elif layout:
        camera_id = scan_dir.name
    else:
        camera_id = "default"
    return camera_id, layout is not None and layout[1] == "references"


def is_reference_video_path(path: Path, scan_dir: Path) -> bool:
    """Recognize paired reference categories and boundary-marked reference subtrees."""
    return video_layout_for_path(path, scan_dir)[1]


def camera_id_for_path(path: Path, scan_dir: Path) -> str:
    return video_layout_for_path(path, scan_dir)[0]


def iter_footage(root: Path) -> Iterator[tuple[Path, str, bool]]:
    """Enumerate visible footage using one layout decision per containing folder."""
    from review_app.app.config import VIDEO_EXTENSIONS

    layouts: dict[Path, tuple[str, bool]] = {}
    siblings: dict[Path, list[Path]] = {}
    for path in sorted(root.rglob("*")):
        if (
            path.suffix.lower() not in VIDEO_EXTENSIONS
            or any(part.startswith(".") for part in path.relative_to(root).parts)
            or not path.is_file()
        ):
            continue
        if path.parent not in layouts:
            layouts[path.parent] = video_layout_for_path(path, root, directory_cache=siblings)
        label, reference = layouts[path.parent]
        yield path, label, reference


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
