import pytest


def test_project_preferences_are_independent_and_updatable(provider_with_project, tmp_path):
    dp, first, _ = provider_with_project
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    second = dp.create_project("Other", str(other_dir))
    dp.set_annotator_active_project("alice", first.id)
    dp.set_annotator_active_project("bob", second.id)
    assert dp.get_annotator_active_project("alice") == first.id
    assert dp.get_annotator_active_project("bob") == second.id
    assert dp.get_annotator_active_project("new-user") is None

    dp.set_annotator_active_project("alice", second.id)
    dp.set_annotator_active_project("bob", None)
    assert dp.get_annotator_active_project("alice") == second.id
    assert dp.get_annotator_active_project("bob") is None


def test_deleted_project_is_not_restored(provider_with_project):
    dp, project, _ = provider_with_project
    dp.set_annotator_active_project("alice", project.id)
    dp.delete_project(project.id)
    assert dp.get_annotator_active_project("alice") is None


def test_invalid_project_does_not_overwrite_preference(provider_with_project):
    dp, project, _ = provider_with_project
    dp.set_annotator_active_project("alice", project.id)
    with pytest.raises(ValueError, match="Project not found"):
        dp.set_annotator_active_project("alice", "missing")
    assert dp.get_annotator_active_project("alice") == project.id


def test_deleting_annotator_removes_only_their_project_preference(provider_with_project):
    dp, project, _ = provider_with_project
    dp.add_annotator("alice")
    dp.add_annotator("bob")
    dp.set_annotator_active_project("alice", project.id)
    dp.set_annotator_active_project("bob", project.id)
    dp.remove_annotator("alice")
    dp.add_annotator("alice")
    assert dp.get_annotator_active_project("alice") is None
    assert dp.get_annotator_active_project("bob") == project.id
