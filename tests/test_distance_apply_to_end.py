"""Inclusive, overwriting apply-to-end writes share the ordinary sample transaction logic."""

import math
from datetime import datetime

import pytest
from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceIndividualTag,
    DistanceReviewProgress,
    DistanceSample,
    DistanceSampleBehavior,
    DistanceVideoReview,
    IndividualObservation,
    ReferenceFrame,
    Species,
    Tag,
    Video,
    VideoTag,
)


@pytest.fixture
def apply_data(distance_data):
    data = distance_data
    data["dp"].verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    return data


def apply(data, offset=1000, **kwargs):
    fields = {"reference_frame_id": data["frame"]["id"], "labeled_by": "bob"}
    if "skip_reason" in kwargs:
        fields.pop("reference_frame_id")
    fields.update(kwargs)
    return data["dp"].apply_distance_to_end(data["individual"]["id"], offset, **fields)


def samples(data, individual_id=None):
    individual_id = individual_id or data["individual"]["id"]
    return {
        row["offset_ms"]: row
        for row in data["dp"].get_distance_review_state(data["videos"][0], "alice")["samples"]
        if row["individual_id"] == individual_id
    }


def snapshot(data):
    """Read without completion reconciliation, so rollback comparisons include all metadata."""
    with data["dp"].engine.connect() as conn:
        return {
            model.__tablename__: conn.execute(
                model.__table__.select().order_by(*model.__table__.primary_key.columns)
            ).all()
            for model in (
                DistanceIndividual,
                DistanceIndividualTag,
                DistanceReviewProgress,
                DistanceSample,
                DistanceSampleBehavior,
                DistanceVideoReview,
                IndividualObservation,
                VideoTag,
            )
        }


def individual(data, *, video_id=None, age="Infant", species_id=None):
    return data["dp"].create_distance_individual(
        video_id or data["videos"][0],
        species_id or data["species_id"],
        0,
        anchor=data["anchor"],
        age=age,
        sex="Unknown",
        labeled_by="alice",
    )["id"]


def tag(data, individual_id, age):
    dp = data["dp"]
    dp.create_custom_tag("Bulk test tag")
    dp.set_tag_individual_eligibility("bulk_test_tag", True)
    with dp.Session() as session:
        tag_id = session.query(Tag).filter_by(key="bulk_test_tag").one().id
    dp.set_distance_individual_attributes(
        individual_id, age=age, sex="Unknown", tag_ids=[tag_id], labeled_by="alice"
    )
    return tag_id


@pytest.mark.parametrize("choice", ["distance", "individual absent", "midpoint not visible"])
def test_overwrites_every_state_inclusively_with_unsaved_choice_and_behaviors(apply_data, choice):
    data, dp = apply_data, apply_data["dp"]
    parent, video = data["individual"]["id"], data["videos"][0]
    dp.save_distance_sample(parent, 1000, skip_reason="individual absent", labeled_by="alice")
    dp.save_distance_sample(
        parent, 3000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    dp.save_distance_sample(parent, 5000, skip_reason="individual absent", labeled_by="alice")
    dp.save_distance_sample(parent, 7000, skip_reason="midpoint not visible", labeled_by="alice")
    before = samples(data)
    tag_id = tag(data, parent, "Adult")
    new_frame = dp.add_reference_frame(data["reference"]["id"], 4000, 7.25)
    fields = (
        {"reference_frame_id": new_frame["id"]}
        if choice == "distance"
        else {"skip_reason": choice}
    )
    result = apply(data, 3000, behavior_ids=[data["behavior_id"], data["behavior_id"]], **fields)

    assert set(result) == {"samples", "infant_id"}
    assert result["infant_id"] is None
    assert [row["offset_ms"] for row in result["samples"]] == [3000, 5000, 7000, 9000]
    after = samples(data)
    assert after[1000] == before[1000]
    for offset in (3000, 5000, 7000, 9000):
        row = after[offset]
        if offset in before:
            assert row["id"] == before[offset]["id"]
        assert row["individual_id"] == parent and row["labeled_by"] == "bob"
        assert row["behavior_ids"] == [data["behavior_id"]]
        assert row["carried_by_individual_id"] is None
        assert row["status"] == ("annotated" if choice == "distance" else "skipped")
        assert row["skip_reason"] == (None if choice == "distance" else choice)
        assert row["reference_frame_id"] == (new_frame["id"] if choice == "distance" else None)
        assert row["distance_m"] == (7.25 if choice == "distance" else None)
    assert dp.list_distance_individuals(video)[0]["tag_ids"] == [tag_id]
    # Omitted behaviors mean an empty choice, not "preserve existing".
    apply(data, 7000, **fields)
    assert samples(data)[5000]["behavior_ids"] == [data["behavior_id"]]
    assert samples(data)[7000]["behavior_ids"] == samples(data)[9000]["behavior_ids"] == []


def test_other_individuals_videos_ordinary_review_and_active_selection_are_untouched(apply_data):
    data, dp = apply_data, apply_data["dp"]
    other = individual(data, age="Adult")
    other_video = individual(data, age="Adult", video_id=data["videos"][1])
    dp.verify_distance_recording_time(
        data["videos"][1], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    for target in (other, other_video):
        dp.save_distance_sample(
            target,
            5000,
            reference_frame_id=data["frame"]["id"],
            behavior_ids=[data["behavior_id"]],
            labeled_by="alice",
        )
    dp.set_distance_active_individual(data["videos"][0], "alice", other)
    dp.set_distance_active_individual(data["videos"][0], "bob", data["individual"]["id"])
    with dp.Session.begin() as session:
        session.add(
            IndividualObservation(video_id=data["videos"][0], id=1, species_id=data["species_id"])
        )
    before = snapshot(data)
    apply(data, 5000, skip_reason="individual absent")
    after = snapshot(data)
    for table in ("individual_observations", "distance_review_progress", "video_tags"):
        assert after[table] == before[table]
    for table in ("distance_individuals", "distance_samples"):
        key = "id" if table == "distance_individuals" else "individual_id"
        assert [row for row in after[table] if getattr(row, key) in {other, other_video}] == [
            row for row in before[table] if getattr(row, key) in {other, other_video}
        ]
    assert set(samples(data)) == {5000, 7000, 9000}
    assert dp.list_distance_individuals(data["videos"][0])[0]["review_status"] == "in_progress"


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("choice", ["distance", "individual absent", "midpoint not visible"])
def test_one_persistent_new_or_reused_infant_receives_evidence_and_behaviors(
    apply_data, reuse, choice
):
    data, dp = apply_data, apply_data["dp"]
    infant_id = individual(data) if reuse else None
    if reuse:
        infant_tag = tag(data, infant_id, "Infant")
        for offset in (1000, 3000, 7000):
            dp.save_distance_sample(
                infant_id, offset, reference_frame_id=data["frame"]["id"], labeled_by="alice"
            )
        earlier = samples(data, infant_id)[1000]
    fields = {} if choice == "distance" else {"skip_reason": choice}
    result = apply(
        data,
        3000,
        infant_id=infant_id,
        create_infant=not reuse,
        behavior_ids=[data["behavior_id"]],
        **fields,
    )
    assert result["infant_id"] and (not reuse or result["infant_id"] == infant_id)
    infant_id = result["infant_id"]
    individuals = dp.list_distance_individuals(data["videos"][0])
    assert len(individuals) == 2
    infant = next(row for row in individuals if row["id"] == infant_id)
    assert (infant["age"], infant["sex"], infant["species_id"]) == (
        "Infant",
        "Unknown",
        data["species_id"],
    )
    if reuse:
        assert infant["tag_ids"] == [infant_tag]
    parent_samples, infant_samples = samples(data), samples(data, infant_id)
    assert set(infant_samples) == {1000, 3000, 5000, 7000, 9000}
    if reuse:
        assert infant_samples[1000] == earlier
    for offset in (3000, 5000, 7000, 9000):
        parent, child = parent_samples[offset], infant_samples[offset]
        for key in (
            "status",
            "skip_reason",
            "reference_frame_id",
            "distance_m",
            "behavior_ids",
            "labeled_by",
        ):
            assert child[key] == parent[key]
        assert child["behavior_ids"] == [data["behavior_id"]]
        assert child["carried_by_individual_id"] == data["individual"]["id"]
    # Repeating the operation uses the same identity and replaces behaviors too.
    apply(data, 5000, infant_id=infant_id, **fields)
    assert len(dp.list_distance_individuals(data["videos"][0])) == 2
    assert samples(data, infant_id)[3000]["behavior_ids"] == [data["behavior_id"]]
    assert samples(data, infant_id)[5000]["behavior_ids"] == []


@pytest.mark.parametrize("reason", ["individual absent", "midpoint not visible"])
def test_skipped_infant_retains_durable_carried_status(apply_data, reason):
    result = apply(apply_data, skip_reason=reason, create_infant=True)
    assert all(
        row["carried_by_individual_id"] == apply_data["individual"]["id"]
        for row in samples(apply_data, result["infant_id"]).values()
    )


@pytest.mark.parametrize("choice", ["distance", "individual absent", "midpoint not visible"])
@pytest.mark.parametrize(
    "initial_choice", ["distance", "individual absent", "midpoint not visible"]
)
def test_carries_off_removes_only_linked_suffix_and_reopens_infant(
    apply_data, choice, initial_choice
):
    data, dp = apply_data, apply_data["dp"]
    initial_fields = {} if initial_choice == "distance" else {"skip_reason": initial_choice}
    infant_id = apply(
        data, create_infant=True, behavior_ids=[data["behavior_id"]], **initial_fields
    )["infant_id"]
    # Independently correct one infant sample; it must survive carries-off.
    other_frame = dp.add_reference_frame(data["reference"]["id"], 4500, 8.5)
    dp.save_distance_sample(
        infant_id,
        7000,
        reference_frame_id=other_frame["id"],
        behavior_ids=[data["behavior_id"]],
        labeled_by="alice",
    )
    before = samples(data, infant_id)
    dp.finish_distance_video(data["videos"][0], "alice")
    fields = {} if choice == "distance" else {"skip_reason": choice}
    assert apply(data, 5000, **fields)["infant_id"] is None
    assert samples(data, infant_id) == {offset: before[offset] for offset in (1000, 3000, 7000)}
    rows = {row["id"]: row for row in dp.list_distance_individuals(data["videos"][0])}
    assert rows[data["individual"]["id"]]["review_status"] == "completed"
    assert rows[infant_id]["review_status"] == "in_progress"
    assert rows[infant_id]["completed_at"] is None
    state = dp.get_distance_review_state(data["videos"][0], "alice")
    assert state["review"]["completed_at"] is None
    remaining_ids = {row["id"] for row in state["samples"]}
    with dp.Session() as session:
        assert all(row.sample_id in remaining_ids for row in session.query(DistanceSampleBehavior))


def test_switching_infants_cleans_old_links_but_not_independent_evidence(apply_data):
    data, dp = apply_data, apply_data["dp"]
    old = apply(data, create_infant=True)["infant_id"]
    dp.save_distance_sample(old, 7000, skip_reason="individual absent", labeled_by="alice")
    before = samples(data, old)
    new = individual(data)
    result = apply(data, 5000, infant_id=new, behavior_ids=[data["behavior_id"]])
    assert result["infant_id"] == new
    assert samples(data, old) == {offset: before[offset] for offset in (1000, 3000, 7000)}
    assert set(samples(data, new)) == {5000, 7000, 9000}


@pytest.mark.parametrize("mode", ["new", "reuse", "off"])
def test_failure_after_mid_batch_writes_rolls_back_everything(apply_data, monkeypatch, mode):
    data, dp = apply_data, apply_data["dp"]
    old = apply(data, create_infant=True, behavior_ids=[data["behavior_id"]])["infant_id"]
    dp.finish_distance_video(data["videos"][0], "alice")
    before = snapshot(data)
    original = dp._save_distance_sample
    written = []

    def fail(session, individual_id, offset_ms, **kwargs):
        result = original(session, individual_id, offset_ms, **kwargs)
        written.append(offset_ms)
        if offset_ms == 5000:
            raise RuntimeError("injected after parent/infant writes")
        return result

    monkeypatch.setattr(dp, "_save_distance_sample", fail)
    fields = (
        {"create_infant": True} if mode == "new" else {"infant_id": old} if mode == "reuse" else {}
    )
    with pytest.raises(RuntimeError, match="injected"):
        apply(data, skip_reason="midpoint not visible", **fields)
    assert written == [1000, 3000, 5000]
    assert snapshot(data) == before


def test_late_carrier_conflict_rolls_back_earlier_successful_writes(apply_data):
    data, dp = apply_data, apply_data["dp"]
    infant = individual(data)
    other = individual(data, age="Adult")
    dp.save_distance_sample(
        other, 9000, reference_frame_id=data["frame"]["id"], infant_id=infant, labeled_by="alice"
    )
    before = snapshot(data)
    with pytest.raises(ValueError, match="already carried"):
        apply(data, 3000, infant_id=infant)
    assert snapshot(data) == before


@pytest.mark.parametrize("offset", [-1, 0, 2000, 10000, True, 3000.0, None])
def test_unscheduled_or_invalid_start_is_atomic(apply_data, offset):
    before = snapshot(apply_data)
    with pytest.raises(ValueError):
        apply(apply_data, offset, create_infant=True)
    assert snapshot(apply_data) == before


@pytest.mark.parametrize(
    "fields",
    [
        {"reference_frame_id": None},
        {"reference_frame_id": "missing"},
        {"reference_frame_id": ""},
        {"skip_reason": "other"},
        {"skip_reason": []},
        {"skip_reason": False},
        {"skip_reason": "individual absent", "reference_frame_id": "frame"},
        {"behavior_ids": ["missing"], "create_infant": True},
        {"behavior_ids": "not-a-list"},
        {"behavior_ids": [None]},
        {"infant_id": "missing"},
        {"infant_id": "existing", "create_infant": True},
        {"create_infant": "yes"},
        {"labeled_by": ""},
        {"labeled_by": "  "},
        {"labeled_by": None},
    ],
)
def test_invalid_choice_is_atomic(apply_data, fields):
    before = snapshot(apply_data)
    with pytest.raises(ValueError):
        apply(apply_data, **fields)
    assert snapshot(apply_data) == before


def test_missing_individual_and_unverified_clock_are_atomic(distance_data):
    data, dp = distance_data, distance_data["dp"]
    before = snapshot(data)
    with pytest.raises(ValueError, match="Verify"):
        apply(data, create_infant=True)
    with pytest.raises(ValueError, match="not found"):
        dp.apply_distance_to_end(
            "missing", 1000, skip_reason="individual absent", labeled_by="bob"
        )
    assert snapshot(data) == before


@pytest.mark.parametrize("target", ["self", "other_video", "adult", "species"])
def test_invalid_infant_relationship_is_atomic(apply_data, target):
    data, dp = apply_data, apply_data["dp"]
    if target == "self":
        infant = data["individual"]["id"]
    else:
        with dp.Session() as session:
            fox = session.query(Species).filter_by(scientific_name="fox").one().id
        infant = individual(
            data,
            video_id=data["videos"][1] if target == "other_video" else None,
            age="Adult" if target == "adult" else "Infant",
            species_id=fox if target == "species" else None,
        )
    before = snapshot(data)
    with pytest.raises(ValueError):
        apply(data, infant_id=infant, skip_reason="midpoint not visible")
    assert snapshot(data) == before


def test_infant_cannot_carry_another_infant(apply_data):
    data, dp = apply_data, apply_data["dp"]
    infant = apply(data, create_infant=True)["infant_id"]
    before = snapshot(data)
    with pytest.raises(ValueError, match="cannot also carry"):
        dp.apply_distance_to_end(
            infant,
            1000,
            reference_frame_id=data["frame"]["id"],
            create_infant=True,
            labeled_by="bob",
        )
    assert snapshot(data) == before


@pytest.mark.parametrize(
    "mismatch", ["camera", "project", "missing_camera", "missing_project", "distance"]
)
def test_invalid_deployment_or_reference_distance_is_atomic(apply_data, mismatch):
    data, dp = apply_data, apply_data["dp"]
    reference = data["frame"]["id"]
    if mismatch in {"camera", "project"}:
        project = dp.create_project("Other deployment", "/other-deployment")
        source = dp.register_reference_video(
            project.id if mismatch == "project" else data["project"].id,
            "elsewhere" if mismatch == "camera" else "cam_x",
            "references/other.mp4",
        )
        reference = dp.add_reference_frame(source["id"], 0, 4)["id"]
    else:
        with dp.Session.begin() as session:
            if mismatch == "distance":
                session.get(ReferenceFrame, reference).distance_m = math.inf
            else:
                video = session.get(Video, data["videos"][0])
                if mismatch == "missing_camera":
                    video.camera_id = None
                else:
                    video.project_id = None
    before = snapshot(data)
    with pytest.raises(ValueError):
        apply(data, reference_frame_id=reference, create_infant=True)
    if mismatch.startswith("missing"):
        with pytest.raises(ValueError):
            apply(data, skip_reason="individual absent", create_infant=True)
    assert snapshot(data) == before


@pytest.mark.parametrize("opening,start", [(False, 2920), (True, 0), (True, 2920)])
def test_calibrated_schedule_is_inclusive_and_honors_opening_exception(
    distance_data, opening, start
):
    data, dp = distance_data, distance_data["dp"]
    dp.calibrate_distance_clock(
        data["videos"][0],
        datetime(2026, 10, 12, 16, 2, 50),
        2920.125,
        "alice",
        opening_even_second=opening,
    )
    result = apply(data, start, skip_reason="midpoint not visible")
    expected = ([0] if start == 0 else []) + [2920, 4920, 6920, 8920]
    assert [row["offset_ms"] for row in result["samples"]] == expected
    assert dp.get_distance_review_state(data["videos"][0], "alice")["offsets"] == (
        ([0] if opening else []) + [2920, 4920, 6920, 8920]
    )
    status = dp.list_distance_individuals(data["videos"][0])[0]["review_status"]
    assert status == ("in_progress" if opening and start != 0 else "completed")


@pytest.mark.parametrize("duration", [None, 0, -1, 2, math.inf])
def test_invalidated_calibrated_schedule_cannot_be_written(distance_data, duration):
    data, dp = distance_data, distance_data["dp"]
    dp.calibrate_distance_clock(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 50), 2920.125, "alice"
    )
    with dp.Session.begin() as session:
        session.get(Video, data["videos"][0]).duration_sec = duration
    before = snapshot(data)
    with pytest.raises(ValueError, match="scheduled"):
        apply(data, 2920, create_infant=True)
    assert snapshot(data) == before


def test_bulk_reopens_finished_video_and_ordinary_corrections_still_work(apply_data):
    data, dp = apply_data, apply_data["dp"]
    video, parent = data["videos"][0], data["individual"]["id"]
    apply(data, skip_reason="individual absent")
    dp.finish_distance_video(video, "alice")
    assert dp.get_distance_review_state(video, "alice")["review"]["completed_at"]
    result = apply(data, create_infant=True, behavior_ids=[data["behavior_id"]])
    infant = result["infant_id"]
    assert all(row["review_status"] == "completed" for row in dp.list_distance_individuals(video))
    assert dp.get_distance_review_state(video, "alice")["review"]["completed_at"] is None
    dp.finish_distance_video(video, "alice")
    # Manual carrier assignments replace the infant's behavior choice too.
    frame = dp.add_reference_frame(data["reference"]["id"], 5000, 9.75)
    dp.save_distance_sample(
        parent, 5000, reference_frame_id=frame["id"], infant_id=infant, labeled_by="alice"
    )
    assert samples(data, infant)[5000]["distance_m"] == 9.75
    assert samples(data, infant)[5000]["behavior_ids"] == []
    assert samples(data)[5000]["behavior_ids"] == []
    dp.save_distance_sample(parent, 5000, skip_reason="midpoint not visible", labeled_by="alice")
    assert 5000 not in samples(data, infant)
    assert samples(data)[5000]["skip_reason"] == "midpoint not visible"
    assert dp.get_distance_review_state(video, "alice")["review"]["completed_at"] is None
    rows = {row["id"]: row for row in dp.list_distance_individuals(video)}
    assert rows[parent]["review_status"] == "completed"
    assert rows[infant]["review_status"] == "in_progress"


def test_final_sample_is_included_without_filling_earlier_gaps(apply_data):
    result = apply(apply_data, 9000, skip_reason="individual absent")
    assert [row["offset_ms"] for row in result["samples"]] == [9000]
    assert set(samples(apply_data)) == {9000}


def test_uncalibrated_even_start_includes_the_opening_sample(distance_data):
    data = distance_data
    data["dp"].verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 48), "alice"
    )
    result = apply(data, 0)
    assert [row["offset_ms"] for row in result["samples"]] == [0, 2000, 4000, 6000, 8000]
    assert (
        data["dp"].list_distance_individuals(data["videos"][0])[0]["review_status"] == "completed"
    )


@pytest.mark.parametrize("individual_id", [None, "", []])
def test_invalid_individual_id_is_rejected_without_writes(apply_data, individual_id):
    data = apply_data
    before = snapshot(data)
    with pytest.raises(ValueError):
        data["dp"].apply_distance_to_end(
            individual_id, 1000, skip_reason="individual absent", labeled_by="alice"
        )
    assert snapshot(data) == before


def test_old_bulk_absence_api_is_removed(apply_data):
    assert not hasattr(apply_data["dp"], "mark_distance_absent_to_end")
    assert not hasattr(apply_data["dp"], "undo_distance_absent_to_end")
