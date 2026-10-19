from datetime import datetime

import pytest
from review_app.backend.db.models import (
    DistanceIndividualTag,
    DistanceReviewProgress,
)


def test_annotation_counts_reflect_labeled_by(populated_provider):
    dp, _ = populated_provider

    # alice labeled v1, bob labeled v3 (see populated_provider fixture).
    counts = dp.get_annotator_annotation_counts()
    assert counts.get("alice", 0) >= 1
    assert counts.get("bob", 0) >= 1


def test_unused_annotator_is_absent_from_counts(populated_provider):
    dp, _ = populated_provider

    # A freshly added annotator has no annotations anywhere, so they must not
    # appear in the counts map (which is what gates deletion in the UI).
    dp.add_annotator("charlie")
    assert "charlie" in dp.get_all_annotators()
    assert "charlie" not in dp.get_annotator_annotation_counts()


def test_remove_annotator_drops_registry_and_assignments(provider_with_project):
    dp, project, _ = provider_with_project

    dp.add_annotator("dora")
    dp.apply_distribution(project.id, {"dora": ["cam_x"]})
    assert dp.get_camera_assignment_map(project.id)["cam_x"] == "dora"

    dp.remove_annotator("dora")

    assert "dora" not in dp.get_all_annotators()
    assert dp.get_camera_assignment_map(project.id)["cam_x"] is None


def test_removing_one_annotator_leaves_others_intact(populated_provider):
    dp, _ = populated_provider

    dp.add_annotator("charlie")
    dp.remove_annotator("charlie")

    assert "charlie" not in dp.get_all_annotators()
    # Existing annotators and their counts are untouched.
    counts = dp.get_annotator_annotation_counts()
    assert counts.get("alice", 0) >= 1
    assert counts.get("bob", 0) >= 1


@pytest.mark.parametrize("work", ["individual", "sample", "clock", "completion", "tag"])
def test_distance_only_work_protects_annotator_in_ui_and_backend(distance_data, work):
    data, dp = distance_data, distance_data["dp"]
    dp.add_annotator("bob")
    video_id, individual_id = data["videos"][0], data["individual"]["id"]
    if work == "individual":
        dp.set_distance_individual_attributes(
            individual_id, age="Adult", sex=None, tag_ids=[], labeled_by="bob"
        )
    elif work == "tag":
        key = dp.create_custom_tag("Injured")
        tag_id = next(tag["id"] for tag in dp.get_all_tags() if tag["key"] == key)
        with dp.Session.begin() as session:
            session.add(
                DistanceIndividualTag(individual_id=individual_id, tag_id=tag_id, tagged_by="bob")
            )
    else:
        dp.verify_distance_recording_time(
            video_id, datetime(2024, 1, 1), "bob" if work == "clock" else "alice"
        )
        if work == "sample":
            dp.save_distance_sample(
                individual_id, 0, reference_frame_id=data["frame"]["id"], labeled_by="bob"
            )
        elif work == "completion":
            dp.set_distance_individual_attributes(
                individual_id, age="Unknown", sex="Unknown", tag_ids=[]
            )
            for offset in dp.get_distance_review_state(video_id, "alice")["offsets"]:
                dp.save_distance_sample(
                    individual_id, offset, skip_reason="individual absent", labeled_by="alice"
                )
            dp.finish_distance_video(video_id, "bob")
    dp.set_distance_active_individual(video_id, "bob", individual_id)
    assert dp.get_annotator_annotation_counts().get("bob", 0) > 0
    with pytest.raises(ValueError, match="saved work"):
        dp.remove_annotator("bob")
    assert "bob" in dp.get_all_annotators()
    with dp.Session() as session:
        assert session.get(DistanceReviewProgress, (video_id, "bob")) is not None


def test_removing_unused_annotator_cleans_selection_only_progress(distance_data):
    data, dp = distance_data, distance_data["dp"]
    dp.add_annotator("charlie")
    video_id = data["videos"][0]
    dp.set_distance_active_individual(video_id, "charlie", data["individual"]["id"])
    assert "charlie" not in dp.get_annotator_annotation_counts()
    dp.remove_annotator("charlie")
    assert "charlie" not in dp.get_all_annotators()
    with dp.Session() as session:
        assert session.get(DistanceReviewProgress, (video_id, "charlie")) is None
    assert len(dp.list_distance_individuals(video_id)) == 1
