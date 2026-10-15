"""Transactional Distance sample and carried-infant mutation helpers."""

from __future__ import annotations

import math

from sqlalchemy import func

from review_app.backend.camera_layout import deployment_key
from review_app.backend.db.models import (
    Behavior,
    DistanceIndividual,
    DistanceSample,
    DistanceSampleBehavior,
    DistanceVideoReview,
    ProjectDir,
    ReferenceFrame,
    ReferenceVideo,
    Video,
)
from review_app.backend.provider.distance_review import (
    reopen_individual_review,
    review_offsets,
)


def _record(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _offset(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Offset must be a non-negative integer in milliseconds")


def scheduled_sample(session, individual_id: str, offset_ms: int):
    _offset(offset_ms)
    individual = session.get(DistanceIndividual, individual_id)
    if individual is None:
        raise ValueError("Distance individual not found")
    review = session.get(DistanceVideoReview, individual.video_id)
    if review is None:
        raise ValueError("Verify the camera recording time before annotating samples")
    video = session.get(Video, individual.video_id)
    if offset_ms not in review_offsets(review, video.duration_sec):
        raise ValueError("Offset is not a scheduled even camera second")
    return individual, video


def save_distance_sample(
    utcnow,
    session,
    individual_id: str,
    offset_ms: int,
    *,
    reference_frame_id: str | None,
    skip_reason: str | None,
    behavior_ids: list[str] | None,
    infant_id: str | None,
    create_infant: bool,
    labeled_by: str,
    creation_offset_ms: int | float,
    scheduled_context=None,
    scheduled_offsets: set[int] | None = None,
    deferred_completion_ids: set[str] | None = None,
) -> dict:
    """Transaction-aware writer for parent and carried-infant evidence."""
    if not labeled_by or not labeled_by.strip():
        raise ValueError("Annotator is required")
    if create_infant and infant_id:
        raise ValueError("Select an existing infant or create one")
    skipping = skip_reason is not None
    if skipping and (
        skip_reason not in {"individual absent", "midpoint not visible"} or reference_frame_id
    ):
        raise ValueError("A skipped sample needs a supported reason and no reference frame")
    if scheduled_context is None:
        individual, video = scheduled_sample(session, individual_id, offset_ms)
    else:
        individual, video = scheduled_context
        if scheduled_offsets is None or offset_ms not in scheduled_offsets:
            raise ValueError("Offset is not a scheduled even camera second")
    if (
        isinstance(creation_offset_ms, bool)
        or not isinstance(creation_offset_ms, (int, float))
        or not math.isfinite(creation_offset_ms)
        or creation_offset_ms < 0
        or creation_offset_ms > (video.duration_sec or 0) * 1000
    ):
        raise ValueError("Infant creation position is invalid")
    frame = None
    if not skipping:
        frame = session.get(ReferenceFrame, reference_frame_id) if reference_frame_id else None
        if frame is None:
            raise ValueError("Select a prepared reference frame")
        reference = session.get(ReferenceVideo, frame.reference_video_id)
        roots = [
            (row.id, row.path)
            for row in session.query(ProjectDir).filter_by(project_id=video.project_id)
        ]
        scope = deployment_key(video.project_id, video.camera_id, video.video_path, roots)
        if not math.isfinite(frame.distance_m) or frame.distance_m <= 0:
            raise ValueError("Reference distance must be finite and positive")
        if (
            scope is None
            or deployment_key(
                reference.project_id, reference.camera_id, reference.video_path, roots
            )
            != scope
        ):
            raise ValueError("Reference frame must match the video's project and deployment")
    behaviors = set(behavior_ids or [])
    if behaviors != {
        bid for (bid,) in session.query(Behavior.id).filter(Behavior.id.in_(behaviors))
    }:
        raise ValueError("Behavior not found")
    infant = None
    if infant_id:
        infant = session.get(DistanceIndividual, infant_id)
        if infant is None or infant.video_id != video.video_id or infant.id == individual.id:
            raise ValueError("Carried infant must be another individual in this video")
        if infant.age != "Infant":
            raise ValueError("Select an individual with age Infant")
        if infant.species_id != individual.species_id:
            raise ValueError("Carried infant must have the carrier's species")
    current = (
        session.query(DistanceSample)
        .filter_by(individual_id=individual_id, offset_ms=offset_ms)
        .first()
    )
    if infant_id or create_infant:
        if individual.age == "Infant" or (current and current.carried_by_individual_id):
            raise ValueError("A carried infant cannot also carry an infant")
    if infant:
        infant_sample = (
            session.query(DistanceSample)
            .filter_by(individual_id=infant.id, offset_ms=offset_ms)
            .first()
        )
        if infant_sample and infant_sample.carried_by_individual_id not in (
            None,
            individual_id,
        ):
            raise ValueError("Infant is already carried by another individual at this sample")
        if (
            session.query(DistanceSample.id)
            .filter_by(carried_by_individual_id=infant.id, offset_ms=offset_ms)
            .first()
        ):
            raise ValueError("A carrier cannot also be a carried infant")
    now = utcnow()
    if create_infant:
        last_number = (
            session.query(func.max(DistanceIndividual.individual_number))
            .filter_by(video_id=video.video_id)
            .scalar()
        )
        infant = DistanceIndividual(
            video_id=video.video_id,
            individual_number=last_number + 1,
            species_id=individual.species_id,
            first_seen_offset_ms=individual.first_seen_offset_ms,
            anchor_x=individual.anchor_x,
            anchor_y=individual.anchor_y,
            anchor_width=individual.anchor_width,
            anchor_height=individual.anchor_height,
            age="Infant",
            sex="Unknown",
            labeled_by=labeled_by,
            labeled_at=now,
            updated_at=now,
        )
        session.add(infant)
        session.flush()
        review = session.get(DistanceVideoReview, video.video_id)
        if review:
            session.add_all(
                DistanceSample(
                    individual_id=infant.id,
                    offset_ms=sample_offset,
                    status="skipped",
                    skip_reason="individual absent",
                    labeled_by=labeled_by,
                    labeled_at=now,
                    updated_at=now,
                )
                for sample_offset in review_offsets(review, video.duration_sec)
                if sample_offset < creation_offset_ms
            )
            session.flush()
    children = (
        session.query(DistanceSample)
        .filter_by(carried_by_individual_id=individual_id, offset_ms=offset_ms)
        .all()
    )
    removed_children = []
    for child in children:
        if infant and child.individual_id == infant.id:
            continue
        removed_children.append(child.individual_id)
        session.delete(child)
    session.flush()
    for child_id in removed_children:
        if deferred_completion_ids is None:
            reopen_individual_review(session, session.get(DistanceIndividual, child_id))
        else:
            deferred_completion_ids.add(child_id)

    def write(target, carrier_id=None, sample_behaviors=()):
        row = (
            session.query(DistanceSample)
            .filter_by(individual_id=target.id, offset_ms=offset_ms)
            .first()
        )
        if row is None:
            row = DistanceSample(individual_id=target.id, offset_ms=offset_ms)
            session.add(row)
        row.status = "skipped" if skipping else "annotated"
        row.skip_reason = skip_reason
        row.reference_frame_id = frame.id if frame else None
        row.distance_m = frame.distance_m if frame else None
        row.carried_by_individual_id = carrier_id
        row.labeled_by, row.labeled_at, row.updated_at = labeled_by, now, now
        session.flush()
        session.query(DistanceSampleBehavior).filter_by(sample_id=row.id).delete(
            synchronize_session=False
        )
        for bid in sample_behaviors:
            session.add(DistanceSampleBehavior(sample_id=row.id, behavior_id=bid))
        if deferred_completion_ids is None:
            reopen_individual_review(session, target)
        else:
            deferred_completion_ids.add(target.id)
        target.updated_at = now
        return row

    # Behavior corrections to a carried sample keep its relationship when
    # the selected evidence still matches its carrier's annotation.
    carrier_id = None
    if current and current.carried_by_individual_id and frame:
        carrier_sample = (
            session.query(DistanceSample)
            .filter_by(individual_id=current.carried_by_individual_id, offset_ms=offset_ms)
            .first()
        )
        if carrier_sample and carrier_sample.reference_frame_id == frame.id:
            carrier_id = current.carried_by_individual_id
    row = write(individual, carrier_id, behaviors)
    if infant:
        write(infant, individual_id, behaviors)
    return {**_record(row), "infant_id": infant.id if infant else None}
