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
    # Queue eligibility requires Species annotations, even for reference clips.
    for video_id in ids.values():
        dp.update_manual_review(video_id, [{"species": "deer"}], is_blank=False)
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
    with dp.Session() as session:
        ids = [video.video_id for video in session.query(Video).filter_by(project_id=project.id)]
    for video_id in ids:
        dp.update_manual_review(video_id, [{"species": "deer"}], is_blank=False)
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


@pytest.mark.parametrize(
    "reference",
    [
        "188_Cam508_0795487_1135723_20260220_VR",
        "188-Cam508 _0795487_1135723_20260220 vr",
        "188_cam508__0795487_1135723_20260220_V_R",
        "VR_188_Cam508_0795487_1135723_20260220",
        "v-r 188-Cam508-0795487-1135723-20260220",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_marked_references_share_exact_normalized_deployment(tmp_path, reference, nested):
    site = tmp_path / "DS_E5" / "T188"
    name = "188_Cam508_0795487_1135723_20260220"
    ordinary = site / name
    reference_folder = site / reference
    ordinary.mkdir(parents=True)
    reference_folder.mkdir()
    reference_parent = reference_folder / reference / "copies" if nested else reference_folder
    reference_parent.mkdir(parents=True, exist_ok=True)
    reference_path = reference_parent / "clip.mp4"
    expected = f"DS_E5/T188/{name}"
    assert is_reference_video_path(reference_path, tmp_path)
    assert camera_id_for_path(reference_path, tmp_path) == expected
    assert camera_id_for_path(ordinary / "clip.mp4", tmp_path) == expected
    assert not is_reference_video_path(ordinary / "clip.mp4", tmp_path)
    # Processing folders within the paired ordinary deployment keep its identity.
    (ordinary / "Pantro" / "traite").mkdir(parents=True)
    assert camera_id_for_path(ordinary / "Pantro/traite/clip.mp4", tmp_path) == expected
    assert not is_reference_video_path(ordinary / "Pantro/traite/clip.mp4", tmp_path)


def test_nested_marked_reference_matches_enclosing_deployment(tmp_path):
    deployment = tmp_path / "T110/110_cam392_0180912_1112313_20260214"
    nested = deployment / "Pantro/110-cam392_0180912_1112313_20260214_VR"
    nested.mkdir(parents=True)
    # A sibling reference must not hide a reference subtree inside ordinary footage.
    deployment.with_name(deployment.name + "_VR").mkdir()
    path = nested / "copy/clip.mp4"
    assert is_reference_video_path(path, tmp_path)
    assert camera_id_for_path(path, tmp_path) == str(deployment.relative_to(tmp_path))


@pytest.mark.parametrize("marker", ["ref", "reference", "references"])
@pytest.mark.parametrize("prefix", [False, True])
@pytest.mark.parametrize("separator", ["_", "--", " _ "])
def test_textual_reference_aliases_match_normalized_deployment(
    tmp_path, marker, prefix, separator
):
    ordinary = tmp_path / "cam_a_20260220"
    marked_name = (
        f"{marker.upper()}{separator}CAM-A_20260220"
        if prefix
        else f"CAM-A_20260220{separator}{marker.upper()}"
    )
    reference = tmp_path / marked_name
    ordinary.mkdir()
    (reference / "copy").mkdir(parents=True)
    path = reference / "copy/clip.mp4"
    assert is_reference_video_path(path, tmp_path)
    assert camera_id_for_path(path, tmp_path) == ordinary.name
    assert not is_reference_video_path(ordinary / "clip.mp4", tmp_path)


@pytest.mark.parametrize("reference", ["VR", "V_R", "v-r"])
def test_standalone_marker_pairs_with_video_category(tmp_path, reference):
    deployment = tmp_path / "cam_a"
    for category in (reference, "Video"):
        (deployment / category).mkdir(parents=True)
    assert camera_id_for_path(deployment / reference / "clip.mp4", tmp_path) == "cam_a"
    assert is_reference_video_path(deployment / reference / "clip.mp4", tmp_path)
    assert camera_id_for_path(deployment / "Video/clip.mp4", tmp_path) == "cam_a"
    assert not is_reference_video_path(deployment / "Video/clip.mp4", tmp_path)


@pytest.mark.parametrize(
    "folder",
    [
        "cam_vr_copy",
        "cam_v_r_copy",
        "VRcam",
        "camVR",
        "survey",
        "cam_ref_copy",
        "cam_reference_copy",
        "cam_references_copy",
        "refcam",
        "camreference",
        "cam_referencesExtra",
    ],
)
def test_embedded_marker_is_not_a_reference(tmp_path, folder):
    (tmp_path / folder).mkdir()
    path = tmp_path / folder / "clip_VR.mp4"
    assert not is_reference_video_path(path, tmp_path)
    assert camera_id_for_path(path, tmp_path) == folder


@pytest.mark.parametrize(
    "ordinary,reference",
    [
        ("187_Cam328_0791930_1132398_20260702", "187_Cam328_0791930_1132398_20260207_VR"),
        ("38_cam565_806440_1113432_20260209", "VR-Cam_473"),
        ("44_cam559_802831_1104239_20260210", "VR-Cam_559"),
        ("cam_12_3", "cam_1_23_VR"),
    ],
)
def test_marked_references_do_not_guess_partial_matches(tmp_path, ordinary, reference):
    (tmp_path / ordinary).mkdir()
    (tmp_path / reference).mkdir()
    path = tmp_path / reference / "clip.mp4"
    assert is_reference_video_path(path, tmp_path)
    assert camera_id_for_path(path, tmp_path) == reference
    assert camera_id_for_path(tmp_path / ordinary / "clip.mp4", tmp_path) == ordinary


def test_marked_subtree_inside_legacy_video_category_is_still_reference(tmp_path):
    deployment = tmp_path / "cam_a"
    for category in ("videos", "references"):
        (deployment / category).mkdir(parents=True)
    reference = deployment / "videos/cam_a_VR"
    reference.mkdir()
    path = reference / "clip.mp4"
    assert is_reference_video_path(path, tmp_path)
    assert camera_id_for_path(path, tmp_path) == "cam_a"
    assert camera_id_for_path(deployment / "videos/animal.mp4", tmp_path) == "cam_a"


def test_normalized_deployment_collisions_are_not_matched(tmp_path):
    for folder in ("cam_1", "CAM-1", "cam 1_VR"):
        (tmp_path / folder).mkdir()
    path = tmp_path / "cam 1_VR" / "clip.mp4"
    assert is_reference_video_path(path, tmp_path)
    assert camera_id_for_path(path, tmp_path) == "cam 1_VR"


def test_marked_matching_stays_in_same_parent_and_import_root(tmp_path):
    (tmp_path / "T1/cam_a").mkdir(parents=True)
    reference = tmp_path / "T2/cam_a_VR"
    reference.mkdir(parents=True)
    assert camera_id_for_path(reference / "clip.mp4", tmp_path) == "T2/cam_a_VR"
    # Importing a marked folder itself must not inspect siblings outside the root.
    assert camera_id_for_path(reference / "clip.mp4", reference) == "default"
    assert not is_reference_video_path(reference / "clip.mp4", reference)


def test_missing_directories_keep_camera_inference(tmp_path):
    path = tmp_path / "missing/nested/clip.mp4"
    assert camera_id_for_path(path, tmp_path) == "missing/nested"
    assert not is_reference_video_path(path, tmp_path)


@pytest.mark.parametrize("marker", ["V_R", "ref", "reference", "references"])
def test_rescan_detects_marked_references_and_preserves_tags(tmp_db, mock_probe, marker):
    root = tmp_db["video_dir"]
    ordinary = root / "T48/48_Cam077_814566_1117151_20260221"
    ordinary.mkdir(parents=True)
    (ordinary / "animal.mp4").touch()
    dp = LocalDataProvider()
    project = dp.create_project("Marked references", str(root))
    dp.sync_videos(active_project_id=project.id)
    reference = root / f"T48/48-Cam077_814566_1117151_20260221 {marker}/copy"
    reference.mkdir(parents=True)
    (reference / "reference.mp4").touch()
    # This rescan must see new sibling folders, not a stale directory cache.
    dp.sync_videos(active_project_id=project.id)
    with dp.Session() as session:
        videos = session.query(Video).filter_by(project_id=project.id).all()
        ids = {Path(video.video_path).name: video.video_id for video in videos}
        assert {video.camera_id for video in videos} == {str(ordinary.relative_to(root))}
    ref_id = ids["reference.mp4"]
    assert dp.get_video_tags(ref_id) == ["reference_video"]
    assert dp.get_video_tags(ids["animal.mp4"]) == []
    for video_id in ids.values():
        dp.update_manual_review(video_id, [{"species": "deer"}], is_blank=False)
    assert [row["video_id"] for row in dp.get_distance_video_queue(project.id)] == [
        ids["animal.mp4"]
    ]
    assert [
        row["source_video_id"] for row in dp.get_distance_reference_videos(ids["animal.mp4"])
    ] == [ref_id]
    custom = dp.create_custom_tag("Keep me")
    dp.set_video_tags(ref_id, ["reference_video", custom])
    dp.sync_videos(active_project_id=project.id)
    assert set(dp.get_video_tags(ref_id)) == {"reference_video", custom}
    with dp.Session() as session:
        assert session.query(VideoTag).filter_by(video_id=ref_id).count() == 2


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
