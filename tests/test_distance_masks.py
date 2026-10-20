"""Animal masks are original-frame overlays, not review evidence."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from review_app.app.pages.distance_review.player import (
    animal_mask_overlays,
    configure_anchor,
    configure_masks,
)


def test_masks_decode_both_rle_forms_and_survive_confirmation():
    samples = [
        {
            "individual_id": "one",
            "status": "pending",
            "ai_original": {
                "time_s": 1.250000001,
                "mask": {"size": [2, 3], "counts": "024"},
            },
        },
        {
            "individual_id": "two",
            "status": "annotated",
            "ai_original": {
                "time_s": 2.125,
                "mask": {"size": [2, 3], "counts": [1, 3, 2]},
            },
        },
        {
            "individual_id": "one",
            "status": "pending",
            "ai_original": {
                "time_s": 3.25,
                "skip_reason": "individual absent",
                "inferred": True,
            },
        },
        {"individual_id": "manual", "status": "annotated", "ai_original": None},
    ]
    before = deepcopy(samples)
    assert animal_mask_overlays(samples) == [
        {"individual_id": "one", "time_s": 1.250000001, "size": [2, 3], "counts": [0, 2, 4]},
        {"individual_id": "two", "time_s": 2.125, "size": [2, 3], "counts": [1, 3, 2]},
    ]
    assert samples == before


def test_selection_and_visibility_updates_preserve_overlay_state(monkeypatch):
    evaluate = Mock()
    monkeypatch.setattr("review_app.app.pages.distance_review.player.ui.run_javascript", evaluate)
    player = SimpleNamespace(id=42)
    configure_masks(player, [], enabled=False)
    configure_anchor(player, {"id": "one"})
    first, second = [call.args[0] for call in evaluate.call_args_list]
    assert '"show_masks": false' in first
    assert "...states.get(42)" in first and "...states.get(42)" in second
