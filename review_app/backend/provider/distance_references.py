"""Distance queue and deployment-scoped reference discovery and preparation."""

from __future__ import annotations

import math
import uuid

from sqlalchemy import func, literal, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from review_app.backend.camera_layout import deployment_key
from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceReviewTask,
    DistanceSample,
    Project,
    ProjectDir,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Tag,
    Video,
    VideoTag,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.distance_review import reopen_individual_review
from review_app.backend.provider.distance_tasks import ensure_tasks


def _record(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


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
        .filter(
            Video.project_id == project_id, Video.is_missing.is_(False), ~reference_tagged(session)
        )
    )
    if camera_id:
        query = query.filter(Video.camera_id == camera_id)
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
            Video.camera_id, Video.video_path, DistanceReviewTask.species_id
        )
    ]


def register_reference_video(session, project_id: str, camera_id: str, video_path: str) -> dict:
    if not project_id or not camera_id or not camera_id.strip():
        raise ValueError("Reference videos require a project and camera")
    if not video_path or not video_path.strip():
        raise ValueError("Reference videos require a video path")
    if session.get(Project, project_id) is None:
        raise ValueError("Project not found")
    row = ReferenceVideo(
        project_id=project_id,
        camera_id=camera_id.strip(),
        video_path=normalize_path_str(video_path),
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
        .filter_by(project_id=project_id, camera_id=camera_id.strip())
        .order_by(ReferenceVideo.video_path, ReferenceVideo.id)
    ]


def get_distance_reference_videos(session, video_id: str) -> list[dict]:
    """Discover tagged clips while retaining references for saved sample evidence."""
    video = session.get(Video, video_id)
    if video is None:
        raise ValueError("Video not found")
    project_id, camera_id = video.project_id, video.camera_id
    if not project_id or not camera_id or not camera_id.strip():
        raise ValueError("Resolve the animal video's project and camera before distance review")
    roots = [
        (row.id, row.path) for row in session.query(ProjectDir).filter_by(project_id=project_id)
    ]
    scope = deployment_key(project_id, camera_id, video.video_path, roots)
    if scope is None:
        raise ValueError("Resolve the video's import root before matching references")
    sources = {
        normalize_path_str(source.video_path): source
        for source in session.query(Video).filter(
            Video.project_id == project_id,
            Video.camera_id == camera_id.strip(),
            Video.is_missing.is_(False),
            reference_tagged(session),
        )
        if deployment_key(project_id, source.camera_id, source.video_path, roots) == scope
    }
    if sources:
        session.execute(
            sqlite_insert(ReferenceVideo)
            .values(
                [
                    {"project_id": project_id, "camera_id": camera_id.strip(), "video_path": path}
                    for path in sources
                ]
            )
            .on_conflict_do_nothing(index_elements=["project_id", "video_path"])
        )
    saved_reference_ids = (
        session.query(ReferenceFrame.reference_video_id)
        .join(DistanceSample, DistanceSample.reference_frame_id == ReferenceFrame.id)
        .join(DistanceIndividual, DistanceSample.individual_id == DistanceIndividual.id)
        .filter(DistanceIndividual.video_id == video_id)
    )
    rows = (
        session.query(ReferenceVideo)
        .filter(
            ReferenceVideo.project_id == project_id,
            ReferenceVideo.camera_id == camera_id.strip(),
            or_(
                ReferenceVideo.video_path.in_(sources),
                ReferenceVideo.id.in_(saved_reference_ids),
            ),
        )
        .order_by(ReferenceVideo.video_path, ReferenceVideo.id)
    )
    result = []
    for row in rows:
        if deployment_key(project_id, row.camera_id, row.video_path, roots) != scope:
            continue
        source = sources.get(row.video_path)
        is_tagged = source is not None
        if source is None:
            source = (
                session.query(Video)
                .filter(
                    Video.project_id == project_id,
                    Video.camera_id == camera_id.strip(),
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
    """Fill an unknown distance without replacing an existing mapping.

    The conditional insert is one SQLite write statement so simultaneous first
    clicks cannot create two mappings for the same distance. Legacy prepared
    frames (including repeated distances) remain untouched.
    """
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
        reference_video_id=reference_video_id, distance_m=distance_m
    )
    session.execute(
        sqlite_insert(ReferenceFrame).from_select(
            ["id", "reference_video_id", "offset_ms", "distance_m", "playback_offset_ms"],
            select(
                literal(str(uuid.uuid4())),
                literal(reference_video_id),
                literal(offset_ms),
                literal(distance_m),
                literal(playback_offset_ms),
            ).where(~existing.exists()),
        )
    )
    return _record(existing.order_by(ReferenceFrame.offset_ms).first())


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
    if row is None:
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


def delete_reference_frame(session, frame_id: str, *, clear_samples: bool = False) -> int:
    """Remove a mapping, clearing its saved evidence only after explicit confirmation."""
    if not isinstance(clear_samples, bool):
        raise ValueError("Confirm whether to clear saved annotations")
    frame = session.get(ReferenceFrame, frame_id)
    if frame is None:
        raise ValueError("Reference frame not found")
    samples = session.query(DistanceSample).filter_by(reference_frame_id=frame_id).all()
    if samples and not clear_samples:
        raise ValueError("Confirm deletion to clear saved annotations using this reference frame")
    individual_ids = {sample.individual_id for sample in samples}
    for sample in samples:
        session.delete(sample)
    session.flush()
    for individual_id in individual_ids:
        reopen_individual_review(session, session.get(DistanceIndividual, individual_id))
    session.delete(frame)
    session.flush()
    return len(samples)


def list_reference_frames(session, reference_video_id: str) -> list[dict]:
    return [
        _record(row)
        for row in session.query(ReferenceFrame)
        .filter_by(reference_video_id=reference_video_id)
        .order_by(ReferenceFrame.offset_ms)
    ]
