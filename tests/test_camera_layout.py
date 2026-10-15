import pytest
from review_app.backend.camera_layout import camera_id_for_path, deployment_key
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.mark.parametrize("nested", [False, True])
def test_scan_groups_paired_clip_folders_by_deployment(tmp_db, nested):
    root = tmp_db["video_dir"]
    deployment = root / "cam_a" if nested else root
    for role in ("references", "videos"):
        folder = deployment / role
        folder.mkdir(parents=True)
        (folder / "clip.MOV").touch()
    other = root / "cam_b"
    other.mkdir()
    (other / "animal.mp4").touch()
    dp = LocalDataProvider()
    scanned = dp._scan_videos(root).set_index("video_path")["camera_id"].to_dict()
    expected = "cam_a" if nested else root.name
    assert scanned[str(deployment / "references" / "clip.MOV")] == expected
    assert scanned[str(deployment / "videos" / "clip.MOV")] == expected
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


@pytest.mark.parametrize("role", ["videos", "references"])
def test_import_root_inside_a_paired_layout_keeps_inference_inside_root(tmp_path, role):
    (tmp_path / "videos").mkdir()
    (tmp_path / "references").mkdir()
    root = tmp_path / role
    assert camera_id_for_path(root / "clip.MOV", root) == "default"


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
