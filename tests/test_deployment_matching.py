"""Folder deployment matching through real scans."""

from review_app.backend.provider.local_data_provider import LocalDataProvider


def test_rescan_replaces_automatic_match_when_reference_pair_arrives(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    ordinary = root / "cam-a/Pantro/animal.mp4"
    ordinary.parent.mkdir(parents=True)
    ordinary.touch()
    dp = LocalDataProvider()
    project = dp.create_project("Folder inference", str(root))
    dp.sync_videos(active_project_id=project.id)
    assert dp.get_deployment_management(project.id)["videos"][0]["deployment"] == "cam-a/Pantro"

    reference = root / "cam-a_VR/reference.mp4"
    reference.parent.mkdir()
    reference.touch()
    dp.sync_videos(active_project_id=project.id)
    management = dp.get_deployment_management(project.id)
    videos = {row["filename"]: row for row in management["videos"]}
    assert videos["animal.mp4"]["deployment"] == videos["reference.mp4"]["deployment"] == "cam-a"
    assert (
        dp.get_distance_reference_videos(videos["animal.mp4"]["video_id"])[0]["source_video_id"]
        == videos["reference.mp4"]["video_id"]
    )
    assert management["links"] == []


def test_equal_deployment_names_across_roots_require_explicit_links(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    other_root = tmp_db["root"] / "other"
    for directory in (root, other_root):
        (directory / "A").mkdir(parents=True)
        (directory / "A/clip.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project("Roots", str(root))
    dp.sync_videos(active_project_id=project.id)
    dp.add_project_dir(project.id, str(other_root))
    dp.sync_videos(active_project_id=project.id)
    first, second = dp.get_deployment_management(project.id)["videos"]
    assert first["deployment_id"] != second["deployment_id"]

    target = first["deployment_id"]
    for directory in dp.get_project_dirs(project.id):
        preview = dp.preview_deployment_assignment(
            project.id,
            target_id=target,
            action="folder",
            folder={"root_id": directory.id, "path": "A"},
        )
        dp.apply_deployment_assignment(preview)
    dp.sync_videos(active_project_id=project.id)
    assert {
        row["deployment_id"] for row in dp.get_deployment_management(project.id)["videos"]
    } == {target}
