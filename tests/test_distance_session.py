from review_app.app.pages.distance_review.session import DraftState, SampleSelection


def test_selection_distinguishes_visibility_from_frame_ids():
    visibility_ids = {"midpoint not visible", "individual absent"}

    assert SampleSelection.from_legacy_id("frame-uuid", visibility_ids) == SampleSelection(
        "frame", "frame-uuid"
    )
    assert SampleSelection.from_legacy_id("individual absent", visibility_ids) == SampleSelection(
        "visibility", "individual absent"
    )
    assert SampleSelection.from_legacy_id(None, visibility_ids) is None


def test_draft_state_keeps_unsaved_draft_separate_from_carried_default():
    state = DraftState()
    state.drafts[("individual", 1000)] = {"behavior_ids": ["saved-choice"]}
    draft = {"behavior_ids": ["draft-choice"]}
    state.remember("individual", draft)
    draft["behavior_ids"].append("later-edit")

    assert state.drafts[("individual", 1000)] == {"behavior_ids": ["saved-choice"]}
    assert state.defaults["individual"] == {"behavior_ids": ["draft-choice"]}
