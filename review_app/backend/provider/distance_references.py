"""Distance queue and deployment-scoped reference discovery and preparation."""

from __future__ import annotations

import math

from sqlalchemy import func, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from review_app.backend.camera_layout import deployment_key
from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceSample,
    DistanceVideoReview,
    IndividualObservation,
    ModelAnnotation,
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
    query = session.query(Video).filter(
        Video.project_id == project_id,
        Video.is_missing.is_(False),
        ~reference_tagged(session),
    )
    if camera_id:
        query = query.filter(Video.camera_id == camera_id)
    if status:
        if status not in {"not_started", "in_progress", "completed"}:
            raise ValueError("Invalid distance review status")
        review_exists = session.query(DistanceVideoReview.video_id).filter(
            DistanceVideoReview.video_id == Video.video_id
        )
        if status == "not_started":
            query = query.filter(~review_exists.exists())
        elif status == "completed":
            query = query.filter(
                review_exists.filter(DistanceVideoReview.completed_at.is_not(None)).exists()
            )
        else:
            query = query.filter(
                review_exists.filter(DistanceVideoReview.completed_at.is_(None)).exists()
            )
    if species_id:
        species = session.get(Species, species_id)
        if species is None:
            raise ValueError("Species not found")
        query = query.filter(
            or_(
                session.query(DistanceIndividual.id)
                .filter_by(species_id=species_id)
                .filter(DistanceIndividual.video_id == Video.video_id)
                .exists(),
                session.query(IndividualObservation.id)
                .filter_by(species_id=species_id)
                .filter(IndividualObservation.video_id == Video.video_id)
                .exists(),
                session.query(ModelAnnotation.id)
                .filter(
                    ModelAnnotation.video_id == Video.video_id,
                    ModelAnnotation.annotation_type.in_(("species", "object_detection")),
                    ModelAnnotation.value_text == species.scientific_name,
                )
                .exists(),
            )
        )
    return [_record(row) for row in query.order_by(Video.camera_id, Video.video_path)]


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
    _offset(offset_ms)
    if playback_offset_ms is not None and (
        isinstance(playback_offset_ms, bool)
        or not isinstance(playback_offset_ms, (int, float))
        or not math.isfinite(playback_offset_ms)
        or playback_offset_ms < 0
        or math.floor(playback_offset_ms + 0.5) != offset_ms
    ):
        raise ValueError("Playback position must round to the reference frame offset")
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


def list_reference_frames(session, reference_video_id: str) -> list[dict]:
    return [
        _record(row)
        for row in session.query(ReferenceFrame)
        .filter_by(reference_video_id=reference_video_id)
        .order_by(ReferenceFrame.offset_ms)
    ]
