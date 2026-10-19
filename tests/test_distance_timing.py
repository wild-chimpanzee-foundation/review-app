"""Timing, provenance, atomic carried samples, and independent review completion."""

from datetime import datetime, timedelta, timezone

import pytest
from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceSample,
    DistanceVideoReview,
    ReferenceFrame,
    Video,
)
from review_app.backend.distance_timing import (
    sample_camera_time,
    sample_frame_time,
    sample_offsets,
)
from review_app.backend.provider import video as video_module
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import text


@pytest.mark.parametrize(
    "start,duration,expected",
    [
        ("2026-10-12T16:02:48", 6, [0, 2000, 4000]),
        ("2026-10-12T16:02:49", 6, [1000, 3000, 5000]),
        ("2026-10-12T23:59:59.250", 5, [750, 2750, 4750]),
        ("2026-10-12T16:02:48.250", 6, [1750, 3750, 5750]),
        ("2026-10-12T16:02:49", 1, []),
        ("2026-10-12T16:02:49", 1.001, [1000]),
        ("2026-10-12T16:02:48", 0.001, [0]),
    ],
)
def test_sample_offsets_follow_camera_clock_and_exclude_video_endpoint(start, duration, expected):
    recorded = datetime.fromisoformat(start)
    assert sample_offsets(recorded, duration) == expected
    assert all((recorded + timedelta(milliseconds=offset)).second % 2 == 0 for offset in expected)
    assert all((recorded + timedelta(milliseconds=offset)).microsecond == 0 for offset in expected)


@pytest.mark.parametrize("duration", [None, 0, -1, float("nan"), float("inf")])
def test_unknown_or_invalid_duration_cannot_be_scheduled(duration):
    with pytest.raises(ValueError, match="duration"):
        sample_offsets(datetime(2026, 10, 12), duration)


def test_recording_time_is_camera_wall_clock_with_millisecond_precision():
    with pytest.raises(ValueError, match="timezone"):
        sample_offsets(datetime(2026, 10, 12, tzinfo=timezone.utc), 10)
    with pytest.raises(ValueError, match="precision"):
        sample_offsets(datetime(2026, 10, 12, microsecond=1), 10)


@pytest.mark.parametrize(
    "offset,rate,expected",
    [
        (0, 25, 0),
        (0, None, 0),
        (1000, 25, 1),
        (750, 25, 0.76),
        (1000, 30000 / 1001, 1.001),
        (1000, None, 1.04),
        (1000, 0, 1.04),
        (1000, float("nan"), 1.04),
    ],
)
def test_sample_estimates_next_frame_from_fps_without_a_frame_scan(offset, rate, expected):
    assert sample_frame_time(offset, rate) == pytest.approx(expected)
    assert sample_frame_time(offset, rate) >= offset / 1000


@pytest.mark.parametrize("rate", [25, 30000 / 1001, None])
def test_frame_estimate_stays_inside_a_short_clip(rate):
    assert 1 <= sample_frame_time(1000, rate, 1.0001) < 1.0001


@pytest.mark.parametrize(
    "opening_even,anchor,expected",
    [
        (False, 920.125, [920, 2920, 4920, 6920, 8920]),
        (True, 1080.125, [0, 1080, 3080, 5080, 7080, 9080]),
        (True, 2040.125, [0, 2040, 4040, 6040, 8040]),
    ],
)
def test_selected_even_frame_calibrates_the_whole_video_and_survives_reopen(
    distance_data, opening_even, anchor, expected
):
    data, dp = distance_data, distance_data["dp"]
    camera_time = datetime(2026, 10, 13, 0, 0, 0)
    dp.calibrate_distance_clock(
        data["videos"][0], camera_time, anchor, "alice", opening_even_second=opening_even
    )
    calibrated = state(data)
    assert calibrated["offsets"] == expected
    assert calibrated["review"]["clock_anchor_offset_ms"] == int(anchor)
    assert calibrated["review"]["clock_anchor_playback_ms"] == anchor
    assert sample_camera_time(calibrated["review"], int(anchor)) == camera_time
    if opening_even:
        assert sample_camera_time(calibrated["review"], 0) == camera_time - timedelta(seconds=2)
    assert all(
        sample_camera_time(calibrated["review"], offset).second % 2 == 0 for offset in expected
    )
    confirm(data, int(anchor))
    with pytest.raises(ValueError, match="scheduled"):
        confirm(data, int(anchor) + 1)
    reopened = LocalDataProvider()
    try:
        assert reopened.get_distance_review_state(data["videos"][0], "alice") == state(data)
    finally:
        reopened.engine.dispose()


@pytest.mark.parametrize(
    "camera,offset,opening_even",
    [
        (datetime(2026, 10, 12, 16, 2, 49), 920, False),
        (datetime(2026, 10, 12, 16, 2, 50, 1000), 920, False),
        (datetime(2026, 10, 12, 16, 2, 50, tzinfo=timezone.utc), 920, False),
        (datetime(2026, 10, 12, 16, 2, 50), -1, False),
        (datetime(2026, 10, 12, 16, 2, 50), True, False),
        (datetime(2026, 10, 12, 16, 2, 50), float("nan"), False),
        (datetime(2026, 10, 12, 16, 2, 50), 10000, False),
        (datetime(2026, 10, 12, 16, 2, 50), 0, False),
        (datetime(2026, 10, 12, 16, 2, 50), 920, "yes"),
    ],
)
def test_invalid_clock_calibration_is_atomic(distance_data, camera, offset, opening_even):
    data = distance_data
    with pytest.raises(ValueError):
        data["dp"].calibrate_distance_clock(
            data["videos"][0], camera, offset, "alice", opening_even_second=opening_even
        )
    assert state(data)["review"] is None
    assert state(data)["offsets"] == []


def test_alignment_changes_require_explicit_reset_after_a_sample(distance_data):
    data, dp = distance_data, distance_data["dp"]
    camera = datetime(2026, 10, 12, 16, 2, 50)
    dp.calibrate_distance_clock(data["videos"][0], camera, 920.125, "alice")
    confirm(data, 920)
    dp.calibrate_distance_clock(data["videos"][0], camera, 920.125, "bob")
    for offset, opening in [(920.5, False), (920.125, True), (1020.125, False)]:
        with pytest.raises(ValueError, match="requires resetting"):
            dp.calibrate_distance_clock(
                data["videos"][0], camera, offset, "alice", opening_even_second=opening
            )
    assert state(data)["review"]["clock_anchor_playback_ms"] == 920.125


def test_calibrated_even_opening_requires_first_still_for_completion(distance_data):
    data, dp = distance_data, distance_data["dp"]
    dp.set_distance_individual_attributes(
        data["individual"]["id"], age="Adult", sex="Unknown", tag_ids=[]
    )
    dp.calibrate_distance_clock(
        data["videos"][0],
        datetime(2026, 10, 12, 16, 2, 50),
        1080.125,
        "alice",
        opening_even_second=True,
    )
    for offset in state(data)["offsets"][1:]:
        dp.save_distance_sample(
            data["individual"]["id"], offset, skip_reason="individual absent", labeled_by="alice"
        )
    with pytest.raises(ValueError, match="every scheduled"):
        dp.finish_distance_video(data["videos"][0], "alice")
    confirm(data, 0)
    dp.finish_distance_video(data["videos"][0], "alice")


@pytest.fixture
def timed_data(distance_data):
    data = distance_data
    data["dp"].set_distance_individual_attributes(
        data["individual"]["id"], age="Adult", sex="Unknown", tag_ids=[]
    )
    data["dp"].verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    return data


def confirm(data, offset=1000, **kwargs):
    return data["dp"].save_distance_sample(
        data["individual"]["id"],
        offset,
        reference_frame_id=data["frame"]["id"],
        labeled_by="alice",
        **kwargs,
    )


def state(data):
    return data["dp"].get_distance_review_state(data["videos"][0], "alice")


def test_unverified_import_metadata_never_schedules_samples(distance_data):
    data = distance_data
    assert state(data)["offsets"] == []
    with pytest.raises(ValueError, match="Verify"):
        confirm(data)
    assert state(data)["samples"] == []


@pytest.mark.parametrize("offset", [0, 2000, 10000, -1, True, 1000.0])
def test_only_scheduled_offsets_can_be_saved(timed_data, offset):
    with pytest.raises(ValueError):
        confirm(timed_data, offset)
    assert state(timed_data)["samples"] == []


def test_confirmation_persists_provenance_behaviors_and_durable_sample_identity(timed_data):
    data = timed_data
    row = confirm(data, behavior_ids=[data["behavior_id"], data["behavior_id"]])
    saved = state(data)["samples"][0]
    assert saved["id"] == row["id"]
    assert saved["reference_frame_id"] == data["frame"]["id"]
    assert saved["reference_video_id"] == data["reference"]["id"]
    assert saved["distance_m"] == 5.0 and saved["status"] == "annotated"
    assert saved["behavior_ids"] == [data["behavior_id"]]
    assert saved["labeled_by"] == "alice" and saved["labeled_at"] is not None
    edited = confirm(data, behavior_ids=[])
    assert edited["id"] == row["id"] and state(data)["samples"][0]["behavior_ids"] == []
    assert (
        data["dp"].list_distance_individuals(data["videos"][0])[0]["review_status"]
        == "in_progress"
    )
    with data["dp"].engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM individual_observations")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM video_labels")).scalar() == 0


def test_skip_replaces_annotation_and_counts_as_reviewed(timed_data):
    data = timed_data
    annotated = confirm(data, behavior_ids=[data["behavior_id"]])
    data["dp"].save_distance_sample(
        data["individual"]["id"], 1000, skip_reason="midpoint not visible", labeled_by="bob"
    )
    sample = state(data)["samples"][0]
    assert sample["id"] == annotated["id"] and sample["status"] == "skipped"
    assert sample["reference_frame_id"] is None and sample["distance_m"] is None
    assert sample["behavior_ids"] == [] and sample["labeled_by"] == "bob"
    assert len(state(data)["samples"]) == 1
    confirm(data)
    assert state(data)["samples"][0]["skip_reason"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"skip_reason": "other"},
        {"skip_reason": "individual absent", "reference_frame_id": "frame"},
    ],
)
def test_invalid_skip_fields_are_atomic(timed_data, kwargs):
    data = timed_data
    with pytest.raises(ValueError, match="skipped"):
        data["dp"].save_distance_sample(
            data["individual"]["id"], 1000, labeled_by="alice", **kwargs
        )
    assert state(data)["samples"] == []


@pytest.mark.parametrize("mismatch", ["camera", "project", "missing"])
def test_confirmation_validates_reference_deployment_and_bin(timed_data, mismatch):
    data = timed_data
    dp = data["dp"]
    if mismatch == "missing":
        frame_id = "missing"
    else:
        project = (
            dp.create_project("Other", "/other") if mismatch == "project" else data["project"]
        )
        reference = dp.register_reference_video(
            project.id, "other" if mismatch == "camera" else "cam_x", "other.mp4"
        )
        frame_id = dp.add_reference_frame(reference["id"], 0, 10)["id"]
    with pytest.raises(ValueError):
        dp.save_distance_sample(
            data["individual"]["id"], 1000, reference_frame_id=frame_id, labeled_by="alice"
        )
    assert state(data)["samples"] == []


def test_invalid_behavior_cannot_partially_create_infant(timed_data):
    data = timed_data
    with pytest.raises(ValueError, match="Behavior"):
        confirm(data, behavior_ids=["missing"], create_infant=True)
    assert state(data)["samples"] == []
    assert len(data["dp"].list_distance_individuals(data["videos"][0])) == 1


def test_new_carried_infant_inherits_anchor_and_only_precreation_samples_are_absent(timed_data):
    data, dp = timed_data, timed_data["dp"]
    result = dp.save_distance_sample(
        data["individual"]["id"],
        3000,
        skip_reason="midpoint not visible",
        behavior_ids=[data["behavior_id"]],
        create_infant=True,
        creation_offset_ms=2500,
        labeled_by="alice",
    )
    infant_id = result["infant_id"]
    parent = next(
        row
        for row in dp.list_distance_individuals(data["videos"][0])
        if row["id"] == data["individual"]["id"]
    )
    infant = next(
        row for row in dp.list_distance_individuals(data["videos"][0]) if row["id"] == infant_id
    )
    assert infant["first_seen_offset_ms"] == parent["first_seen_offset_ms"]
    assert [infant[key] for key in ("anchor_x", "anchor_y", "anchor_width", "anchor_height")] == [
        parent[key] for key in ("anchor_x", "anchor_y", "anchor_width", "anchor_height")
    ]
    child = {
        row["offset_ms"]: row
        for row in state(data)["samples"]
        if row["individual_id"] == infant_id
    }
    assert child[1000]["skip_reason"] == "individual absent"
    assert child[3000]["skip_reason"] == "midpoint not visible"
    assert child[3000]["behavior_ids"] == [data["behavior_id"]]
    assert 5000 not in child
    # Once carrying is untoggled, later parent writes do not create infant evidence.
    dp.save_distance_sample(
        data["individual"]["id"], 5000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    assert 5000 not in {
        row["offset_ms"] for row in state(data)["samples"] if row["individual_id"] == infant_id
    }
    assert state(data)["review"]["completed_at"] is None


def test_carried_infant_is_created_atomically_reused_then_reviewed_independently(timed_data):
    data, dp = timed_data, timed_data["dp"]
    result = confirm(data, create_infant=True, behavior_ids=[data["behavior_id"]])
    infant_id = result["infant_id"]
    infant = dp.list_distance_individuals(data["videos"][0])[1]
    assert infant["id"] == infant_id and infant["age"] == "Infant" and infant["sex"] == "Unknown"
    assert (
        infant["individual_number"] == 2
        and infant["first_seen_offset_ms"] == data["individual"]["first_seen_offset_ms"]
    )
    samples = state(data)["samples"]
    assert len(samples) == 2
    child = next(row for row in samples if row["individual_id"] == infant_id)
    assert child["carried_by_individual_id"] == data["individual"]["id"]
    assert child["reference_frame_id"] == result["reference_frame_id"]
    assert child["distance_m"] == result["distance_m"] and child["offset_ms"] == 1000
    assert child["behavior_ids"] == [data["behavior_id"]]
    confirm(data, 3000, infant_id=infant_id)
    assert len(dp.list_distance_individuals(data["videos"][0])) == 2
    dp.save_distance_sample(
        infant_id,
        5000,
        reference_frame_id=data["frame"]["id"],
        labeled_by="alice",
        behavior_ids=[data["behavior_id"]],
    )
    independent = next(
        row
        for row in state(data)["samples"]
        if row["individual_id"] == infant_id and row["offset_ms"] == 5000
    )
    assert independent["carried_by_individual_id"] is None


def test_carrier_corrections_sync_infant_evidence_preserve_behavior_and_remove_stale_links(
    timed_data,
):
    data, dp = timed_data, timed_data["dp"]
    infant_id = confirm(data, create_infant=True)["infant_id"]
    dp.save_distance_sample(
        infant_id,
        1000,
        reference_frame_id=data["frame"]["id"],
        labeled_by="alice",
        behavior_ids=[data["behavior_id"]],
    )
    confirm(data, 3000, infant_id=infant_id)
    other_frame = dp.add_reference_frame(data["reference"]["id"], 0, 10)
    dp.save_distance_sample(
        data["individual"]["id"],
        1000,
        reference_frame_id=other_frame["id"],
        infant_id=infant_id,
        labeled_by="bob",
    )
    child = next(
        row
        for row in state(data)["samples"]
        if row["individual_id"] == infant_id and row["offset_ms"] == 1000
    )
    assert child["reference_frame_id"] == other_frame["id"] and child["distance_m"] == 10.0
    assert child["behavior_ids"] == []
    assert child["carried_by_individual_id"] == data["individual"]["id"]
    dp.save_distance_sample(
        data["individual"]["id"], 1000, skip_reason="individual absent", labeled_by="bob"
    )
    assert not any(
        row["individual_id"] == infant_id and row["offset_ms"] == 1000
        for row in state(data)["samples"]
    )
    assert any(
        row["individual_id"] == infant_id and row["offset_ms"] == 3000
        for row in state(data)["samples"]
    )


@pytest.mark.parametrize("target", ["self", "other_video", "non_infant", "missing"])
def test_invalid_carried_identity_does_not_overwrite_previous_sample(timed_data, target):
    data, dp = timed_data, timed_data["dp"]
    confirm(data)
    before = state(data)
    if target == "self":
        infant_id = data["individual"]["id"]
    elif target == "missing":
        infant_id = "missing"
    else:
        infant_id = dp.create_distance_individual(
            data["videos"][1 if target == "other_video" else 0],
            data["species_id"],
            0,
            anchor=data["anchor"],
            age="Infant" if target == "other_video" else "Adult",
        )["id"]
    with pytest.raises(ValueError):
        confirm(data, infant_id=infant_id)
    assert state(data) == before


def test_carry_relationship_cannot_conflict_or_invalidate_ages(timed_data):
    data, dp = timed_data, timed_data["dp"]
    infant_id = confirm(data, create_infant=True)["infant_id"]
    other = dp.create_distance_individual(
        data["videos"][0], data["species_id"], 0, anchor=data["anchor"]
    )
    with pytest.raises(ValueError, match="another individual"):
        dp.save_distance_sample(
            other["id"],
            1000,
            reference_frame_id=data["frame"]["id"],
            infant_id=infant_id,
            labeled_by="alice",
        )
    with pytest.raises(ValueError, match="cannot also carry"):
        dp.save_distance_sample(
            infant_id,
            1000,
            reference_frame_id=data["frame"]["id"],
            create_infant=True,
            labeled_by="alice",
        )
    with pytest.raises(ValueError, match="retain age Infant"):
        dp.set_distance_individual_attributes(infant_id, age="Adult", sex="Unknown", tag_ids=[])
    with pytest.raises(ValueError, match="cannot have age Infant"):
        dp.set_distance_individual_attributes(
            data["individual"]["id"], age="Infant", sex=None, tag_ids=[]
        )
    assert len(state(data)["samples"]) == 2


def test_verified_timestamp_correction_keeps_the_sampling_grid(timed_data):
    data, dp = timed_data, timed_data["dp"]
    corrected_start = datetime(2026, 10, 12, 16, 2, 51)
    dp.verify_distance_recording_time(data["videos"][0], corrected_start, "bob")
    assert state(data)["offsets"] == [1000, 3000, 5000, 7000, 9000]
    confirm(data)
    with pytest.raises(ValueError, match="requires resetting"):
        dp.verify_distance_recording_time(
            data["videos"][0], corrected_start + timedelta(seconds=1), "alice"
        )
    assert state(data)["review"]["recorded_start"] == corrected_start


def test_even_start_cannot_complete_without_reviewing_the_opening_still(distance_data):
    data, dp = distance_data, distance_data["dp"]
    dp.set_distance_individual_attributes(
        data["individual"]["id"], age="Adult", sex="Unknown", tag_ids=[]
    )
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 48), "alice"
    )
    offsets = state(data)["offsets"]
    assert offsets[0] == 0
    for offset in offsets[1:]:
        dp.save_distance_sample(
            data["individual"]["id"], offset, skip_reason="individual absent", labeled_by="alice"
        )
    with pytest.raises(ValueError, match="every scheduled"):
        dp.finish_distance_video(data["videos"][0], "alice")
    confirm(data, 0)
    dp.finish_distance_video(data["videos"][0], "alice")
    assert state(data)["review"]["completed_at"] is not None


def test_progress_is_annotator_specific_and_survives_reopen(timed_data):
    data, dp = timed_data, timed_data["dp"]
    second = dp.create_distance_individual(
        data["videos"][0], data["species_id"], 0, anchor=data["anchor"]
    )
    dp.set_distance_active_individual(data["videos"][0], "alice", second["id"])
    dp.set_distance_active_individual(data["videos"][0], "bob", data["individual"]["id"])
    confirm(data)
    assert state(data)["active_individual_id"] == second["id"]
    assert (
        dp.get_distance_review_state(data["videos"][0], "bob")["active_individual_id"]
        == data["individual"]["id"]
    )
    with pytest.raises(ValueError, match="belong"):
        dp.set_distance_active_individual(data["videos"][1], "alice", second["id"])
    reopened = LocalDataProvider()
    try:
        assert reopened.get_distance_review_state(data["videos"][0], "alice") == state(data)
    finally:
        reopened.engine.dispose()


def complete(data):
    dp = data["dp"]
    for offset in state(data)["offsets"]:
        dp.save_distance_sample(
            data["individual"]["id"], offset, skip_reason="individual absent", labeled_by="alice"
        )
    dp.finish_distance_video(data["videos"][0], "alice")


def test_completion_requires_all_samples_and_automatically_finishes_individual(timed_data):
    data, dp = timed_data, timed_data["dp"]
    confirm(data)
    with pytest.raises(ValueError, match="every scheduled"):
        dp.finish_distance_individual(data["individual"]["id"], "alice")
    with pytest.raises(ValueError, match="every scheduled"):
        dp.finish_distance_video(data["videos"][0], "alice")
    complete(data)
    individual = dp.list_distance_individuals(data["videos"][0])[0]
    assert individual["review_status"] == "completed" and individual["completed_by"] == "alice"
    assert state(data)["review"]["completed_at"] is not None
    with dp.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM video_labels")).scalar() == 0


@pytest.mark.parametrize("change", ["sample", "attributes", "anchor", "new_individual"])
def test_corrections_reopen_completed_distance_review(timed_data, change):
    data, dp = timed_data, timed_data["dp"]
    complete(data)
    if change == "sample":
        confirm(data)
    elif change == "attributes":
        dp.set_distance_individual_attributes(
            data["individual"]["id"], age="Adult", sex="Female", tag_ids=[]
        )
    elif change == "anchor":
        dp.set_distance_individual_anchor(
            data["individual"]["id"], 500, anchor={"x": 0.5, "y": 0.5, "width": 0.2, "height": 0.2}
        )
    else:
        dp.create_distance_individual(
            data["videos"][0], data["species_id"], 0, anchor=data["anchor"]
        )
    assert state(data)["review"]["completed_at"] is None
    assert state(data)["review"]["completed_by"] is None
    individual = dp.list_distance_individuals(data["videos"][0])[0]
    assert individual["review_status"] == "completed"
    if change != "new_individual":
        dp.finish_distance_video(data["videos"][0], "alice")


@pytest.mark.parametrize("duration", [10, 12, None, 0])
@pytest.mark.parametrize("bulk", [False, True])
def test_reprobe_reconciles_distance_completion_without_losing_evidence(
    timed_data, monkeypatch, duration, bulk
):
    data, dp = timed_data, timed_data["dp"]
    complete(data)
    video_id = data["videos"][0]
    saved = state(data)["samples"]
    probe = video_module.ProbeResult(duration, bool(duration), True, None, None, None, None)
    if bulk:
        with dp.Session.begin() as session:
            session.get(Video, video_id).is_valid = False
        monkeypatch.setattr(video_module, "_probe_many", lambda paths: {p: probe for p in paths})
        dp.reprobe_invalid_videos()
    else:
        monkeypatch.setattr(video_module, "_probe_video", lambda path: probe)
        dp.reprobe_video(video_id)
    review_state = state(data)
    assert review_state["samples"] == saved
    assert bool(review_state["review"]["completed_at"]) == (duration == 10)
    individual = dp.list_distance_individuals(video_id)[0]
    assert (individual["review_status"] == "completed") == (duration == 10)
    completed_ids = {
        row["video_id"]
        for row in dp.get_distance_video_queue(data["project"].id, status="completed")
    }
    assert (video_id in completed_ids) == (duration == 10)
    if not duration:
        assert review_state["offsets"] == []
        with pytest.raises(ValueError, match="every scheduled"):
            dp.finish_distance_video(video_id, "alice")


def test_mixed_samples_complete_automatically_and_reconcile_legacy_status(timed_data):
    data, dp = timed_data, timed_data["dp"]
    offsets = state(data)["offsets"]
    for offset in reversed(offsets[1:]):
        dp.save_distance_sample(
            data["individual"]["id"],
            offset,
            skip_reason="midpoint not visible" if offset == offsets[-1] else "individual absent",
            labeled_by="alice",
        )
    assert dp.list_distance_individuals(data["videos"][0])[0]["review_status"] == "in_progress"
    confirm(data, offsets[0])
    with dp.Session.begin() as session:
        individual = session.get(DistanceIndividual, data["individual"]["id"])
        assert individual.review_status == "completed" and individual.completed_at is not None
        # Older versions required a separate finish click after all samples were saved.
        individual.review_status = "in_progress"
        individual.completed_at = individual.completed_by = None
    individual = dp.list_distance_individuals(data["videos"][0])[0]
    assert individual["review_status"] == "completed" and individual["completed_by"] == "alice"
    dp.finish_distance_video(data["videos"][0], "alice")


def test_carried_infant_completes_automatically_and_reopens_when_sample_removed(timed_data):
    data, dp = timed_data, timed_data["dp"]
    offsets = state(data)["offsets"]
    infant_id = confirm(data, offsets[0], create_infant=True)["infant_id"]
    for offset in offsets[1:]:
        confirm(data, offset, infant_id=infant_id)
    assert all(
        row["review_status"] == "completed"
        for row in dp.list_distance_individuals(data["videos"][0])
    )
    dp.finish_distance_video(data["videos"][0], "alice")
    confirm(data, offsets[0])  # Removing the carried link removes this infant sample.
    individuals = dp.list_distance_individuals(data["videos"][0])
    assert individuals[0]["review_status"] == "completed"
    assert individuals[1]["review_status"] == "in_progress"
    assert state(data)["review"]["completed_at"] is None


def test_video_completion_rechecks_actual_coverage(timed_data):
    data, dp = timed_data, timed_data["dp"]
    complete(data)
    with dp.Session.begin() as session:
        session.query(DistanceSample).filter_by(
            individual_id=data["individual"]["id"], offset_ms=1000
        ).delete()
        session.get(DistanceVideoReview, data["videos"][0]).completed_at = None
    with pytest.raises(ValueError, match="every scheduled"):
        dp.finish_distance_video(data["videos"][0], "alice")


@pytest.mark.parametrize("meters", [0.001, 0.5, 1, 2.375, 25, 125.75])
def test_confirmation_stores_matching_reference_distance(timed_data, meters):
    data = timed_data
    frame = data["dp"].add_reference_frame(data["reference"]["id"], 0, meters)
    saved = data["dp"].save_distance_sample(
        data["individual"]["id"], 1000, reference_frame_id=frame["id"], labeled_by="alice"
    )
    assert saved["distance_m"] == meters
    assert state(data)["samples"][0]["distance_m"] == meters
    with data["dp"].Session() as session:
        assert session.get(DistanceSample, saved["id"]).distance_m == meters


def test_saved_distance_is_preserved_until_reference_is_confirmed_again(timed_data):
    data = timed_data
    saved = confirm(data)
    with data["dp"].Session.begin() as session:
        session.get(ReferenceFrame, data["frame"]["id"]).distance_m = 7.25
    assert state(data)["samples"][0]["distance_m"] == 5.0
    corrected = confirm(data, create_infant=True)
    assert corrected["id"] == saved["id"] and corrected["distance_m"] == 7.25
    assert all(sample["distance_m"] == 7.25 for sample in state(data)["samples"])
