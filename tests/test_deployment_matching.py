"""CSV deployment matching through new-project creation and real scans (no UI)."""

import pytest
from review_app.backend.db.models import Video
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("DS_M1Phase5_E1/T48/48_Cam086_814419_1116757_20260221", "48_cam086"),
        ("DS_M1Phase5_E1/T48/48-CAM086-814419-1116757-20260221", "48_cam086"),
        ("DS_M1Phase5_E1/T48/48 Cam086 814419 1116757 20260221", "48_cam086"),
        ("DS_M1Phase5_E1/T48/48_Cam086_814419_1116757_20260221/videos", "48_cam086"),
        ("DS_M1Phase5_E1/T48/48_Cam086", "48_cam086"),
        ("DS_M1Phase5_E1/T48/48_Cam0860_814419_1116757_20260221", None),
        ("DS_M1Phase5_E1/T48/148_Cam086_814419_1116757_20260221", None),
        ("DS_M1Phase5_E1/T48/prefix48_Cam086_814419_1116757_20260221", None),
    ],
)
def test_new_csv_project_matches_bounded_deployment_id_prefix(
    tmp_db, mock_probe, directory, expected
):
    root = tmp_db["video_dir"]
    folder = root / directory
    folder.mkdir(parents=True)
    path = folder / "clip.mp4"
    path.touch()
    dp = LocalDataProvider()
    project = dp.create_project_with_deployments(
        "Phase 5", str(root), csv_data=b"deployment_id,folder_path\n48_cam086,\n"
    )
    for _ in range(2):
        dp.sync_videos(active_project_id=project.id)
        with dp.Session() as session:
            video = (
                session.query(Video).filter_by(project_id=project.id, video_path=str(path)).one()
            )
            assert video.camera_id == expected
            assert (video.deployment_id is not None) == (expected is not None)


@pytest.mark.parametrize("identifiers", [("48", "48_cam086"), ("48_cam086", "48-CAM086")])
def test_competing_prefix_candidates_stay_unresolved(tmp_db, mock_probe, identifiers):
    root = tmp_db["video_dir"]
    folder = root / "T48/48_Cam086_814419_1116757_20260221"
    folder.mkdir(parents=True)
    (folder / "clip.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project_with_deployments(
        "Ambiguous", str(root), csv_data=("deployment_id\n" + "\n".join(identifiers)).encode()
    )
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        assert session.query(Video).filter_by(project_id=project.id).one().deployment_id is None


def test_explicit_folder_path_wins_over_id_prefix(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    directory = "T48/48_Cam086_814419_1116757_20260221"
    folder = root / directory
    folder.mkdir(parents=True)
    (folder / "clip.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project_with_deployments(
        "Explicit",
        str(root),
        csv_data=("deployment_id,folder_path\n48_cam086,\n48_cam077," + directory + "\n").encode(),
    )
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        assert session.query(Video).filter_by(project_id=project.id).one().camera_id == "48_cam077"


def test_camera_number_substrings_and_filenames_are_not_aliases(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    for directory in ("cam453_814419_1116757_20260221", "unrelated"):
        folder = root / directory
        folder.mkdir()
        (folder / "cam45_20260221.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project_with_deployments(
        "No substrings", str(root), csv_data=b"deployment_id\ncam45\n"
    )
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        assert all(
            v.deployment_id is None for v in session.query(Video).filter_by(project_id=project.id)
        )


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


def test_csv_competition_across_roots_requires_explicit_links(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    other_root = tmp_db["root"] / "other"
    for directory in (root, other_root):
        (directory / "A").mkdir(parents=True)
        (directory / "A/clip.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project_with_deployments(
        "Roots", str(root), csv_data=b"deployment_id\nA\n"
    )
    dp.sync_videos(active_project_id=project.id)
    dp.add_project_dir(project.id, str(other_root))
    dp.sync_videos(active_project_id=project.id)
    assert {row["deployment"] for row in dp.get_deployment_management(project.id)["videos"]} == {
        None
    }

    target = dp.get_project_deployments(project.id)[0]["id"]
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
