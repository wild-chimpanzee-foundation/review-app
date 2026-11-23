"""Folder-only project setup and deployment inventory validation."""

from datetime import date

import pytest
from review_app.backend.deployments import (
    DeploymentInputError,
    preview_deployment_setup,
    relative_deployment_folder,
    validate_deployment_fields,
)
from review_app.backend.provider.local_data_provider import LocalDataProvider


def add_footage(root, *paths):
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_deployment_fields_validate_metadata():
    values = validate_deployment_fields(
        {"deployment_id": " 48_Cam086 ", "installation_date": "2026-02-21", "name": " Forêt "}
    )
    assert values["deployment_id"] == "48_Cam086"
    assert values["name"] == "Forêt"
    assert values["installation_date"] == date(2026, 2, 21)
    assert values["retrieval_date"] is None


def test_folder_links_require_directories_within_the_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "clip.mp4").touch()
    assert relative_deployment_folder("future/reference", root) == "future/reference"
    for path in ("../elsewhere", "clip.mp4", "link"):
        with pytest.raises(DeploymentInputError, match="deployment_error_path"):
            relative_deployment_folder(path, root)


def test_folder_setup_marks_unmatched_references_and_skips_hidden_nonvideo_files(tmp_path):
    add_footage(
        tmp_path,
        "cam-a/animal.mp4",
        "cam-a/ref/calibration.mp4",
        "cam-b/clip.MP4",
        "shared/ref/calibration.mp4",
        ".hidden/clip.mp4",
        "cam-c/readme.txt",
    )
    preview = preview_deployment_setup(str(tmp_path))
    assert preview.deployments == ("cam-a", "cam-b", "shared/ref")
    folders = {row.folder: row for row in preview.folders}
    assert folders["cam-a/ref"].deployment == "cam-a"
    assert folders["cam-a/ref"].references == 1
    assert not folders["cam-a/ref"].unmatched_reference
    assert folders["shared/ref"].unmatched_reference


def test_setup_rejects_stale_footage_and_matches_initial_scan(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    add_footage(root, "videos/cam_a/animal.mp4", "ref/cam_a/calibration.mp4")
    preview = preview_deployment_setup(str(root))
    (root / "videos/cam_a/animal.mp4").rename(root / "videos/cam_a/replacement.mp4")
    dp = LocalDataProvider()
    with pytest.raises(DeploymentInputError, match="deployment_error_setup_stale"):
        dp.create_project("Project", str(root), expected_preview=preview)
    assert dp.list_projects() == []

    preview = preview_deployment_setup(str(root))
    project = dp.create_project("Project", str(root), expected_preview=preview)
    inventory = dp.get_project_deployments(project.id)
    assert [row["deployment_id"] for row in inventory] == ["cam_a"]
    for _ in range(2):
        dp.sync_videos(active_project_id=project.id)
    assert dp.get_project_deployments(project.id) == inventory
    videos = dp.get_deployment_management(project.id)["videos"]
    assert {video["deployment"] for video in videos} == {"cam_a"}
    assert {video["filename"] for video in videos if video["reference"]} == {"calibration.mp4"}


def test_setup_rejects_empty_directory(tmp_path):
    with pytest.raises(DeploymentInputError, match="deployment_error_empty"):
        preview_deployment_setup(str(tmp_path))


def test_category_first_groups_stay_with_their_recording_round(tmp_path):
    add_footage(
        tmp_path,
        "round_1/videos/cam_a/clip.mp4",
        "round_1/ref/cam_a/calibration.mp4",
        "round_2/videos/cam_a/clip.mp4",
        "round_2/ref/cam_a/calibration.mp4",
        "round_2/ref/cam_b/unknown.mp4",
        "round_2/cam_b/unrelated.mp4",
    )
    preview = preview_deployment_setup(str(tmp_path))
    folders = {row.folder: row for row in preview.folders}
    assert folders["round_1/ref/cam_a"].deployment == "round_1/cam_a"
    assert folders["round_2/ref/cam_a"].deployment == "round_2/cam_a"
    assert folders["round_2/ref/cam_b"].unmatched_reference
    assert folders["round_2/ref/cam_b"].deployment != folders["round_2/cam_b"].deployment
