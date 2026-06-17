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
