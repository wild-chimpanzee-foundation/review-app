"""Exact work planning and saved-scope reads without running the UI."""

import pytest
from review_app.app.pages.data_assignments.assignment_plan import (
    apply_species_request,
    species_requests,
    species_work_groups,
)
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import Video


def test_planning_preserves_split_camera_allocations_without_saving(provider_with_project):
    dp, project, _ = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    dp.add_annotator("alice")
    dp.add_annotator("bob")
    for name, video_id in zip(("alice", "bob"), ids, strict=True):
        apply_species_request(dp, project.id, AssignmentRequest(name, (video_id,)))
    groups = species_work_groups(dp, project.id)
    requests = species_requests(groups)
    assert {request.recipient: request.video_ids for request in requests} == {
        "alice": (ids[0],),
        "bob": (ids[1],),
    }
    groups[0].recipient = "bob"
    assert dp.get_assigned_video_ids(project.id, "alice") == [ids[0]]
    assert requests[0].recipient == "alice"  # Earlier review snapshots are immutable.


def test_planning_includes_labelled_missing_and_cameraless_media(provider_with_project):
    dp, project, _ = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    dp.update_manual_review(
        ids[0], [{"species": "deer", "count": 1}], active_project_id=project.id
    )
    with dp.Session.begin() as session:
        session.get(Video, ids[0]).is_missing = True
        session.get(Video, ids[1]).camera_id = None
    groups = species_work_groups(dp, project.id)
    assert {vid for group in groups for vid in group.video_ids} == set(ids)
    assert species_requests(groups) == ()
    for group in groups:
        group.recipient = "alice"
    (request,) = species_requests(groups)
    assert set(request.video_ids) == set(ids)


def test_preparation_applies_explicit_unallocation(provider_with_project):
    dp, project, _ = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    dp.add_annotator("alice")
    dp.add_annotator("bob")
    apply_species_request(dp, project.id, AssignmentRequest("alice", tuple(ids)))
    apply_species_request(dp, project.id, AssignmentRequest("bob", (ids[0],)), (ids[1],))
    assert dp.get_assigned_video_ids(project.id, "bob") == [ids[0]]
    assert not dp.get_assigned_video_ids(project.id, "alice")


def test_saved_scope_read_ignores_later_allocation_and_checks_project(provider_with_project):
    dp, project, _ = provider_with_project
    ids = dp.get_assignment_video_ids(project.id)
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", (ids[0],)))
    dp.add_annotator("bob")
    apply_species_request(dp, project.id, AssignmentRequest("bob", tuple(ids)))
    media = dp.get_assignment_media(project.id, saved.id)
    assert len(media) == 1 and media[0]["assigned"]
    assert media[0]["video_path"].endswith(".mp4")
    with pytest.raises(ValueError, match="not found in this project"):
        dp.get_assignment_media("another-project", saved.id)
