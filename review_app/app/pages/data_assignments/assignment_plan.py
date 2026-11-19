"""Editable Species work selection; prepared assignments keep their own frozen scope."""

from dataclasses import dataclass

from review_app.backend.annotation_package.scope import AssignmentRequest


@dataclass
class WorkGroup:
    label: str
    video_ids: tuple[str, ...]
    recipient: str | None = None


def species_work_groups(dp, project_id):
    """Preserve exact existing allocations, including split cameras and missing media."""
    assigned = {
        video_id: name
        for name in dp.get_all_annotators()
        for video_id in dp.get_assigned_video_ids(project_id, name)
    }
    chunks = dp.get_video_chunks(project_id)
    covered = {video_id for chunk in chunks for video_id in chunk["video_ids"]}
    other = [vid for vid in dp.get_assignment_video_ids(project_id) if vid not in covered]
    if other:
        chunks.append({"chunk_id": "other", "label": "", "video_ids": other})
    groups = []
    for chunk in chunks:
        recipients = {}
        for video_id in chunk["video_ids"]:
            recipients.setdefault(assigned.get(video_id), []).append(video_id)
        for name, ids in recipients.items():
            groups.append(WorkGroup(chunk["label"], tuple(ids), name))
    return groups


def species_requests(groups):
    """Freeze one request per recipient from the work actually shown in the draft."""
    recipients = {}
    for group in groups:
        if group.recipient and group.video_ids:
            recipients.setdefault(group.recipient, []).extend(group.video_ids)
    return tuple(
        AssignmentRequest(name, tuple(dict.fromkeys(ids)))
        for name, ids in sorted(recipients.items())
    )
