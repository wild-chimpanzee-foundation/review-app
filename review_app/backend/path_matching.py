from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import NamedTuple


def normalize_path_str(path: str) -> str:
    """Canonicalize a path string for storage/matching: backslashes -> forward slashes.

    Windows paths (native or pasted from Explorer) use "\\"; every other OS uses "/".
    Normalizing at every DB write/read boundary makes stored paths, CSV/bundle exports,
    and cross-machine matching independent of which OS produced the string.
    """
    return path.replace("\\", "/")


class VideoPathLookup(NamedTuple):
    by_path: dict[str, set[str]]
    by_suffix: dict[str, set[str]]
    # stem -> [(video_id, camera_id_lower)] — all videos sharing that stem
    by_cam_stem: dict[str, list[tuple[str, str]]]
    cam_by_id: dict[str, str]  # video_id -> camera_id_lower
    by_filename: dict[str, set[str]]


def _cameras_share_token(csv_cam: str, db_cam: str) -> bool:
    """Return True if the two camera folder names share at least one non-trivial token.

    Tokens are produced by splitting on '_' and keeping only those with 3+ characters
    to avoid matching on short noise segments like 'a', 'b', 'f2', 'c8'.
    """
    if not csv_cam or not db_cam:
        return False
    csv_tokens = {t.lower() for t in csv_cam.split("_") if len(t) >= 3}
    db_tokens = {t.lower() for t in db_cam.split("_") if len(t) >= 3}
    return bool(csv_tokens & db_tokens)


def build_video_path_lookup(
    video_rows: list[tuple[str, str, str | None]],
    scan_dirs: list[Path],
) -> VideoPathLookup:
    """Build path lookup structures from DB rows.

    video_rows: [(video_id, video_path, camera_id), ...]
    scan_dirs: project scan root directories for computing relative paths
    """
    by_path: dict[str, set[str]] = defaultdict(set)
    by_suffix: dict[str, set[str]] = defaultdict(set)
    by_camera_prefix: dict[str, set[str]] = defaultdict(set)
    by_cam_stem: dict[str, list[tuple[str, str]]] = {}
    cam_by_id: dict[str, str] = {}
    by_filename: dict[str, set[str]] = defaultdict(set)
    roots = set(scan_dirs)

    for vid, video_path, camera_id in video_rows:
        p = Path(normalize_path_str(video_path))
        cam_lower = (camera_id or "").lower()
        cam_by_id[vid] = cam_lower
        stem = p.stem.lower()
        by_path[str(p).lower()].add(vid)
        by_filename[p.name.lower()].add(vid)
        by_filename[stem].add(vid)

        # Full relative path from each project scan dir
        for scan_dir in roots.intersection(p.parents):
            rel = p.relative_to(scan_dir)
            by_suffix[str(rel).lower()].add(vid)
            by_suffix[str(rel.with_suffix("")).lower()].add(vid)
            cam_prefix = rel.parts[0].split("_")[0].lower()
            by_camera_prefix[f"{cam_prefix}/{stem}"].add(vid)

        # Legacy parent/name suffixes can collide with paths relative to another root.
        by_suffix[f"{p.parent.name}/{p.name}".lower()].add(vid)
        by_suffix[f"{p.parent.name}/{stem}".lower()].add(vid)
        by_cam_stem.setdefault(stem, []).append((vid, cam_lower))

    for key, candidates in by_camera_prefix.items():
        by_suffix.setdefault(key, candidates)

    return VideoPathLookup(
        by_path=by_path,
        by_suffix=by_suffix,
        by_cam_stem=by_cam_stem,
        cam_by_id=cam_by_id,
        by_filename=by_filename,
    )


def resolve_video_path(
    raw_path: str,
    lookup: VideoPathLookup,
    known_video_ids: set[str] | None = None,
    use_filename_match: bool = False,
) -> tuple[str | None, str]:
    """Resolve a CSV path string to a video_id.

    Returns (video_id | None, tier) where tier is one of:
      "exact_id"  – raw_path was already a known video_id
      "suffix"    – matched via an exact path or suffix
      "cam_stem"  – matched via camera-aware stem fallback
      "filename"  – matched via filename-only lookup (use_filename_match=True)
      "ambiguous" – multiple videos match at the most specific available tier
      ""          – no match
    """
    if known_video_ids and raw_path in known_video_ids:
        return raw_path, "exact_id"

    p = Path(normalize_path_str(raw_path))

    if candidates := lookup.by_path.get(str(p).lower()):
        return _resolve_candidates(candidates, "suffix")

    # An ambiguous match at the longest matching suffix must not fall through
    # to a shorter suffix or a stem guess.
    if p.name:
        for i in range(len(p.parts)):
            suffix = Path(*p.parts[i:])
            candidates = lookup.by_suffix.get(str(suffix).lower()) or lookup.by_suffix.get(
                str(suffix.with_suffix("")).lower()
            )
            if candidates:
                return _resolve_candidates(candidates, "suffix")

    # Camera-aware stem fallback: require camera folder substring match
    stem = p.stem.lower()
    csv_cam = p.parent.name.lower()
    candidates = lookup.by_cam_stem.get(stem, [])
    matching = {v for v, c in candidates if _cameras_share_token(csv_cam, c)}
    if matching:
        return _resolve_candidates(matching, "cam_stem")

    if use_filename_match:
        candidates = lookup.by_filename.get(p.name.lower()) or lookup.by_filename.get(stem)
        if candidates:
            return _resolve_candidates(candidates, "filename")

    return None, ""


def _resolve_candidates(candidates: set[str], tier: str) -> tuple[str | None, str]:
    if len(candidates) == 1:
        return next(iter(candidates)), tier
    return None, "ambiguous"
