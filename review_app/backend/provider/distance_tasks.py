"""Canonical species-specific Distance tasks, independent of package membership."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from review_app.backend.db.models import DistanceReviewTask, IndividualObservation, Video


def ensure_tasks(session, project_id: str) -> None:
    """Add missing tasks from confirmed labels; never delete work after label edits."""
    count = func.coalesce(IndividualObservation.count, 1)
    existing = (
        select(DistanceReviewTask.id)
        .where(
            DistanceReviewTask.video_id == IndividualObservation.video_id,
            DistanceReviewTask.species_id == IndividualObservation.species_id,
        )
        .exists()
    )
    missing = session.execute(
        select(
            IndividualObservation.video_id,
            IndividualObservation.species_id,
            func.sum(count),
        )
        .join(Video, IndividualObservation.video_id == Video.video_id)
        .where(
            Video.project_id == project_id,
            IndividualObservation.species_id.is_not(None),
            count > 0,
            ~existing,
        )
        .group_by(IndividualObservation.video_id, IndividualObservation.species_id)
    ).all()
    if not missing:
        return

    # Another refresh can create a task after the SELECT. Keep its identity and work.
    session.execute(
        insert(DistanceReviewTask).on_conflict_do_nothing(
            index_elements=["video_id", "species_id"]
        ),
        [
            dict(
                id=str(uuid.uuid4()), video_id=video_id, species_id=species_id, source_count=count
            )
            for video_id, species_id, count in missing
        ],
    )


def resolve_task(session, task_or_video_id: str, species_id: str | None = None):
    """Task IDs are canonical. A video ID is accepted only when unambiguous.

    The single-video compatibility path keeps existing integrations usable, but must
    never guess the target species for mixed-species footage.
    """
    task = session.get(DistanceReviewTask, task_or_video_id)
    if task is not None:
        if species_id is not None and species_id != task.species_id:
            raise ValueError("Individual species must match the task's target species")
        return task
    video = session.get(Video, task_or_video_id)
    if video is None:
        raise ValueError("Distance task not found")
    ensure_tasks(session, video.project_id)
    query = session.query(DistanceReviewTask).filter_by(video_id=video.video_id)
    if species_id is not None:
        query = query.filter_by(species_id=species_id)
    tasks = query.all()
    if len(tasks) != 1:
        raise ValueError(
            "Select a species-specific Distance task from confirmed Species Review labels"
        )
    return tasks[0]


def clear_completion(task) -> None:
    task.completed_by = task.completed_at = task.completion_outcome = None


def complete_task(task, annotator_name: str, now, outcome: str) -> None:
    task.completed_by, task.completed_at, task.completion_outcome = annotator_name, now, outcome
    task.completion_history = [
        *(task.completion_history or []),
        {
            "author": annotator_name,
            "time": now.isoformat(),
            "outcome": outcome,
            "source_count": task.source_count,
        },
    ]
