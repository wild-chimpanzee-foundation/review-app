"""Manual snapshots preserve task evidence without touching ordinary Review."""

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from nicegui import app, ui
from nicegui.testing import User
from review_app.app.pages.data_assignments.distance_export import render_distance_export
from review_app.app.pages.data_assignments.results import render_results
from review_app.app.translations import t
from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceReviewTask,
    DistanceSample,
    IndividualObservation,
    ModelAnnotation,
    ReferenceFrame,
    Species,
    Tag,
    Video,
)
from review_app.backend.distance_transfer import decode_archive, encode_archive
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import JSON, null, update

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def manual_data(provider_with_project):
    dp, project, root = provider_with_project
    video = dp.get_video_queue({}, active_project_id=project.id)[0]
    with dp.Session.begin() as session:
        species = {row.scientific_name: row.id for row in session.query(Species)}
        for number, name in enumerate(("deer", "fox"), 1):
            session.add(
                IndividualObservation(video_id=video, id=number, species_id=species[name], count=2)
            )
        tag = Tag(
            key="distinctive",
            name_en="Distinctive\tcoat\nmarking",
            individual_eligible=True,
            is_custom=True,
        )
        session.add(tag)
        session.flush()
        tag_id = tag.id
    tasks = {row["species_id"]: row["task_id"] for row in dp.get_distance_video_queue(project.id)}
    deer, fox = tasks[species["deer"]], tasks[species["fox"]]
    dp.align_distance_on_even_frame(
        deer, 500.375, "alice", camera_time=datetime(2025, 1, 1, 18, 0, 2)
    )
    dp.record_distance_task_absence(fox, "bob")
    reference = dp.register_reference_video(
        project.id, "cam_x", str(Path(root.path) / "cam_x" / "reference.mp4")
    )
    frame = dp.add_reference_frame(reference["id"], 1250, 3.75, playback_offset_ms=1250.25)
    parent = dp.create_distance_individual(
        deer,
        species["deer"],
        0,
        age="Adult",
        sex="Unknown",
        labeled_by="alice",
        anchor={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        create_infant=True,
        tag_ids=[tag_id],
    )
    infant = parent["infant_id"]
    behavior = dp.get_distance_behavior_id("grazing")
    offsets = dp.get_distance_review_state(deer, "alice")["offsets"]
    for index, offset in enumerate(offsets):
        dp.save_distance_sample(
            parent["id"],
            offset,
            reference_frame_id=frame["id"] if index == 0 else None,
            skip_reason=None if index == 0 else "midpoint not visible",
            behavior_ids=[behavior] if index == 0 else [],
            infant_id=infant,
            labeled_by="alice",
        )
    dp.set_distance_active_individual(deer, "alice", infant)
    dp.finish_distance_task(deer, "alice")
    return dp, project, root, video, species, deer, fox, parent, frame


def export_state(dp, project_id, tasks):
    return decode_archive(dp.export_distance_annotations(project_id, tasks))[1]


def test_full_round_trip_is_idempotent_and_task_scoped(manual_data):
    dp, project, _, video, _, deer, fox, parent, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer, fox])
    before = export_state(dp, project.id, [deer, fox])
    with dp.Session() as session:
        ordinary = [
            (row.id, row.species_id, row.count)
            for row in session.query(IndividualObservation).filter_by(video_id=video)
        ]
    dp.align_distance_on_even_frame(deer, 800.75, "bob", reset_tracks=True)
    # Default decisions and explicit skips never restore evidence.
    assert dp.import_distance_annotations(project.id, archive, {}) == {
        "imported": [],
        "skipped": sorted([deer, fox]),
    }
    assert dp.list_distance_individuals(deer) == []
    preview = dp.preview_distance_annotations_import(project.id, archive)
    assert all(row["exists"] and row["error"] is None for row in preview)
    assert dp.import_distance_annotations(project.id, archive, {deer: "replace", fox: "skip"}) == {
        "imported": [deer],
        "skipped": [fox],
    }
    assert export_state(dp, project.id, [deer, fox]) == before
    dp.import_distance_annotations(project.id, archive, {deer: "replace", fox: "replace"})
    assert export_state(dp, project.id, [deer, fox]) == before
    state = dp.get_distance_review_state(deer, "alice")
    assert state["active_individual_id"] == parent["infant_id"]
    assert state["review"]["clock_anchor_playback_ms"] == 500.375
    with dp.Session() as session:
        assert ordinary == [
            (row.id, row.species_id, row.count)
            for row in session.query(IndividualObservation).filter_by(video_id=video)
        ]
        assert session.query(DistanceIndividual).count() == 2


def test_analysis_is_canonical_tsv_and_archive_has_no_absolute_paths(manual_data):
    dp, project, root, _, _, deer, fox, _, _ = manual_data
    data = dp.export_distance_measurements(project.id, [deer, fox])
    rows = list(csv.DictReader(io.StringIO(data.decode()), delimiter="\t"))
    assert {row["distance_m"] for row in rows} == {"3.75", ""}
    assert all(row["root_id"] == root.id and not row["video_path"].startswith("/") for row in rows)
    assert {row["skip_reason"] for row in rows} == {"", "midpoint not visible"}
    with zipfile.ZipFile(
        io.BytesIO(dp.export_distance_annotations(project.id, [deer, fox]))
    ) as archive:
        assert len(archive.namelist()) == 9
        assert all(
            str(root.path).encode() not in archive.read(name) for name in archive.namelist()
        )
    tables = export_state(dp, project.id, [deer])
    assert tables["tags"][0]["name_en"] == "Distinctive\tcoat\nmarking"


def test_zero_distance_round_trips_without_reference_dependencies(manual_data):
    dp, project, _, _, _, deer, _, parent, _ = manual_data
    dp.apply_distance_to_end(
        parent["id"], 500, distance_m=0, infant_id=parent["infant_id"], labeled_by="alice"
    )
    archive = dp.export_distance_annotations(project.id, [deer])
    before = decode_archive(archive)[1]
    assert before["references"] == []
    assert before["samples"] and all(
        (row["status"], row["distance_m"], row["reference_frame_id"]) == ("annotated", 0, None)
        for row in before["samples"]
    )
    measurements = dp.export_distance_measurements(project.id, [deer]).decode()
    rows = list(csv.DictReader(io.StringIO(measurements), delimiter="\t"))
    assert {row["distance_m"] for row in rows} == {"0.0"}
    assert {row["reference_frame_id"] for row in rows} == {""}
    dp.align_distance_on_even_frame(deer, 800.75, "bob", reset_tracks=True)
    assert dp.import_distance_annotations(project.id, archive, {deer: "replace"})["imported"] == [
        deer
    ]
    assert export_state(dp, project.id, [deer]) == before


def test_selection_uses_confirmed_labels_and_start_time_across_days(provider_with_project):
    dp, project, _ = provider_with_project
    videos = dp.get_video_queue({}, active_project_id=project.id)
    with dp.Session.begin() as session:
        deer = session.query(Species).filter_by(scientific_name="deer").one().id
        for video in videos:
            session.add(IndividualObservation(video_id=video, id=1, species_id=deer))
    tasks = {row["video_id"]: row["task_id"] for row in dp.get_distance_video_queue(project.id)}
    for video, day, hour, minute, second in ((videos[0], 1, 17, 59, 58), (videos[1], 2, 18, 0, 0)):
        dp.verify_distance_recording_time(
            tasks[video], datetime(2025, 1, day, hour, minute, second), "alice"
        )
    # Clip 1 overlaps 18:00 but its start is outside; day 2's clip qualifies.
    result = dp.preview_distance_export(project.id, deer, start_time="18:00", end_time="20:00")
    assert [row["task_id"] for row in result["tasks"]] == [tasks[videos[1]]]
    dp.verify_distance_recording_time(
        tasks[videos[0]], datetime(2025, 1, 1, 19), "alice", reset_tracks=True
    )
    assert (
        len(
            dp.preview_distance_export(project.id, deer, start_time="18:00", end_time="20:00")[
                "tasks"
            ]
        )
        == 2
    )
    with dp.Session.begin() as session:
        session.query(IndividualObservation).filter_by(video_id=videos[1]).delete()
        session.add(
            ModelAnnotation(
                video_id=videos[1],
                annotation_type="species",
                model_name="model",
                value_text="deer",
            )
        )
    assert [row["task_id"] for row in dp.preview_distance_export(project.id, deer)["tasks"]] == [
        tasks[videos[0]]
    ]


def test_unknown_camera_time_is_reported_not_taken_from_file_metadata(manual_data):
    dp, project, _, video, species, deer, fox, _, _ = manual_data
    with dp.Session.begin() as session:
        session.get(Video, video).created_at = datetime(2025, 1, 1, 19)
    result = dp.preview_distance_export(
        project.id, species["fox"], start_time="18:00", end_time="20:00"
    )
    assert result["tasks"] == [] and result["unknown_time"][0]["task_id"] == fox
    assert dp.preview_distance_export(project.id, species["fox"])["tasks"][0]["task_id"] == fox
    # Simplified alignment with no absolute clock remains editable after round trip.
    dp.align_distance_on_even_frame(deer, 500.375, "alice", reset_tracks=True)
    archive = dp.export_distance_annotations(project.id, [deer])
    dp.import_distance_annotations(project.id, archive, {deer: "replace"})
    assert dp.get_distance_review_state(deer, "alice")["review"]["recorded_start"] is None


@pytest.mark.parametrize(
    "start,end,selected",
    [
        ("18:00", "20:00", True),
        ("20:00", "18:00", False),
        ("18:00", "18:01", True),
        ("17:00", "18:00", False),
    ],
)
def test_window_boundaries_and_overnight_start_filter(manual_data, start, end, selected):
    dp, project, _, _, species, deer, _, _, _ = manual_data
    # The aligned clip actually starts at 18:00:01.500, not the anchor's 18:00:02.
    rows = dp.preview_distance_export(project.id, species["deer"], start_time=start, end_time=end)[
        "tasks"
    ]
    assert bool(rows) == selected


def mutate(archive, table, change):
    manifest, tables = decode_archive(archive)
    change(tables[table])
    return encode_archive(tables, manifest)


@pytest.mark.parametrize(
    "table,change,match",
    [
        ("samples", lambda rows: rows[0].update(offset_ms=9999), "unscheduled"),
        ("individuals", lambda rows: rows[0].update(task_id="other"), "exported task"),
        ("samples", lambda rows: rows[0].update(distance_m=float("nan")), "finite"),
        ("tasks", lambda rows: rows[0].update(video_path="../escape.mp4"), "relative paths"),
        ("tasks", lambda rows: rows[0].update(video_path="C:/escape.mp4"), "relative paths"),
        ("tasks", lambda rows: rows[0].update(scheduled_offsets_ms=[0]), "sampling schedule"),
        ("individuals", lambda rows: rows[0].update(sex="Invalid"), "sex"),
        ("samples", lambda rows: rows.append(dict(rows[0])), "duplicate"),
    ],
)
def test_invalid_archives_do_not_change_source(manual_data, table, change, match):
    dp, project, _, _, _, deer, _, _, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    before = export_state(dp, project.id, [deer])
    invalid = mutate(archive, table, change)
    with pytest.raises(ValueError, match=match):
        dp.import_distance_annotations(project.id, invalid, {deer: "replace"})
    assert export_state(dp, project.id, [deer]) == before


@pytest.mark.parametrize("verified", [True, False])
def test_completion_claim_requires_its_complete_verified_evidence(manual_data, verified):
    dp, project, _, _, _, deer, _, _, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    manifest, tables = decode_archive(archive)
    tables["samples"] = []
    if not verified:
        tables["tasks"][0].update(verified_at=None, scheduled_offsets_ms=[])
    forged = encode_archive(tables, manifest)
    with pytest.raises(ValueError, match="completion requires every scheduled sample"):
        dp.import_distance_annotations(project.id, forged, {deer: "replace"})
    assert dp.get_distance_review_state(deer, "alice")["task"]["completion_outcome"] == "present"


def test_reference_conflicts_block_only_the_dependent_task(manual_data):
    dp, project, _, _, _, deer, fox, _, frame = manual_data
    archive = dp.export_distance_annotations(project.id, [deer, fox])
    with dp.Session.begin() as session:
        session.get(ReferenceFrame, frame["id"]).distance_m = 99
    preview = {
        row["task_id"]: row for row in dp.preview_distance_annotations_import(project.id, archive)
    }
    assert "calibration conflicts" in preview[deer]["error"]
    assert preview[fox]["error"] is None
    assert dp.import_distance_annotations(project.id, archive, {deer: "skip", fox: "replace"}) == {
        "imported": [fox],
        "skipped": [deer],
    }
    with pytest.raises(ValueError, match="calibration conflicts"):
        dp.import_distance_annotations(project.id, archive, {deer: "replace"})
    with dp.Session() as session:
        assert session.get(ReferenceFrame, frame["id"]).distance_m == 99


@pytest.mark.parametrize("distance", [3.75, 6])
def test_manual_import_matches_distance_and_timestamp(manual_data, distance):
    dp, project, _, _, _, deer, _, _, frame = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    manifest, tables = decode_archive(archive)
    tables["references"][0].update(id="returned-frame", distance_m=distance)
    for sample in tables["samples"]:
        if sample["reference_frame_id"] == frame["id"]:
            sample.update(reference_frame_id="returned-frame", distance_m=distance)
    archive = encode_archive(tables, manifest)
    extra = dp.add_reference_frame(frame["reference_video_id"], frame["offset_ms"], 9)
    assert dp.preview_distance_annotations_import(project.id, archive)[0]["error"] is None
    dp.import_distance_annotations(project.id, archive, {deer: "replace"})
    frames = dp.list_reference_frames(frame["reference_video_id"])
    target = next(row for row in frames if row["distance_m"] == distance)
    assert target["id"] == (frame["id"] if distance == 3.75 else "returned-frame")
    assert frame in frames and extra in frames
    assert all(row["offset_ms"] == frame["offset_ms"] for row in frames)
    assert {
        row["reference_frame_id"]
        for row in dp.get_distance_review_state(deer, "alice")["samples"]
        if row["status"] == "annotated"
    } == {target["id"]}


def test_manual_import_does_not_substitute_different_position_for_same_distance(manual_data):
    dp, project, _, _, _, deer, _, _, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    manifest, tables = decode_archive(archive)
    tables["references"][0].update(offset_ms=2250, playback_offset_ms=2250.25)
    before = export_state(dp, project.id, [deer])
    archive = encode_archive(tables, manifest)
    assert (
        "calibration conflicts"
        in dp.preview_distance_annotations_import(project.id, archive)[0]["error"]
    )
    with pytest.raises(ValueError, match="calibration conflicts"):
        dp.import_distance_annotations(project.id, archive, {deer: "replace"})
    assert export_state(dp, project.id, [deer]) == before


def test_repeated_distance_at_different_timestamps_restores_exact_frames(manual_data):
    dp, project, _, _, _, deer, fox, _, frame = manual_data
    archive = dp.export_distance_annotations(project.id, [deer, fox])
    manifest, tables = decode_archive(archive)
    tables["references"].append(
        {
            **tables["references"][0],
            "id": "legacy-second-position",
            "offset_ms": 2250,
            "playback_offset_ms": 2250.25,
        }
    )
    second_offset = next(
        row["offset_ms"] for row in tables["samples"] if row["status"] == "skipped"
    )
    for sample in tables["samples"]:
        if sample["offset_ms"] == second_offset:
            sample.update(
                status="annotated",
                skip_reason=None,
                reference_frame_id="legacy-second-position",
                distance_m=frame["distance_m"],
            )
    archive = encode_archive(tables, manifest)
    preview = {
        row["task_id"]: row for row in dp.preview_distance_annotations_import(project.id, archive)
    }
    assert preview[deer]["error"] is None
    assert preview[fox]["error"] is None
    dp.import_distance_annotations(project.id, archive, {deer: "replace"})
    restored = dp.get_distance_review_state(deer, "alice")["samples"]
    assert {row["reference_frame_id"] for row in restored if row["status"] == "annotated"} == {
        frame["id"],
        "legacy-second-position",
    }
    _, roundtrip = decode_archive(dp.export_distance_annotations(project.id, [deer]))
    assert {(row["distance_m"], row["offset_ms"]) for row in roundtrip["references"]} == {
        (frame["distance_m"], frame["offset_ms"]),
        (frame["distance_m"], 2250),
    }


def test_manual_v1_still_rejects_two_distances_at_one_timestamp(manual_data):
    dp, project, _, _, _, deer, _, _, frame = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    manifest, tables = decode_archive(archive)
    tables["references"].append(
        {**tables["references"][0], "id": "same-position", "distance_m": 6}
    )
    with pytest.raises(ValueError, match="Duplicate reference frame"):
        dp.preview_distance_annotations_import(project.id, encode_archive(tables, manifest))


def test_import_revalidates_duration_after_preview(manual_data):
    dp, project, _, video, _, deer, _, _, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    assert dp.preview_distance_annotations_import(project.id, archive)[0]["error"] is None
    with dp.Session.begin() as session:
        session.get(Video, video).duration_sec = 1
    with pytest.raises(ValueError, match="duration differs"):
        dp.import_distance_annotations(project.id, archive, {deer: "replace"})
    with dp.Session() as session:
        assert session.query(DistanceIndividual).count() == 2


def test_restore_into_fresh_database_maps_media_and_vocabulary(manual_data, tmp_path, monkeypatch):
    dp, project, _, _, _, deer, fox, parent, frame = manual_data
    archive = dp.export_distance_annotations(project.id, [deer, fox])
    target_data = tmp_path / "target-db"
    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: target_data
    )
    target = LocalDataProvider()
    media = tmp_path / "relocated-footage"
    (media / "cam_x").mkdir(parents=True)
    with dp.Session() as session:
        name = Path(
            session.get(Video, session.get(DistanceReviewTask, deer).video_id).video_path
        ).name
    (media / "cam_x" / name).touch()
    destination = target.create_project("Destination", str(media))
    target.sync_videos(video_dir=media, active_project_id=destination.id)
    assert all(
        row["error"] is None and not row["exists"]
        for row in target.preview_distance_annotations_import(destination.id, archive)
    )
    target.import_distance_annotations(destination.id, archive, {deer: "replace", fox: "replace"})
    assert (
        target.get_distance_review_state(deer, "alice")["active_individual_id"]
        == parent["infant_id"]
    )
    with target.Session() as session:
        restored = session.get(DistanceIndividual, parent["id"])
        assert session.get(Species, restored.task.species_id).scientific_name == "deer"
        assert session.query(IndividualObservation).count() == 0
        assert session.get(ReferenceFrame, frame["id"]).distance_m == 3.75
        assert session.query(Tag).filter_by(key="distinctive").one().individual_eligible

    reference_path = media / "cam_x" / "reference.mp4"
    reference_path.touch()
    target.sync_videos(video_dir=media, active_project_id=destination.id)
    with target.Session() as session:
        observed = session.query(Video).filter_by(video_path=str(media / "cam_x" / name)).one()
        reference = session.query(Video).filter_by(video_path=str(reference_path)).one()
        assert reference.deployment_id == observed.deployment_id
    assert target.preview_distance_annotations_import(destination.id, archive)[0]["error"] is None


@pytest.mark.parametrize("assigned", [True, False])
def test_restore_checks_destination_reference_assignment(
    manual_data, tmp_path, monkeypatch, assigned
):
    dp, project, _, _, _, deer, _, _, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    task_path = decode_archive(archive)[1]["tasks"][0]["video_path"]
    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir",
        lambda: tmp_path / "destination-db",
    )
    target = LocalDataProvider()
    root = tmp_path / "destination"
    for relative in (task_path, "cam_x/reference.mp4"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    destination = target.create_project("Destination", str(root))
    other = target.save_deployment(destination.id, {"deployment_id": "other"})
    target.sync_videos(active_project_id=destination.id)
    reference = next(
        row
        for row in target.get_deployment_management(destination.id)["videos"]
        if row["filename"] == "reference.mp4"
    )
    target.apply_deployment_assignment(
        target.preview_deployment_assignment(
            destination.id, [reference["video_id"]], other if assigned else None
        )
    )

    preview = target.preview_distance_annotations_import(destination.id, archive)
    assert "must match the target video's deployment" in preview[0]["error"]
    with pytest.raises(ValueError, match="must match the target video's deployment"):
        target.import_distance_annotations(destination.id, archive, {deer: "replace"})
    with target.Session() as session:
        assert session.query(ReferenceFrame).count() == 0


def test_import_failure_rolls_back_all_accepted_tasks(manual_data, monkeypatch):
    from review_app.backend.provider import distance_transfer_import

    dp, project, _, _, _, deer, fox, _, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer, fox])
    dp.align_distance_on_even_frame(deer, 800.75, "bob", reset_tracks=True)
    before = export_state(dp, project.id, [deer, fox])
    original = distance_transfer_import.apply_task
    count = 0

    def fail_second(*args):
        nonlocal count
        count += 1
        original(*args)
        if count == 2:
            raise ValueError("simulated write failure")

    monkeypatch.setattr(distance_transfer_import, "apply_task", fail_second)
    with pytest.raises(ValueError, match="simulated write failure"):
        dp.import_distance_annotations(project.id, archive, {deer: "replace", fox: "replace"})
    assert export_state(dp, project.id, [deer, fox]) == before


@pytest.mark.parametrize(
    "task_mode, individual_ai, sample_ai, rejected_kind",
    [
        (None, JSON.NULL, null(), None),
        (None, null(), JSON.NULL, None),
        (None, JSON.NULL, {}, "samples"),
        (None, {}, {}, "individuals"),
        ("imported", {}, {}, "tasks"),
    ],
    ids=["json-null-individual", "json-null-sample", "sample", "individual", "task"],
)
def test_preview_and_export_agree_on_ai_state(
    manual_data, task_mode, individual_ai, sample_ai, rejected_kind
):
    dp, project, _, _, species, deer, _, parent, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer])
    with dp.Session.begin() as session:
        if task_mode:
            session.get(DistanceReviewTask, deer).alignment_mode = task_mode
        session.execute(
            update(DistanceIndividual)
            .where(DistanceIndividual.id == parent["id"])
            .values(ai_original=individual_ai)
        )
        session.execute(
            update(DistanceSample)
            .where(DistanceSample.individual_id == parent["id"])
            .values(ai_original=sample_ai)
        )
    (preview,) = dp.preview_distance_export(project.id, species["deer"])["tasks"]
    (restore_preview,) = dp.preview_distance_annotations_import(project.id, archive)
    if rejected_kind is None:
        assert preview["error"] is restore_preview["error"] is None
        assert dp.export_distance_annotations(project.id, [deer])
        assert dp.export_distance_measurements(project.id, [deer])
    else:
        error = f"AI-linked {rejected_kind} are not supported by the manual Distance format"
        assert preview["error"] == restore_preview["error"] == error
        for export in (dp.export_distance_annotations, dp.export_distance_measurements):
            with pytest.raises(ValueError, match=f"^{error}$"):
                export(project.id, [deer])


async def test_results_offer_analysis_without_editable_snapshot(user: User, manual_data):
    dp, project, *_ = manual_data

    @ui.page("/test-distance-results")
    async def page():
        app.storage.user["active_project_id"] = project.id
        await render_results(dp, project.id, ui.dialog())

    await user.open("/test-distance-results")
    tabs = [e for e in user.client.elements.values() if isinstance(e, ui.tab)]
    assert {tab._props["name"] for tab in tabs} == {"species", "distance", "ai", "footage"}


async def test_manual_export_ui_preview_and_download(user: User, manual_data):
    dp, project, _, _, species, deer, _, _, _ = manual_data

    @ui.page("/test-distance-manual-export")
    async def page():
        app.storage.user["active_project_id"] = project.id
        await render_distance_export(dp, project.id, ui.dialog())
        for element in list(ui.context.client.elements.values()):
            if isinstance(element, ui.expansion):
                element.set_value(True)

    await user.open("/test-distance-manual-export")
    with user:
        select = next(
            e
            for e in user.client.elements.values()
            if isinstance(e, ui.select) and e.label == t("distance_manual_species")
        )
        select.set_value(species["deer"])
    user.find(t("distance_manual_start")).type("18:00")
    user.find(t("distance_manual_end")).type("20:00")
    user.find(t("distance_manual_preview"), kind=ui.button).click()
    await user.should_see(t("distance_manual_preview_count", count=1))
    await user.should_not_see("Download editable ZIP")
    user.find(t("distance_manual_analysis"), kind=ui.button).click()
    response = await user.download.next()
    rows = list(csv.DictReader(io.StringIO(response.content.decode()), delimiter="\t"))
    assert rows
    assert {row["task_id"] for row in rows} == {deer}
    user.find(t("distance_manual_start")).clear().type("21:00")
    await user.should_not_see(t("distance_manual_analysis"))


def test_bad_zip_and_wrong_version_are_rejected(manual_data):
    dp, project, _, _, _, deer, _, _, _ = manual_data
    with pytest.raises(ValueError, match="Invalid manual Distance archive"):
        dp.preview_distance_annotations_import(project.id, b"not a zip")
    archive = dp.export_distance_annotations(project.id, [deer])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "manifest.json":
                content = content.replace(b'"version":1', b'"version":999')
            target.writestr(name, content)
    with pytest.raises(ValueError, match="format/version"):
        dp.preview_distance_annotations_import(project.id, output.getvalue())
