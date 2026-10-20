from pathlib import Path

import pytest
from review_app.backend.camera_layout import (
    camera_id_for_path,
    deployment_key,
    is_reference_video_path,
)
from review_app.backend.db.models import Video, VideoTag
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.mark.parametrize("category_first", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_scan_groups_paired_clip_folders_by_deployment(tmp_db, nested, category_first):
    root = tmp_db["video_dir"]
    deployment = root / "cam_a" if nested else root
    for role in ("references", "videos"):
        folder = deployment / role / "cam_b" if category_first else deployment / role
        folder.mkdir(parents=True)
        (folder / "clip.MOV").touch()
    other = root / "cam_b"
    other.mkdir()
    (other / "animal.mp4").touch()
    dp = LocalDataProvider()
    scanned = dp._scan_videos(root).set_index("video_path")["camera_id"].to_dict()
    expected = (
        ("cam_a/cam_b" if nested else "cam_b")
        if category_first
        else ("cam_a" if nested else root.name)
    )
    for role in ("references", "videos"):
        folder = deployment / role / "cam_b" if category_first else deployment / role
        assert scanned[str(folder / "clip.MOV")] == expected
    assert scanned[str(other / "animal.mp4")] == "cam_b"


@pytest.mark.parametrize("folder", ["references", "videos", "cam_a"])
def test_unpaired_camera_folders_keep_existing_inference(tmp_path, folder):
    (tmp_path / folder).mkdir()
    assert camera_id_for_path(tmp_path / folder / "clip.mp4", tmp_path) == folder
    assert camera_id_for_path(tmp_path / "clip.mp4", tmp_path) == "default"


def test_multiple_registered_deployment_roots_have_distinct_camera_ids(tmp_path):
    ids = []
    for name in ("deployment_a", "deployment_b"):
        root = tmp_path / name
        (root / "references").mkdir(parents=True)
        (root / "videos").mkdir()
        ids.append(camera_id_for_path(root / "videos" / "animal.mp4", root))
    assert ids == ["deployment_a", "deployment_b"]


@pytest.mark.parametrize("camera", [None, "cam_a"])
@pytest.mark.parametrize("role", ["videos", "references"])
def test_import_root_inside_a_paired_layout_keeps_inference_inside_root(tmp_path, role, camera):
    (tmp_path / "videos").mkdir()
    (tmp_path / "references").mkdir()
    root = tmp_path / role
    path = root / camera / "clip.MOV" if camera else root / "clip.MOV"
    assert camera_id_for_path(path, root) == (camera or "default")
    assert not is_reference_video_path(path, root)


@pytest.mark.parametrize("category_first", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_sync_automatically_tags_paired_references(tmp_db, mock_probe, nested, category_first):
    root = tmp_db["video_dir"]
    deployment = root / "cam_a" if nested else root
    for role in ("references", "videos"):
        folder = deployment / role / "cam_a" if category_first else deployment / role
        folder.mkdir(parents=True)
        (folder / "clip.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project("Paired clips", str(root))
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        videos = session.query(Video).filter_by(project_id=project.id).all()
        ids = {
            (
                Path(video.video_path).parent.parent
                if category_first
                else Path(video.video_path).parent
            ).name: video.video_id
            for video in videos
        }
    assert dp.get_video_tags(ids["references"]) == ["reference_video"]
    assert dp.get_video_tags(ids["videos"]) == []
    assert [row["video_id"] for row in dp.get_distance_video_queue(project.id)] == [ids["videos"]]
    assert [row["source_video_id"] for row in dp.get_distance_reference_videos(ids["videos"])] == [
        ids["references"]
    ]

    custom_tag = dp.create_custom_tag("Keep me")
    dp.set_video_tags(ids["references"], ["reference_video", custom_tag])
    dp.toggle_video_tag(ids["references"], "reference_video")
    dp.toggle_video_tag(ids["references"], "reference_video", tagged_by="alice")
    with dp.Session() as session:
        original = (
            session.query(VideoTag)
            .filter_by(video_id=ids["references"], tagged_by="alice")
            .one()
            .tagged_at
        )
    dp.sync_videos(active_project_id=project.id)
    assert set(dp.get_video_tags(ids["references"])) == {"reference_video", custom_tag}
    with dp.Session() as session:
        saved = (
            session.query(VideoTag).filter_by(video_id=ids["references"], tagged_by="alice").one()
        )
        assert saved.tagged_at == original
        assert session.query(VideoTag).filter_by(video_id=ids["references"]).count() == 2


def test_category_first_references_match_only_their_camera(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    for role in ("references", "videos"):
        for camera in ("cam_a", "cam_b"):
            folder = root / role / camera
            folder.mkdir(parents=True)
            (folder / "clip.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project("Category-first clips", str(root))
    dp.sync_videos(active_project_id=project.id)
    queue = dp.get_distance_video_queue(project.id)
    assert {video["camera_id"] for video in queue} == {"cam_a", "cam_b"}
    for video in queue:
        references = dp.get_distance_reference_videos(video["video_id"])
        assert len(references) == 1
        assert references[0]["video_path"] == str(
            root / "references" / video["camera_id"] / "clip.mp4"
        )


def test_rescan_tags_existing_references_only_in_the_destination_project(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    (root / "references").mkdir()
    (root / "references/clip.mp4").touch()
    dp = LocalDataProvider()
    projects = [dp.create_project(name, str(root)) for name in ("First", "Second")]
    ids = []
    for project in projects:
        dp.sync_videos(active_project_id=project.id)
        with dp.Session() as session:
            ids.append(session.query(Video).filter_by(project_id=project.id).one().video_id)
    assert all(dp.get_video_tags(video_id) == [] for video_id in ids)
    (root / "videos").mkdir()
    dp.sync_videos(active_project_id=projects[0].id)
    assert dp.get_video_tags(ids[0]) == ["reference_video"]
    assert dp.get_video_tags(ids[1]) == []


def test_sync_does_not_infer_reference_role_outside_the_import_root(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    (root / "videos").mkdir()
    (root / "references").mkdir()
    (root / "references/clip.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project("Reference-named camera", str(root / "references"))
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        video_id = session.query(Video).filter_by(project_id=project.id).one().video_id
    assert dp.get_video_tags(video_id) == []


def test_deployment_key_prefers_the_most_specific_root_and_survives_relocation():
    roots = [("outer-id", "/data"), ("inner-id", "/data/nested")]
    original = deployment_key("project", "cam_1", "/data/nested/cam_1/animal.mp4", roots)
    assert original == ("project", "inner-id", "cam_1")
    assert (
        deployment_key("project", "cam_1", "/moved/cam_1/animal.mp4", [("inner-id", "/moved")])
        == original
    )
    assert deployment_key("project", "cam_1", "/data/cam_1/animal.mp4", roots) != original


def test_explicit_external_reference_is_unambiguous_only_for_a_single_root():
    assert deployment_key(
        "project", "custom-camera", "references/manual.mp4", [("root", "/data")]
    ) == ("project", "root", "custom-camera")
    assert (
        deployment_key("project", "cam_1", "references/manual.mp4", [("a", "/a"), ("b", "/b")])
        is None
    )
