"""Distance identity creation, correction, and relationship maintenance."""

from __future__ import annotations

import math

from sqlalchemy import func, or_

from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceIndividualTag,
    DistanceSample,
    Species,
    Tag,
    Video,
)
from review_app.backend.provider.distance_review import (
    mark_review_started,
    refresh_individual_completion,
    reopen_individual_review,
    review_offsets,
)
from review_app.backend.provider.distance_tasks import clear_completion, resolve_task

AGES = {"Adult", "Adolescent", "Juvenile", "Infant", "Unknown"}
SEXES = {"Male", "Female", "Unknown"}


def _record(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _offset(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Offset must be a non-negative integer in milliseconds")


def _attributes(age: str | None, sex: str | None) -> None:
    if age is not None and age not in AGES:
        raise ValueError("Invalid distance individual age")
    if sex is not None and sex not in SEXES:
        raise ValueError("Invalid distance individual sex")


def _anchor_fields(anchor: dict[str, float] | None) -> dict[str, float | None]:
    fields = dict.fromkeys(("anchor_x", "anchor_y", "anchor_width", "anchor_height"))
    if anchor is None:
        return fields
    if set(anchor) != {"x", "y", "width", "height"}:
        raise ValueError("Anchor bounding box must contain x, y, width, and height")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in anchor.values()
    ):
        raise ValueError("Anchor coordinates must be finite numbers")
    x, y = anchor["x"], anchor["y"]
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        raise ValueError("Anchor box must be within the normalized video frame")
    width, height = anchor["width"], anchor["height"]
    if width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ValueError("Anchor box must have positive dimensions and fit within the frame")
    fields.update({f"anchor_{key}": value for key, value in anchor.items()})
    return fields


def _first_seen(video: Video, offset_ms: int) -> None:
    _offset(offset_ms)
    duration = video.duration_sec
    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise ValueError("A known positive video duration is required")
    if offset_ms >= duration * 1000:
        raise ValueError("First-seen offset must be within the video duration")


def create_distance_individual(
    session,
    utcnow,
    video_id: str,
    species_id: str,
    first_seen_offset_ms: int,
    *,
    age: str | None = None,
    sex: str | None = None,
    anchor: dict[str, float] | None = None,
    labeled_by: str | None = None,
    create_infant: bool = False,
    tag_ids: list[str] | None = None,
) -> dict:
    _attributes(age, sex)
    if create_infant and age == "Infant":
        raise ValueError("A carried infant cannot also carry an infant")
    if anchor is None:
        raise ValueError("A bounding box is required to create a distance individual")
    geometry = _anchor_fields(anchor)
    review = resolve_task(session, video_id, species_id)
    video_id = review.video_id
    video = session.get(Video, video_id)
    if video is None:
        raise ValueError("Video not found")
    if not video.project_id or not video.camera_id or not video.camera_id.strip():
        raise ValueError("Resolve the video's project and camera before distance review")
    _first_seen(video, first_seen_offset_ms)
    if session.get(Species, species_id) is None:
        raise ValueError("Species not found")
    last_number = (
        session.query(func.max(DistanceIndividual.individual_number))
        .filter_by(task_id=review.id)
        .scalar()
    )
    now = utcnow()
    row = DistanceIndividual(
        video_id=video_id,
        task_id=review.id,
        species_id=species_id,
        individual_number=(last_number or 0) + 1,
        first_seen_offset_ms=first_seen_offset_ms,
        age=age,
        sex=sex,
        labeled_by=labeled_by,
        labeled_at=now,
        updated_at=now,
        **geometry,
    )
    session.add(row)
    session.flush()
    if review.verified_at is not None:
        mark_review_started(session, review.id)
        # The identifying frame establishes when this animal first appears.
        # Earlier scheduled samples are reviewed as absent, atomically with
        # creation; the first-seen sample itself remains unreviewed.
        session.add_all(
            DistanceSample(
                individual_id=row.id,
                offset_ms=offset,
                status="skipped",
                skip_reason="individual absent",
                labeled_by=labeled_by,
                labeled_at=now,
                updated_at=now,
            )
            for offset in review_offsets(review, video.duration_sec)
            if offset < first_seen_offset_ms
        )
        session.flush()
        refresh_individual_completion(session, row)
        clear_completion(review)
    clear_completion(review)
    if tag_ids:
        set_distance_individual_attributes(
            session,
            utcnow,
            row.id,
            age=age,
            sex=sex,
            tag_ids=tag_ids,
            labeled_by=labeled_by,
        )
    result = _record(row)
    if create_infant:
        infant = create_distance_individual(
            session,
            utcnow,
            review.id,
            species_id,
            first_seen_offset_ms,
            age="Infant",
            sex="Unknown",
            anchor=anchor,
            labeled_by=labeled_by,
        )
        result["infant_id"] = infant["id"]
        session.get(DistanceIndividual, infant["id"]).initial_carrier_individual_id = row.id
        session.flush()
    return result


def delete_distance_individual(session, individual_id: str) -> None:
    """Delete an animal and its samples, reopening reviews of linked animals."""
    row = session.get(DistanceIndividual, individual_id)
    if row is None:
        raise ValueError("Distance individual not found")
    linked_samples = (
        session.query(DistanceSample)
        .filter(
            or_(
                DistanceSample.individual_id == individual_id,
                DistanceSample.carried_by_individual_id == individual_id,
            )
        )
        .all()
    )
    linked_ids = {
        linked_id
        for sample in linked_samples
        for linked_id in (sample.individual_id, sample.carried_by_individual_id)
        if linked_id and linked_id != individual_id
    }
    linked_ids.update(
        child.id
        for child in session.query(DistanceIndividual).filter_by(
            initial_carrier_individual_id=individual_id
        )
    )
    for sample in linked_samples:
        session.delete(sample)
    session.flush()
    for linked_id in linked_ids:
        reopen_individual_review(session, session.get(DistanceIndividual, linked_id))
    reopen_individual_review(session, row)
    session.delete(row)


def set_distance_individual_attributes(
    session,
    utcnow,
    individual_id: str,
    *,
    age: str | None,
    sex: str | None,
    tag_ids: list[str],
    species_id: str | None = None,
    labeled_by: str | None = None,
) -> None:
    """Replace individual-level attributes and tags in one transaction."""
    _attributes(age, sex)
    row = session.get(DistanceIndividual, individual_id)
    if row is None:
        raise ValueError("Distance individual not found")
    if age != "Infant" and (
        row.initial_carrier_individual_id
        or session.query(DistanceSample.id)
        .filter_by(individual_id=individual_id)
        .filter(DistanceSample.carried_by_individual_id.is_not(None))
        .first()
    ):
        raise ValueError("An individual with carried samples must retain age Infant")
    if age == "Infant" and (
        session.query(DistanceSample.id).filter_by(carried_by_individual_id=individual_id).first()
        or session.query(DistanceIndividual.id)
        .filter_by(initial_carrier_individual_id=individual_id)
        .first()
    ):
        raise ValueError("An individual carrying an infant cannot have age Infant")
    unique_tags = set(tag_ids)
    tag_rows = {tag.id: tag for tag in session.query(Tag).filter(Tag.id.in_(unique_tags))}
    if set(tag_rows) != unique_tags:
        raise ValueError("Tag not found")
    previously_assigned = {
        tag_id
        for (tag_id,) in session.query(DistanceIndividualTag.tag_id).filter_by(
            individual_id=individual_id
        )
    }
    if any(
        not tag.individual_eligible and tag_id not in previously_assigned
        for tag_id, tag in tag_rows.items()
    ):
        raise ValueError("Tag is not eligible for individual assignments")
    if species_id is not None and session.get(Species, species_id) is None:
        raise ValueError("Species not found")
    now = utcnow()
    if species_id is not None and species_id != row.species_id:
        raise ValueError("Individual species must match the task's target species")
    row.age, row.sex = age, sex
    row.labeled_by, row.labeled_at, row.updated_at = labeled_by, now, now
    reopen_individual_review(session, row)
    previous = {
        tag.tag_id: tag
        for tag in session.query(DistanceIndividualTag).filter_by(individual_id=individual_id)
    }
    for tag_id, tag in previous.items():
        if tag_id not in unique_tags:
            session.delete(tag)
    for tag_id in unique_tags - previous.keys():
        session.add(
            DistanceIndividualTag(
                individual_id=individual_id,
                tag_id=tag_id,
                tagged_by=labeled_by,
                tagged_at=now,
            )
        )


def set_distance_individual_anchor(
    session,
    utcnow,
    individual_id: str,
    first_seen_offset_ms: int,
    *,
    anchor: dict[str, float] | None = None,
    labeled_by: str | None = None,
) -> None:
    """Replace the first-seen frame and its marker; omitting the marker clears it."""
    geometry = _anchor_fields(anchor)
    row = session.get(DistanceIndividual, individual_id)
    if row is None:
        raise ValueError("Distance individual not found")
    _first_seen(session.get(Video, row.video_id), first_seen_offset_ms)
    if row.first_seen_offset_ms != first_seen_offset_ms:
        row.first_seen_playback_ms = None
    row.first_seen_offset_ms = first_seen_offset_ms
    for name, value in geometry.items():
        setattr(row, name, value)
    now = utcnow()
    row.labeled_by, row.labeled_at, row.updated_at = labeled_by, now, now
    reopen_individual_review(session, row)


def list_distance_individuals(session, video_id: str) -> list[dict]:
    task = resolve_task(session, video_id)
    individuals = (
        session.query(DistanceIndividual)
        .filter_by(task_id=task.id)
        .order_by(DistanceIndividual.individual_number)
        .all()
    )
    tags: dict[str, list[str]] = {}
    for individual_id, tag_id in (
        session.query(DistanceIndividualTag.individual_id, DistanceIndividualTag.tag_id)
        .join(
            DistanceIndividual,
            DistanceIndividualTag.individual_id == DistanceIndividual.id,
        )
        .filter(DistanceIndividual.task_id == task.id)
        .order_by(DistanceIndividualTag.tag_id)
    ):
        tags.setdefault(individual_id, []).append(tag_id)
    for row in individuals:
        refresh_individual_completion(session, row)
    return [{**_record(row), "tag_ids": tags.get(row.id, [])} for row in individuals]
