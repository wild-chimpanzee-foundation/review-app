"""Saved-scope footage delivery, independent of mutable recipient distribution."""

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from review_app.backend.annotation_package.naming import footage_folder
from review_app.backend.annotation_package.scope import bound_videos, safe_relative
from review_app.backend.db.models import HandoffIdentityMap, ProjectDir, Video
from review_app.backend.path_matching import normalize_path_str


@dataclass(frozen=True)
class CopyResult:
    path: str
    media_roots: dict[str, str]
    copied_count: int
    missing_video_ids: tuple[str, ...]
    failures: dict[str, str]
    problem_paths: dict[str, str] = field(default_factory=dict)


def resolve_delivery(session, binding):
    """Freeze source paths while retaining the original root-relative destinations."""
    if binding is None or binding.role != "origin" or binding.removed_at is not None:
        raise ValueError("Choose an originating saved assignment")
    folders = {key: footage_folder(i) for i, key in enumerate(binding.scope["roots"], 1)}
    videos = bound_videos(session, binding)
    roots = [
        Path(normalize_path_str(row.path)).expanduser().resolve()
        for row in session.query(ProjectDir).filter_by(project_id=binding.project_id)
    ]
    media = []
    for row in binding.scope["media"]:
        video = videos.get(row["video_id"])
        if video is None:
            mapping = session.get(HandoffIdentityMap, (binding.id, "video", row["video_id"]))
            video = session.get(Video, mapping.local_id) if mapping else None
        if video is not None:
            source = Path(normalize_path_str(video.video_path)).expanduser().resolve()
        else:
            mapping = session.get(HandoffIdentityMap, (binding.id, "root", row["root_id"]))
            root = session.get(ProjectDir, mapping.local_id) if mapping else None
            if root is None or root.project_id != binding.project_id:
                raise ValueError("Assignment reference media root is missing")
            source = (Path(root.path) / row["video_path"]).resolve()
        if not any(source.is_relative_to(root) for root in roots):
            raise ValueError("Assignment footage escapes its registered project folders")
        relative = Path(folders[row["root_id"]]) / safe_relative(row["video_path"])
        media.append((row["video_id"], source, relative))
    return roots, folders, media


def copy_media(
    stem,
    project_roots,
    footage_folders,
    media,
    output_dir,
    progress_callback=None,
    *,
    setup_archive=None,
):
    """Copy a resolved list to a new directory; never overwrite an earlier delivery.

    Progress counts attempted files, including missing/failed files. Partial results
    retain their full root mapping so missing footage is never silently excluded.
    """
    if not isinstance(output_dir, (str, Path)) or not str(output_dir).strip():
        raise ValueError("Choose an output folder outside the project footage folders")
    destination = Path(output_dir).expanduser().resolve() / stem
    if any(destination.is_relative_to(root) for root in project_roots):
        raise ValueError("Delivery must be outside the project footage folders")
    destination.mkdir(parents=True, exist_ok=False)
    if setup_archive is not None:
        (destination / f"{stem}.zip").write_bytes(setup_archive)
    media_roots = {key: str(destination / folder) for key, folder in footage_folders.items()}
    for path in media_roots.values():
        Path(path).mkdir(parents=True)

    def copy_one(source, relative):
        if not source.is_file():
            return "missing"
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # Stage on the same filesystem so package opening only sees complete files.
        with TemporaryDirectory(prefix=".copy-", dir=target.parent) as staging_dir:
            staged = Path(staging_dir) / "footage.tmp"
            shutil.copy2(source, staged)
            staged.replace(target)
        return "copied"

    copied, missing, failures = 0, [], {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(copy_one, source, rel): key for key, source, rel in media}
        for done, future in enumerate(as_completed(futures), 1):
            key = futures[future]
            try:
                if future.result() == "missing":
                    missing.append(key)
                else:
                    copied += 1
            except OSError as exc:
                failures[key] = str(exc)
            if progress_callback:
                progress_callback(done, len(media))
    problems = set(missing).union(failures)
    problem_paths = {key: relative.as_posix() for key, _, relative in media if key in problems}
    return CopyResult(
        str(destination), media_roots, copied, tuple(sorted(missing)), failures, problem_paths
    )
