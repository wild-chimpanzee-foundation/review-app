"""Explicit selection and root-relative media identity, independent of distribution queries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy.orm import Session

from review_app.backend.db.models import (
    HandoffAssignment,
    HandoffIdentityMap,
    Project,
    ProjectDir,
    Video,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.utils import sqlite_values as id_selection


@dataclass(frozen=True)
class AssignmentRequest:
    recipient: str
    video_ids: tuple[str, ...]
    stage: str = "species"
    target_species: str | None = None
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True)
class PreparedAssignment:
    id: str
    assignment_id: str
    stage: str
    recipient: str
    video_count: int
    missing_video_ids: tuple[str, ...]


@dataclass(frozen=True)
class OpenedAssignment:
    id: str
    assignment_id: str
    project_id: str
    video_count: int
    missing_video_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReturnPreview:
    token: str
    policy: str
    units: tuple[dict, ...]
    totals: dict[str, int]
    warnings: tuple[str, ...]
    reference_substitutions: tuple[dict, ...] = ()
    stage: str = "species"
    target_species: str | None = None
    species_context_changed: bool = False


@dataclass(frozen=True)
class AcceptanceResult:
    assignment_id: str
    totals: dict[str, int]


def bound_videos(session, binding):
    maps = {
        row.portable_id: row.local_id
        for row in session.query(HandoffIdentityMap).filter_by(handoff_id=binding.id, kind="video")
        if row.portable_id in binding.scope["work_keys"]
    }
    if maps.keys() != set(binding.scope["work_keys"]):
        raise ValueError("Assignment media identity mapping is incomplete")
    rows = {
        row.video_id: row
        for row in session.query(Video).filter(Video.video_id.in_(id_selection(maps.values())))
    }
    if len(rows) != len(maps) or any(
        row.project_id != binding.project_id for row in rows.values()
    ):
        raise ValueError("Assigned media is missing or belongs to another project")
    return {key: rows[local] for key, local in maps.items()}


def safe_relative(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"..", ".", ""} for part in value.split("/"))
    ):
        raise ValueError("Media paths must be safe relative paths")
    return value


def portable_path(path: str, roots: list[ProjectDir]) -> tuple[str, str]:
    path = PurePosixPath(normalize_path_str(path))
    for root in sorted(roots, key=lambda row: len(PurePosixPath(row.path).parts), reverse=True):
        base = PurePosixPath(normalize_path_str(root.path))
        if path.is_relative_to(base):
            return root.id, safe_relative(path.relative_to(base).as_posix())
    raise ValueError("Assigned media must be inside a registered project folder")


def distance_selection(session, project_id, target_species, start_time=None, end_time=None):
    """Confirmed target-species tasks, including AI work and missing footage.

    Share eligibility with preparation, not the legacy manual-only transfer format.
    Return unknown-time tasks separately only when a time window was requested.
    """
    from review_app.backend.db.models import DistanceReviewTask, Species
    from review_app.backend.provider.distance_references import reference_tagged
    from review_app.backend.provider.distance_tasks import ensure_tasks
    from review_app.backend.provider.distance_transfer import _manual_presence, _time_window

    if session.get(Project, project_id) is None:
        raise ValueError("Project not found")
    target = session.query(Species).filter_by(scientific_name=target_species).one_or_none()
    if target is None:
        raise ValueError("Choose a Distance target species by scientific name")
    window = _time_window(start_time, end_time)
    ensure_tasks(session, project_id)
    rows = (
        session.query(DistanceReviewTask, Video)
        .join(Video, Video.video_id == DistanceReviewTask.video_id)
        .filter(
            Video.project_id == project_id,
            DistanceReviewTask.species_id == target.id,
            _manual_presence(session, Video.video_id, target.id),
            ~reference_tagged(session),
        )
        .order_by(Video.video_path, Video.video_id)
        .all()
    )
    selected, unknown = [], []
    for task, video in rows:
        if window:
            if task.recorded_start is None:
                unknown.append(video)
                continue
            start, end = window
            value = task.recorded_start.time()
            if not (start <= value < end if start < end else value >= start or value < end):
                continue
        selected.append((task, video))
    return selected, unknown


def select_videos(session: Session, project_id: str, request: AssignmentRequest):
    if request.stage not in {"species", "distance"}:
        raise ValueError("Unknown assignment stage")
    if request.stage == "species" and (
        request.target_species is not None or request.start_time or request.end_time
    ):
        raise ValueError("Species assignments cannot have Distance selection options")
    if not isinstance(request.recipient, str) or not request.recipient.strip():
        raise ValueError("Choose an assignment recipient")
    if (
        not isinstance(request.video_ids, (tuple, list))
        or not request.video_ids
        or any(not isinstance(value, str) or not value for value in request.video_ids)
        or len(request.video_ids) != len(set(request.video_ids))
    ):
        raise ValueError("Select one or more distinct videos")
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found")
    if session.query(HandoffAssignment).filter_by(project_id=project_id, role="working").first():
        raise ValueError("Cannot prepare assignments from a working assignment project")
    videos = (
        session.query(Video)
        .filter(
            Video.project_id == project_id, Video.video_id.in_(id_selection(request.video_ids))
        )
        .order_by(Video.video_id)
        .all()
    )
    if len(videos) != len(request.video_ids):
        raise ValueError("Every selected video must belong to the source project")
    if request.stage == "distance":
        rows, _ = distance_selection(
            session, project_id, request.target_species, request.start_time, request.end_time
        )
        eligible = {video.video_id for _, video in rows}
        if any(video.video_id not in eligible for video in videos):
            raise ValueError(
                "Distance preparation requires confirmed target-species labels; selected tasks "
                "must be inside the camera-local time window and have known time when filtered"
            )
    roots = session.query(ProjectDir).filter_by(project_id=project_id).all()
    return project, videos, roots
