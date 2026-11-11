"""Manual snapshots preserve task evidence without touching ordinary Review."""

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.pages.model_import._distance_manual_tab import setup_distance_manual_tab
from review_app.app.translations import t
from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceReviewTask,
    IndividualObservation,
    ModelAnnotation,
    ReferenceFrame,
    Species,
    Tag,
    Video,
)
from review_app.backend.distance_transfer import decode_archive, encode_archive
from review_app.backend.provider.local_data_provider import LocalDataProvider

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
        assert session.get(Species, restored.species_id).scientific_name == "deer"
        assert session.query(IndividualObservation).count() == 0
        assert session.get(ReferenceFrame, frame["id"]).distance_m == 3.75
        assert session.query(Tag).filter_by(key="distinctive").one().individual_eligible


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


def test_ai_state_is_rejected_instead_of_silently_dropped(manual_data):
    dp, project, _, _, species, deer, _, parent, _ = manual_data
    with dp.Session.begin() as session:
        session.get(DistanceIndividual, parent["id"]).ai_original = {"source": "prediction"}
    assert (
        "AI-linked" in dp.preview_distance_export(project.id, species["deer"])["tasks"][0]["error"]
    )
    with pytest.raises(ValueError, match="AI-linked"):
        dp.export_distance_annotations(project.id, [deer])


async def test_manual_export_ui_preview_and_download(user: User, manual_data):
    dp, project, _, _, species, deer, _, _, _ = manual_data

    @ui.page("/test-distance-manual-export")
    async def page():
        app.storage.user["active_project_id"] = project.id
        await setup_distance_manual_tab(dp, ui.dialog())

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
    user.find(t("distance_manual_download"), kind=ui.button).click()
    response = await user.download.next()
    assert decode_archive(response.content)[1]["tasks"][0]["id"] == deer
    user.find(t("distance_manual_start")).clear().type("21:00")
    await user.should_not_see(t("distance_manual_download"))


async def test_manual_import_ui_is_explicit_and_does_not_retain_skips(user: User, manual_data):
    dp, project, _, _, _, deer, fox, _, _ = manual_data
    archive = dp.export_distance_annotations(project.id, [deer, fox])
    dp.align_distance_on_even_frame(deer, 800.75, "bob", reset_tracks=True)

    @ui.page("/test-distance-manual-import")
    async def page():
        app.storage.user["active_project_id"] = project.id
        await setup_distance_manual_tab(dp, ui.dialog())

    await user.open("/test-distance-manual-import")
    upload = next(e for e in user.client.elements.values() if isinstance(e, ui.upload))
    with user:
        await upload.handle_uploads([SmallFileUpload("manual.zip", "application/zip", archive)])
    await user.should_see(t("distance_manual_apply"))
    assert dp.list_distance_individuals(deer) == []
    choices = [
        e
        for e in user.client.elements.values()
        if isinstance(e, ui.select) and e.label == t("distance_manual_task_decision")
    ]
    assert len(choices) == 2 and all(e.value == "skip" for e in choices)
    # First apply skips everything and drops the uploaded payload from this UI.
    user.find(t("distance_manual_apply"), kind=ui.button).click()
    await user.should_see(t("distance_manual_imported", imported=0, skipped=2))
    await user.should_not_see(t("distance_manual_apply"))
    assert dp.list_distance_individuals(deer) == []
    with user:
        await upload.handle_uploads([SmallFileUpload("manual.zip", "application/zip", archive)])
    await user.should_see(t("distance_manual_apply"))
    with user:
        choices = [
            e
            for e in user.client.elements.values()
            if isinstance(e, ui.select) and e.label == t("distance_manual_task_decision")
        ]
        for choice in choices:
            choice.set_value("replace")
    user.find(t("distance_manual_apply"), kind=ui.button).click()
    await user.should_see(t("distance_manual_imported", imported=2, skipped=0))
    assert len(dp.list_distance_individuals(deer)) == 2


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
