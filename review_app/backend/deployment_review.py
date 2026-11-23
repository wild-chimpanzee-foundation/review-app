"""Folder review summaries and advisory matches; never assign or rename anything.

Hints interpret familiar camera/transect spellings only to rank suggestions. The
inventory's opaque IDs are preserved and all writes still require explicit review.
"""

import re
from collections import Counter
from pathlib import PurePosixPath

from review_app.backend.path_matching import normalize_path_str

_FULL_CAMERA = re.compile(r"^(\d+)[\s_-]+cam[\s_-]*(\d+)(?:$|[\s_-])", re.IGNORECASE)
_CAMERA = re.compile(r"\bcam[\s_-]*(\d+)", re.IGNORECASE)
_TRANSECT = re.compile(r"t[\s_-]*(\d+)", re.IGNORECASE)


def group_deployment_folders(videos, links, *, ordinary=None):
    if ordinary is None:
        ordinary = {
            v["deployment_id"] for v in videos if not v["reference"] and v["deployment_id"]
        }
    linked = {(link["root_id"], link["folder"]): link for link in links}
    groups = {}
    for video in videos:
        folder = video["folder"]
        if folder is None:
            folder = str(PurePosixPath(normalize_path_str(video["video_path"])).parent)
        key = video["root_id"], folder
        if key not in groups:
            groups[key] = {
                "id": str(key),
                "root_id": key[0],
                "folder": folder,
                "total": 0,
                "ordinary": 0,
                "references": 0,
                "unresolved": 0,
                "unresolved_references": 0,
                "unmatched_references": 0,
                "exceptions": 0,
                "missing": 0,
                "assignments": Counter(),
                "reference_deployments": set(),
                "link": linked.get(key),
                "ordinary_unavailable": False,
                "new_comparison_candidates": [],
                "review_note": None,
            }
        group = groups[key]
        group["total"] += 1
        group["references" if video["reference"] else "ordinary"] += 1
        group["unresolved"] += video["deployment_id"] is None
        group["unresolved_references"] += video["reference"] and video["deployment_id"] is None
        group["unmatched_references"] += bool(
            video["reference"]
            and video["deployment_id"]
            and video["deployment_id"] not in ordinary
        )
        group["exceptions"] += video["assignment"] == "manual"
        group["missing"] += video["missing"]
        group["assignments"][video["deployment"]] += 1
        if video["reference"] and video["deployment_id"]:
            group["reference_deployments"].add(video["deployment_id"])
    for group in groups.values():
        group["reference_deployments"] = sorted(group["reference_deployments"])
        group["assignments"] = [
            {"deployment": label, "count": count}
            for label, count in sorted(
                group["assignments"].items(), key=lambda item: item[0] or ""
            )
        ]
        group["needs_reference_review"] = bool(
            group["unmatched_references"] or group["unresolved_references"]
        )
        group["needs_attention"] = bool(group["unresolved"] or group["needs_reference_review"])
    return sorted(
        groups.values(), key=lambda row: (row["folder"].casefold(), row["root_id"] or "")
    )


def _key(value):
    return re.sub(r"[\s_-]+", "_", str(value).strip().casefold())


def _distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _number(value):
    match = re.fullmatch(r"(?:t[\s_-]*)?(\d+)", str(value).strip(), re.IGNORECASE)
    return (match[1].lstrip("0") or "0") if match else None


def _transect(parts):
    return next(
        (match[1] for part in reversed(parts) if (match := _TRANSECT.fullmatch(part))), None
    )


def _camera_core(leaf):
    core = re.split(r"[\s_-]+\d{5,}", leaf, maxsplit=1)[0]
    return re.sub(r"[\s_-]+(?:vr|ref|references?)$", "", core, flags=re.IGNORECASE)


def rank_folder_deployments(folder, inventory, limit=5, *, ordinary_deployments=()):
    """Rank edit-distance suggestions; scores are similarity, not confidence.

    A matching transect takes precedence over a camera reused elsewhere. Leading
    zeros and shortened VR names are ranking hints, never automatic assignment rules.
    """
    parts = PurePosixPath(normalize_path_str(folder["folder"])).parts
    leaf = parts[-1] if parts else ""
    if _key(leaf) in {"videos", "references", "reference", "vr", "ref"} and len(parts) > 1:
        leaf = parts[-2]
    core = _camera_core(leaf)
    full = _FULL_CAMERA.match(core)
    camera = _CAMERA.search(core.replace("_", " "))
    transect_raw = _transect(parts[:-1])
    transect = _number(transect_raw) if transect_raw else _number(full[1]) if full else None
    camera_number = _number(camera[1]) if camera else None
    if full:
        query = _key(f"{full[1]}_cam{full[2]}")
    elif camera and transect_raw:
        query = _key(f"{transect_raw}_cam{camera[1]}")
    else:
        query = _key(core)
    if (
        not query
        or query in {".", "vr", "ref", "reference", "references", "videos"}
        and transect is None
    ):
        return []
    unmatched_reference = bool(folder.get("unmatched_references"))
    ranked = []
    for row in inventory:
        if unmatched_reference and row["id"] not in ordinary_deployments:
            continue
        path = PurePosixPath(normalize_path_str(row["deployment_id"]))
        core = _camera_core(path.name)
        match = _FULL_CAMERA.match(core)
        identifier = _key(f"{match[1]}_cam{match[2]}" if match else core)
        candidate_transect = _number(row.get("transect_id") or "") or _number(
            _transect(path.parts[:-1]) or (match[1] if match else "")
        )
        camera_metadata = (row.get("camera_id") or "").strip()
        camera_match = _CAMERA.search(camera_metadata.replace("_", " ")) or _CAMERA.search(
            core.replace("_", " ")
        )
        candidate_camera = _number(
            camera_metadata
            if camera_metadata.isdecimal()
            else camera_match[1]
            if camera_match
            else ""
        )
        same_transect = transect is not None and candidate_transect == transect
        different_transect = (
            transect is not None
            and candidate_transect is not None
            and candidate_transect != transect
        )
        same_camera = camera_number is not None and candidate_camera == camera_number
        if (
            unmatched_reference
            and transect
            and camera_number
            and not (same_transect or same_camera)
        ):
            continue
        queries = {query}
        if not full and not (camera and transect_raw):
            queries.add("_".join(query.split("_")[: len(identifier.split("_"))]))
        distance, compared = min((_distance(identifier, text), text) for text in queries)
        similarity = round(100 * (1 - distance / max(len(identifier), len(compared), 1)))
        if similarity < 40 and not same_transect and not same_camera:
            continue
        reasons = []
        if same_transect:
            reasons.append("deployment_reason_same_transect")
        if different_transect:
            reasons.append("deployment_reason_other_transect")
        if same_camera:
            reasons.append("deployment_reason_same_camera")
        elif camera and candidate_camera:
            reasons.append("deployment_reason_other_camera")
        if full and match and full[1] != match[1] and _number(full[1]) == _number(match[1]):
            reasons.append("deployment_reason_zero_padding")
        if not full and same_camera and same_transect:
            reasons.append("deployment_reason_short_reference")
        reasons.append("deployment_reason_edit_distance")
        ranked.append(
            (
                (
                    different_transect,
                    not same_transect,
                    not same_camera,
                    -similarity,
                    distance,
                    identifier,
                    row["id"],
                ),
                {
                    "id": row["id"],
                    "deployment_id": row["deployment_id"],
                    "name": row.get("name"),
                    "distance": distance,
                    "similarity": similarity,
                    "query": compared,
                    "reasons": reasons,
                },
            )
        )
    return [row for _, row in sorted(ranked, key=lambda pair: pair[0])[:limit]]


def available_folder_candidates(folder, inventory, ordinary):
    """Identify plausible available matches strong enough to reopen folder review."""
    candidates = rank_folder_deployments(
        folder | {"unmatched_references": True},
        inventory,
        limit=len(inventory),
        ordinary_deployments=ordinary,
    )
    mismatches = {"deployment_reason_other_camera", "deployment_reason_other_transect"}
    return ordinary.intersection(folder["reference_deployments"]) | {
        row["id"]
        for row in candidates
        if row["distance"] == 0 and not mismatches.intersection(row["reasons"])
    }


def summarize_folder_preview(preview):
    groups = Counter(
        (
            r["before_label"],
            r["before_label"] if r["preserved"] else r["after_label"],
            r["preserved"],
            r["blocked"],
        )
        for r in preview["rows"]
    )
    return [
        {
            "before": before,
            "after": after,
            "preserved": preserved,
            "blocked": blocked,
            "count": count,
        }
        for (before, after, preserved, blocked), count in groups.items()
    ]
