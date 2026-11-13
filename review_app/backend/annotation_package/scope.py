"""Explicit selection and root-relative media identity, independent of distribution queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from review_app.backend.db.models import (
    HandoffAssignment,
    Project,
    ProjectDir,
    Video,
)
from review_app.backend.path_matching import normalize_path_str


@dataclass(frozen=True)
class AssignmentRequest:
    recipient: str
    video_ids: tuple[str, ...]
    stage: str = "species"
    target_species: str | None = None


@dataclass(frozen=True)
class PreparedAssignment:
    id: str
    assignment_id: str
    stage: str
    recipient: str
    video_count: int
    missing_video_ids: tuple[str, ...]


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


def id_selection(ids):
    """Keep large selections below SQLite's bound-variable limit."""
    return select(func.json_each(json.dumps(list(ids))).table_valued("value").c.value)


def select_videos(session: Session, project_id: str, request: AssignmentRequest):
    if request.stage != "species" or request.target_species is not None:
        raise ValueError(
            "Only Species assignment preparation is implemented; Distance is deferred"
        )
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
    roots = session.query(ProjectDir).filter_by(project_id=project_id).all()
    return project, videos, roots
