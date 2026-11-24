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
        [{"species": "deer", "tags": ["grazing"], "start_sec": 6.0, "labeled_by": "bob"}],
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


def test_queue_model_filters_scoped_to_active_project(tmp_db, mock_probe):
    """Model-annotation CTEs aggregate predictions for the active project's videos."""
    import uuid
    from datetime import datetime, timezone

    from review_app.backend.db.models import ModelAnnotation

    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    (video_dir / "cam_a" / "p1.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project("P", str(video_dir))
    dp.sync_videos(progress_callback=None, video_dir=video_dir, active_project_id=project.id)
    vid = dp.get_video_queue({}, active_project_id=project.id)[0]

    with dp.Session() as s:
        s.add(
            ModelAnnotation(
                id=str(uuid.uuid4()),
                video_id=vid,
                annotation_type="blank_non_blank",
                model_name="m",
                value_text="blank",
                probability=0.99,
                updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        )
        s.commit()

    assert queue(dp, {"selected_model_blank": "Blank"}, project_id=project.id) == {vid}
    assert queue(dp, {"selected_model_blank": "Non-Blank"}, project_id=project.id) == set()
    assert queue(dp, {"selected_needs_review": "No Review"}, project_id=project.id) == {vid}
    assert (
        queue(
            dp,
            {"selected_sort": "species_prob", "selected_possible_species": ["deer"]},
            project_id=project.id,
        )
        == set()
    )


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


def test_queue_sorts_by_effective_blank_probability(populated_provider):
    import uuid
    from datetime import datetime, timezone

    from review_app.backend.db.models import ModelAnnotation

    dp, ids = populated_provider

    # Legacy imports in real databases store a direct blank probability with
    # no value_text label.
    with dp.Session() as s:
        s.add(
            ModelAnnotation(
                id=str(uuid.uuid4()),
                video_id=ids["v3"],
                annotation_type="blank_non_blank",
                model_name="legacy_blank_model",
                value_text=None,
                probability=0.70,
                updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        )
        s.commit()

    desc = dp.get_video_queue(
        {"selected_sort": "blank_prob", "selected_sort_direction": "desc"},
        active_project_id=None,
    )
    asc = dp.get_video_queue(
        {"selected_sort": "blank_prob", "selected_sort_direction": "asc"},
        active_project_id=None,
    )

    # v2 predicts blank at 0.95; v3 has a legacy direct blank probability of
    # 0.70; v1 predicts non-blank at 0.80, making P(blank) 0.20. Videos with
    # no blank prediction sort last in either direction.
    assert desc[:3] == [ids["v2"], ids["v3"], ids["v1"]]
    assert asc[:3] == [ids["v1"], ids["v3"], ids["v2"]]


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
    "attributes",
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
    assert "grazing" in str(row["attributes"]).lower()
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
# Project Bundle Export
# ---------------------------------------------------------------------------


def test_export_project_bundle_model_annotations_with_camera_filter(populated_provider):
    import io
    import zipfile

    import pandas as pd

    dp, ids = populated_provider
    # cam_a has v1 and v2. v1 has model annotations, v2 has model annotations.
    # cam_b has v3 and v4. v3 has model annotations.

    # Export bundle for cam_a only
    bundle_bytes = dp.export_project_bundle(
        project_id=None, include=["model_annotations"], camera_ids=["cam_a"]
    )

    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
        assert "model_annotations.csv" in zf.namelist()
        content = zf.read("model_annotations.csv").decode("utf-8")
        df = pd.read_csv(io.StringIO(content))

        # Should contain cam_a videos (v1, v2) but not cam_b (v3)
        paths = df["video_path"].unique()
        assert any("v1.mp4" in p for p in paths)
        assert any("v2.mp4" in p for p in paths)
        assert not any("v3.mp4" in p for p in paths)


def test_export_project_bundle_model_annotations_no_filter(populated_provider):
    import io
    import zipfile

    import pandas as pd

    dp, ids = populated_provider

    bundle_bytes = dp.export_project_bundle(
        project_id=None, include=["model_annotations"], camera_ids=None
    )

    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
        assert "model_annotations.csv" in zf.namelist()
        content = zf.read("model_annotations.csv").decode("utf-8")
        df = pd.read_csv(io.StringIO(content))

        # Should contain all videos with model annotations (v1, v2, v3)
        paths = df["video_path"].unique()
        assert any("v1.mp4" in p for p in paths)
        assert any("v2.mp4" in p for p in paths)
        assert any("v3.mp4" in p for p in paths)


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


def test_species_and_prediction_writes_and_reads_follow_video_project(provider_with_project):
    import pandas as pd
    import pytest
    from review_app.backend.errors import VideoError

    dp, project, root = provider_with_project
    other = dp.create_project("Another project with the same paths", root.path)
    dp.sync_videos(active_project_id=other.id)
    video = dp.get_video_queue({}, project.id)[0]
    other_video = next(
        vid
        for vid in dp.get_video_queue({}, other.id)
        if dp.get_video_detail(vid)["video_path"] == dp.get_video_detail(video)["video_path"]
    )
    prediction = pd.DataFrame(
        [
            {
                "video_id": video,
                "annotation_type": "species",
                "model_name": "project-model",
                "value_text": "deer",
                "probability": 0.99,
            },
            {
                "video_id": other_video,
                "annotation_type": "species",
                "model_name": "other-model",
                "value_text": "fox",
                "probability": 0.99,
            },
        ]
    )
    with pytest.raises(VideoError, match="does not belong"):
        dp.import_model_csv(prediction, project.id)
    assert dp.export_model_annotations_csv(None).empty
    dp.import_model_csv(prediction.iloc[:1], project.id)
    dp.import_model_csv(prediction.iloc[1:], other.id)
    for project_id, video_id, species, model in (
        (project.id, video, "deer", "project-model"),
        (other.id, other_video, "fox", "other-model"),
    ):
        dp.update_manual_review(
            video_id, [{"species": species, "tags": ["grazing"]}], active_project_id=project_id
        )
        assert queue(dp, {"selected_needs_review": "No Review"}, project_id) == {video_id}
        assert dp.get_queue_filter_options(project_id)["possible_species_values"] == [species]
        assert dp.export_annotations_csv(project_id)["species"].dropna().tolist() == [species]
        assert dp.export_model_annotations_csv(project_id)["model_name"].tolist() == [model]
        stats = dp.get_overview_stats(project_id)
        assert [row["species"] for row in stats["species_counts"]] == [species]
        assert [row["model_name"] for row in stats["model_coverage"]] == [model]
    assert dp.delete_model_annotations(project.id) == 1
    assert dp.export_model_annotations_csv(project.id).empty
    assert dp.delete_project(project.id)["deleted"]
    assert dp.export_model_annotations_csv(other.id)["model_name"].tolist() == ["other-model"]
    assert dp.export_annotations_csv(other.id)["species"].dropna().tolist() == ["fox"]
