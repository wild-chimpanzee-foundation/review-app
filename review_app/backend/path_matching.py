from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class VideoPathLookup(NamedTuple):
    by_suffix: dict[str, str]
    # stem -> [(video_id, camera_id_lower)] — all videos sharing that stem
    by_cam_stem: dict[str, list[tuple[str, str]]]
    cam_by_id: dict[str, str]  # video_id -> camera_id_lower
    # filename (basename only) -> video_id — only populated for unambiguous filenames
    by_filename: dict[str, str]


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
    by_suffix: dict[str, str] = {}
    cam_stem_to_id: dict[str, str] = {}
    cam_stem_count: dict[str, int] = {}
    by_cam_stem: dict[str, list[tuple[str, str]]] = {}
    cam_by_id: dict[str, str] = {}
    by_filename_lists: dict[str, list[str]] = {}

    legacy_suffix_lists: dict[str, list[str]] = {}

    for vid, video_path, camera_id in video_rows:
        p = Path(video_path)
        cam_lower = (camera_id or "").lower()
        cam_by_id[vid] = cam_lower
        by_filename_lists.setdefault(p.name.lower(), []).append(vid)
        by_filename_lists.setdefault(p.stem.lower(), []).append(vid)

        # Full relative path from each project scan dir
        for scan_dir in scan_dirs:
            try:
                rel = p.relative_to(scan_dir)
                by_suffix[str(rel).lower()] = vid
                by_suffix[str(rel.with_suffix("")).lower()] = vid
                # Short cam-ID prefix (first _-segment of top-level folder) → by_suffix
                if rel.parts:
                    cam_prefix = rel.parts[0].split("_")[0].lower()
                    key = f"{cam_prefix}/{p.stem.lower()}"
                    cam_stem_to_id[key] = vid
                    cam_stem_count[key] = cam_stem_count.get(key, 0) + 1
            except ValueError:
                continue

        # Legacy parent_dir/name fallback — collect first, add only if unambiguous
        legacy_suffix_lists.setdefault(f"{p.parent.name}/{p.name}".lower(), []).append(vid)
        legacy_suffix_lists.setdefault(f"{p.parent.name}/{p.stem}".lower(), []).append(vid)

        # Camera-aware stem lookup (replaces the old by_stem)
        stem = p.stem.lower()
        by_cam_stem.setdefault(stem, []).append((vid, cam_lower))

    # Merge unambiguous cam-prefix entries into by_suffix
    for key, vid in cam_stem_to_id.items():
        if cam_stem_count[key] == 1:
            by_suffix.setdefault(key, vid)

    # Only add legacy parent/name entries when they're unambiguous across all DB videos
    for key, vids in legacy_suffix_lists.items():
        if len(vids) == 1:
            by_suffix.setdefault(key, vids[0])

    by_filename = {k: v[0] for k, v in by_filename_lists.items() if len(v) == 1}

    return VideoPathLookup(
        by_suffix=by_suffix,
        by_cam_stem=by_cam_stem,
        cam_by_id=cam_by_id,
        by_filename=by_filename,
    )


def resolve_video_path(
    raw_path: str,
    lookup: VideoPathLookup,
    known_video_ids: set[str] | None = None,
    extra_suffix_map: dict[str, str] | None = None,
    use_filename_match: bool = False,
) -> tuple[str | None, str]:
    """Resolve a CSV path string to a video_id.

    Returns (video_id | None, tier) where tier is one of:
      "exact_id"  – raw_path was already a known video_id
      "suffix"    – matched via by_suffix or extra_suffix_map
      "cam_stem"  – matched via camera-aware stem fallback
      "filename"  – matched via filename-only lookup (use_filename_match=True)
      ""          – no match
    """
    if known_video_ids and raw_path in known_video_ids:
        return raw_path, "exact_id"

    p = Path(raw_path)
    raw_lower = raw_path.lower()

    if extra_suffix_map:
        vid = extra_suffix_map.get(raw_lower)
        if vid:
            return vid, "suffix"

    # Try full path first, then progressively shorter suffixes (longest = most specific first).
    # This ensures e.g. "CamA/DCIM/100EK113/file.mp4" is preferred over the ambiguous
    # short fallback "100EK113/file.mp4" when two cameras share the same sub-folder structure.
    parts = p.parts
    for i in range(len(parts)):
        suffix_str = str(Path(*parts[i:])).lower() if i > 0 else raw_lower
        vid = lookup.by_suffix.get(suffix_str) or lookup.by_suffix.get(
            str(Path(*parts[i:]).with_suffix("")).lower()
        )
        if vid:
            return vid, "suffix"

    # Camera-aware stem fallback: require camera folder substring match
    stem = p.stem.lower()
    csv_cam = p.parent.name.lower()
    candidates = lookup.by_cam_stem.get(stem, [])
    matching = [(v, c) for v, c in candidates if _cameras_share_token(csv_cam, c)]
    if len(matching) == 1:
        return matching[0][0], "cam_stem"

    if use_filename_match:
        vid = lookup.by_filename.get(p.name.lower()) or lookup.by_filename.get(p.stem.lower())
        if vid:
            return vid, "filename"

    return None, ""
