from review_app.app.pages.distance_review.session import SampleSelection


def test_selection_distinguishes_visibility_from_frame_ids():
    visibility_ids = {"midpoint not visible", "individual absent"}

    assert SampleSelection.from_legacy_id("frame-uuid", visibility_ids) == SampleSelection(
        "frame", "frame-uuid"
    )
    assert SampleSelection.from_legacy_id("individual absent", visibility_ids) == SampleSelection(
        "visibility", "individual absent"
    )
    assert SampleSelection.from_legacy_id(None, visibility_ids) is None
