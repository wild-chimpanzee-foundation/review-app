"""Prepare once and download the frozen handout, using the caller's transaction."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from review_app.backend.annotation_package.archive import FORMAT, VERSION, encode_archive
from review_app.backend.annotation_package.identity import IdentityMap
from review_app.backend.annotation_package.scope import (
    AssignmentRequest,
    PreparedAssignment,
    id_selection,
    select_videos,
)
from review_app.backend.annotation_package.species import collect
from review_app.backend.db.models import HandoffAssignment, IndividualObservation


def prepare(session: Session, project_id: str, request: AssignmentRequest) -> PreparedAssignment:
    project, videos, roots = select_videos(session, project_id, request)
    context, work = collect(session, project, videos, roots)
    assignment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "direction": "setup",
        "assignment_id": assignment_id,
        "source_project_id": project_id,
        "recipient": request.recipient.strip(),
        "stage": request.stage,
        "target_species": None,
        "created_at": now.isoformat(),
        "work_keys": [video.video_id for video in videos],
        "roots": sorted({row["root_id"] for row in context["media"]}),
    }
    data = encode_archive(manifest, context, work)
    handoff = HandoffAssignment(
        assignment_id=assignment_id,
        project_id=project_id,
        role="origin",
        source_project_id=project_id,
        recipient=manifest["recipient"],
        stage=request.stage,
        target_species=None,
        created_at=now,
        scope={
            "work_keys": manifest["work_keys"],
            "roots": manifest["roots"],
            "media": context["media"],
        },
        setup_archive=data,
    )
    session.add(handoff)
    session.flush()
    identities = IdentityMap(session, handoff.id)
    for kind, ids in (
        ("video", manifest["work_keys"]),
        ("root", manifest["roots"]),
        ("prediction", [row["id"] for row in context["predictions"]]),
    ):
        for value in ids:
            identities.bind(kind, value, value)
    for row in session.query(IndividualObservation).filter(
        IndividualObservation.video_id.in_(id_selection(manifest["work_keys"]))
    ):
        identities.bind(
            "observation",
            json.dumps([row.video_id, row.observation_uuid]),
            json.dumps([row.video_id, row.id]),
        )
    return PreparedAssignment(
        id=handoff.id,
        assignment_id=assignment_id,
        stage=handoff.stage,
        recipient=handoff.recipient,
        video_count=len(videos),
        missing_video_ids=tuple(row.video_id for row in videos if row.is_missing),
    )


def export(session: Session, assignment_id: str) -> bytes:
    handoff = session.get(HandoffAssignment, assignment_id)
    if handoff is None:
        raise ValueError("Assignment not found")
    if handoff.role != "origin":
        raise ValueError("Download setup from the originating assignment, not a working project")
    return handoff.setup_archive
