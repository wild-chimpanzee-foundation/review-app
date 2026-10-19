"""Backend foundations for reference-based distance annotation."""

from __future__ import annotations

import math
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from review_app.backend.db.migrations import MIGRATIONS, run_migrations
from review_app.backend.db.models import (
    Base,
    DistanceIndividual,
    DistanceIndividualTag,
    DistanceSample,
    DistanceSampleBehavior,
    DistanceVideoReview,
    IndividualObservation,
    ModelAnnotation,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Video,
)
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

DISTANCE_TABLES = (
    "reference_videos",
    "reference_frames",
    "distance_individuals",
    "distance_samples",
    "distance_individual_tags",
    "distance_sample_behaviors",
    "distance_video_reviews",
    "distance_review_progress",
)


def tagged_reference(dp, project_id, camera_id, path, *, missing=False):
    video_id = str(uuid.uuid4())
    with dp.Session.begin() as session:
        session.add(
            Video(
                video_id=video_id,
                project_id=project_id,
                camera_id=camera_id,
                video_path=path,
                duration_sec=10,
                is_missing=missing,
            )
        )
    dp.set_video_tags(video_id, ["reference_video"])
    return video_id


def test_reference_lookup_is_scoped_to_deployment_and_has_no_limit(distance_data):
    data = distance_data
    dp, project = data["dp"], data["project"]
    tagged_reference(dp, project.id, "cam_x", data["reference"]["video_path"])
    for number in range(12):
        tagged_reference(dp, project.id, "cam_x", f"references/patrol-{number}.mp4")
    tagged_reference(dp, project.id, "another-camera", "references/another.mp4")
    other = dp.create_project("Other project", "/other-project")
    tagged_reference(dp, other.id, "cam_x", "references/patrol.mp4")

    references = dp.get_distance_reference_videos(data["videos"][0])

    assert len(references) == 13
    assert all(
        row["project_id"] == project.id and row["camera_id"] == "cam_x" for row in references
    )
    assert all(row["is_tagged"] for row in references)


@pytest.mark.parametrize("load_queue_first", [False, True, "already_synced"])
def test_refresh_repairs_legacy_category_ids_and_preserves_prepared_evidence(
    distance_data, tmp_path, load_queue_first
):
    data, dp = distance_data, distance_data["dp"]
    root = tmp_path / "deployments"
    for camera in ("cam_a", "cam_b"):
        for role in ("videos", "references"):
            (root / camera / role).mkdir(parents=True)
        (root / camera / "videos" / "animal.MOV").touch()
        (root / camera / "references" / "ref.MOV").touch()
    project = dp.create_project("Paired folders", str(root))
    animals, sources = {}, {}
    for camera in ("cam_a", "cam_b"):
        animal_id = str(uuid.uuid4())
        with dp.Session.begin() as session:
            session.add(
                Video(
                    video_id=animal_id,
                    project_id=project.id,
                    camera_id=f"{camera}/videos",
                    video_path=str(root / camera / "videos" / "animal.MOV"),
                    duration_sec=10,
                )
            )
        animals[camera] = animal_id
        sources[camera] = tagged_reference(
            dp, project.id, f"{camera}/references", str(root / camera / "references" / "ref.MOV")
        )
    prepared = dp.register_reference_video(
        project.id, "cam_a/references", str(root / "cam_a" / "references" / "ref.MOV")
    )
    frame = dp.add_reference_frame(prepared["id"], 0, 5)
    individual = dp.create_distance_individual(
        animals["cam_a"], data["species_id"], 0, anchor=data["anchor"]
    )
    with dp.Session.begin() as session:
        session.add(
            DistanceSample(
                individual_id=individual["id"],
                offset_ms=0,
                status="annotated",
                reference_frame_id=frame["id"],
                distance_m=5.0,
            )
        )
    if load_queue_first == "already_synced":
        # Folder sync can repair Video rows before reference discovery runs.
        with dp.Session.begin() as session:
            for camera in animals:
                session.get(Video, animals[camera]).camera_id = camera
                session.get(Video, sources[camera]).camera_id = camera
    elif load_queue_first:
        queue = dp.get_distance_video_queue(project.id)
        assert {row["camera_id"] for row in queue} == {"cam_a/videos", "cam_b/videos"}
    dp.repair_distance_camera_layout(project.id)
    found = dp.get_distance_reference_videos(animals["cam_a"])
    assert len(found) == 1
    assert found[0]["id"] == prepared["id"]
    assert found[0]["camera_id"] == "cam_a"
    assert found[0]["source_video_id"] == sources["cam_a"]
    assert dp.list_reference_frames(prepared["id"]) == [frame]
    assert dp.get_distance_reference_videos(animals["cam_a"]) == found
    assert dp.get_distance_reference_videos(animals["cam_b"])[0]["camera_id"] == "cam_b"
    with dp.Session() as session:
        assert (
            session.query(DistanceSample)
            .filter_by(individual_id=individual["id"])
            .one()
            .reference_frame_id
            == frame["id"]
        )
    # Subsequent folder sync must keep the repaired grouping.
    assert set(dp._scan_videos(root)["camera_id"]) == {"cam_a", "cam_b"}


def test_layout_repair_preserves_explicit_camera_assignments(distance_data, tmp_path):
    dp = distance_data["dp"]
    root = tmp_path / "deployment"
    (root / "videos").mkdir(parents=True)
    (root / "references").mkdir()
    project = dp.create_project("Explicit camera", str(root))
    source = tagged_reference(
        dp, project.id, "custom-camera", str(root / "references" / "ref.mp4")
    )
    dp.repair_distance_camera_layout(project.id)
    with dp.Session() as session:
        assert session.get(Video, source).camera_id == "custom-camera"


def test_reference_tag_is_available_on_fresh_databases_and_provider_reopen(distance_data):
    dp = distance_data["dp"]
    tags = [tag for tag in dp.get_all_tags() if tag["key"] == "reference_video"]
    assert len(tags) == 1 and tags[0]["name_en"] == "Reference Video"
    reopened = LocalDataProvider()
    try:
        assert [tag for tag in reopened.get_all_tags() if tag["key"] == "reference_video"] == tags
    finally:
        reopened.engine.dispose()


def test_discovery_reuses_prepared_frames_and_survives_retagging(distance_data):
    data, dp = distance_data, distance_data["dp"]
    source_id = tagged_reference(dp, data["project"].id, "cam_x", data["reference"]["video_path"])
    first = dp.get_distance_reference_videos(data["videos"][0])
    assert first[0]["id"] == data["reference"]["id"]
    assert first[0]["source_video_id"] == source_id
    assert dp.get_distance_reference_videos(data["videos"][0]) == first
    dp.set_video_tags(source_id, [])
    assert dp.get_distance_reference_videos(data["videos"][0]) == []
    dp.set_video_tags(source_id, ["reference_video"])
    assert dp.get_distance_reference_videos(data["videos"][0]) == first
    assert dp.list_reference_frames(first[0]["id"]) == [data["frame"]]


def test_discovery_creates_durable_reference_records_for_tagged_videos(distance_data):
    data, dp = distance_data, distance_data["dp"]
    source_id = tagged_reference(dp, data["project"].id, "cam_x", "references/new.mp4")
    found = dp.get_distance_reference_videos(data["videos"][0])
    assert len(found) == 1 and found[0]["source_video_id"] == source_id
    frame = dp.add_reference_frame(found[0]["id"], 0, 2)
    assert dp.get_distance_reference_videos(data["videos"][1]) == found
    assert dp.list_reference_frames(found[0]["id"]) == [frame]


def test_reference_video_tags_exclude_clips_from_distance_queue_only(distance_data):
    data, dp = distance_data, distance_data["dp"]
    first_id = data["videos"][0]
    dp.set_video_tags(first_id, ["reference_video"])
    assert [row["video_id"] for row in dp.get_distance_video_queue(data["project"].id)] == [
        data["videos"][1]
    ]
    assert dp.get_distance_video_queue(data["project"].id, data["species_id"]) == []
    assert first_id in dp.get_video_queue({}, active_project_id=data["project"].id)
    dp.set_video_tags(first_id, [])
    assert len(dp.get_distance_video_queue(data["project"].id)) == 2


def test_discovery_uses_video_tag_key_not_label_or_individual_tags(distance_data):
    data, dp = distance_data, distance_data["dp"]
    with dp.Session.begin() as session:
        from review_app.backend.db.models import Tag

        tag = session.query(Tag).filter_by(key="reference_video").one()
        tag.name_en = "Renamed reference tag"
        tag.name_fr = "Référence"
        session.add(DistanceIndividualTag(individual_id=data["individual"]["id"], tag_id=tag.id))
    assert dp.get_distance_reference_videos(data["videos"][0]) == []
    source_id = tagged_reference(dp, data["project"].id, "cam_x", "references/new.mp4")
    assert dp.get_distance_reference_videos(data["videos"][0])[0]["source_video_id"] == source_id


def test_missing_or_unassigned_tagged_videos_are_not_discovered(distance_data):
    data, dp = distance_data, distance_data["dp"]
    tagged_reference(dp, data["project"].id, "cam_x", "references/missing.mp4", missing=True)
    tagged_reference(dp, data["project"].id, None, "references/unassigned.mp4")
    tagged_reference(dp, None, "cam_x", "references/no-project.mp4")
    assert dp.get_distance_reference_videos(data["videos"][0]) == []


def test_discovery_normalizes_windows_paths_and_exposes_transcoded_source(distance_data):
    data, dp = distance_data, distance_data["dp"]
    reference = dp.register_reference_video(data["project"].id, "cam_x", "C:/patrol/reference.mp4")
    source_id = tagged_reference(dp, data["project"].id, "cam_x", "C:\\patrol\\reference.mp4")
    with dp.Session.begin() as session:
        session.get(Video, source_id).transcoded_path = "/cache/reference.mp4"
    found = dp.get_distance_reference_videos(data["videos"][0])
    assert found[0]["id"] == reference["id"]
    assert found[0]["video_path"] == "C:/patrol/reference.mp4"
    assert found[0]["transcoded_path"] == "/cache/reference.mp4"


def test_untagging_keeps_reference_evidence_for_previously_annotated_video(distance_data):
    data, dp = distance_data, distance_data["dp"]
    source_id = tagged_reference(dp, data["project"].id, "cam_x", data["reference"]["video_path"])
    with dp.Session.begin() as session:
        session.add(_annotated_sample(data))
        session.get(Video, source_id).transcoded_path = "/cache/reference.mp4"
    dp.set_video_tags(source_id, [])
    found = dp.get_distance_reference_videos(data["videos"][0])
    assert len(found) == 1 and found[0]["id"] == data["reference"]["id"]
    assert found[0]["is_tagged"] is False
    assert found[0]["transcoded_path"] == "/cache/reference.mp4"
    assert dp.get_distance_reference_videos(data["videos"][1]) == []
    assert dp.list_reference_frames(found[0]["id"]) == [data["frame"]]


def test_tag_discovery_does_not_change_existing_reference_deployment(distance_data):
    data, dp = distance_data, distance_data["dp"]
    tagged_reference(dp, data["project"].id, "another-camera", data["reference"]["video_path"])
    with dp.Session.begin() as session:
        video = session.get(Video, data["videos"][1])
        video.camera_id = "another-camera"
    assert dp.get_distance_reference_videos(data["videos"][1]) == []
    assert dp.list_reference_videos(data["project"].id, "cam_x") == [data["reference"]]


def test_reference_paths_are_portable_and_unique_within_project(distance_data):
    dp, project = distance_data["dp"], distance_data["project"]
    reference = dp.register_reference_video(project.id, "cam_x", "C:\\patrol\\reference.mp4")
    assert reference["video_path"] == "C:/patrol/reference.mp4"
    with pytest.raises(IntegrityError):
        dp.register_reference_video(project.id, "another-camera", "C:/patrol/reference.mp4")


def test_reference_frames_preserve_known_distance_and_playback_offset(distance_data):
    dp, reference = distance_data["dp"], distance_data["reference"]
    close = dp.add_reference_frame(reference["id"], 0, 0.5)
    frames = dp.list_reference_frames(reference["id"])
    assert [row["offset_ms"] for row in frames] == [0, 3500]
    assert frames[0] == close
    assert frames[1]["distance_m"] == 5.0
    with pytest.raises(IntegrityError):
        dp.add_reference_frame(reference["id"], 0, 10)


def test_startup_retains_species_used_only_by_distance(distance_data, tmp_db):
    (tmp_db["root"] / "species.csv").write_text("scientific_name;english_name\nfox;Red Fox\n")
    reopened = LocalDataProvider()
    individual = reopened.list_distance_individuals(distance_data["videos"][0])[0]
    assert individual["species_id"] == distance_data["species_id"]
    with reopened.Session() as session:
        assert session.get(Species, distance_data["species_id"]) is not None


def test_reference_frame_preserves_submillisecond_seek_on_reopen(distance_data):
    data = distance_data
    frame = data["dp"].add_reference_frame(
        data["reference"]["id"], 333, 4, playback_offset_ms=333.334
    )
    reopened = LocalDataProvider()
    assert reopened.list_reference_frames(data["reference"]["id"])[0] == frame
    assert frame["playback_offset_ms"] == 333.334


@pytest.mark.parametrize("playback", [-1, math.inf, math.nan, True, "333", 334])
def test_reference_frame_rejects_invalid_precise_position(distance_data, playback):
    with pytest.raises(ValueError, match="Playback position"):
        distance_data["dp"].add_reference_frame(
            distance_data["reference"]["id"], 333, 4, playback_offset_ms=playback
        )


@pytest.mark.parametrize("distance", [0, -1, math.inf, -math.inf, math.nan, True, "5"])
def test_reference_frame_rejects_invalid_distance(distance_data, distance):
    dp = distance_data["dp"]
    with pytest.raises(ValueError):
        dp.add_reference_frame(distance_data["reference"]["id"], 0, distance)
    assert len(dp.list_reference_frames(distance_data["reference"]["id"])) == 1


@pytest.mark.parametrize("offset", [-1, 0.5, True])
def test_reference_frame_requires_integer_offset(distance_data, offset):
    with pytest.raises(ValueError):
        distance_data["dp"].add_reference_frame(distance_data["reference"]["id"], offset, 5)


def test_missing_deployment_metadata_blocks_reference_lookup_and_individuals(distance_data):
    data = distance_data
    dp, video_id = data["dp"], data["videos"][1]
    with dp.engine.begin() as conn:
        conn.execute(text("UPDATE videos SET camera_id=NULL WHERE video_id=:id"), {"id": video_id})
    with pytest.raises(ValueError, match="project and camera"):
        dp.get_distance_reference_videos(video_id)
    with pytest.raises(ValueError, match="project and camera"):
        dp.create_distance_individual(video_id, data["species_id"], 0, anchor=data["anchor"])


def test_reference_preparation_validates_missing_parents_and_metadata(distance_data):
    dp = distance_data["dp"]
    with pytest.raises(ValueError, match="Project not found"):
        dp.register_reference_video("missing", "camera", "reference.mp4")
    with pytest.raises(ValueError, match="project and camera"):
        dp.register_reference_video(distance_data["project"].id, " ", "reference.mp4")
    with pytest.raises(ValueError, match="video path"):
        dp.register_reference_video(distance_data["project"].id, "camera", " ")
    with pytest.raises(ValueError, match="Reference video not found"):
        dp.add_reference_frame("missing", 0, 5)
    with pytest.raises(ValueError, match="Video not found"):
        dp.get_distance_reference_videos("missing")


@pytest.mark.parametrize("explicit_none", [False, True])
def test_creation_requires_bounding_box_without_partial_writes(distance_data, explicit_none):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    before = dp.list_distance_individuals(video_id)
    kwargs = {"anchor": None} if explicit_none else {}
    with pytest.raises(ValueError, match="bounding box is required"):
        dp.create_distance_individual(video_id, data["species_id"], 1000, **kwargs)
    assert dp.list_distance_individuals(video_id) == before
    created = dp.create_distance_individual(
        video_id, data["species_id"], 1000, anchor=data["anchor"]
    )
    assert created["individual_number"] == 2
    assert created["first_seen_offset_ms"] == 1000
    assert {key: created[f"anchor_{key}"] for key in data["anchor"]} == data["anchor"]


def test_individuals_have_durable_identity_and_video_local_numbers(distance_data):
    data = distance_data
    dp, first = data["dp"], data["individual"]
    second = dp.create_distance_individual(
        data["videos"][0], data["species_id"], 1500, anchor=data["anchor"]
    )
    infant = dp.create_distance_individual(
        data["videos"][0],
        data["species_id"],
        2000,
        anchor=data["anchor"],
        age="Infant",
        sex="Unknown",
    )
    another_video = dp.create_distance_individual(
        data["videos"][1], data["species_id"], 0, anchor=data["anchor"]
    )
    assert [
        first["individual_number"],
        second["individual_number"],
        infant["individual_number"],
    ] == [1, 2, 3]
    assert another_video["individual_number"] == 1
    assert len({row["id"] for row in (first, second, infant, another_video)}) == 4
    assert first["age"] is None and first["sex"] is None
    assert first["first_seen_offset_ms"] == 500
    assert first["labeled_by"] == "alice" and first["labeled_at"] is not None
    assert [row["id"] for row in dp.list_distance_individuals(data["videos"][0])] == [
        first["id"],
        second["id"],
        infant["id"],
    ]
    with dp.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM individual_observations")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM video_labels")).scalar() == 0


@pytest.mark.parametrize("offset", [-1, 10000, 10001, True, 0.5])
def test_first_seen_must_be_an_integer_within_duration(distance_data, offset):
    data = distance_data
    with pytest.raises(ValueError):
        data["dp"].create_distance_individual(
            data["videos"][0], data["species_id"], offset, anchor=data["anchor"]
        )
    assert len(data["dp"].list_distance_individuals(data["videos"][0])) == 1


def test_first_seen_requires_known_duration(distance_data):
    data = distance_data
    with data["dp"].engine.begin() as conn:
        conn.execute(
            text("UPDATE videos SET duration_sec=NULL WHERE video_id=:id"),
            {"id": data["videos"][0]},
        )
    with pytest.raises(ValueError, match="duration"):
        data["dp"].create_distance_individual(
            data["videos"][0], data["species_id"], 0, anchor=data["anchor"]
        )


@pytest.mark.parametrize(
    "anchor",
    [
        {"x": 0.2},
        {"x": 0.2, "y": 0.3},
        {"x": -0.1, "y": 0.2},
        {"x": 0.2, "y": 1.1},
        {"x": math.nan, "y": 0.2},
        {"x": True, "y": 0.2},
        {"x": 0.2, "y": 0.2, "width": 0.1},
        {"x": 0.2, "y": 0.2, "width": 0, "height": 0.1},
        {"x": 0.9, "y": 0.2, "width": 0.2, "height": 0.1},
        {"x": -0.1, "y": 0.2, "width": 0.2, "height": 0.1},
        {"x": 0.2, "y": math.nan, "width": 0.2, "height": 0.1},
        {"x": 0.2, "y": 0.2, "width": True, "height": 0.1},
        {"x": 0.2, "y": 0.2, "width": math.inf, "height": 0.1},
        {"x": 0.2, "y": 0.2, "width": 0.2, "height": -0.1},
        {"x": 0.2, "y": 0.9, "width": 0.2, "height": 0.2},
    ],
)
def test_invalid_anchors_do_not_change_the_individual(distance_data, anchor):
    data = distance_data
    before = data["dp"].list_distance_individuals(data["videos"][0])
    with pytest.raises(ValueError):
        data["dp"].set_distance_individual_anchor(data["individual"]["id"], 1000, anchor=anchor)
    with pytest.raises(ValueError):
        data["dp"].create_distance_individual(
            data["videos"][0], data["species_id"], 1000, anchor=anchor
        )
    assert data["dp"].list_distance_individuals(data["videos"][0]) == before


@pytest.mark.parametrize(
    "geometry",
    [
        (0.2, 0.3, None, None),
        (0.2, 0.3, 0.2, None),
        (None, None, 0.2, 0.3),
        (0.2, 0.3, 0, 0.1),
        (0.9, 0.3, 0.2, 0.1),
    ],
)
def test_database_rejects_incomplete_or_invalid_bounding_boxes(distance_data, geometry):
    dp = distance_data["dp"]
    before = dp.list_distance_individuals(distance_data["videos"][0])
    with pytest.raises(IntegrityError, match="ck_distance_anchor"), dp.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE distance_individuals SET anchor_x=:x, anchor_y=:y, "
                "anchor_width=:width, anchor_height=:height WHERE id=:id"
            ),
            dict(zip(("x", "y", "width", "height"), geometry))
            | {"id": distance_data["individual"]["id"]},
        )
    assert dp.list_distance_individuals(distance_data["videos"][0]) == before


def test_changing_first_seen_replaces_or_clears_its_anchor(distance_data):
    data = distance_data
    dp, individual_id = data["dp"], data["individual"]["id"]
    dp.set_distance_individual_anchor(
        individual_id, 1500, anchor={"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.5}
    )
    row = dp.list_distance_individuals(data["videos"][0])[0]
    assert row["first_seen_offset_ms"] == 1500
    assert (row["anchor_x"], row["anchor_y"], row["anchor_width"], row["anchor_height"]) == (
        0.2,
        0.3,
        0.4,
        0.5,
    )
    dp.set_distance_individual_anchor(individual_id, 2500)
    row = dp.list_distance_individuals(data["videos"][0])[0]
    assert row["first_seen_offset_ms"] == 2500
    assert all(
        row[field] is None for field in ("anchor_x", "anchor_y", "anchor_width", "anchor_height")
    )
    assert row["id"] == individual_id


def test_individual_tags_require_eligibility_but_preserve_legacy_evidence(distance_data):
    data = distance_data
    dp, individual_id = data["dp"], data["individual"]["id"]
    reference_tag = next(tag for tag in dp.get_all_tags() if tag["key"] == "reference_video")
    custom_key = dp.create_custom_tag("Video only")
    custom_tag = next(tag for tag in dp.get_all_tags() if tag["key"] == custom_key)
    assert custom_tag["individual_eligible"] is False

    # Ordinary video tagging still permits the same ineligible vocabulary.
    dp.set_video_tags(data["videos"][0], ["reference_video", custom_key])
    assert set(dp.get_video_tags(data["videos"][0])) == {"reference_video", custom_key}
    with pytest.raises(ValueError, match="not eligible"):
        dp.set_distance_individual_attributes(
            individual_id, age=None, sex=None, tag_ids=[reference_tag["id"]]
        )

    dp.set_tag_individual_eligibility(custom_key, True)
    dp.set_distance_individual_attributes(
        individual_id, age=None, sex=None, tag_ids=[custom_tag["id"]]
    )
    dp.set_tag_individual_eligibility(custom_key, False)
    # Existing evidence remains selected and writable; disabling eligibility does not
    # silently erase a saved annotation, while new assignments remain blocked.
    dp.set_distance_individual_attributes(
        individual_id, age="Adult", sex=None, tag_ids=[custom_tag["id"]]
    )
    with pytest.raises(ValueError, match="not eligible"):
        dp.set_distance_individual_attributes(
            individual_id, age=None, sex=None, tag_ids=[reference_tag["id"]]
        )
    assert dp.list_distance_individuals(data["videos"][0])[0]["tag_ids"] == [custom_tag["id"]]


def test_deleting_custom_tag_cascades_distance_tag_association_only(distance_data):
    data = distance_data
    dp, individual_id = data["dp"], data["individual"]["id"]
    key = dp.create_custom_tag("Temporary individual tag")
    dp.set_tag_individual_eligibility(key, True)
    tag_id = next(tag["id"] for tag in dp.get_all_tags() if tag["key"] == key)
    dp.set_distance_individual_attributes(
        individual_id, age="Unknown", sex="Unknown", tag_ids=[tag_id]
    )

    dp.verify_distance_recording_time(data["videos"][0], datetime(2026, 1, 1, 0, 0, 0), "alice")
    for offset in dp.get_distance_review_state(data["videos"][0], "alice")["offsets"]:
        dp.save_distance_sample(
            individual_id, offset, skip_reason="individual absent", labeled_by="alice"
        )
    dp.finish_distance_video(data["videos"][0], "alice")

    dp.delete_custom_tag(key)

    assert dp.list_distance_individuals(data["videos"][0])[0]["id"] == individual_id
    assert dp.list_distance_individuals(data["videos"][0])[0]["tag_ids"] == []
    with dp.engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert (
            conn.execute(
                text("SELECT completed_at FROM distance_video_reviews WHERE video_id = :video_id"),
                {"video_id": data["videos"][0]},
            ).scalar_one()
            is None
        )
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM distance_individual_tags WHERE tag_id = :tag_id"),
                {"tag_id": tag_id},
            ).scalar_one()
            == 0
        )


def test_individual_attributes_and_tags_are_atomic_and_preserve_tag_metadata(distance_data):
    data = distance_data
    dp, individual_id = data["dp"], data["individual"]["id"]
    with dp.engine.connect() as conn:
        tag_id = conn.execute(text("SELECT id FROM tags WHERE key='injured'")).scalar()
    dp.set_distance_individual_attributes(
        individual_id, age="Adult", sex="Female", tag_ids=[tag_id, tag_id], labeled_by="alice"
    )
    before = dp.list_distance_individuals(data["videos"][0])[0]
    with pytest.raises(ValueError, match="Tag not found"):
        dp.set_distance_individual_attributes(
            individual_id, age="Infant", sex="Unknown", tag_ids=["missing"]
        )
    assert dp.list_distance_individuals(data["videos"][0])[0] == before
    with dp.Session() as session:
        tagged_at = session.get(DistanceIndividualTag, (individual_id, tag_id)).tagged_at
    dp.set_distance_individual_attributes(
        individual_id, age="Adult", sex="Unknown", tag_ids=[tag_id], labeled_by="bob"
    )
    with dp.Session() as session:
        tag = session.get(DistanceIndividualTag, (individual_id, tag_id))
        assert tag.tagged_by == "alice" and tag.tagged_at == tagged_at
    row = dp.list_distance_individuals(data["videos"][0])[0]
    assert row["tag_ids"] == [tag_id] and row["sex"] == "Unknown" and row["labeled_by"] == "bob"
    dp.set_distance_individual_attributes(individual_id, age=None, sex=None, tag_ids=[])
    assert dp.list_distance_individuals(data["videos"][0])[0]["tag_ids"] == []


def test_manual_sample_rejects_non_finite_reference_distance(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id, individual_id = data["videos"][0], data["individual"]["id"]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    with dp.Session.begin() as session:
        session.get(ReferenceFrame, data["frame"]["id"]).distance_m = math.inf
    with pytest.raises(ValueError, match="Reference distance must be finite and positive"):
        dp.save_distance_sample(
            individual_id,
            1000,
            reference_frame_id=data["frame"]["id"],
            labeled_by="alice",
        )
    assert dp.get_distance_review_state(video_id, "alice")["samples"] == []


@pytest.mark.parametrize("age,sex", [("Child", None), (None, "F"), ("adult", "Female")])
def test_invalid_individual_attributes_are_rejected(distance_data, age, sex):
    data = distance_data
    with pytest.raises(ValueError):
        data["dp"].set_distance_individual_attributes(
            data["individual"]["id"], age=age, sex=sex, tag_ids=[]
        )


def test_individual_storage_survives_provider_reopen(distance_data):
    data = distance_data
    before = data["dp"].list_distance_individuals(data["videos"][0])
    reopened = LocalDataProvider()
    try:
        assert reopened.list_distance_individuals(data["videos"][0]) == before
        assert reopened.list_reference_frames(data["reference"]["id"]) == [data["frame"]]
    finally:
        reopened.engine.dispose()


def test_distance_queue_is_project_scoped_and_filters_existing_individuals(distance_data):
    data = distance_data
    dp = data["dp"]
    other = dp.create_project("Other deployment", "/other-deployment")
    assert dp.get_distance_video_queue(other.id) == []
    assert len(dp.get_distance_video_queue(data["project"].id)) == 2
    assert [
        row["video_id"]
        for row in dp.get_distance_video_queue(data["project"].id, data["species_id"])
    ] == [data["videos"][0]]


@pytest.mark.parametrize("source", ["ordinary", "prediction"])
def test_distance_queue_finds_species_before_distance_individuals_are_added(distance_data, source):
    data = distance_data
    dp, second_video = data["dp"], data["videos"][1]
    with dp.Session.begin() as session:
        if source == "ordinary":
            session.add(
                IndividualObservation(video_id=second_video, id=1, species_id=data["species_id"])
            )
        else:
            session.add(
                ModelAnnotation(
                    video_id=second_video,
                    annotation_type="species",
                    model_name="demo",
                    value_text="deer",
                )
            )
    assert {
        row["video_id"]
        for row in dp.get_distance_video_queue(data["project"].id, data["species_id"])
    } == set(data["videos"])


def test_distance_species_options_use_project_vocabulary_and_language(distance_data):
    data = distance_data
    dp = data["dp"]
    dp.set_project_species(data["project"].id, ["deer"])
    assert dp.get_distance_species_options(data["project"].id) == {
        data["species_id"]: "Red Deer (deer)"
    }


def test_reviewed_species_are_ordered_unique_manual_labels_for_this_clip(distance_data):
    data = distance_data
    dp, video_id = data["dp"], data["videos"][0]
    with dp.Session.begin() as session:
        fox = session.query(Species).filter_by(scientific_name="fox").one().id
        session.add_all(
            [
                IndividualObservation(video_id=video_id, id=3, species_id=fox, count=4),
                IndividualObservation(
                    video_id=video_id, id=1, species_id=data["species_id"], count=2
                ),
                IndividualObservation(
                    video_id=video_id, id=2, species_id=data["species_id"], count=3
                ),
                ModelAnnotation(
                    video_id=data["videos"][1],
                    annotation_type="species",
                    model_name="demo",
                    value_text="fox",
                ),
            ]
        )
    assert dp.get_distance_reviewed_species(video_id) == [data["species_id"], fox]
    assert dp.get_distance_reviewed_species_counts(video_id) == [
        {"species_id": data["species_id"], "count": 5},
        {"species_id": fox, "count": 4},
    ]
    assert dp.get_distance_reviewed_species_counts(data["videos"][1]) == []


def test_species_correction_preserves_evidence_and_reopens_completion(distance_data):
    data = distance_data
    dp, video_id, individual_id = data["dp"], data["videos"][0], data["individual"]["id"]
    dp.set_distance_individual_attributes(individual_id, age="Adult", sex="Unknown", tag_ids=[])
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    offsets = dp.get_distance_review_state(video_id, "alice")["offsets"]
    for offset in offsets:
        dp.save_distance_sample(
            individual_id,
            offset,
            reference_frame_id=data["frame"]["id"],
            behavior_ids=[data["behavior_id"]],
            labeled_by="alice",
        )
    dp.finish_distance_video(video_id, "alice")
    before = dp.list_distance_individuals(video_id)[0]
    samples = dp.get_distance_review_state(video_id, "alice")["samples"]
    with dp.Session.begin() as session:
        fox = session.query(Species).filter_by(scientific_name="fox").one().id
        session.add(IndividualObservation(video_id=video_id, id=1, species_id=data["species_id"]))

    dp.set_distance_individual_attributes(
        individual_id, species_id=fox, age="Adult", sex="Female", tag_ids=[], labeled_by="bob"
    )

    after = dp.list_distance_individuals(video_id)[0]
    assert after["species_id"] == fox and after["labeled_by"] == "bob"
    for key in ("id", "individual_number", "first_seen_offset_ms", "anchor_x", "anchor_y"):
        assert after[key] == before[key]
    assert after["review_status"] == "completed" and after["completed_at"] is not None
    state = dp.get_distance_review_state(video_id, "alice")
    assert state["samples"] == samples
    assert state["review"]["completed_at"] is None
    assert dp.get_distance_reviewed_species(video_id) == [data["species_id"]]
    # Callers saving only attributes retain the corrected species.
    dp.set_distance_individual_attributes(individual_id, age="Adult", sex=None, tag_ids=[])
    assert dp.list_distance_individuals(video_id)[0]["species_id"] == fox


def test_invalid_species_correction_is_atomic(distance_data):
    data = distance_data
    dp, individual_id = data["dp"], data["individual"]["id"]
    before = dp.list_distance_individuals(data["videos"][0])
    with pytest.raises(ValueError, match="Species not found"):
        dp.set_distance_individual_attributes(
            individual_id, species_id="missing", age="Adult", sex="Female", tag_ids=[]
        )
    assert dp.list_distance_individuals(data["videos"][0]) == before


@pytest.mark.parametrize("correct_infant", [False, True])
def test_species_correction_keeps_connected_carriers_and_infant_consistent(
    distance_data, correct_infant
):
    data = distance_data
    dp, video_id, carrier = data["dp"], data["videos"][0], data["individual"]["id"]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    second_carrier = dp.create_distance_individual(
        video_id, data["species_id"], 3000, anchor=data["anchor"], age="Adult"
    )
    unrelated = dp.create_distance_individual(
        video_id, data["species_id"], 1000, anchor=data["anchor"]
    )
    other_video = dp.create_distance_individual(
        data["videos"][1], data["species_id"], 1000, anchor=data["anchor"]
    )
    first_sample = dp.save_distance_sample(
        carrier,
        1000,
        reference_frame_id=data["frame"]["id"],
        create_infant=True,
        labeled_by="alice",
    )
    infant = first_sample["infant_id"]
    dp.save_distance_sample(
        second_carrier["id"],
        3000,
        reference_frame_id=data["frame"]["id"],
        infant_id=infant,
        labeled_by="alice",
    )
    before = {row["id"]: row for row in dp.list_distance_individuals(video_id)}
    samples = dp.get_distance_review_state(video_id, "alice")["samples"]
    connected = {carrier, second_carrier["id"], infant}
    with dp.Session.begin() as session:
        fox = session.query(Species).filter_by(scientific_name="fox").one().id
        for individual_id in connected:
            row = session.get(DistanceIndividual, individual_id)
            row.review_status, row.completed_at = "completed", datetime(2026, 10, 12)
        session.get(DistanceVideoReview, video_id).completed_at = datetime(2026, 10, 12)
    selected = infant if correct_infant else carrier
    dp.set_distance_individual_attributes(
        selected,
        species_id=fox,
        age=before[selected]["age"],
        sex=before[selected]["sex"],
        tag_ids=before[selected]["tag_ids"],
        labeled_by="bob",
    )
    after = {row["id"]: row for row in dp.list_distance_individuals(video_id)}
    for individual_id in connected:
        row = after[individual_id]
        assert row["species_id"] == fox and row["completed_at"] is None
        assert row["review_status"] == "in_progress" and row["labeled_by"] == "bob"
        for key in ("age", "sex", "tag_ids", "first_seen_offset_ms", "individual_number"):
            assert row[key] == before[individual_id][key]
    assert after[unrelated["id"]] == before[unrelated["id"]]
    assert (
        dp.list_distance_individuals(data["videos"][1])[0]["species_id"]
        == other_video["species_id"]
    )
    state = dp.get_distance_review_state(video_id, "alice")
    assert state["samples"] == samples and state["review"]["completed_at"] is None
    # The existing carrier/infant pair can still be saved after the correction.
    dp.save_distance_sample(
        carrier, 1000, reference_frame_id=data["frame"]["id"], infant_id=infant, labeled_by="bob"
    )


def _annotated_sample(data, **overrides):
    fields = {
        "individual_id": data["individual"]["id"],
        "offset_ms": 1000,
        "status": "annotated",
        "reference_frame_id": data["frame"]["id"],
        "distance_m": data["frame"]["distance_m"],
    }
    fields.update(overrides)
    return DistanceSample(**fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "unreviewed"},
        {"reference_frame_id": None},
        {"distance_m": None},
        {"distance_m": 0},
        {"distance_m": -1},
        {"skip_reason": "individual absent"},
        {"status": "skipped", "skip_reason": "individual absent"},
        {"status": "skipped", "reference_frame_id": None, "distance_m": None},
        {
            "status": "skipped",
            "skip_reason": "other",
            "reference_frame_id": None,
            "distance_m": None,
        },
        {"offset_ms": -1},
    ],
)
def test_sample_state_constraints_reject_invalid_rows(distance_data, fields):
    with pytest.raises(IntegrityError), distance_data["dp"].Session.begin() as session:
        session.add(_annotated_sample(distance_data, **fields))


def test_sample_uniqueness_and_selected_reference_deletion_restriction(distance_data):
    data = distance_data
    with data["dp"].Session.begin() as session:
        session.add(_annotated_sample(data))
    with pytest.raises(IntegrityError), data["dp"].Session.begin() as session:
        session.add(_annotated_sample(data))
    with pytest.raises(IntegrityError), data["dp"].Session.begin() as session:
        session.query(ReferenceVideo).filter_by(id=data["reference"]["id"]).delete()
    with data["dp"].Session() as session:
        assert session.get(ReferenceFrame, data["frame"]["id"]) is not None


def test_an_individual_cannot_carry_itself(distance_data):
    with pytest.raises(IntegrityError), distance_data["dp"].Session.begin() as session:
        session.add(
            _annotated_sample(
                distance_data, carried_by_individual_id=distance_data["individual"]["id"]
            )
        )


@pytest.mark.parametrize("action", ["remove_directory", "delete_missing", "delete_project"])
def test_distance_rows_cascade_through_existing_deletion_paths(distance_data, action):
    data = distance_data
    dp, video_id = data["dp"], data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.set_distance_active_individual(video_id, "alice", data["individual"]["id"])
    infant = dp.create_distance_individual(
        video_id, data["species_id"], 1000, anchor=data["anchor"], age="Infant", sex="Unknown"
    )
    with dp.Session.begin() as session:
        mother_sample = _annotated_sample(data)
        infant_sample = _annotated_sample(
            data, individual_id=infant["id"], carried_by_individual_id=data["individual"]["id"]
        )
        session.add_all([mother_sample, infant_sample])
        session.flush()
        session.add(
            DistanceSampleBehavior(sample_id=infant_sample.id, behavior_id=data["behavior_id"])
        )
        session.add(
            DistanceSample(
                individual_id=infant["id"],
                offset_ms=3000,
                status="skipped",
                skip_reason="individual absent",
            )
        )
    if action == "remove_directory":
        dp.remove_project_dir(data["directory"].id)
    elif action == "delete_missing":
        with dp.engine.begin() as conn:
            conn.execute(
                text("UPDATE videos SET is_missing=1 WHERE video_id=:id"), {"id": video_id}
            )
        dp.delete_missing_videos(data["project"].id)
    else:
        dp.delete_project(data["project"].id)
    with dp.engine.connect() as conn:
        for table in (
            "distance_individuals",
            "distance_samples",
            "distance_sample_behaviors",
            "distance_video_reviews",
            "distance_review_progress",
        ):
            assert conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0
        reference_count = conn.execute(text("SELECT COUNT(*) FROM reference_videos")).scalar()
        assert reference_count == (0 if action == "delete_project" else 1)
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []


@pytest.mark.parametrize("deleted", ["carrier", "infant"])
def test_deleting_individual_cleans_links_and_reopens_reviews(distance_data, deleted):
    data, dp = distance_data, distance_data["dp"]
    video_id, carrier_id = data["videos"][0], data["individual"]["id"]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    pair = dp.save_distance_sample(
        carrier_id,
        1000,
        reference_frame_id=data["frame"]["id"],
        behavior_ids=[data["behavior_id"]],
        create_infant=True,
        labeled_by="alice",
    )
    infant_id = pair["infant_id"]
    dp.save_distance_sample(infant_id, 3000, skip_reason="individual absent", labeled_by="alice")
    deleted_id = carrier_id if deleted == "carrier" else infant_id
    survivor_id = infant_id if deleted == "carrier" else carrier_id
    dp.set_distance_active_individual(video_id, "alice", deleted_id)
    dp.set_distance_active_individual(video_id, "bob", survivor_id)
    other = dp.create_distance_individual(
        data["videos"][1], data["species_id"], 0, anchor=data["anchor"]
    )
    with dp.engine.connect() as conn:
        tag_id = conn.execute(text("SELECT id FROM tags WHERE key='injured'")).scalar()
    dp.set_distance_individual_attributes(
        deleted_id,
        age="Adult" if deleted == "carrier" else "Infant",
        sex="Unknown",
        tag_ids=[tag_id],
    )
    with dp.Session.begin() as session:
        for individual_id in (carrier_id, infant_id):
            row = session.get(DistanceIndividual, individual_id)
            row.review_status, row.completed_by, row.completed_at = (
                "completed",
                "alice",
                datetime.now(),
            )
        review = session.get(DistanceVideoReview, video_id)
        review.completed_by, review.completed_at = "alice", datetime.now()

    dp.delete_distance_individual(deleted_id)

    survivor = dp.list_distance_individuals(video_id)
    assert len(survivor) == 1 and survivor[0]["id"] == survivor_id
    assert survivor[0]["review_status"] == "in_progress"
    assert survivor[0]["completed_at"] is None
    state = dp.get_distance_review_state(video_id, "alice")
    assert state["active_individual_id"] is None
    assert state["review"]["completed_at"] is None
    assert dp.get_distance_review_state(video_id, "bob")["active_individual_id"] == survivor_id
    assert [sample["offset_ms"] for sample in state["samples"]] == (
        [3000] if deleted == "carrier" else [1000]
    )
    assert all(sample["carried_by_individual_id"] is None for sample in state["samples"])
    assert dp.list_distance_individuals(data["videos"][1])[0]["id"] == other["id"]
    with dp.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM distance_individual_tags")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM distance_sample_behaviors")).scalar() == (
            0 if deleted == "carrier" else 1
        )
        assert conn.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    assert dp.list_reference_frames(data["reference"]["id"])[0]["id"] == data["frame"]["id"]
    with pytest.raises(ValueError, match="Distance individual not found"):
        dp.delete_distance_individual(deleted_id)


def test_distance_migration_upgrades_v22_and_matches_constraints(tmp_path):
    upgraded_path = tmp_path / "upgraded.db"
    with sqlite3.connect(upgraded_path) as conn:
        conn.executescript((Path(__file__).parent / "fixtures/schema_snapshot.sql").read_text())
        conn.execute(
            "INSERT INTO projects (id, name, created_at) VALUES ('p1', 'Existing', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO videos (video_id, project_id, video_path, last_seen_at) VALUES ('v1', 'p1', 'animal.mp4', '2026-01-01')"
        )
    upgraded = create_engine(f"sqlite:///{upgraded_path}")
    fresh = create_engine("sqlite://")
    try:
        Base.metadata.create_all(fresh)
        run_migrations(upgraded)
        run_migrations(upgraded)
        with upgraded.connect() as conn:
            assert (
                conn.execute(text("SELECT name FROM projects WHERE id='p1'")).scalar()
                == "Existing"
            )
            assert (
                conn.execute(text("SELECT video_path FROM videos WHERE video_id='v1'")).scalar()
                == "animal.mp4"
            )
            assert conn.execute(text("SELECT version FROM _schema_version")).scalar() == len(
                MIGRATIONS
            )
        for table in DISTANCE_TABLES:

            def checks(engine):
                return {
                    (row["name"], " ".join(row["sqltext"].split()))
                    for row in inspect(engine).get_check_constraints(table)
                }

            assert checks(upgraded) == checks(fresh)

            def foreign_keys(engine):
                # SQLAlchemy can omit ON DELETE from ALTER-added inline FKs.
                # Compare SQLite's actual constraints, ignoring declaration IDs.
                with engine.connect() as conn:
                    return sorted(
                        tuple(row[1:])
                        for row in conn.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")')
                    )

            assert foreign_keys(upgraded) == foreign_keys(fresh)
        for engine in (upgraded, fresh):
            with engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO projects (id, name, created_at) "
                    "VALUES ('p1', 'Existing', '2026-01-01')"
                )
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO videos "
                    "(video_id, project_id, video_path, last_seen_at) "
                    "VALUES ('v1', 'p1', 'animal.mp4', '2026-01-01')"
                )
                conn.execute(
                    Species.__table__.insert(),
                    {"id": "species", "scientific_name": "deer"},
                )
                conn.execute(
                    DistanceIndividual.__table__.insert(),
                    [
                        {
                            "id": individual_id,
                            "video_id": "v1",
                            "individual_number": number,
                            "species_id": "species",
                            "first_seen_offset_ms": 0,
                            "initial_carrier_individual_id": carrier_id,
                        }
                        for individual_id, number, carrier_id in (
                            ("carrier", 1, None),
                            ("infant", 2, "carrier"),
                        )
                    ],
                )
                conn.exec_driver_sql("DELETE FROM distance_individuals WHERE id='carrier'")
                assert conn.exec_driver_sql(
                    "SELECT id, initial_carrier_individual_id FROM distance_individuals"
                ).all() == [("infant", None)]
                assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        upgraded.dispose()
        fresh.dispose()


def test_current_schema_startup_preserves_individuals_and_all_linked_data(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id, individual_id = data["videos"][0], data["individual"]["id"]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    sample = dp.save_distance_sample(
        individual_id,
        1000,
        reference_frame_id=data["frame"]["id"],
        behavior_ids=[data["behavior_id"]],
        create_infant=True,
        labeled_by="alice",
    )
    dp.set_distance_active_individual(video_id, "alice", individual_id)
    with dp.engine.connect() as conn:
        tag_id = conn.execute(text("SELECT id FROM tags WHERE key='injured'")).scalar()
    dp.set_distance_individual_attributes(
        individual_id, age="Adult", sex="Female", tag_ids=[tag_id], labeled_by="alice"
    )
    box = dp.create_distance_individual(
        video_id,
        data["species_id"],
        2000,
        anchor={"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.5},
        labeled_by="alice",
    )

    individuals = dp.list_distance_individuals(video_id)
    state = dp.get_distance_review_state(video_id, "alice")
    run_migrations(dp.engine)
    run_migrations(dp.engine)

    after = dp.list_distance_individuals(video_id)
    assert after == individuals
    assert next(row for row in after if row["id"] == box["id"])["anchor_width"] == 0.4
    assert dp.get_distance_review_state(video_id, "alice") == state
    assert any(row["id"] == sample["infant_id"] for row in after)
    with dp.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(IntegrityError, match="ck_distance_anchor"), dp.engine.begin() as conn:
        conn.execute(
            text("UPDATE distance_individuals SET anchor_width=NULL WHERE id=:id"),
            {"id": individual_id},
        )


def test_distance_queue_filters_use_distance_completion_and_camera(distance_data):
    data = distance_data
    dp, project = data["dp"], data["project"]
    first, second = data["videos"][:2]
    with dp.Session.begin() as session:
        session.get(Video, second).camera_id = "cam_other"
    # Ordinary review has no effect on Distance's not-started filter.
    dp.update_manual_review(first, [], is_blank=True)
    assert first in {
        row["video_id"] for row in dp.get_distance_video_queue(project.id, status="not_started")
    }
    dp.verify_distance_recording_time(first, datetime(2026, 10, 12, 16, 2, 49), "alice")
    assert [
        row["video_id"]
        for row in dp.get_distance_video_queue(project.id, camera_id="cam_x", status="in_progress")
    ] == [first]
    assert first not in {
        row["video_id"] for row in dp.get_distance_video_queue(project.id, status="not_started")
    }
    individual = data["individual"]
    for offset in dp.get_distance_review_state(first, "alice")["offsets"]:
        dp.save_distance_sample(
            individual["id"], offset, skip_reason="individual absent", labeled_by="alice"
        )
    assert dp.list_distance_individuals(first)[0]["review_status"] == "completed"
    # Complete sample coverage alone does not finish its video.
    dp.set_distance_individual_attributes(
        individual["id"], age="Unknown", sex="Unknown", tag_ids=[]
    )
    assert dp.get_distance_video_queue(project.id, status="completed") == []
    dp.finish_distance_video(first, "alice")
    assert [
        row["video_id"] for row in dp.get_distance_video_queue(project.id, status="completed")
    ] == [first]
    assert dp.get_distance_video_queue(project.id, camera_id="cam_other", status="completed") == []
    dp.save_distance_sample(
        individual["id"], 1000, skip_reason="midpoint not visible", labeled_by="alice"
    )
    assert dp.get_distance_video_queue(project.id, status="completed") == []
    assert [
        row["video_id"] for row in dp.get_distance_video_queue(project.id, status="in_progress")
    ] == [first]


def test_distance_queue_rejects_unknown_status(distance_data):
    with pytest.raises(ValueError, match="Invalid distance review status"):
        distance_data["dp"].get_distance_video_queue(
            distance_data["project"].id, status="annotated"
        )
