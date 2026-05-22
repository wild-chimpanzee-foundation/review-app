"""Unit tests for review_app.backend.path_matching — pure functions, no DB."""

from pathlib import Path

from review_app.backend.path_matching import (
    VideoPathLookup,
    _cameras_share_token,
    build_video_path_lookup,
    resolve_video_path,
)

# ---------------------------------------------------------------------------
# _cameras_share_token
# ---------------------------------------------------------------------------


def test_cameras_share_token_exact():
    assert _cameras_share_token("cam002", "cam002")


def test_cameras_share_token_in_compound_name():
    assert _cameras_share_token("cam002", "c8_cam002_f2")


def test_cameras_share_token_symmetric():
    assert _cameras_share_token("c8_cam002_f2", "cam002")


def test_cameras_share_token_shared_compound_token():
    # Both compound names share "cam003" as a token
    assert _cameras_share_token("c8_cam003_f2", "p4_cam003_l1")


def test_cameras_share_token_no_match():
    assert not _cameras_share_token("cam002", "cam003")


def test_cameras_share_token_no_match_different_compounds():
    assert not _cameras_share_token("c8_cam002_f2", "p4_cam003_l1")


def test_cameras_share_token_empty_csv():
    assert not _cameras_share_token("", "cam002")


def test_cameras_share_token_empty_db():
    assert not _cameras_share_token("cam002", "")


def test_cameras_share_token_both_empty():
    assert not _cameras_share_token("", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lookup(
    rows: list[tuple[str, str, str | None]],
    scan_dirs: list[Path] | None = None,
) -> VideoPathLookup:
    return build_video_path_lookup(rows, scan_dirs or [])


# ---------------------------------------------------------------------------
# build_video_path_lookup
# ---------------------------------------------------------------------------


def test_build_by_suffix_parent_name(tmp_path):
    vid1 = "vid-001"
    row = (vid1, str(tmp_path / "cam_a" / "v1.mp4"), "cam_a")
    lookup = _make_lookup([row])
    assert lookup.by_suffix.get("cam_a/v1.mp4") == vid1
    assert lookup.by_suffix.get("cam_a/v1") == vid1


def test_build_by_suffix_relative_path(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    vid1 = "vid-001"
    abs_path = scan / "cam_a" / "v1.mp4"
    row = (vid1, str(abs_path), "cam_a")
    lookup = _make_lookup([row], [scan])
    assert lookup.by_suffix.get("cam_a/v1.mp4") == vid1


def test_build_by_cam_stem_populated():
    rows = [
        ("vid-001", "/root/cam_a/v1.mp4", "cam_a"),
        ("vid-002", "/root/cam_b/v2.mp4", "cam_b"),
    ]
    lookup = _make_lookup(rows)
    assert ("vid-001", "cam_a") in lookup.by_cam_stem["v1"]
    assert ("vid-002", "cam_b") in lookup.by_cam_stem["v2"]


def test_build_cam_by_id():
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _make_lookup(rows)
    assert lookup.cam_by_id["vid-001"] == "cam_a"


def test_build_cam_by_id_none_camera():
    rows = [("vid-001", "/root/v1.mp4", None)]
    lookup = _make_lookup(rows)
    assert lookup.cam_by_id["vid-001"] == ""


# ---------------------------------------------------------------------------
# resolve_video_path
# ---------------------------------------------------------------------------


def _simple_lookup(rows: list[tuple[str, str, str | None]]) -> VideoPathLookup:
    return build_video_path_lookup(rows, [])


def test_resolve_exact_id():
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _simple_lookup(rows)
    vid, tier = resolve_video_path("vid-001", lookup, known_video_ids={"vid-001"})
    assert vid == "vid-001"
    assert tier == "exact_id"


def test_resolve_suffix_parent_name():
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _simple_lookup(rows)
    vid, tier = resolve_video_path("cam_a/v1.mp4", lookup)
    assert vid == "vid-001"
    assert tier == "suffix"


def test_resolve_suffix_without_extension():
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _simple_lookup(rows)
    vid, tier = resolve_video_path("cam_a/v1", lookup)
    assert vid == "vid-001"
    assert tier == "suffix"


def test_resolve_cam_stem_unique_matching_camera():
    """Unique stem + matching camera folder (shared token) → resolved via cam_stem tier."""
    rows = [("vid-001", "/root/P4_Cam002_L1/v1.mp4", "P4_Cam002_L1")]
    lookup = _simple_lookup(rows)
    # "Cam002" is a shared token between csv_cam and db_cam
    vid, tier = resolve_video_path("C8_Cam002_F2/v1.mp4", lookup)
    assert vid == "vid-001"
    assert tier == "cam_stem"


def test_resolve_cam_stem_unique_nonmatching_camera():
    """Unique stem + non-matching camera folder → no match (the bug fix)."""
    rows = [("vid-001", "/root/P4_Cam003_L1/01180001.mp4", "P4_Cam003_L1")]
    lookup = _simple_lookup(rows)
    vid, tier = resolve_video_path("C8_Cam002_F2/01180001.mp4", lookup)
    assert vid is None
    assert tier == ""


def test_resolve_cam_stem_ambiguous_one_camera_matches():
    """Two videos share a stem; only one matches the CSV camera → resolved."""
    rows = [
        ("vid-001", "/root/Cam002/clip.mp4", "Cam002"),
        ("vid-002", "/root/Cam003/clip.mp4", "Cam003"),
    ]
    lookup = _simple_lookup(rows)
    vid, tier = resolve_video_path("C8_Cam002_F2/clip.mp4", lookup)
    assert vid == "vid-001"
    assert tier == "cam_stem"


def test_resolve_cam_stem_ambiguous_both_cameras_match():
    """Both DB cameras share the same token with the CSV camera → ambiguous, no match."""
    rows = [
        ("vid-001", "/root/CamA_Cam001/clip.mp4", "CamA_Cam001"),
        ("vid-002", "/root/CamB_Cam001/clip.mp4", "CamB_Cam001"),
    ]
    lookup = _simple_lookup(rows)
    # "Cam001" is a token in both DB camera IDs → ambiguous
    vid, tier = resolve_video_path("Cam001/clip.mp4", lookup)
    assert vid is None


def test_resolve_suffix_beats_cam_stem():
    """When suffix tier matches, it takes priority and returns 'suffix' tier."""
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _simple_lookup(rows)
    vid, tier = resolve_video_path("cam_a/v1.mp4", lookup)
    assert tier == "suffix"  # not "cam_stem"


def test_resolve_empty_csv_cam_no_cam_stem_match():
    """Empty parent directory in CSV path → cam_stem fallback returns nothing."""
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _simple_lookup(rows)
    # Path("v1.mp4").parent.name == "" (no parent dir in the string)
    vid, tier = resolve_video_path("v1.mp4", lookup)
    assert vid is None


def test_resolve_extra_suffix_map():
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _simple_lookup(rows)
    extra = {"/absolute/path/to/v1.mp4": "vid-001"}
    vid, tier = resolve_video_path("/absolute/path/to/v1.mp4", lookup, extra_suffix_map=extra)
    assert vid == "vid-001"
    assert tier == "suffix"


def test_resolve_no_match():
    rows = [("vid-001", "/root/cam_a/v1.mp4", "cam_a")]
    lookup = _simple_lookup(rows)
    vid, tier = resolve_video_path("completely/unknown/path.mp4", lookup)
    assert vid is None
    assert tier == ""
