"""Distance clock, annotator progress, and completion derived from saved evidence."""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy import func

from review_app.backend.db.models import (
    DistanceImport,
    DistanceIndividual,
    DistanceReviewProgress,
    DistanceReviewTask,
    DistanceSample,
    DistanceSampleBehavior,
    ReferenceFrame,
    ReferenceVideo,
    Video,
)
from review_app.backend.distance_timing import sample_offsets
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.distance_species import distance_species_settings
from review_app.backend.provider.distance_tasks import (
    clear_completion,
    complete_task,
    resolve_task,
)


def _record(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def review_offsets(review, duration_sec: float | None) -> list[int]:
    if review.verified_at is None:
        return []
    try:
        return sample_offsets(
            review.recorded_start,
            duration_sec,
            clock_anchor_offset_ms=review.clock_anchor_offset_ms,
            opening_even_second=review.opening_even_second,
            alignment_mode=review.alignment_mode,
            clock_anchor_playback_ms=review.clock_anchor_playback_ms,
            imported_sample_times_s=review.imported_sample_times_s,
        )
    except ValueError:
        # Keep evidence readable when a re-probe invalidates an existing schedule.
        return []


def refresh_individual_completion(
    session, individual: DistanceIndividual, expected_offsets: set[int] | None = None
) -> None:
    """Derive individual completion from the verified schedule and saved samples."""
    review = individual.task
    video = session.get(Video, review.video_id)
    expected = (
        expected_offsets
        if expected_offsets is not None
        else set(review_offsets(review, video.duration_sec))
        if review
        else set()
    )
    samples = (
        session.query(DistanceSample)
        .filter_by(individual_id=individual.id)
        .filter(DistanceSample.status != "pending")
        .all()
    )
    if expected and expected.issubset({row.offset_ms for row in samples}):
        individual.review_status = "completed"
        if individual.completed_at is None:
            latest = max(samples, key=lambda row: row.updated_at.replace(tzinfo=None))
            individual.completed_by = latest.labeled_by
            individual.completed_at = latest.updated_at
    else:
        individual.review_status = (
            "in_progress"
            if samples or individual.review_status != "not_started"
            else "not_started"
        )
        individual.completed_by = individual.completed_at = None
        if review:
            clear_completion(review)


def reopen_individual_review(session, individual: DistanceIndividual) -> None:
    mark_review_started(session, individual.task_id)
    refresh_individual_completion(session, individual)
    review = individual.task
    if review:
        clear_completion(review)


def mark_review_started(session, task_id: str) -> None:
    review = session.get(DistanceReviewTask, task_id)
    if review and review.started_at is None:
        review.started_at = datetime.now()


def reconcile_video_completion(session, video_id: str) -> None:
    for individual in (
        session.query(DistanceIndividual)
        .join(DistanceIndividual.task)
        .filter(DistanceReviewTask.video_id == video_id)
    ):
        refresh_individual_completion(session, individual)


def set_active_individual(session, video_id: str, annotator_name: str, individual_id: str, now):
    task = resolve_task(session, video_id)
    individual = session.get(DistanceIndividual, individual_id)
    if individual is None or individual.task_id != task.id:
        raise ValueError("Active individual must belong to this task")
    progress = session.get(DistanceReviewProgress, (task.id, annotator_name))
    if progress is None:
        progress = DistanceReviewProgress(task_id=task.id, annotator_name=annotator_name)
        session.add(progress)
    progress.active_individual_id = individual_id
    progress.updated_at = now


def get_review_state(session, video_id: str, annotator_name: str) -> dict:
    review = resolve_task(session, video_id)
    video = session.get(Video, review.video_id)
    if video is None:
        raise ValueError("Video not found")
    offsets = review_offsets(review, video.duration_sec) if review else []
    rows = (
        session.query(DistanceSample)
        .join(DistanceIndividual, DistanceSample.individual_id == DistanceIndividual.id)
        .filter(DistanceIndividual.task_id == review.id)
        .order_by(DistanceSample.offset_ms)
        .all()
    )
    frames = {
        frame.id: frame
        for frame in session.query(ReferenceFrame).filter(
            ReferenceFrame.id.in_(
                {row.reference_frame_id for row in rows if row.reference_frame_id}
            )
        )
    }
    retired = {frame.reference_video_id for frame in frames.values() if frame.retired}
    alternatives = {}
    if retired:
        for frame in (
            session.query(ReferenceFrame)
            .filter(
                ReferenceFrame.reference_video_id.in_(retired), ReferenceFrame.retired.is_(False)
            )
            .order_by(ReferenceFrame.offset_ms, ReferenceFrame.id)
        ):
            alternatives.setdefault((frame.reference_video_id, frame.distance_m), frame.id)
    behaviors: dict[str, list[str]] = {}
    for sample_id, behavior_id in (
        session.query(DistanceSampleBehavior.sample_id, DistanceSampleBehavior.behavior_id)
        .filter(DistanceSampleBehavior.sample_id.in_([row.id for row in rows]))
        .order_by(DistanceSampleBehavior.behavior_id)
    ):
        behaviors.setdefault(sample_id, []).append(behavior_id)
    suggestions = (
        _suggestion_frames(session, video) if any(row.ai_original for row in rows) else {}
    )
    samples = []
    for row in rows:
        frame = frames.get(row.reference_frame_id)
        suggested = None
        if row.ai_original and not row.ai_original.get("skip_reason"):
            suggested = suggestions.get(row.ai_original.get("reference_frame_id"))
            if suggested is None:
                suggested = min(
                    suggestions.values(),
                    key=lambda f: (
                        abs(f.distance_m - row.ai_original["predicted_distance_m"]),
                        f.distance_m,
                        f.id,
                    ),
                    default=None,
                )
        samples.append(
            {
                **_record(row),
                "suggestion": {
                    "predicted_distance_m": row.ai_original.get("predicted_distance_m"),
                    "skip_reason": row.ai_original.get("skip_reason"),
                    "reference_frame_id": suggested.id if suggested else None,
                    "reference_video_id": suggested.reference_video_id if suggested else None,
                    "distance_m": suggested.distance_m if suggested else None,
                }
                if row.ai_original
                else None,
                "behavior_ids": behaviors.get(row.id, []),
                "reference_video_id": frame.reference_video_id if frame else None,
                **(
                    {
                        "display_reference_frame_id": alternatives.get(
                            (frame.reference_video_id, frame.distance_m)
                        )
                    }
                    if frame and frame.retired
                    else {}
                ),
            }
        )
    progress = session.get(DistanceReviewProgress, (review.id, annotator_name))
    return {
        "task": _record(review),
        "review": _record(review) if review.verified_at is not None else None,
        "offsets": offsets,
        "samples": samples,
        "active_individual_id": progress.active_individual_id if progress else None,
    }


def _suggestion_frames(session, video):
    """Only currently available references in this deployment may be suggested."""
    from review_app.backend.provider.distance_references import reference_tagged

    if not video.deployment_id:
        return {}
    paths = {
        normalize_path_str(source.video_path)
        for source in session.query(Video).filter(
            Video.deployment_id == video.deployment_id,
            Video.is_missing.is_(False),
            reference_tagged(session),
        )
    }
    return {
        frame.id: frame
        for frame, source in session.query(ReferenceFrame, ReferenceVideo)
        .join(ReferenceVideo, ReferenceFrame.reference_video_id == ReferenceVideo.id)
        .filter(
            ReferenceVideo.deployment_id == video.deployment_id,
            ReferenceFrame.retired.is_(False),
        )
        if normalize_path_str(source.video_path) in paths
    }


def finish_individual(session, individual_id: str, annotator_name: str) -> None:
    if not annotator_name or not annotator_name.strip():
        raise ValueError("Annotator is required")
    individual = session.get(DistanceIndividual, individual_id)
    if individual is None:
        raise ValueError("Distance individual not found")
    review = individual.task
    if review is None:
        raise ValueError("Verify the camera recording time first")
    refresh_individual_completion(session, individual)
    if individual.review_status != "completed":
        raise ValueError("Review every scheduled sample before finishing the individual")


def finish_video(session, video_id: str, annotator_name: str, now) -> None:
    if not annotator_name or not annotator_name.strip():
        raise ValueError("Annotator is required")
    review = resolve_task(session, video_id)
    individuals = session.query(DistanceIndividual).filter_by(task_id=review.id).all()
    if review.verified_at is None or not individuals:
        raise ValueError("Review all individuals before finishing the task")
    for individual in individuals:
        refresh_individual_completion(session, individual)
        if individual.review_status != "completed":
            raise ValueError("Review every scheduled sample before finishing the task")
    video = session.get(Video, review.video_id)
    settings = distance_species_settings(session, video.project_id, review.species_id)
    required = [field for field in ("age", "sex") if getattr(settings, f"require_{field}")]
    missing = []
    for individual in sorted(individuals, key=lambda row: row.individual_number):
        fields = [field for field in required if getattr(individual, field) is None]
        if fields:
            missing.append(f"#{individual.individual_number} ({', '.join(fields)})")
    if missing:
        raise ValueError(
            "Set the required attributes before finishing the task. Missing: "
            + "; ".join(missing)
            + ". Select Unknown if undetermined."
        )
    complete_task(review, annotator_name, now, "present")


def record_absence(session, task_id: str, annotator_name: str, now, *, reject_ai=False) -> None:
    if not annotator_name or not annotator_name.strip():
        raise ValueError("Annotator is required")
    task = resolve_task(session, task_id)
    individuals = session.query(DistanceIndividual).filter_by(task_id=task.id).all()
    if any(row.labeled_at is not None or row.import_id is None for row in individuals):
        raise ValueError("Remove human target-species individuals before recording absence")
    if individuals and not reject_ai:
        raise ValueError("Explicitly reject pending AI suggestions before recording absence")
    rejected = []
    for individual in individuals:
        rejected.append(
            {
                "individual_id": individual.id,
                "import_id": individual.import_id,
                "original": individual.ai_original,
                "author": annotator_name,
                "time": now.isoformat(),
            }
        )
    if individuals:
        reset_video_evidence(session, task.id)
    task.rejected_ai = [*(task.rejected_ai or []), *rejected]
    complete_task(task, annotator_name, now, "absent")


def reset_video_evidence(session, video_id: str) -> None:
    """Clear a video's human Distance work, retaining references and ordinary Review."""
    review = resolve_task(session, video_id)
    individuals = session.query(DistanceIndividual.id).filter_by(task_id=review.id)
    # Infant samples may reference another individual; remove all samples first.
    session.query(DistanceSample).filter(DistanceSample.individual_id.in_(individuals)).delete(
        synchronize_session=False
    )
    session.query(DistanceReviewProgress).filter_by(task_id=review.id).delete(
        synchronize_session=False
    )
    session.query(DistanceIndividual).filter_by(task_id=review.id).delete(
        synchronize_session=False
    )
    clear_completion(review)


def delete_distance_ai_annotations(session, project_id: str) -> None:
    """Remove AI drafts and predictions while retaining saved human Distance work."""
    tasks = session.query(DistanceReviewTask).filter(
        DistanceReviewTask.video_id.in_(
            session.query(Video.video_id).filter_by(project_id=project_id)
        )
    )
    for task in tasks.filter(
        DistanceReviewTask.import_id.is_not(None),
        DistanceReviewTask.started_at.is_(None),
        DistanceReviewTask.completed_at.is_(None),
    ):
        reset_video_evidence(session, task.id)
        task.alignment_mode = "exact"
        task.imported_sample_times_s = None
        task.verified_by = None
        task.verified_at = None

    task_ids = tasks.with_entities(DistanceReviewTask.id)
    individuals = session.query(DistanceIndividual).filter(
        DistanceIndividual.task_id.in_(task_ids)
    )
    session.query(DistanceSample).filter(
        DistanceSample.individual_id.in_(individuals.with_entities(DistanceIndividual.id)),
        func.json_type(DistanceSample.ai_original) != "null",
    ).update(
        {DistanceSample.ai_original: None, DistanceSample.updated_at: DistanceSample.updated_at},
        synchronize_session=False,
    )
    individuals.filter(func.json_type(DistanceIndividual.ai_original) != "null").update(
        {
            DistanceIndividual.ai_original: None,
            DistanceIndividual.updated_at: DistanceIndividual.updated_at,
        },
        synchronize_session=False,
    )
    tasks.filter(DistanceReviewTask.rejected_ai != []).update(
        {DistanceReviewTask.rejected_ai: []}, synchronize_session=False
    )
    # Foreign keys detach the import from retained reviews and reference frames.
    session.query(DistanceImport).filter_by(project_id=project_id).delete(
        synchronize_session=False
    )


def verify_recording_time(
    session,
    utcnow,
    video_id: str,
    recorded_start: datetime | None,
    annotator_name: str,
    *,
    clock_anchor_offset_ms: int | None = None,
    clock_anchor_playback_ms: float | None = None,
    opening_even_second: bool = False,
    reset_tracks: bool = False,
    alignment_mode: str = "exact",
) -> None:
    if not isinstance(reset_tracks, bool):
        raise ValueError("Confirm whether to reset Distance review")
    if not annotator_name or not annotator_name.strip():
        raise ValueError("Annotator is required")
    review = resolve_task(session, video_id)
    video = session.get(Video, review.video_id)
    if video is None:
        raise ValueError("Video not found")
    if not video.deployment_id:
        raise ValueError("Resolve the video deployment before Distance review")
    offsets = sample_offsets(
        recorded_start,
        video.duration_sec,
        clock_anchor_offset_ms=clock_anchor_offset_ms,
        opening_even_second=opening_even_second,
        alignment_mode=alignment_mode,
        clock_anchor_playback_ms=clock_anchor_playback_ms,
    )
    if clock_anchor_offset_ms is None:
        if clock_anchor_playback_ms is not None or (
            opening_even_second and alignment_mode == "exact"
        ):
            raise ValueError("Select an anchor frame before setting clock alignment")
    elif (
        isinstance(clock_anchor_playback_ms, bool)
        or not isinstance(clock_anchor_playback_ms, (int, float))
        or not math.isfinite(clock_anchor_playback_ms)
        or not 0 <= clock_anchor_playback_ms < video.duration_sec * 1000
        or math.floor(clock_anchor_playback_ms) != clock_anchor_offset_ms
    ):
        raise ValueError("Select an anchor frame within the video")
    if not offsets:
        raise ValueError("No even camera second occurs within this video")
    if review.verified_at is not None:
        timestamp_only = (
            alignment_mode == review.alignment_mode
            and (
                recorded_start is None
                and review.recorded_start is None
                or recorded_start is not None
                and (
                    review.recorded_start is None
                    and alignment_mode == "simplified"
                    or review.recorded_start is not None
                    and (recorded_start - review.recorded_start).total_seconds() % 2 == 0
                )
            )
            and clock_anchor_offset_ms == review.clock_anchor_offset_ms
            and clock_anchor_playback_ms == review.clock_anchor_playback_ms
            and opening_even_second == review.opening_even_second
            and offsets == review_offsets(review, video.duration_sec)
        )
        has_tracks = (
            session.query(DistanceIndividual.id).filter_by(task_id=review.id).first() is not None
        )
        if (
            not timestamp_only
            and (
                has_tracks
                or alignment_mode != review.alignment_mode
                or alignment_mode == "simplified"
            )
            and not reset_tracks
        ):
            raise ValueError(
                "Changing clock alignment requires resetting this task's Distance review"
            )
        if reset_tracks:
            reset_video_evidence(session, review.id)
    review.recorded_start = recorded_start
    review.alignment_mode = alignment_mode
    review.imported_sample_times_s = None
    review.import_id = None
    review.started_at = None if reset_tracks else review.started_at
    review.clock_anchor_offset_ms = clock_anchor_offset_ms
    review.clock_anchor_playback_ms = clock_anchor_playback_ms
    review.opening_even_second = opening_even_second
    review.verified_by = annotator_name
    review.verified_at = utcnow()
