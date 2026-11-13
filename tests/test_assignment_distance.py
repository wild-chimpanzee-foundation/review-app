"""Distance assignment contract at the public provider boundary (not legacy v1)."""

import copy
from datetime import datetime
from pathlib import Path

import pytest
from review_app.backend.annotation_package.archive import decode_archive, encode_archive
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import (
    Behavior,
    DistanceImport,
    DistanceIndividual,
    DistanceIndividualTag,
    DistanceReviewProgress,
    DistanceReviewTask,
    DistanceSample,
    DistanceSampleBehavior,
    HandoffIdentityMap,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Tag,
    Video,
)
from review_app.backend.provider.distance_tasks import ensure_tasks


@pytest.fixture
def distance_assignment(provider_with_project):
    dp, project, root = provider_with_project
    ids = dp.get_video_queue({}, active_project_id=project.id)
    for video_id in ids:
        dp.update_manual_review(
            video_id,
            [{"species": "deer", "count": 2}],
            is_blank=False,
            labeled_by="admin",
            active_project_id=project.id,
        )
    now = datetime(2025, 1, 2, 18, 0)
    reference_path = Path(root.path) / "cam_x" / "reference.mp4"
    reference_path.touch()
    with dp.Session.begin() as session:
        ensure_tasks(session, project.id)
        target = session.query(Species).filter_by(scientific_name="deer").one()
        ref = ReferenceVideo(
            id="ref", project_id=project.id, camera_id="cam_x", video_path=str(reference_path)
        )
        session.add(ref)
        source = DistanceImport(
            id="source",
            project_id=project.id,
            content_hash="original-hash",
            manifest={"model": {"name": "test"}},
            payload={},
            imported_at=now,
        )
        session.add(source)
        session.flush()
        for number, distance in enumerate((5.0, 10.0)):
            session.add(
                ReferenceFrame(
                    id=f"frame-{number}",
                    reference_video_id=ref.id,
                    offset_ms=1000,
                    playback_offset_ms=1000.25,
                    distance_m=distance,
                    mask={"person": number},
                    import_id=source.id,
                )
            )
        task = (
            session.query(DistanceReviewTask)
            .filter_by(video_id=ids[0], species_id=target.id)
            .one()
        )
        task.import_id = source.id
        task.alignment_mode = "imported"
        task.imported_sample_times_s = [0, 2]
        task.recorded_start = now
        task.verified_at, task.verified_by = now, "alice"
        task.clock_anchor_offset_ms, task.clock_anchor_playback_ms = 0, 0.25
        task.started_at = now
        task.rejected_ai = [
            {"original": {"individual_id": "rejected", "species": "deer"}, "author": "alice"}
        ]
        for number in (1, 2):
            session.add(
                DistanceIndividual(
                    id=f"individual-{number}",
                    video_id=ids[0],
                    species_id=target.id,
                    task_id=task.id,
                    import_id=source.id,
                    individual_number=number,
                    first_seen_offset_ms=0,
                    first_seen_playback_ms=0.25,
                    age="Infant" if number == 2 else "Adult",
                    sex="Unknown",
                    ai_original={"individual_id": f"ai-{number}", "species": "deer"},
                    labeled_by="alice",
                    labeled_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        for number in (1, 2):
            session.add(
                DistanceSample(
                    id=f"sample-{number}",
                    individual_id=f"individual-{number}",
                    offset_ms=0,
                    status="annotated",
                    reference_frame_id=f"frame-{number - 1}",
                    distance_m=5.5 if number == 1 else 10.5,
                    carried_by_individual_id="individual-1" if number == 2 else None,
                    labeled_by="alice",
                    labeled_at=now,
                    updated_at=now,
                    ai_original={
                        "reference_frame_id": f"frame-{number - 1}",
                        "predicted_distance_m": number * 5,
                    },
                )
            )
        session.add(
            DistanceSample(
                id="pending",
                individual_id="individual-1",
                offset_ms=2000,
                status="pending",
                updated_at=now,
                ai_original={"reference_frame_id": "frame-1", "predicted_distance_m": 10},
            )
        )
        session.flush()
        session.query(DistanceIndividual).filter_by(id="individual-2").update(
            {"initial_carrier_individual_id": "individual-1", "updated_at": now}
        )
        behavior = session.query(Behavior).filter_by(key="grazing").one()
        tag = session.query(Tag).filter_by(individual_eligible=True).first()
        if tag is None:
            tag = Tag(key="test-individual", name_en="Test individual", individual_eligible=True)
            session.add(tag)
            session.flush()
        session.add(DistanceSampleBehavior(sample_id="sample-1", behavior_id=behavior.id))
        session.add(
            DistanceIndividualTag(
                individual_id="individual-2", tag_id=tag.id, tagged_by="alice", tagged_at=now
            )
        )
        session.add(
            DistanceReviewProgress(
                task_id=task.id,
                annotator_name="alice",
                active_individual_id="individual-2",
                updated_at=now,
            )
        )
        source.payload = {
            "references": [
                {"reference_frame_id": f"frame-{n}", "reference_id": f"r-{n}"} for n in (0, 1)
            ],
            "individuals": [
                {"distance_individual_id": f"individual-{n}", "individual_id": f"ai-{n}"}
                for n in (1, 2)
            ]
            + [{"distance_individual_id": "unrelated", "individual_id": "other"}],
            "predictions": [{"individual_id": "ai-1"}, {"individual_id": "other"}],
            "sampling": None,
        }
    request = AssignmentRequest("alice", tuple(ids), "distance", "deer")
    prepared = dp.prepare_assignment(project.id, request)
    data = dp.export_assignment(prepared.id)
    return dp, project, root, ids, prepared, data


def open_work(fixture):
    dp, _, root, _, _, data = fixture
    return dp.open_assignment(data, "Distance worker", {root.id: root.path})


def test_complete_setup_same_frame_people_and_projected_ai_round_trip(distance_assignment):
    dp, project, root, ids, prepared, data = distance_assignment
    manifest, context, work = decode_archive(data)
    assert manifest["stage"] == "distance"
    assert len(context["media"]) == 3  # two tasks plus registered-only calibration footage
    frames = context["distance"]["frames"]
    assert {f["distance_m"] for f in frames} == {5, 10}
    assert {f["offset_ms"] for f in frames} == {1000}
    source = context["distance"]["imports"][0]
    assert source["source_hash"] == "original-hash"
    assert "other" not in str(source["payload"])
    first = open_work(distance_assignment)
    second = open_work(distance_assignment)
    assert first.project_id != second.project_id
    with dp.Session() as session:
        local_frames = (
            session.query(ReferenceFrame)
            .join(ReferenceVideo)
            .filter(ReferenceVideo.project_id == first.project_id)
            .all()
        )
        assert len(local_frames) == 2
        assert {f.mask["person"] for f in local_frames} == {0, 1}
        samples = (
            session.query(DistanceSample)
            .join(DistanceIndividual, DistanceSample.individual_id == DistanceIndividual.id)
            .join(Video)
            .filter(Video.project_id == first.project_id)
            .all()
        )
        local_ids = {f.id for f in local_frames}
        assert all(s.ai_original["reference_frame_id"] in local_ids for s in samples)
        assert {s.distance_m for s in samples if s.status == "annotated"} == {5.5, 10.5}
        imported = session.query(DistanceImport).filter_by(project_id=first.project_id).one()
        assert imported.manifest["assignment_projection"]["source_hash"] == "original-hash"
    returned = dp.export_assignment_return(first.project_id)
    preview = dp.preview_assignment_return(project.id, returned)
    assert preview.totals["unchanged"] == 2
    assert preview.reference_substitutions == ()
    assert sum(u["returned"]["behaviors"] for u in preview.units) == 1
    assert sum(u["returned"]["tags"] for u in preview.units) == 1
    dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    assert dp.preview_assignment_return(project.id, returned).token == preview.token
    copied = dp.copy_assignment_footage(prepared.id, Path(root.path).parent / "delivery")
    assert copied.copied_count == 3


def test_saved_scope_after_species_edits_and_calibration_substitution(distance_assignment):
    dp, project, _, ids, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    with dp.Session.begin() as session:
        mapping = session.get(HandoffIdentityMap, (opened.id, "video", ids[0]))
        local_video = mapping.local_id
        sample_map = session.get(HandoffIdentityMap, (opened.id, "distance_sample", "sample-1"))
        sample = session.get(DistanceSample, sample_map.local_id)
        sample.distance_m = 6.25
        frame_map = session.get(HandoffIdentityMap, (opened.id, "distance_frame", "frame-0"))
        session.get(ReferenceFrame, frame_map.local_id).offset_ms = 1500
    # Ordinary cross-stage editing is unrestricted, and neither side's changed labels
    # may shrink return scope or be overwritten by accepting Distance work.
    for video_id, pid in ((ids[0], project.id), (local_video, opened.project_id)):
        dp.update_manual_review(
            video_id,
            [{"species": "fox", "count": 1}],
            is_blank=False,
            labeled_by="editor",
            active_project_id=pid,
        )
    returned = dp.export_assignment_return(opened.project_id)
    keep = dp.preview_assignment_return(project.id, returned, "keep_existing")
    assert keep.totals["unapplied"] == 1
    assert not keep.reference_substitutions
    preview = dp.preview_assignment_return(project.id, returned)
    assert preview.totals["replaced"] == 1
    assert preview.reference_substitutions[0]["existing_offset_ms"] == 1000
    assert any("Species context" in w for w in preview.warnings)
    dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    with dp.Session() as session:
        assert session.get(DistanceSample, "sample-1").distance_m == 6.25
        assert session.get(ReferenceFrame, "frame-0").offset_ms == 1000
    assert dp.get_video_detail(ids[0])["manual_selections"][0]["species"] == "fox"
    assert dp.preview_assignment_return(project.id, returned).totals["unchanged"] == 2


def test_new_frame_at_shared_timestamp_and_stale_preview(distance_assignment):
    dp, project, _, _, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    with dp.Session.begin() as session:
        mapping = session.get(HandoffIdentityMap, (opened.id, "distance_reference", "ref"))
        session.add(
            ReferenceFrame(
                id="new-frame",
                reference_video_id=mapping.local_id,
                distance_m=15,
                offset_ms=1000,
                mask={"person": 3},
            )
        )
        sample = session.get(
            DistanceSample,
            session.get(HandoffIdentityMap, (opened.id, "distance_sample", "sample-1")).local_id,
        )
        sample.reference_frame_id, sample.distance_m = "new-frame", 15.25
    returned = dp.export_assignment_return(opened.project_id)
    assert (
        decode_archive(dp.export_assignment_return(opened.project_id))[2]
        == decode_archive(returned)[2]
    )
    preview = dp.preview_assignment_return(project.id, returned)
    with dp.Session.begin() as session:
        session.get(ReferenceFrame, "frame-0").mask = {"changed": True}
    with pytest.raises(ValueError, match="stale"):
        dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    preview = dp.preview_assignment_return(project.id, returned)
    dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    with dp.Session() as session:
        frames = session.query(ReferenceFrame).filter_by(reference_video_id="ref").all()
        assert len(frames) == 3
        assert {f.offset_ms for f in frames} == {1000}
        assert session.get(DistanceSample, "sample-1").distance_m == 15.25


@pytest.mark.parametrize("mutation", ["scope", "species", "dangling", "carrier", "duplicate"])
def test_invalid_distance_returns_are_rejected(distance_assignment, mutation):
    dp, project, _, _, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    work.sort(key=lambda u: not bool(u["samples"]))
    if mutation == "scope":
        manifest["work_keys"].append("unassigned")
    elif mutation == "species":
        manifest["target_species"] = "fox"
    elif mutation == "dangling":
        work[0]["samples"][0]["reference_frame_id"] = "missing"
    elif mutation == "carrier":
        work[0]["samples"][0]["carried_by_individual_id"] = "other-task"
    else:
        work.append(copy.deepcopy(work[0]))
    with pytest.raises(ValueError):
        returned = encode_archive(manifest, context, work)
        dp.preview_assignment_return(project.id, returned)


def test_empty_replacement_and_omission_preserve_other_task(distance_assignment):
    from review_app.backend.annotation_package.distance_handoff import scoped_context

    dp, project, _, ids, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    work = [u for u in work if u["samples"]]
    task_id = work[0]["task"]["id"]
    work[0].update(task=None, individuals=[], samples=[], progress=[])
    context = scoped_context(context, work)
    returned = encode_archive(manifest, context, work)
    preview = dp.preview_assignment_return(project.id, returned)
    assert preview.totals["replaced"] == 1
    assert preview.totals["annotations_removed"] >= 6
    dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    with dp.Session() as session:
        assert session.get(DistanceReviewTask, task_id) is None
        assert session.query(DistanceReviewTask).filter_by(video_id=ids[1]).count() == 1
        assert session.get(ReferenceFrame, "frame-0") is not None


def test_first_manual_return_is_new_despite_pristine_task_scaffolding(distance_assignment):
    dp, project, _, ids, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    with dp.Session.begin() as session:
        local = session.get(HandoffIdentityMap, (opened.id, "video", ids[1])).local_id
        task = session.query(DistanceReviewTask).filter_by(video_id=local).one()
        task.clock_anchor_offset_ms = 0  # zero is saved alignment, not pristine scaffolding
        session.add(
            DistanceIndividual(
                id="manual-person",
                task_id=task.id,
                video_id=local,
                species_id=task.species_id,
                individual_number=1,
                first_seen_offset_ms=0,
                labeled_by="manual",
                labeled_at=datetime(2025, 2, 1),
            )
        )
    returned = dp.export_assignment_return(opened.project_id)
    preview = dp.preview_assignment_return(project.id, returned, "keep_existing")
    assert preview.totals["added"] == 1
    dp.accept_assignment_return(project.id, returned, "keep_existing", preview.token)
    assert dp.preview_assignment_return(project.id, returned).totals["unchanged"] == 2
    with dp.Session() as session:
        task = session.query(DistanceReviewTask).filter_by(video_id=ids[1]).one()
        assert task.clock_anchor_offset_ms == 0
        assert (
            session.query(DistanceIndividual).filter_by(task_id=task.id).one().labeled_by
            == "manual"
        )


def test_late_acceptance_failure_rolls_back_graph_dependencies_and_identity_maps(
    distance_assignment, monkeypatch
):
    from review_app.backend.annotation_package import distance

    dp, project, _, ids, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    with dp.Session.begin() as session:
        for key in ids:
            local = session.get(HandoffIdentityMap, (opened.id, "video", key)).local_id
            session.query(DistanceReviewTask).filter_by(video_id=local).update(
                {"completed_by": "changed"}
            )
    returned = dp.export_assignment_return(opened.project_id)
    preview = dp.preview_assignment_return(project.id, returned)
    original = distance.restore_work
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("late failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(distance, "restore_work", fail_second)
    with pytest.raises(RuntimeError, match="late failure"):
        dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    assert len(calls) == 2
    assert dp.preview_assignment_return(project.id, returned).token == preview.token


def test_reference_dependencies_discovered_on_untouched_task_and_listed_only_sync(
    distance_assignment,
):
    dp, _, root, ids, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    with dp.Session() as session:
        local = session.get(HandoffIdentityMap, (opened.id, "video", ids[1])).local_id
    references = dp.get_distance_reference_videos(local)
    assert len(references) == 1
    assert references[0]["is_tagged"]
    (Path(root.path) / "cam_x" / "unlisted.mp4").touch()
    dp.sync_videos(
        progress_callback=None, video_dir=Path(root.path), active_project_id=opened.project_id
    )
    with dp.Session() as session:
        assert session.query(Video).filter_by(project_id=opened.project_id).count() == 3
    assert len(dp.get_distance_reference_videos(local)) == 1


@pytest.mark.parametrize(
    "start,end,accepted", [("17:00", "19:00", True), ("19:00", "17:00", False)]
)
def test_distance_preparation_uses_existing_camera_local_window(
    distance_assignment, start, end, accepted
):
    dp, project, _, ids, _, _ = distance_assignment
    request = AssignmentRequest("alice", (ids[0],), "distance", "deer", start, end)
    if accepted:
        assert dp.prepare_assignment(project.id, request).video_count == 1
    else:
        with pytest.raises(ValueError, match="window"):
            dp.prepare_assignment(project.id, request)


@pytest.mark.parametrize("policy", ["keep_existing", "use_imported"])
def test_identical_distance_return_performs_no_annotation_or_dependency_writes(
    distance_assignment, policy
):
    dp, project, _, _, _, _ = distance_assignment
    opened = open_work(distance_assignment)
    returned = dp.export_assignment_return(opened.project_id)
    preview = dp.preview_assignment_return(project.id, returned, policy)
    with dp.engine.begin() as conn:
        for table in (
            "distance_review_tasks",
            "distance_individuals",
            "distance_samples",
            "distance_review_progress",
            "distance_individual_tags",
            "distance_sample_behaviors",
            "reference_frames",
            "reference_videos",
            "distance_imports",
            "handoff_identity_maps",
        ):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                conn.exec_driver_sql(
                    f"CREATE TRIGGER no_write_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'identical return must not write'); END"
                )
    result = dp.accept_assignment_return(project.id, returned, policy, preview.token)
    assert result.totals["unchanged"] == 2


def test_distance_opens_and_returns_from_another_database(
    distance_assignment, tmp_path, monkeypatch
):
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    dp, project, root, _, _, data = distance_assignment
    workspace = tmp_path / "other-instance"
    workspace.mkdir()
    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: workspace
    )
    worker = LocalDataProvider()
    try:
        opened = worker.open_assignment(data, "Other instance", {root.id: root.path})
        returned = worker.export_assignment_return(opened.project_id)
        preview = dp.preview_assignment_return(project.id, returned)
        assert preview.totals["unchanged"] == 2
        assert "alice" in worker.get_all_annotators()
    finally:
        worker.engine.dispose()
