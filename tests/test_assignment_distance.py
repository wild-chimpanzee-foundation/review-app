"""Distance assignment backend and functional UI round trips (not legacy v1)."""

import copy
from datetime import datetime
from pathlib import Path

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.components.assignment_handoff import render_assignment_handoff
from review_app.app.pages.data_assignments.assignments import render_assignments
from review_app.app.setup_wizard import SetupWizard
from review_app.app.state import get_active_project_id, set_data_provider
from review_app.app.translations import t
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
from review_app.backend.video_tools import VideoToolsStatus

pytest_plugins = ["nicegui.testing.user_plugin"]


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


def test_removed_distance_assignment_still_accepts_returns(distance_assignment):
    dp, project, _, _, prepared, _ = distance_assignment
    working = open_work(distance_assignment)
    dp.remove_assignment(project.id, prepared.id)
    returned = dp.export_assignment_return(working.project_id)
    preview = dp.preview_assignment_return(project.id, returned)
    result = dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    assert result.totals["unchanged"] == 2
    assert not dp.list_assignments(project.id)


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
        videos = session.query(Video).filter_by(project_id=opened.project_id).all()
        assert len(videos) == 3
        assert all(video.is_valid is True for video in videos)
        reference = next(video for video in videos if video.video_path.endswith("reference.mp4"))
        reference_id = reference.video_id
        reference_path = Path(reference.video_path)
    assert len(dp.get_distance_reference_videos(local)) == 1
    reference_path.unlink()
    dp.sync_videos(progress_callback=None, active_project_id=opened.project_id)
    with dp.Session() as session:
        assert session.get(Video, reference_id).is_missing is True
    reference_path.touch()
    dp.sync_videos(progress_callback=None, active_project_id=opened.project_id)
    with dp.Session() as session:
        assert session.get(Video, reference_id).is_missing is False
        assert session.query(Video).filter_by(project_id=opened.project_id).count() == 3


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


@pytest.mark.parametrize(
    "start,end,count,unknown",
    [
        (None, None, 2, 0),
        ("17:00", "06:00", 1, 1),
        ("19:00", "17:00", 0, 1),
        ("17:00", "18:00", 0, 1),
    ],
)
def test_distance_assignment_selection_includes_ai_and_reports_unknown_times(
    distance_assignment, start, end, count, unknown
):
    dp, project, _, ids, _, _ = distance_assignment
    with dp.Session.begin() as session:
        session.get(Video, ids[0]).is_missing = True
    options = dp.get_assignment_distance_species_options(project.id)
    assert set(options) == {"deer"}
    result = dp.preview_assignment_distance_selection(
        project.id, "deer", start_time=start, end_time=end
    )
    assert len(result["videos"]) == count
    assert result["unknown_time_count"] == unknown
    if count:
        assert any(row["is_missing"] for row in result["videos"])
    # The assignment selector must not inherit or change the legacy manual restriction.
    with dp.Session() as session:
        species_id = session.query(Species).filter_by(scientific_name="deer").one().id
    legacy = dp.preview_distance_export(project.id, species_id)
    assert any(row["error"] and "AI" in row["error"] for row in legacy["tasks"])


@pytest.fixture
def distance_ui(distance_assignment, monkeypatch):
    dp, project, _, _, _, _ = distance_assignment
    set_data_provider(dp)
    dp.add_annotator("alice")
    monkeypatch.setattr(
        "review_app.app.setup_wizard.check_video_tools",
        lambda: VideoToolsStatus({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}),
    )
    monkeypatch.setattr(
        "review_app.app.setup_wizard.get_default_db_path", lambda: Path(dp.engine.url.database)
    )

    @ui.page("/distance-handoff-admin")
    def admin():
        app.storage.user["active_project_id"] = project.id
        render_assignments(dp, project.id)

    @ui.page("/distance-handoff-open")
    def opening():
        SetupWizard(lambda: ui.label("Distance package opened")).build(package_only=True)

    @ui.page("/distance-handoff-working")
    def working():
        render_assignment_handoff(dp, get_active_project_id())

    yield distance_assignment
    set_data_provider(None)


def select_field(user, key):
    return next(iter(user.find(t(key), kind=ui.select).elements))


def handoff_button(user, key):
    return min(user.find(t(key), kind=ui.button).elements, key=lambda element: element.id)


def click_first_handoff_button(user, key):
    with user:
        handoff_button(user, key).mark(f"first-{key}")
    user.find(f"first-{key}", kind=ui.button).click()


async def upload_distance_zip(user, raw, key="handoff_return_upload"):
    if not any(isinstance(e, ui.upload) for e in user.client.elements.values()):
        user.find(t("assign_accept"), kind=ui.button).click()
        await user.should_see(t(key))
    uploader = next(
        e
        for e in user.client.elements.values()
        if isinstance(e, ui.upload) and e.props.get("label") == t(key)
    )
    with user:
        await uploader.handle_uploads([SmallFileUpload("distance.zip", "application/zip", raw)])


async def load_distance_selection(user):
    user.find(t("assign_title"), kind=ui.button).click()
    await user.should_see(t("handoff_stage"))
    with user:
        select_field(user, "handoff_stage").set_value("distance")
        select_field(user, "assign_distance_species").set_value(["deer"])
    await user.should_see(t("assign_video_count", count=2))
    with user:
        select_field(user, "handoff_recipient").set_value("alice")
    user.find(marker="distance-row-options").click()
    with user:
        for element in list(user.client.elements.values()):
            if isinstance(element, ui.expansion):
                element.set_value(True)
    await user.should_see(t("handoff_selection_result", count=2, unknown=0))


def distance_video_table(user):
    return next(iter(user.find(marker="distance-videos").elements))


def narrow_distance_selection(user, ids):
    table = distance_video_table(user)
    table.selected = [row for row in table.rows if row["id"] in ids]
    user.find(marker="distance-videos").trigger(
        "selection", {"added": True, "rows": [], "keys": []}
    )


async def prepare_distance_selection(user):
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_freeze_help"))
    user.find(t("assign_prepare"), kind=ui.button).click()


async def test_distance_ui_prepare_copy_open_return_preview_accept(
    user: User, distance_ui, tmp_path
):
    dp, project, _, ids, _, _ = distance_ui
    await user.open("/distance-handoff-admin")
    await load_distance_selection(user)
    await prepare_distance_selection(user)
    await user.should_see(t("handoff_prepared", count=2))
    saved = dp.list_assignments(project.id)[0]
    assert saved["stage"] == "distance" and saved["target_species"] == "deer"
    assert saved["video_count"] == 2
    assert saved["reference_video_count"] == 1
    assert saved["media_count"] == 3
    media = dp.get_assignment_media(project.id, saved["id"])
    assert sum(row["assigned"] for row in media) == 2
    assert sum(not row["assigned"] for row in media) == 1
    # The newly prepared handoff opens directly in the saved-assignment details.
    assert not dp.get_assigned_video_ids(project.id, "alice")
    user.find(marker="open-delivery").click()
    with user:
        next(iter(user.find(t("assign_include_footage"), kind=ui.checkbox).elements)).set_value(
            True
        )
        min(
            user.find(t("handoff_copy_output"), kind=ui.input).elements, key=lambda e: e.id
        ).set_value(str(tmp_path / "delivery"))
    await user.should_see(
        t("handoff_media_summary", video_count=2, reference_video_count=1, media_count=3)
    )
    user.find(marker="save-delivery").click()
    delivery = tmp_path / "delivery" / f"assignment-{saved['assignment_id']}"
    await user.should_see(
        t("handoff_copy_result", copied=3, missing=0, failed=0, path=str(delivery))
    )
    setup = (delivery / "assignment.zip").read_bytes()
    manifest, context, _ = decode_archive(setup)
    assert manifest["assignment_id"] == saved["assignment_id"]
    assert len(context["media"]) == 3
    root_id = manifest["roots"][0]
    delivered_root = delivery / root_id
    (delivered_root / "unlisted.mp4").touch()

    await user.open("/distance-handoff-open")
    await upload_distance_zip(user, setup, "package_upload")
    await user.should_see(t("handoff_root", number=1))
    await user.should_see(
        t(
            "handoff_identity",
            **dp.inspect_assignment(setup),
            stage_label=t("handoff_stage_distance") + " — deer",
        )
    )
    await user.should_see(
        t("handoff_media_summary", video_count=2, reference_video_count=1, media_count=3)
    )
    user.find(t("handoff_root", number=1)).type(str(delivered_root))
    user.find(t("handoff_open"), kind=ui.button).click()
    await user.should_see("Distance package opened", retries=10)
    with user:
        working_id = get_active_project_id()
    working = dp.list_assignments(working_id)[0]
    stats = dp.get_overview_stats(working_id)["videos"]
    assert stats["total"] == 3 and stats["unprobed"] == 0
    with dp.Session() as session:
        local_frame = session.get(
            HandoffIdentityMap, (working["id"], "distance_frame", "frame-0")
        ).local_id
        local_individual = session.get(
            HandoffIdentityMap, (working["id"], "distance_individual", "individual-1")
        ).local_id
        local_video = session.get(HandoffIdentityMap, (working["id"], "video", ids[0])).local_id
    dp.update_reference_frame_position(local_frame, 1500, playback_offset_ms=1500.25)
    dp.save_distance_sample(
        local_individual, 2000, reference_frame_id=local_frame, labeled_by="alice"
    )
    # Both roles can edit Species; these edits do not shrink or expand Distance scope.
    for video_id, pid in ((ids[0], project.id), (local_video, working_id)):
        dp.update_manual_review(video_id, [{"species": "fox", "count": 1}], active_project_id=pid)
    await user.open("/distance-handoff-working")
    await user.should_see(t("handoff_working_help"))
    user.find(t("handoff_return"), kind=ui.button).click()
    returned = (await user.download.next()).content
    assert len(decode_archive(returned)[2]) == 2

    await user.open("/distance-handoff-admin")
    await upload_distance_zip(user, returned)
    await user.should_see(t("handoff_return_loaded"))
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(t("handoff_species_changed"))
    await user.should_see(t("handoff_substitution_help"))
    await user.should_see(
        t(
            "handoff_substitution",
            path="cam_x/reference.mp4",
            distance=5,
            returned=1500.25,
            existing=1000.25,
        )
    )
    assert handoff_button(user, "handoff_accept").enabled
    with user:
        select_field(user, "handoff_policy").set_value("keep_existing")
    await user.should_see(t("handoff_keep_warning"))
    await user.should_not_see(t("handoff_substitution_help"))
    assert not handoff_button(user, "handoff_accept").enabled
    with user:
        select_field(user, "handoff_policy").set_value("use_imported")
    await user.should_see(t("handoff_substitution_help"))
    # Calibration edits also stale an otherwise confirmed preview.
    dp.update_reference_frame_position("frame-0", 1200, playback_offset_ms=1200.25)
    user.find(t("handoff_accept"), kind=ui.button).click()
    await user.should_see("Return preview is stale")
    assert not handoff_button(user, "handoff_accept").enabled
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(
        t(
            "handoff_substitution",
            path="cam_x/reference.mp4",
            distance=5,
            returned=1500.25,
            existing=1200.25,
        )
    )
    user.find(t("handoff_accept"), kind=ui.button).click()
    await user.should_see(t("handoff_accepted"))
    with dp.Session() as session:
        assert session.get(DistanceSample, "sample-1").distance_m == 5.5
        assert session.get(ReferenceFrame, "frame-0").offset_ms == 1200
        assert session.get(ReferenceFrame, "frame-0").mask == {"person": 0}
    assert dp.get_video_detail(ids[0])["manual_selections"][0]["species"] == "fox"
    assert dp.preview_assignment_return(project.id, returned).totals["unchanged"] == 2
    # Repeat downloads/returns remain frozen in scope and identical on acceptance.
    with user:
        app.storage.user["active_project_id"] = working_id
    await user.open("/distance-handoff-working")
    user.find(t("handoff_return"), kind=ui.button).click()
    repeated = (await user.download.next()).content
    await user.open("/distance-handoff-admin")
    await upload_distance_zip(user, repeated)
    await user.should_see(t("handoff_return_loaded"))
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(
        t("handoff_totals", added=0, unchanged=2, replaced=0, unapplied=0, annotations_removed=0)
    )
    assert not handoff_button(user, "handoff_accept").enabled


async def test_distance_ui_selects_species_before_recipients_and_removes_deselected_rows(
    user: User, distance_ui
):
    await user.open("/distance-handoff-admin")
    user.find(t("assign_title"), kind=ui.button).click()
    await user.should_see(t("handoff_stage"))
    with user:
        select_field(user, "handoff_stage").set_value("distance")
    await user.should_see(marker="distance-species")
    await user.should_not_see(marker="distance-videos")
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("handoff_select_distance"))

    with user:
        select_field(user, "assign_distance_species").set_value(["deer"])
    await user.should_see(t("assign_video_count", count=2))
    recipient = select_field(user, "handoff_recipient")
    assert recipient.value is None
    with user:
        recipient.set_value("alice")
        select_field(user, "assign_distance_species").set_value([])
    await user.should_not_see(t("assign_video_count", count=2))
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("handoff_select_distance"))

    with user:
        select_field(user, "assign_distance_species").set_value(["deer"])
    await user.should_see(t("assign_video_count", count=2))
    assert select_field(user, "handoff_recipient").value is None


async def test_distance_ui_prepares_per_species_and_groups_review_by_recipient(
    user: User, distance_ui
):
    dp, project, _, ids, _, _ = distance_ui
    dp.update_manual_review(
        ids[0],
        [{"species": "deer", "count": 2}, {"species": "fox", "count": 1}],
        active_project_id=project.id,
    )
    await user.open("/distance-handoff-admin")
    await load_distance_selection(user)
    deer_table = distance_video_table(user)
    deer_recipient = select_field(user, "handoff_recipient")
    with user:
        select_field(user, "assign_distance_species").set_value(["deer", "fox"])
    await user.should_see(t("assign_video_count", count=1))
    assert deer_recipient.value == "alice"
    assert distance_video_table(user) is deer_table
    with user:
        recipient = max(
            user.find(t("handoff_recipient"), kind=ui.select).elements, key=lambda e: e.id
        )
        recipient.set_value("alice")
    await user.should_see(t("assign_video_count", count=1))
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_freeze_help"))
    assert len(user.find(marker="assignment-review-recipient").elements) == 1
    await user.should_see(t("assign_group_count", count=2))
    user.find(t("assign_prepare"), kind=ui.button).click()
    await user.should_see(t("handoff_prepared", count=1))
    await user.should_see(t("assign_view"))
    assignments = dp.list_assignments(project.id)
    assert len(assignments) == 3  # Existing fixture handoff plus the two new rows.
    assert {
        (row["recipient"], row["target_species"], row["video_count"]) for row in assignments
    } == {
        ("alice", "deer", 2),
        ("alice", "fox", 1),
    }
    assert not dp.get_assigned_video_ids(project.id, "alice")  # Not Species allocation.
    fox = next(row for row in assignments if row["target_species"] == "fox")
    media = dp.get_assignment_media(project.id, fox["id"])
    assert sum(row["assigned"] for row in media) == 1


async def test_distance_ui_selection_filters_subset_and_prepare_revalidation(
    user: User, distance_ui
):
    dp, project, _, ids, _, _ = distance_ui
    await user.open("/distance-handoff-admin")
    await load_distance_selection(user)
    with user:
        next(iter(user.find(t("handoff_start"), kind=ui.input).elements)).set_value("17:00")
    assert distance_video_table(user).selected == []
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("handoff_select_distance"))
    await user.should_see("Provide both start and end times")
    with user:
        next(iter(user.find(t("handoff_end"), kind=ui.input).elements)).set_value("06:00")
    await user.should_see(t("handoff_selection_result", count=1, unknown=1))
    assert [row["id"] for row in distance_video_table(user).selected] == [ids[0]]
    with user:
        next(iter(user.find(t("handoff_start"), kind=ui.input).elements)).set_value("")
        next(iter(user.find(t("handoff_end"), kind=ui.input).elements)).set_value("")
    await user.should_see(t("handoff_selection_result", count=2, unknown=0))
    with user:
        narrow_distance_selection(user, [ids[1]])
    await prepare_distance_selection(user)
    await user.should_see(t("handoff_prepared", count=1))
    saved = dp.list_assignments(project.id)[0]
    assert decode_archive(dp.export_assignment(saved["id"]))[0]["work_keys"] == [ids[1]]
    # A label edit after loading must fail preparation, never silently drop selection.
    await load_distance_selection(user)
    with user:
        narrow_distance_selection(user, [ids[1]])
    dp.update_manual_review(ids[1], [], active_project_id=project.id)
    await prepare_distance_selection(user)
    await user.should_see("Distance preparation requires confirmed target-species labels")
    assert len(dp.list_assignments(project.id)) == 2


async def test_distance_ui_empty_replacement_warns_and_preserves_omitted_task(
    user: User, distance_ui
):
    dp, project, _, ids, _, setup = distance_ui
    manifest, context, work = decode_archive(setup)
    manifest["direction"] = "return"
    unit = next(unit for unit in work if unit["samples"])
    unit.update(task=None, individuals=[], samples=[], progress=[])
    from review_app.backend.annotation_package.distance_handoff import scoped_context

    returned = encode_archive(manifest, scoped_context(context, [unit]), [unit])
    await user.open("/distance-handoff-admin")
    await upload_distance_zip(user, returned)
    await user.should_see(t("handoff_return_loaded"))
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(t("handoff_remove_warning"))
    await user.should_see(t("handoff_replace_warning"))
    await user.should_see(
        t("handoff_preview_scope", stage_label=t("handoff_stage_distance") + " — deer")
    )
    user.find(t("handoff_accept"), kind=ui.button).click()
    await user.should_see(t("handoff_accepted"))
    assert dp.list_distance_individuals(ids[0]) == []
    with dp.Session() as session:
        assert session.query(DistanceReviewTask).filter_by(video_id=ids[1]).count() == 1
        assert session.get(ReferenceFrame, "frame-0") is not None
