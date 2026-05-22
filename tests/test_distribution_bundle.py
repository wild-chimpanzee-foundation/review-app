"""Tests for work distribution and per-annotator bundle export/import."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from review_app.backend.provider.local_data_provider import LocalDataProvider


# ---------------------------------------------------------------------------
# Fixture: two cameras, two videos each
# ---------------------------------------------------------------------------


@pytest.fixture
def two_camera_provider(tmp_db, mock_probe):
    """Provider with a project containing cam_a (2 videos) and cam_b (2 videos)."""
    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    (video_dir / "cam_a" / "a1.mp4").touch()
    (video_dir / "cam_a" / "a2.mp4").touch()
    (video_dir / "cam_b").mkdir()
    (video_dir / "cam_b" / "b1.mp4").touch()
    (video_dir / "cam_b" / "b2.mp4").touch()

    dp = LocalDataProvider()
    project = dp.create_project("Test", str(video_dir))
    dp.sync_videos(progress_callback=None, video_dir=video_dir, active_project_id=project.id)

    dp.add_annotator("alice")
    dp.add_annotator("bob")

    return dp, project


# ---------------------------------------------------------------------------
# Distribution (assign + query)
# ---------------------------------------------------------------------------


def test_apply_distribution_stores_per_camera_assignments(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    cam_map = dp.get_camera_assignment_map(project.id)
    assert cam_map["cam_a"] == "alice"
    assert cam_map["cam_b"] == "bob"


def test_auto_distribute_assigns_all_cameras(two_camera_provider):
    dp, project = two_camera_provider
    result = dp.auto_distribute(project.id, ["alice", "bob"])

    # Every camera must appear in exactly one annotator's list
    all_assigned = [cam for cams in result.values() for cam in cams]
    camera_stats = dp.get_camera_stats(project.id)
    all_cameras = {c["camera_id"] for c in camera_stats}

    assert set(all_assigned) == all_cameras
    assert len(all_assigned) == len(all_cameras), "no camera assigned twice"


def test_apply_distribution_clears_unassigned_cameras(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a", "cam_b"]})
    assert dp.get_camera_assignment_map(project.id)["cam_b"] == "alice"

    # Reassign cam_a to bob, explicitly clear cam_b
    dp.apply_distribution(project.id, {"bob": ["cam_a"]}, clear_cameras=["cam_b"])

    cam_map = dp.get_camera_assignment_map(project.id)
    assert cam_map["cam_a"] == "bob"
    assert cam_map["cam_b"] is None, "cam_b should be unassigned after explicit clear"


def test_apply_distribution_replaces_existing(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a", "cam_b"]})
    assert dp.get_camera_assignment_map(project.id)["cam_b"] == "alice"

    # Re-assign cam_b to bob
    dp.apply_distribution(project.id, {"bob": ["cam_b"]})
    assert dp.get_camera_assignment_map(project.id)["cam_a"] == "alice"
    assert dp.get_camera_assignment_map(project.id)["cam_b"] == "bob"


def test_get_assignment_summary(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    summary = dp.get_assignment_summary(project.id)
    by_name = {r["annotator"]: r for r in summary}

    assert by_name["alice"]["cameras"] == 1
    assert by_name["alice"]["video_count"] == 2
    assert by_name["bob"]["cameras"] == 1
    assert by_name["bob"]["video_count"] == 2


# ---------------------------------------------------------------------------
# export_project_bundle — camera filter
# ---------------------------------------------------------------------------


def _read_bundle(bundle_bytes: bytes) -> dict[str, bytes]:
    """Unpack a bundle ZIP into {filename: raw_bytes}."""
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_export_bundle_metadata_camera_filter(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    bundle = _read_bundle(
        dp.export_project_bundle(project.id, ["metadata"], camera_ids=["cam_a"])
    )
    df = pd.read_csv(io.BytesIO(bundle["metadata.csv"]))

    assert set(df["camera_id"].unique()) == {"cam_a"}
    assert "cam_b" not in df["camera_id"].values
    assert (df["assigned_to"] == "alice").all()


def test_export_bundle_metadata_no_filter_includes_all(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    bundle = _read_bundle(dp.export_project_bundle(project.id, ["metadata"]))
    df = pd.read_csv(io.BytesIO(bundle["metadata.csv"]))

    assert set(df["camera_id"].unique()) == {"cam_a", "cam_b"}


def test_export_bundle_manifest_lists_included_components(two_camera_provider):
    dp, project = two_camera_provider
    bundle = _read_bundle(dp.export_project_bundle(project.id, ["metadata", "species"]))
    manifest = json.loads(bundle["bundle.json"])

    assert set(manifest["contents"]) == {"metadata", "species"}
    assert "model_annotations" not in manifest["contents"]


def test_export_bundle_omits_unselected_components(two_camera_provider):
    dp, project = two_camera_provider
    bundle = _read_bundle(dp.export_project_bundle(project.id, ["metadata"]))

    assert "metadata.csv" in bundle
    assert "species.csv" not in bundle
    assert "model_annotations.csv" not in bundle


# ---------------------------------------------------------------------------
# export_all_bundles — each annotator gets only their cameras
# ---------------------------------------------------------------------------


def _unpack_all_bundles(outer_bytes: bytes) -> dict[str, dict[str, bytes]]:
    """Unpack outer ZIP → {inner_filename: {inner_member: raw_bytes}}."""
    result = {}
    with zipfile.ZipFile(io.BytesIO(outer_bytes)) as outer:
        for name in outer.namelist():
            result[name] = _read_bundle(outer.read(name))
    return result


def test_export_all_bundles_each_annotator_only_sees_own_cameras(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    bundles = _unpack_all_bundles(dp.export_all_bundles(project.id, ["metadata"]))

    alice_bundle = next(v for k, v in bundles.items() if "alice" in k)
    bob_bundle = next(v for k, v in bundles.items() if "bob" in k)

    alice_df = pd.read_csv(io.BytesIO(alice_bundle["metadata.csv"]))
    bob_df = pd.read_csv(io.BytesIO(bob_bundle["metadata.csv"]))

    assert set(alice_df["camera_id"].unique()) == {"cam_a"}, "alice should only see cam_a"
    assert set(bob_df["camera_id"].unique()) == {"cam_b"}, "bob should only see cam_b"


def test_export_all_bundles_assigned_to_column_is_correct(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    bundles = _unpack_all_bundles(dp.export_all_bundles(project.id, ["metadata"]))

    alice_bundle = next(v for k, v in bundles.items() if "alice" in k)
    alice_df = pd.read_csv(io.BytesIO(alice_bundle["metadata.csv"]))

    assert (alice_df["assigned_to"] == "alice").all()


def test_export_all_bundles_annotator_without_assignment_is_skipped(two_camera_provider):
    dp, project = two_camera_provider
    # Only assign cam_a to alice; bob has no cameras
    dp.apply_distribution(project.id, {"alice": ["cam_a"]})

    bundles = _unpack_all_bundles(dp.export_all_bundles(project.id, ["metadata"]))

    assert all("bob" not in name for name in bundles), (
        "annotator with no assigned cameras should be omitted from the outer ZIP"
    )
    assert any("alice" in name for name in bundles)


def test_export_all_bundles_produces_one_zip_per_assigned_annotator(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    bundles = _unpack_all_bundles(dp.export_all_bundles(project.id, ["metadata"]))
    assert len(bundles) == 2

    # With only alice assigned, only one bundle is produced
    dp2 = LocalDataProvider()
    project2 = dp2.create_project("P2", dp.get_project_dirs(project.id)[0].path)
    dp2.sync_videos(progress_callback=None, video_dir=Path(dp.get_project_dirs(project.id)[0].path), active_project_id=project2.id)
    dp2.add_annotator("alice")
    dp2.add_annotator("bob")
    dp2.apply_distribution(project2.id, {"alice": ["cam_a"]})

    bundles2 = _unpack_all_bundles(dp2.export_all_bundles(project2.id, ["metadata"]))
    assert len(bundles2) == 1
    assert any("alice" in k for k in bundles2)


# ---------------------------------------------------------------------------
# Round-trip: export → import restores assignments on annotator's machine
# ---------------------------------------------------------------------------


def test_bundle_roundtrip_restores_assignments(two_camera_provider):
    dp, project = two_camera_provider
    dp.apply_distribution(project.id, {"alice": ["cam_a"], "bob": ["cam_b"]})

    alice_bundle_bytes = dp.export_project_bundle(
        project.id, ["metadata"], camera_ids=["cam_a"]
    )

    # Simulate fresh install: new provider, same video files synced
    video_dir = Path(dp.get_project_dirs(project.id)[0].path)
    dp2 = LocalDataProvider()
    project2 = dp2.create_project("Fresh", video_dir)
    dp2.sync_videos(
        progress_callback=None,
        video_dir=video_dir,
        active_project_id=project2.id,
    )

    dp2.import_project_bundle(project2.id, alice_bundle_bytes)

    cam_map = dp2.get_camera_assignment_map(project2.id)
    assert cam_map.get("cam_a") == "alice", "assignment should survive export → import"
    assert cam_map.get("cam_b") is None, "cam_b was not in alice's bundle"
