"""Distance queue and deployment-scoped reference discovery and preparation."""

from __future__ import annotations

import math
import uuid

from sqlalchemy import func, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import contains_eager

from review_app.backend.db.models import (
    Deployment,
    DistanceIndividual,
    DistanceReviewTask,
    DistanceSample,
    Project,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Tag,
    Video,
    VideoTag,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.deployments import (
    assign_reference_deployment,
    resolve_reference_deployment,
    video_deployment_for_label,
)
from review_app.backend.provider.distance_review import reopen_individual_review
from review_app.backend.provider.distance_tasks import ensure_tasks


def _record(row) -> dict:
    record = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in {"retired", "unresolved_camera_label"}
    }
    if isinstance(row, (Video, ReferenceVideo)):
        record["camera_id"] = row.camera_id
    return record


def _offset(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Offset must be a non-negative integer in milliseconds")


def reference_tagged(session):
    return (
        session.query(VideoTag.video_id)
        .join(Tag, VideoTag.tag_id == Tag.id)
        .filter(Tag.key == "reference_video", VideoTag.video_id == Video.video_id)
        .exists()
    )


def get_distance_video_queue(
    session,
    project_id: str,
    species_id: str | None = None,
    *,
    camera_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    ensure_tasks(session, project_id)
    query = (
        session.query(Video, DistanceReviewTask)
        .join(DistanceReviewTask, DistanceReviewTask.video_id == Video.video_id)
        .join(Deployment, Deployment.id == Video.deployment_id)
        .options(contains_eager(Video.deployment))
        .filter(
            Video.project_id == project_id,
            Video.is_missing.is_(False),
            Video.deployment_id.is_not(None),
            ~reference_tagged(session),
        )
    )
    if camera_id:
        query = query.filter(Deployment.deployment_id == camera_id)
    if species_id:
        if session.get(Species, species_id) is None:
            raise ValueError("Species not found")
        query = query.filter(DistanceReviewTask.species_id == species_id)
    if status:
        if status not in {"not_started", "in_progress", "completed"}:
            raise ValueError("Invalid distance review status")
        if status == "not_started":
            query = query.filter(
                DistanceReviewTask.verified_at.is_(None), DistanceReviewTask.completed_at.is_(None)
            )
        elif status == "completed":
            query = query.filter(DistanceReviewTask.completed_at.is_not(None))
        else:
            query = query.filter(
                DistanceReviewTask.verified_at.is_not(None),
                DistanceReviewTask.completed_at.is_(None),
            )
    return [
        {
            **_record(video),
            "task_id": task.id,
            "species_id": task.species_id,
            "source_count": task.source_count,
            "completion_outcome": task.completion_outcome,
        }
        for video, task in query.order_by(
            Deployment.deployment_id, Video.video_path, DistanceReviewTask.species_id
        )
    ]


def register_reference_video(session, project_id: str, camera_id: str, video_path: str) -> dict:
    if not project_id or not camera_id or not camera_id.strip():
        raise ValueError("Reference videos require a project and camera")
    if not video_path or not video_path.strip():
        raise ValueError("Reference videos require a video path")
    if session.get(Project, project_id) is None:
        raise ValueError("Project not found")
    label, path = camera_id.strip(), normalize_path_str(video_path)
    deployment_id = resolve_reference_deployment(session, project_id, path, label)
    row = ReferenceVideo(
        project_id=project_id,
        unresolved_camera_label=label if deployment_id is None else None,
        deployment_id=deployment_id,
        video_path=path,
    )
    session.add(row)
    session.flush()
    return _record(row)


def list_reference_videos(session, project_id: str, camera_id: str) -> list[dict]:
    if not project_id or not camera_id or not camera_id.strip():
        raise ValueError("Reference lookup requires a project and camera")
    return [
        _record(row)
        for row in session.query(ReferenceVideo)
        .filter(
            ReferenceVideo.project_id == project_id, ReferenceVideo.camera_id == camera_id.strip()
        )
        .order_by(ReferenceVideo.video_path, ReferenceVideo.id)
    ]


def get_distance_reference_videos(session, task_or_video_id: str) -> list[dict]:
    """Discover tagged clips while retaining references for saved sample evidence."""
    task = session.get(DistanceReviewTask, task_or_video_id)
    video = session.get(Video, task.video_id if task else task_or_video_id)
    if video is None:
        raise ValueError("Video not found")
    project_id, deployment_id = video.project_id, video.deployment_id
    if not deployment_id:
        raise ValueError(
            "Resolve the animal video's project and deployment before distance review"
        )
    sources = {
        normalize_path_str(source.video_path): source
        for source in session.query(Video).filter(
            Video.deployment_id == deployment_id,
            Video.is_missing.is_(False),
            reference_tagged(session),
        )
    }
    if sources:
        session.execute(
            sqlite_insert(ReferenceVideo)
            .values(
                [
                    {
                        "project_id": project_id,
                        "video_path": path,
                        "deployment_id": deployment_id,
                    }
                    for path in sources
                ]
            )
            .on_conflict_do_nothing(index_elements=["project_id", "video_path"])
        )
    saved_reference_ids = (
        session.query(ReferenceFrame.reference_video_id)
        .join(DistanceSample, DistanceSample.reference_frame_id == ReferenceFrame.id)
        .join(DistanceIndividual, DistanceSample.individual_id == DistanceIndividual.id)
        .join(DistanceIndividual.task)
        .filter(
            DistanceReviewTask.id == task.id
            if task
            else DistanceReviewTask.video_id == video.video_id
        )
    )
    rows = (
        session.query(ReferenceVideo)
        .filter(
            ReferenceVideo.project_id == project_id,
            or_(
                ReferenceVideo.video_path.in_(sources),
                ReferenceVideo.id.in_(saved_reference_ids),
            ),
        )
        .order_by(ReferenceVideo.video_path, ReferenceVideo.id)
    )
    result = []
    for row in rows:
        if row.deployment_id is None and row.video_path in sources:
            candidate = video_deployment_for_label(
                sources[row.video_path], row.unresolved_camera_label
            )
            if candidate == deployment_id:
                assign_reference_deployment(row, candidate)
        if row.deployment_id != deployment_id:
            continue
        source = sources.get(row.video_path)
        is_tagged = source is not None
        if source is None:
            source = (
                session.query(Video)
                .filter(
                    Video.project_id == project_id,
                    func.replace(Video.video_path, "\\", "/") == row.video_path,
                )
                .first()
            )
        result.append(
            {
                **_record(row),
                "is_tagged": is_tagged,
                "source_video_id": source.video_id if source else None,
                "transcoded_path": source.transcoded_path if source else None,
            }
        )
    return result


def add_reference_frame(
    session,
    reference_video_id: str,
    offset_ms: int,
    distance_m: float,
    *,
    playback_offset_ms: float | None = None,
) -> dict:
    _validate_reference_position(offset_ms, playback_offset_ms)
    if (
        isinstance(distance_m, bool)
        or not isinstance(distance_m, (int, float))
        or not math.isfinite(distance_m)
        or distance_m <= 0
    ):
        raise ValueError("Reference distance must be a finite positive number")
    if session.get(ReferenceVideo, reference_video_id) is None:
        raise ValueError("Reference video not found")
    retired = (
        session.query(ReferenceFrame)
        .filter_by(
            reference_video_id=reference_video_id,
            distance_m=distance_m,
            offset_ms=offset_ms,
            retired=True,
        )
        .one_or_none()
    )
    if retired is not None:
        retired.retired = False
        retired.playback_offset_ms = playback_offset_ms
        session.flush()
        return _record(retired)
    row = ReferenceFrame(
        reference_video_id=reference_video_id,
        offset_ms=offset_ms,
        playback_offset_ms=playback_offset_ms,
        distance_m=distance_m,
    )
    session.add(row)
    session.flush()
    return _record(row)


def _validate_reference_position(offset_ms: int, playback_offset_ms: float | None) -> None:
    _offset(offset_ms)
    if playback_offset_ms is not None and (
        isinstance(playback_offset_ms, bool)
        or not isinstance(playback_offset_ms, (int, float))
        or not math.isfinite(playback_offset_ms)
        or playback_offset_ms < 0
        or math.floor(playback_offset_ms + 0.5) != offset_ms
    ):
        raise ValueError("Playback position must round to the reference frame offset")


def configure_reference_distance(
    session,
    reference_video_id: str,
    offset_ms: int,
    distance_m: float,
    *,
    playback_offset_ms: float | None = None,
) -> dict:
    """Save a frame or reuse the existing mapping at the same distance and offset."""
    _validate_reference_position(offset_ms, playback_offset_ms)
    if (
        isinstance(distance_m, bool)
        or not isinstance(distance_m, (int, float))
        or not math.isfinite(distance_m)
        or distance_m <= 0
    ):
        raise ValueError("Reference distance must be a finite positive number")
    if session.get(ReferenceVideo, reference_video_id) is None:
        raise ValueError("Reference video not found")
    existing = session.query(ReferenceFrame).filter_by(
        reference_video_id=reference_video_id, distance_m=distance_m, offset_ms=offset_ms
    )
    session.execute(
        sqlite_insert(ReferenceFrame)
        .values(
            id=str(uuid.uuid4()),
            reference_video_id=reference_video_id,
            offset_ms=offset_ms,
            distance_m=distance_m,
            playback_offset_ms=playback_offset_ms,
        )
        .on_conflict_do_update(
            index_elements=["reference_video_id", "distance_m", "offset_ms"],
            set_={"retired": False, "playback_offset_ms": playback_offset_ms},
            where=ReferenceFrame.retired.is_(True),
        )
    )
    return _record(existing.one())


def update_reference_frame_position(
    session,
    frame_id: str,
    offset_ms: int,
    *,
    playback_offset_ms: float | None = None,
) -> dict:
    """Explicitly correct a reference position, retaining its identity and meters."""
    _validate_reference_position(offset_ms, playback_offset_ms)
    row = session.get(ReferenceFrame, frame_id)
    if row is None or row.retired:
        raise ValueError("Reference frame not found")
    row.offset_ms = offset_ms
    row.playback_offset_ms = playback_offset_ms
    session.flush()
    return _record(row)


def count_reference_frame_samples(session, frame_id: str) -> int:
    """Count all saved samples that deleting this mapping would clear."""
    if session.get(ReferenceFrame, frame_id) is None:
        raise ValueError("Reference frame not found")
    return session.query(DistanceSample).filter_by(reference_frame_id=frame_id).count()


def delete_reference_frame(session, frame_id: str) -> int:
    """Retire a mapping while preserving saved measurements and their completion."""
    frame = session.get(ReferenceFrame, frame_id)
    if frame is None or frame.retired:
        raise ValueError("Reference frame not found")
    frame.retired = True
    session.flush()
    return count_reference_frame_samples(session, frame_id)


def clear_reference_frame_samples(session, frame_id: str) -> int:
    """Clear measurements before explicitly replacing or deleting their reference image."""
    samples = session.query(DistanceSample).filter_by(reference_frame_id=frame_id).all()
    individual_ids = {sample.individual_id for sample in samples}
    for sample in samples:
        session.delete(sample)
    session.flush()
    for individual_id in individual_ids:
        reopen_individual_review(session, session.get(DistanceIndividual, individual_id))
    return len(samples)


def list_reference_frames(session, reference_video_id: str) -> list[dict]:
    return [
        _record(row)
        for row in session.query(ReferenceFrame)
        .filter_by(reference_video_id=reference_video_id, retired=False)
        .order_by(ReferenceFrame.offset_ms, ReferenceFrame.distance_m, ReferenceFrame.id)
    ]
