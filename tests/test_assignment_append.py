"""Species assignment append policy behavior at the provider boundary."""

import uuid
from pathlib import Path

import pytest
from review_app.backend.annotation_package.archive import decode_archive, encode_archive
from review_app.backend.db.models import (
    Behavior,
    IndividualObservation,
    ObservationTag,
    VideoLabel,
)
from review_app.backend.provider.assignment_handoff import AssignmentRequest


@pytest.fixture
def handout(provider_with_project):
    dp, project, root = provider_with_project
    ids = dp.get_video_queue({}, active_project_id=project.id)
    dp.update_manual_review(
        ids[0],
        [{"species": "deer", "count": 2, "tags": ["grazing"]}],
        is_blank=False,
        labeled_by="alice",
        active_project_id=project.id,
    )
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    return dp, project, root, ids, dp.export_assignment(prepared.id)


def by_filename(dp, project_id):
    return {
        Path(dp.get_video_detail(vid)["video_path"]).name: vid
        for vid in dp.get_video_queue({}, active_project_id=project_id)
    }


def test_append_merges_observations_tags_and_clears_conflicting_blank_state(handout):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    returned = next(unit for unit in work if unit["video_id"] == ids[0])
    original = returned["observations"][0]
    original["count"] = 9  # Same UUID updates its row.
    added = dict(original, observation_uuid=str(uuid.uuid4()), count=4)
    returned["observations"].append(added)
    returned["tags"].append(
        {"key": "returned_evidence", "tagged_by": "alice", "tagged_at": "2025-01-01T00:00:00"}
    )
    context["tags"].append(
        {
            "key": "returned_evidence",
            "name_en": "Returned",
            "name_fr": None,
            "color": None,
            "icon": None,
            "is_custom": True,
            "individual_eligible": False,
        }
    )
    local_ids = by_filename(dp, project.id)
    name = Path(dp.get_video_detail(ids[0])["video_path"]).name
    dp.update_manual_review(
        ids[0],
        [{"species": "fox", "count": 77}],
        labeled_by="local",
        active_project_id=project.id,
    )
    with dp.Session.begin() as session:
        session.query(VideoLabel).filter_by(video_id=ids[0]).one().is_blank = True
    dp.set_video_tags(ids[0], ["reference_video"])
    local_unit_id = local_ids[name]
    returned_data = encode_archive(manifest, context, work)
    preview = dp.preview_assignment_return(project.id, returned_data, "append")
    assert preview.totals["appended"] == 1
    assert preview.totals["added"] == 0
    assert preview.totals["blank_states_corrected"] == 1
    assert preview.units[0]["removed"] == {
        "labels": 0,
        "observations": 0,
        "behaviors": 0,
        "tags": 0,
    }
    assert "matching UUIDs" in " ".join(preview.warnings)
    dp.accept_assignment_return(project.id, returned_data, "append", preview.token)
    detail = dp.get_video_detail(local_unit_id)
    assert sorted((row["species"], row["count"]) for row in detail["manual_selections"]) == [
        ("deer", 4),
        ("deer", 9),
        ("fox", 77),
    ]
    assert detail["is_blank"] is False
    repeated = dp.preview_assignment_return(project.id, returned_data, "append")
    assert repeated.totals["unchanged"] == 2
    assert repeated.totals["appended"] == 0
    with dp.Session() as session:
        from review_app.backend.db.models import VideoTag

        assert session.query(VideoTag).filter_by(video_id=ids[0]).count() == 2
    csv = dp.export_annotations_csv(project.id)
    dp.import_annotations_csv(csv, project.id, mode="override")
    reimported = dp.get_video_detail(local_unit_id)
    assert reimported["is_blank"] is False
    assert sorted((row["species"], row["count"]) for row in reimported["manual_selections"]) == [
        ("deer", 4),
        ("deer", 9),
        ("fox", 77),
    ]


def test_append_updates_local_uuid_and_behavior_without_duplicate_and_keeps_local_only(handout):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    returned = next(unit for unit in work if unit["video_id"] == ids[0])
    matching_uuid = returned["observations"][0]["observation_uuid"]
    local_uuid = str(uuid.uuid4())
    with dp.Session.begin() as session:
        observation = (
            session.query(IndividualObservation)
            .filter_by(video_id=ids[0], observation_uuid=matching_uuid)
            .one()
        )
        observation.count = 3
        local_observation = IndividualObservation(
            video_id=ids[0],
            id=observation.id + 10,
            observation_uuid=local_uuid,
            project_id=project.id,
            species_id=observation.species_id,
            count=77,
        )
        session.add(local_observation)
        feeding = session.query(Behavior).filter_by(key="feeding").one_or_none()
        if feeding is None:
            feeding = Behavior(key="feeding", name_en="Feeding", name_fr=None, is_custom=True)
            session.add(feeding)
            session.flush()
        context["behaviors"].append(
            {"key": "feeding", "name_en": "Feeding", "name_fr": None, "is_custom": True}
        )
        returned["observations"][0]["count"] = 9
        returned["observations"][0]["behaviors"] = ["feeding"]
    returned_data = encode_archive(manifest, context, work)
    preview = dp.preview_assignment_return(project.id, returned_data, "append")
    assert preview.totals["annotations_removed"] == 1  # Replaced behavior link, not observation.
    changed = next(unit for unit in preview.units if unit["action"] == "appended")
    assert changed["removed"]["observations"] == 0
    assert changed["removed"]["behaviors"] == 1
    dp.accept_assignment_return(project.id, returned_data, "append", preview.token)
    with dp.Session() as session:
        observations = session.query(IndividualObservation).filter_by(video_id=ids[0]).all()
        by_uuid = {row.observation_uuid: row for row in observations}
        assert set(by_uuid) == {matching_uuid, local_uuid}
        assert by_uuid[matching_uuid].count == 9
        assert by_uuid[local_uuid].count == 77
        links = session.query(ObservationTag).filter_by(video_id=ids[0]).all()
        matching_links = [
            link for link in links if link.observation_id == by_uuid[matching_uuid].id
        ]
        assert [session.get(Behavior, link.behavior_id).key for link in matching_links] == [
            "feeding"
        ]


def test_empty_return_keeps_observation_and_placeholder_label_accepts_returned_label(handout):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    returned = next(unit for unit in work if unit["video_id"] == ids[0])
    returned["observations"] = []
    returned["label"] = {
        "is_blank": True,
        "review_later": True,
        "labeled_by": "alice",
        "labeled_at": "2025-01-01T00:00:00",
    }
    with dp.Session.begin() as session:
        label = session.query(VideoLabel).filter_by(video_id=ids[0]).one()
        label.is_blank = None
        label.review_later = False
        label.labeled_by = None
        label.labeled_at = None
    returned_data = encode_archive(manifest, context, work)
    preview = dp.preview_assignment_return(project.id, returned_data, "append")
    dp.accept_assignment_return(project.id, returned_data, "append", preview.token)
    detail = dp.get_video_detail(ids[0])
    assert len(detail["manual_selections"]) == 1
    assert detail["is_blank"] is False
    assert bool(detail["review_later"]) is True


def test_append_is_stale_protected_and_return_can_be_inspected(handout):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    returned = dp.export_assignment_return(opened.project_id)
    info = dp.inspect_assignment_return(returned)
    assert info == {"stage": "species", "target_species": None, "recipient": "alice"}
    preview = dp.preview_assignment_return(project.id, returned, "append")
    dp.update_manual_review(ids[0], [{"species": "fox", "count": 2}], active_project_id=project.id)
    with pytest.raises(ValueError, match="stale"):
        dp.accept_assignment_return(project.id, returned, "append", preview.token)


def test_distance_append_is_rejected(provider_with_project):
    dp, project, root = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    dp.update_manual_review(
        video_id,
        [{"species": "deer", "count": 1}],
        is_blank=False,
        active_project_id=project.id,
    )
    prepared = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", (video_id,), "distance", "deer")
    )
    opened = dp.open_assignment(
        dp.export_assignment(prepared.id), "Distance", {root.id: root.path}
    )
    returned = dp.export_assignment_return(opened.project_id)
    assert dp.inspect_assignment_return(returned)["stage"] == "distance"
    with pytest.raises(ValueError, match="not supported for Distance"):
        dp.preview_assignment_return(project.id, returned, "append")
