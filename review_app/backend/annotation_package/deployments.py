"""Portable deployment context for setup archives."""

from datetime import date
from pathlib import PurePosixPath

from review_app.backend.annotation_package.archive import (
    OPTIONAL_TEXT,
    TEXT,
    _record,
    _records,
    _strings,
)
from review_app.backend.annotation_package.scope import id_selection, safe_relative
from review_app.backend.db.models import (
    Deployment,
    DeploymentFolderLink,
    DeploymentFolderReview,
    ReferenceVideo,
    Video,
)

METADATA = (
    "name",
    "camera_id",
    "installation_date",
    "retrieval_date",
    "transect_id",
    "habitat",
    "notes",
)
DATES = ("installation_date", "retrieval_date")


def _folder_key(media):
    return media["root_id"], PurePosixPath(media["video_path"]).parent.as_posix()


def _reference_folders(context, work):
    species_work = context["distance"]["species_work"] if "distance" in context else work
    references = {
        unit["video_id"]
        for unit in species_work
        if any(tag["key"] == "reference_video" for tag in unit["tags"])
    }
    return {_folder_key(row) for row in context["media"] if row["video_id"] in references}


def _collect_folder_reviews(session, folders, deployment_ids):
    if not folders:
        return []
    reviews = (
        session.query(DeploymentFolderReview)
        .filter(DeploymentFolderReview.project_dir_id.in_(id_selection({r for r, _ in folders})))
        .order_by(DeploymentFolderReview.project_dir_id, DeploymentFolderReview.folder_path)
    )
    return [
        {
            "root_id": review.project_dir_id,
            "folder_path": review.folder_path,
            "note": review.note,
            "available_candidates": sorted(
                deployment_ids.intersection(review.available_candidates)
            ),
        }
        for review in reviews
        if (review.project_dir_id, review.folder_path) in folders
    ]


def collect(session, project_id, context, work, roots):
    media = context["media"]
    videos = {
        row.video_id: row
        for row in session.query(Video).filter(
            Video.project_id == project_id,
            Video.video_id.in_(id_selection(row["video_id"] for row in media)),
        )
    }
    reference_media = {
        row["id"]: row["video_id"] for row in context.get("distance", {}).get("references", [])
    }
    from review_app.backend.provider.deployments import resolved_scope

    root_pairs = [(root.id, root.path) for root in roots]
    references = {}
    for row in session.query(ReferenceVideo).filter(
        ReferenceVideo.project_id == project_id,
        ReferenceVideo.id.in_(id_selection(reference_media)),
    ):
        deployment_id = row.deployment_id
        if deployment_id is None:
            scope = resolved_scope(session, row, root_pairs)
            if scope and scope[1] == "deployment":
                deployment_id = scope[2]
        references[reference_media[row.id]] = deployment_id
    assignments = {
        key: videos[key].deployment_id if key in videos else references.get(key)
        for key in (row["video_id"] for row in media)
    }
    keys = {key for key in (*assignments.values(), *references.values()) if key is not None}
    reviews = _collect_folder_reviews(session, _reference_folders(context, work), keys)
    if (
        not keys
        and not reviews
        and all(row.deployment_assignment == "legacy" for row in videos.values())
    ):
        return None
    deployments = {
        row.id: row
        for row in session.query(Deployment).filter(
            Deployment.project_id == project_id, Deployment.id.in_(id_selection(keys))
        )
    }
    if keys != deployments.keys():
        raise ValueError("Assignment footage links to a deployment outside its project")

    folders = {_folder_key(row) for row in media}
    links = session.query(DeploymentFolderLink).filter(
        DeploymentFolderLink.project_dir_id.in_(id_selection({root for root, _ in folders}))
    )
    return {
        "records": [
            {
                "id": row.id,
                "deployment_id": row.deployment_id,
                **{
                    key: getattr(row, key).isoformat()
                    if key in DATES and getattr(row, key)
                    else getattr(row, key)
                    for key in METADATA
                },
            }
            for row in sorted(deployments.values(), key=lambda row: row.id)
        ],
        "media": [
            {"video_id": key, "deployment_id": deployment_id}
            for key, deployment_id in assignments.items()
        ],
        "references": [
            {"video_id": key, "deployment_id": references[key]} for key in sorted(references)
        ],
        "folders": [
            {
                "root_id": link.project_dir_id,
                "folder_path": link.folder_path,
                "deployment_id": link.deployment_id,
                "source": link.source,
            }
            for link in links.order_by(
                DeploymentFolderLink.project_dir_id, DeploymentFolderLink.folder_path
            )
            if (link.project_dir_id, link.folder_path) in folders
            and (link.deployment_id is None or link.deployment_id in deployments)
        ],
        **({"folder_reviews": reviews} if reviews else {}),
    }


def validate(snapshot, context, work, *, include_reviews):
    schema = {key: (list,) for key in ("records", "media", "references", "folders")}
    if include_reviews:
        schema["folder_reviews"] = (list,)
    _record(snapshot, schema)
    deployments = _records(
        snapshot["records"],
        {"id": TEXT, "deployment_id": TEXT, **dict.fromkeys(METADATA, OPTIONAL_TEXT)},
        "id",
    )
    identifiers = [row["deployment_id"] for row in deployments.values()]
    if any(not key.strip() for key in identifiers) or len(identifiers) != len(set(identifiers)):
        raise ValueError("Invalid deployment identifiers")
    for row in deployments.values():
        for key in DATES:
            if row[key] is not None:
                date.fromisoformat(row[key])

    link_schema = {"video_id": TEXT, "deployment_id": OPTIONAL_TEXT}
    media = _records(snapshot["media"], link_schema, "video_id")
    references = _records(snapshot["references"], link_schema, "video_id")
    if media.keys() != {row["video_id"] for row in context["media"]} or references.keys() != {
        row["video_id"] for row in context.get("distance", {}).get("references", [])
    }:
        raise ValueError("Deployment links must match packaged footage")
    linked = {
        row["deployment_id"]
        for row in (*media.values(), *references.values())
        if row["deployment_id"] is not None
    }
    if linked != deployments.keys():
        raise ValueError("Deployment records must match footage links")

    folders = {_folder_key(row) for row in context["media"]}
    seen = set()
    for row in snapshot["folders"]:
        _record(
            row,
            {"root_id": TEXT, "folder_path": TEXT, "deployment_id": OPTIONAL_TEXT, "source": TEXT},
        )
        key = row["root_id"], row["folder_path"]
        if row["folder_path"] != ".":
            safe_relative(row["folder_path"])
        if (
            key not in folders
            or key in seen
            or (row["deployment_id"] is not None and row["deployment_id"] not in deployments)
        ):
            raise ValueError("Invalid deployment folder link")
        seen.add(key)

    if include_reviews:
        reference_folders = _reference_folders(context, work)
        seen = set()
        for row in snapshot["folder_reviews"]:
            _record(
                row,
                {
                    "root_id": TEXT,
                    "folder_path": TEXT,
                    "note": OPTIONAL_TEXT,
                    "available_candidates": (list,),
                },
            )
            key = row["root_id"], row["folder_path"]
            if (
                key not in reference_folders
                or key in seen
                or not _strings(row["available_candidates"]) <= deployments.keys()
            ):
                raise ValueError("Invalid deployment folder review")
            seen.add(key)


def restore(session, project_id, snapshot, identities, videos, paths):
    local = {None: None}
    for row in snapshot["records"]:
        attributes = {key: row[key] for key in ("deployment_id", *METADATA)}
        for key in DATES:
            if attributes[key] is not None:
                attributes[key] = date.fromisoformat(attributes[key])
        deployment = Deployment(project_id=project_id, **attributes)
        session.add(deployment)
        local[row["id"]] = deployment
    session.flush()
    local = {key: row.id if row else None for key, row in local.items()}
    local_videos = {
        row.video_id: row for row in session.query(Video).filter_by(project_id=project_id)
    }
    for row in snapshot["media"]:
        video = local_videos[videos[row["video_id"]]]
        video.deployment_id = local[row["deployment_id"]]
        video.deployment_assignment = "manual"
    references = {
        row.video_path: row
        for row in session.query(ReferenceVideo).filter_by(project_id=project_id)
    }
    for row in snapshot["references"]:
        references[paths[row["video_id"]][0]].deployment_id = local[row["deployment_id"]]
    for row in snapshot["folders"]:
        session.add(
            DeploymentFolderLink(
                project_dir_id=identities.local("root", row["root_id"]),
                folder_path=row["folder_path"],
                deployment_id=local[row["deployment_id"]],
                source=row["source"],
            )
        )
    for row in snapshot.get("folder_reviews", []):
        session.add(
            DeploymentFolderReview(
                project_dir_id=identities.local("root", row["root_id"]),
                folder_path=row["folder_path"],
                note=row["note"],
                available_candidates=sorted(local[key] for key in row["available_candidates"]),
            )
        )
