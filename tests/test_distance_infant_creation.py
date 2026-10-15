"""Create a carrier and its infant before recording distance evidence."""

from datetime import datetime

import pytest
from review_app.backend.db.models import DistanceIndividual
from sqlalchemy import event


@pytest.mark.parametrize("verified", [False, True])
def test_create_carrier_and_infant_without_distance_evidence(distance_data, verified):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    tag_key = dp.create_custom_tag("Identified")
    dp.set_tag_individual_eligibility(tag_key, True)
    tag_id = next(tag["id"] for tag in dp.get_all_tags() if tag["key"] == tag_key)
    if verified:
        dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    carrier = dp.create_distance_individual(
        video_id,
        data["species_id"],
        3040,
        anchor=data["anchor"],
        labeled_by="alice",
        create_infant=True,
        age="Adult",
        sex="Female",
        tag_ids=[tag_id],
    )
    individuals = dp.list_distance_individuals(video_id)
    assert len(individuals) == 3
    infant = next(row for row in individuals if row["id"] == carrier["infant_id"])
    saved_carrier = next(row for row in individuals if row["id"] == carrier["id"])
    assert saved_carrier["age"] == "Adult" and saved_carrier["sex"] == "Female"
    assert saved_carrier["tag_ids"] == [tag_id]
    assert infant["tag_ids"] == []
    assert infant["initial_carrier_individual_id"] == carrier["id"]
    assert infant["id"] != carrier["id"]
    assert infant["individual_number"] == carrier["individual_number"] + 1
    assert infant["age"] == "Infant" and infant["sex"] == "Unknown"
    for field in (
        "video_id",
        "species_id",
        "first_seen_offset_ms",
        "anchor_x",
        "anchor_y",
        "anchor_width",
        "anchor_height",
        "labeled_by",
    ):
        assert infant[field] == carrier[field]
    state = dp.get_distance_review_state(video_id, "alice")
    for individual in (carrier, infant):
        samples = [row for row in state["samples"] if row["individual_id"] == individual["id"]]
        assert [row["offset_ms"] for row in samples] == ([1000, 3000] if verified else [])
        assert all(
            row["status"] == "skipped" and row["skip_reason"] == "individual absent"
            for row in samples
        )
    if verified:
        saved = dp.save_distance_sample(
            carrier["id"],
            5000,
            reference_frame_id=data["frame"]["id"],
            infant_id=infant["id"],
            labeled_by="alice",
        )
        assert saved["infant_id"] == infant["id"]
        assert len(dp.list_distance_individuals(video_id)) == 3
        samples = dp.get_distance_review_state(video_id, "alice")["samples"]
        carried = next(
            row
            for row in samples
            if row["individual_id"] == infant["id"] and row["offset_ms"] == 5000
        )
        assert carried["carried_by_individual_id"] == carrier["id"]
        assert carried["distance_m"] == saved["distance_m"]


def test_infant_creation_failure_rolls_back_carrier(distance_data):
    data, dp = distance_data, distance_data["dp"]
    before = dp.list_distance_individuals(data["videos"][0])

    def reject_infant(_mapper, _connection, individual):
        if individual.age == "Infant":
            raise ValueError("Infant insert failed")

    event.listen(DistanceIndividual, "before_insert", reject_infant)
    try:
        with pytest.raises(ValueError, match="Infant insert failed"):
            dp.create_distance_individual(
                data["videos"][0],
                data["species_id"],
                1000,
                anchor=data["anchor"],
                create_infant=True,
            )
    finally:
        event.remove(DistanceIndividual, "before_insert", reject_infant)
    assert dp.list_distance_individuals(data["videos"][0]) == before


def test_infant_cannot_be_created_as_a_carrier(distance_data):
    data, dp = distance_data, distance_data["dp"]
    with pytest.raises(ValueError, match="cannot also carry"):
        dp.create_distance_individual(
            data["videos"][0],
            data["species_id"],
            1000,
            anchor=data["anchor"],
            age="Infant",
            create_infant=True,
        )
    assert len(dp.list_distance_individuals(data["videos"][0])) == 1
