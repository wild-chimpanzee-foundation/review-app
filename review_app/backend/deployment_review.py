"""Folder review summaries and advisory matches; never assign or rename anything.

Hints interpret familiar camera/transect spellings only to rank suggestions. The
inventory's opaque IDs are preserved and all writes still require explicit review.
"""

import re
from collections import Counter
from pathlib import PurePosixPath

from review_app.backend.path_matching import normalize_path_str


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
                "unmatched_references": 0,
                "exceptions": 0,
                "missing": 0,
                "assignments": Counter(),
                "link": linked.get(key),
            }
        group = groups[key]
        group["total"] += 1
        group["references" if video["reference"] else "ordinary"] += 1
        group["unresolved"] += video["deployment_id"] is None
        group["unmatched_references"] += bool(
            video["reference"]
            and video["deployment_id"]
            and video["deployment_id"] not in ordinary
        )
        group["exceptions"] += video["assignment"] == "manual"
        group["missing"] += video["missing"]
        group["assignments"][video["deployment"]] += 1
    for group in groups.values():
        group["assignments"] = [
            {"deployment": label, "count": count}
            for label, count in sorted(
                group["assignments"].items(), key=lambda item: item[0] or ""
            )
        ]
        group["needs_attention"] = bool(group["unresolved"] or group["unmatched_references"])
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


def rank_folder_deployments(folder, inventory, limit=5):
    """Rank edit-distance suggestions; scores are similarity, not confidence.

    A matching transect takes precedence over a camera reused elsewhere. Leading
    zeros and shortened VR names are ranking hints, never automatic assignment rules.
    """
    parts = PurePosixPath(normalize_path_str(folder["folder"])).parts
    leaf = parts[-1] if parts else ""
    if _key(leaf) in {"videos", "references", "reference", "vr", "ref"} and len(parts) > 1:
        leaf = parts[-2]
    # Discard long numeric suffixes for suggestions, not scientific metadata.
    core = re.split(r"[\s_-]+\d{5,}", leaf, maxsplit=1)[0]
    core = re.sub(r"[\s_-]+(?:vr|ref|references?)$", "", core, flags=re.IGNORECASE)
    full = re.match(r"^(\d+)[\s_-]+cam[\s_-]*(\d+)(?:$|[\s_-])", core, re.IGNORECASE)
    camera = re.search(r"\bcam[\s_-]*(\d+)", core.replace("_", " "), re.IGNORECASE)
    transect_raw = next(
        (
            m[1]
            for part in reversed(parts[:-1])
            if (m := re.fullmatch(r"t[\s_-]*(\d+)", part, re.IGNORECASE))
        ),
        None,
    )
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
    ranked = []
    for row in inventory:
        identifier = _key(row["deployment_id"])
        match = re.match(
            r"^(\d+)[\s_-]+cam[\s_-]*(\d+)(?:$|[\s_-])", row["deployment_id"], re.IGNORECASE
        )
        candidate_transect = _number(row.get("transect_id") or "") or (
            _number(match[1]) if match else None
        )
        candidate_camera = re.search(
            r"cam[\s_-]*(\d+)", row.get("camera_id") or row["deployment_id"], re.IGNORECASE
        )
        same_transect = transect is not None and candidate_transect == transect
        different_transect = (
            transect is not None
            and candidate_transect is not None
            and candidate_transect != transect
        )
        same_camera = (
            camera_number is not None
            and candidate_camera is not None
            and _number(candidate_camera[1]) == camera_number
        )
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
        if not full and camera and transect_raw:
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
