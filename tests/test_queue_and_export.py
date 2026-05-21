"""
Independent tests for get_video_queue, get_queue_filter_options,
export_annotations_csv, and remove_project_dir.

Each test describes the *expected contract*, not the current implementation.
Tests marked with xfail describe behaviour that is not yet implemented.
"""

from review_app.backend.provider.local_data_provider import LocalDataProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def queue(dp, filters=None, project_id=None):
    return set(dp.get_video_queue(filters or {}, active_project_id=project_id))


# ---------------------------------------------------------------------------
# get_video_queue — filter behaviour
# ---------------------------------------------------------------------------


def test_queue_no_filter_returns_all_videos(populated_provider):
    dp, ids = populated_provider
    assert queue(dp) == {ids["v1"], ids["v2"], ids["v3"], ids["v4"]}


def test_queue_filter_camera_a(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_camera": "cam_a"})
    assert result == {ids["v1"], ids["v2"]}


def test_queue_filter_camera_b(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_camera": "cam_b"})
    assert result == {ids["v3"], ids["v4"]}


def test_queue_filter_manual_blank(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_manual_blank": "Blank"})
    assert result == {ids["v2"]}


def test_queue_filter_manual_non_blank(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_manual_blank": "Non-Blank"})
    assert result == {ids["v1"], ids["v3"]}


def test_queue_filter_manual_unlabeled(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_manual_blank": "Unlabeled"})
    assert result == {ids["v4"]}


def test_queue_filter_species_deer(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_species": ["deer"]})
    assert result == {ids["v1"]}


def test_queue_filter_species_fox(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_species": ["fox"]})
    assert result == {ids["v3"]}


def test_queue_filter_species_multi(populated_provider):
    """Selecting deer AND fox returns both."""
    dp, ids = populated_provider
    result = queue(dp, {"selected_species": ["deer", "fox"]})
    assert result == {ids["v1"], ids["v3"]}


def test_queue_filter_possible_species_deer(populated_provider):
    """Model predicted deer → only v1."""
    dp, ids = populated_provider
    result = queue(dp, {"selected_possible_species": ["deer"]})
    assert result == {ids["v1"]}


def test_queue_filter_possible_species_fox(populated_provider):
    """Model predicted fox → only v3."""
    dp, ids = populated_provider
    result = queue(dp, {"selected_possible_species": ["fox"]})
    assert result == {ids["v3"]}


def test_queue_filter_model_blank(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_model_blank": "Blank"})
    assert result == {ids["v2"]}


def test_queue_filter_model_non_blank(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_model_blank": "Non-Blank"})
    assert result == {ids["v1"]}


def test_queue_filter_annotated(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_annotation_status": "Annotated"})
    assert result == {ids["v1"], ids["v2"], ids["v3"]}


def test_queue_filter_not_annotated(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_annotation_status": "Not Annotated"})
    assert result == {ids["v4"]}


def test_queue_filter_review_later(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_is_review_later": True})
    assert result == {ids["v3"]}


def test_queue_filter_annotator_alice(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_annotator": ["alice"]})
    assert result == {ids["v1"]}


def test_queue_filter_annotator_bob(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_annotator": ["bob"]})
    assert result == {ids["v3"]}


def test_queue_filter_annotator_multi(populated_provider):
    """alice OR bob → both their videos."""
    dp, ids = populated_provider
    result = queue(dp, {"selected_annotator": ["alice", "bob"]})
    assert result == {ids["v1"], ids["v3"]}


def test_queue_filter_multiple_annotators(populated_provider):
    """Only videos with observations from more than one annotator."""
    dp, ids = populated_provider
    # Add a second annotator to v1 so it has both alice and bob.
    dp.update_manual_review(
        ids["v1"],
        [{"species": "deer", "behavior": "grazing", "start_sec": 6.0, "labeled_by": "bob"}],
        append=True,
    )
    result = queue(dp, {"selected_multiple_annotators": True})
    assert result == {ids["v1"]}


def test_queue_filter_search_by_filename(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"search_query": "v1"})
    assert result == {ids["v1"]}


def test_queue_filter_behavior_deer(populated_provider):
    dp, ids = populated_provider
    result = queue(dp, {"selected_behavior": ["grazing"]})
    assert result == {ids["v1"]}


def test_queue_combined_camera_and_blank(populated_provider):
    """Combining two filters: cam_a AND blank → only v2."""
    dp, ids = populated_provider
    result = queue(dp, {"selected_camera": "cam_a", "selected_manual_blank": "Blank"})
    assert result == {ids["v2"]}


def test_queue_combined_camera_and_not_annotated(populated_provider):
    """cam_b AND not annotated → only v4."""
    dp, ids = populated_provider
    result = queue(dp, {"selected_camera": "cam_b", "selected_annotation_status": "Not Annotated"})
    assert result == {ids["v4"]}


def test_queue_sort_direction_does_not_crash(populated_provider):
    dp, ids = populated_provider
    asc = dp.get_video_queue(
        {"selected_sort": "camera", "selected_sort_direction": "asc"}, active_project_id=None
    )
    desc = dp.get_video_queue(
        {"selected_sort": "camera", "selected_sort_direction": "desc"}, active_project_id=None
    )
    assert set(asc) == set(desc) == {ids["v1"], ids["v2"], ids["v3"], ids["v4"]}
    assert asc != desc  # order differs


# ---------------------------------------------------------------------------
# get_queue_filter_options
# ---------------------------------------------------------------------------


def test_filter_options_cameras(populated_provider):
    dp, _ = populated_provider
    opts = dp.get_queue_filter_options(active_project_id=None)
    assert set(opts["camera_values"]) == {"cam_a", "cam_b"}


def test_filter_options_species_from_manual_annotations(populated_provider):
    dp, _ = populated_provider
    opts = dp.get_queue_filter_options(active_project_id=None)
    assert "deer" in opts["species_values"]
    assert "fox" in opts["species_values"]


def test_filter_options_possible_species_from_model(populated_provider):
    dp, _ = populated_provider
    opts = dp.get_queue_filter_options(active_project_id=None)
    assert "deer" in opts["possible_species_values"]
    assert "fox" in opts["possible_species_values"]


def test_filter_options_empty_db(tmp_db):
    """No videos → all option lists are empty."""
    dp = LocalDataProvider()
    opts = dp.get_queue_filter_options(active_project_id=None)
    assert opts["camera_values"] == []
    assert opts["species_values"] == []
    assert opts["possible_species_values"] == []


# ---------------------------------------------------------------------------
# export_annotations_csv
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {
    "video_path",
    "camera_id",
    "duration_sec",
    "is_blank",
    "is_annotated",
    "species",
    "behavior",
    "start_sec",
    "end_sec",
    "annotator",
}


def test_export_contains_all_videos(populated_provider):
    dp, ids = populated_provider
    df = dp.export_annotations_csv(active_project_id=None)
    # export drops video_id; match on filename suffix
    assert df["video_path"].str.endswith("v1.mp4").any()
    assert df["video_path"].str.endswith("v2.mp4").any()
    assert df["video_path"].str.endswith("v3.mp4").any()
    assert df["video_path"].str.endswith("v4.mp4").any()


def test_export_has_required_columns(populated_provider):
    dp, _ = populated_provider
    df = dp.export_annotations_csv(active_project_id=None)
    assert REQUIRED_COLUMNS.issubset(df.columns)


def test_export_blank_video_has_no_species(populated_provider):
    dp, _ = populated_provider
    df = dp.export_annotations_csv(active_project_id=None)
    row = df[df["video_path"].str.endswith("v2.mp4")].iloc[0]
    assert row["is_blank"] == 1
    assert row["species"] is None or str(row["species"]) in ("nan", "None")


def test_export_annotated_video_has_species_and_behavior(populated_provider):
    dp, _ = populated_provider
    df = dp.export_annotations_csv(active_project_id=None)
    row = df[df["video_path"].str.endswith("v1.mp4")].iloc[0]
    assert "deer" in str(row["species"]).lower()
    assert "grazing" in str(row["behavior"]).lower()
    assert row["annotator"] == "alice"


def test_export_unannotated_video_appears_once(populated_provider):
    dp, _ = populated_provider
    df = dp.export_annotations_csv(active_project_id=None)
    v4_rows = df[df["video_path"].str.endswith("v4.mp4")]
    assert len(v4_rows) == 1
    assert v4_rows.iloc[0]["is_annotated"] == 0


def test_export_no_model_columns(populated_provider):
    """Manual export must not contain any AI model columns."""
    dp, _ = populated_provider
    df = dp.export_annotations_csv(active_project_id=None)
    model_cols = [c for c in df.columns if "model_a" in c]
    assert len(model_cols) == 0


def test_export_model_annotations_csv(populated_provider):
    """export_model_annotations_csv should return model_a rows with expected columns."""
    dp, _ = populated_provider
    df = dp.export_model_annotations_csv(active_project_id=None)
    assert {"video_path", "model_name", "annotation_type", "value_text", "probability"}.issubset(
        df.columns
    )
    assert df["model_name"].str.contains("model_a").any()


# ---------------------------------------------------------------------------
# remove_project_dir
# ---------------------------------------------------------------------------


def test_remove_project_dir_removes_dir_record(provider_with_project):
    dp, project, project_dir = provider_with_project
    dp.remove_project_dir(project_dir.id)
    assert dp.get_project_dirs(project.id) == []


def test_remove_project_dir_cascades_videos_out_of_queue(provider_with_project):
    """
    Removing a project directory should remove its videos from the project queue.
    A researcher who removes a directory no longer wants to see its videos.
    """
    dp, project, project_dir = provider_with_project
    assert len(dp.get_video_queue({}, active_project_id=project.id)) == 2

    dp.remove_project_dir(project_dir.id)

    remaining = dp.get_video_queue({}, active_project_id=project.id)
    assert remaining == [], (
        "Videos from a removed project directory should no longer appear in the queue. "
        "Currently they remain because remove_project_dir does not cascade to videos."
    )


def test_remove_nonexistent_dir_does_not_raise(tmp_db):
    dp = LocalDataProvider()
    dp.remove_project_dir("nonexistent-id-xyz")  # should not raise
