"""Visible Distance samples always have behaviors; visibility skips have none."""

from datetime import datetime

import pytest
from review_app.backend.db.models import Behavior


@pytest.fixture
def behavior_data(distance_data):
    data = distance_data
    dp = data["dp"]
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    with dp.Session() as session:
        data["unidentified_id"] = session.query(Behavior.id).filter_by(key="unidentified").scalar()
        data["running_id"] = session.query(Behavior.id).filter_by(key="running").scalar()
    return data


def sample_behaviors(data, individual_id=None):
    return {
        row["offset_ms"]: row["behavior_ids"]
        for row in data["dp"].get_distance_review_state(data["videos"][0], "alice")["samples"]
        if row["individual_id"] == (individual_id or data["individual"]["id"])
    }


@pytest.mark.parametrize("behaviors", [None, [], "multiple"])
def test_visible_samples_default_to_unidentified_and_keep_multiple(behavior_data, behaviors):
    data, dp = behavior_data, behavior_data["dp"]
    chosen = [data["behavior_id"], data["running_id"]] if behaviors == "multiple" else behaviors
    dp.save_distance_sample(
        data["individual"]["id"],
        1000,
        reference_frame_id=data["frame"]["id"],
        behavior_ids=chosen,
        labeled_by="alice",
    )
    assert set(sample_behaviors(data)[1000]) == set(chosen or [data["unidentified_id"]])
    assert dp.get_distance_behavior_options()[data["unidentified_id"]] == "Unidentified"
    assert dp.get_distance_behavior_options("fr")[data["unidentified_id"]] == "Non identifié"


@pytest.mark.parametrize("reason", ["individual absent", "midpoint not visible"])
def test_visibility_skips_clear_behaviors_for_carrier_and_infant(behavior_data, reason):
    data, dp = behavior_data, behavior_data["dp"]
    parent = data["individual"]["id"]
    saved = dp.save_distance_sample(
        parent,
        1000,
        reference_frame_id=data["frame"]["id"],
        create_infant=True,
        labeled_by="alice",
    )
    infant = saved["infant_id"]
    assert (
        sample_behaviors(data)[1000]
        == sample_behaviors(data, infant)[1000]
        == [data["unidentified_id"]]
    )
    dp.save_distance_sample(
        parent,
        1000,
        skip_reason=reason,
        behavior_ids=[data["behavior_id"]],
        infant_id=infant,
        labeled_by="alice",
    )
    assert sample_behaviors(data)[1000] == sample_behaviors(data, infant)[1000] == []
