"""Bundled behaviors available to Distance annotation."""

from datetime import datetime
from pathlib import Path


def test_bundled_walking_behavior_is_available_and_saved(distance_data, monkeypatch):
    data, dp = distance_data, distance_data["dp"]
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    behavior_file = Path(__file__).parents[1] / "review_app" / "data" / "behaviors.csv"
    monkeypatch.setattr(
        "review_app.app.config.get_bundled_behaviors_csv", lambda: str(behavior_file)
    )
    dp._load_species_behaviors()
    walking_id = next(
        key for key, label in dp.get_distance_behavior_options().items() if label == "Walking"
    )
    assert dp.get_distance_behavior_options("fr")[walking_id] == "Marcher"

    # Reloading the vocabulary preserves the ID used by saved annotations.
    dp._load_species_behaviors()
    assert dp.get_distance_behavior_options()[walking_id] == "Walking"
    dp.save_distance_sample(
        data["individual"]["id"],
        1000,
        reference_frame_id=data["frame"]["id"],
        behavior_ids=[walking_id],
        labeled_by="alice",
    )
    samples = dp.get_distance_review_state(data["videos"][0], "alice")["samples"]
    sample = next(
        row
        for row in samples
        if row["individual_id"] == data["individual"]["id"] and row["offset_ms"] == 1000
    )
    assert sample["behavior_ids"] == [walking_id]
