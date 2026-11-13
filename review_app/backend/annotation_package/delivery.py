"""Saved-scope footage delivery, independent of mutable recipient distribution."""

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from review_app.backend.annotation_package.archive import decode_archive
from review_app.backend.annotation_package.scope import bound_videos, safe_relative
from review_app.backend.db.models import HandoffAssignment, HandoffIdentityMap, ProjectDir, Video
from review_app.backend.path_matching import normalize_path_str


@dataclass(frozen=True)
class CopyResult:
    path: str
    media_roots: dict[str, str]
    copied_count: int
    missing_video_ids: tuple[str, ...]
    failures: dict[str, str]


def resolve_delivery(session, assignment_id):
    """Freeze source paths while retaining the original root-relative destinations."""
    binding = session.get(HandoffAssignment, assignment_id)
    if binding is None or binding.role != "origin":
        raise ValueError("Choose an originating saved assignment")
    manifest, context, _ = decode_archive(binding.setup_archive)
    videos = bound_videos(session, binding)
    roots = [
        Path(normalize_path_str(row.path)).expanduser().resolve()
        for row in session.query(ProjectDir).filter_by(project_id=binding.project_id)
    ]
    media = []
    for row in context["media"]:
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
        relative = Path(safe_relative(row["root_id"])) / safe_relative(row["video_path"])
        media.append((row["video_id"], source, relative))
    return manifest["assignment_id"], roots, manifest["roots"], media


def copy_media(assignment_id, project_roots, root_ids, media, output_dir, progress_callback=None):
    """Copy a resolved list to a new directory; never overwrite an earlier delivery.

    Progress counts attempted files, including missing/failed files. Partial results
    retain their full root mapping so missing footage is never silently excluded.
    """
    if not isinstance(output_dir, (str, Path)) or not str(output_dir).strip():
        raise ValueError("Choose an output folder outside the project footage folders")
    destination = Path(output_dir).expanduser().resolve() / f"assignment-{assignment_id}"
    if any(destination.is_relative_to(root) for root in project_roots):
        raise ValueError("Delivery must be outside the project footage folders")
    destination.mkdir(parents=True, exist_ok=False)
    media_roots = {key: str(destination / safe_relative(key)) for key in root_ids}
    for path in media_roots.values():
        Path(path).mkdir(parents=True)

    def copy_one(source, relative):
        if not source.is_file():
            return "missing"
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
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
    return CopyResult(str(destination), media_roots, copied, tuple(sorted(missing)), failures)
