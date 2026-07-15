"""
Tests for validate_historic_csv and import_historic_csv.
"""

from pathlib import Path

import pandas as pd
import pytest
from conftest import seed_builtin_tags
from review_app.backend.provider.import_service import BLANK_SENTINEL, IGNORE_SENTINEL
from review_app.backend.provider.local_data_provider import LocalDataProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def historic_provider(tmp_db, mock_probe):
    """Provider with two videos in separate subdirectories, no annotations."""
    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    (video_dir / "cam_a" / "v1.mp4").touch()
    (video_dir / "cam_b").mkdir()
    (video_dir / "cam_b" / "v2.mp4").touch()
    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    return dp


@pytest.fixture
def ambiguous_provider(tmp_db, mock_probe):
    """Provider with two videos that share the same stem in different folders."""
    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    (video_dir / "cam_a" / "clip.mp4").touch()
    (video_dir / "cam_b").mkdir()
    (video_dir / "cam_b" / "clip.mp4").touch()
    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    return dp


def _ids(dp) -> dict[str, str]:
    """Return {stem: video_id} for all synced videos."""
    queue = dp.get_video_queue({}, active_project_id=None)
    return {Path(dp.get_video_detail(vid)["video_path"]).stem: vid for vid in queue}


def _row(folder="cam_a", video="v1", species="deer", data_type="Video", **kwargs) -> dict:
    return {
        "Folder_name_standard": folder,
        "Video_name": video,
        "Species": species,
        "Data_type": data_type,
        **kwargs,
    }


# ---------------------------------------------------------------------------
# validate_historic_csv
# ---------------------------------------------------------------------------


def test_validate_matched_count(historic_provider):
    df = pd.DataFrame([_row("cam_a", "v1"), _row("cam_b", "v2")])
    result = historic_provider.validate_historic_csv(df, active_project_id=None)
    assert result["matched"] == 2
    assert result["unmatched"] == 0
    assert result["unknown_species"] == []


def test_validate_reports_unmatched_paths(historic_provider):
    df = pd.DataFrame([_row("cam_a", "v1"), _row("cam_x", "ghost")])
    result = historic_provider.validate_historic_csv(df, active_project_id=None)
    assert result["matched"] == 1
    assert result["unmatched"] == 1
    assert "cam_x/ghost" in result["unmatched_paths"]


def test_validate_deduplicates_unmatched(historic_provider):
    df = pd.DataFrame([_row("cam_x", "ghost"), _row("cam_x", "ghost")])
    result = historic_provider.validate_historic_csv(df, active_project_id=None)
    assert result["unmatched"] == 1
    assert len(result["unmatched_paths"]) == 1


def test_validate_skips_non_video_rows(historic_provider):
    df = pd.DataFrame(
        [
            _row("cam_a", "v1", data_type="Video"),
            _row("cam_a", "v1", data_type="Installation"),
        ]
    )
    result = historic_provider.validate_historic_csv(df, active_project_id=None)
    assert result["skipped_installation"] == 1
    assert result["total_rows"] == 1


def test_validate_no_data_type_col_treats_all_as_video(historic_provider):
    df = pd.DataFrame([{"Folder_name_standard": "cam_a", "Video_name": "v1", "Species": "deer"}])
    result = historic_provider.validate_historic_csv(df, active_project_id=None)
    assert result["skipped_installation"] == 0
    assert result["total_rows"] == 1


def test_validate_detects_unknown_species(historic_provider):
    df = pd.DataFrame([_row(species="totally_unknown_xyz_species")])
    result = historic_provider.validate_historic_csv(df, active_project_id=None)
    assert "totally_unknown_xyz_species" in result["unknown_species"]


def test_validate_blank_species_not_flagged_as_unknown(historic_provider):
    for blank in ("Vide", "NA", "nan", ""):
        df = pd.DataFrame([_row(species=blank)])
        result = historic_provider.validate_historic_csv(df, active_project_id=None)
        assert result["unknown_species"] == [], f"Expected no unknowns for species={blank!r}"


def test_validate_mapped_species_not_flagged(historic_provider):
    df = pd.DataFrame([_row(species="unmappable_xyz")])
    result = historic_provider.validate_historic_csv(
        df, active_project_id=None, species_mappings={"unmappable_xyz": "deer"}
    )
    assert result["unknown_species"] == []


def test_validate_mapped_to_unknown_target_is_an_add_new(historic_provider):
    """A target matching no catalog entry is the "add as a new species" choice, not an error."""
    df = pd.DataFrame([_row(species="unmappable_xyz")])
    result = historic_provider.validate_historic_csv(
        df, active_project_id=None, species_mappings={"unmappable_xyz": "also_invalid_xyz"}
    )
    assert result["unknown_species"] == []
    assert result["species_to_add"] == ["also_invalid_xyz"]


def test_validate_cleared_mapping_stays_unknown(historic_provider):
    """A species whose mapping the user cleared is still awaiting a decision."""
    df = pd.DataFrame([_row(species="unmappable_xyz")])
    result = historic_provider.validate_historic_csv(
        df, active_project_id=None, species_mappings={"unmappable_xyz": ""}
    )
    assert result["unknown_species"] == ["unmappable_xyz"]


def test_validate_mapped_to_ignore_not_flagged(historic_provider):
    """Mapping to the ignore sentinel resolves the species; its rows are dropped on import."""
    df = pd.DataFrame([_row(species="unmappable_xyz")])
    result = historic_provider.validate_historic_csv(
        df, active_project_id=None, species_mappings={"unmappable_xyz": IGNORE_SENTINEL}
    )
    assert result["unknown_species"] == []


def test_validate_mapped_to_blank_sentinel_not_flagged(historic_provider):
    """Mapping to the blank sentinel must not be flagged as unknown."""
    df = pd.DataFrame([_row(species="unmappable_xyz")])
    result = historic_provider.validate_historic_csv(
        df, active_project_id=None, species_mappings={"unmappable_xyz": BLANK_SENTINEL}
    )
    assert result["unknown_species"] == []


# ---------------------------------------------------------------------------
# import_historic_csv
# ---------------------------------------------------------------------------


def test_import_vide_creates_blank_label(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="Vide")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
    assert result["skipped"] == []
    assert historic_provider.get_video_detail(ids["v1"])["is_blank"] == 1


def test_import_species_creates_observation(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="deer", Number=2, Behaviour="grazing", Observer="alice")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
    detail = historic_provider.get_video_detail(ids["v1"])
    assert len(detail["manual_selections"]) == 1
    sel = detail["manual_selections"][0]
    assert sel["species"] == "deer"
    assert sel["count"] == 2
    assert sel["labeled_by"] == "alice"


def test_import_na_behaviour_stores_no_behavior(historic_provider):
    """NA/blank behaviour should store observation with null behavior field."""
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="deer", Behaviour="NA")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
    sels = historic_provider.get_video_detail(ids["v1"])["manual_selections"]
    assert len(sels) == 1
    assert sels[0]["species"] == "deer"
    assert sels[0].get("behavior") in (None, "None", "")


def test_import_skips_unmatched_video(historic_provider):
    df = pd.DataFrame([_row("cam_x", "ghost", species="deer")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 0
    assert "cam_x/ghost" in result["skipped"]


def test_import_skips_unknown_species_observation(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="totally_unknown_xyz_species")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1  # video still counted as processed
    assert len(result["skipped_observations"]) == 1
    assert result["skipped_observations"][0]["species"] == "totally_unknown_xyz_species"
    # video should have no observations but is not blank either
    detail = historic_provider.get_video_detail(ids["v1"])
    assert detail["manual_selections"] == []


def test_import_filters_non_video_data_type(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(data_type="Installation", species="deer")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 0
    detail = historic_provider.get_video_detail(ids["v1"])
    assert not detail["manual_selections"]


def test_import_blank_sentinel_mapping_creates_blank_label(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="unknown_sp")])
    result = historic_provider.import_historic_csv(
        df, active_project_id=None, species_mappings={"unknown_sp": BLANK_SENTINEL}
    )
    assert result["imported"] == 1
    assert historic_provider.get_video_detail(ids["v1"])["is_blank"] == 1


def test_import_ignore_sentinel_drops_the_observation(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="unknown_sp")])
    result = historic_provider.import_historic_csv(
        df, active_project_id=None, species_mappings={"unknown_sp": IGNORE_SENTINEL}
    )
    assert result["imported"] == 1
    assert result["skipped_observations"] == []
    detail = historic_provider.get_video_detail(ids["v1"])
    assert detail["manual_selections"] == []


def test_import_ignore_sentinel_does_not_mark_the_video_blank(historic_provider):
    """Ignoring the only species says nothing about whether the video was empty."""
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="unknown_sp")])
    historic_provider.import_historic_csv(
        df, active_project_id=None, species_mappings={"unknown_sp": IGNORE_SENTINEL}
    )
    assert not historic_provider.get_video_detail(ids["v1"])["is_blank"]


def test_import_ignored_species_leaves_its_siblings_alone(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame(
        [
            _row("cam_a", "v1", species="unknown_sp"),
            _row("cam_a", "v1", species="deer"),
        ]
    )
    historic_provider.import_historic_csv(
        df, active_project_id=None, species_mappings={"unknown_sp": IGNORE_SENTINEL}
    )
    sels = historic_provider.get_video_detail(ids["v1"])["manual_selections"]
    assert [s["species"] for s in sels] == ["deer"]


def test_import_add_new_species_registers_and_imports_it(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="unknown_sp")])
    result = historic_provider.import_historic_csv(
        df, active_project_id=None, species_mappings={"unknown_sp": "Novum inventum"}
    )
    assert result["imported"] == 1
    assert result["skipped_observations"] == []
    assert historic_provider.species_exists("Novum inventum")
    sels = historic_provider.get_video_detail(ids["v1"])["manual_selections"]
    assert [s["species"] for s in sels] == ["Novum inventum"]


def test_import_multiple_species_same_video(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame(
        [
            _row("cam_a", "v1", species="deer", Number=1, Behaviour="grazing"),
            _row("cam_a", "v1", species="fox", Number=2, Behaviour="running"),
        ]
    )
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
    detail = historic_provider.get_video_detail(ids["v1"])
    assert len(detail["manual_selections"]) == 2
    assert {s["species"] for s in detail["manual_selections"]} == {"deer", "fox"}


def test_import_override_replaces_existing(historic_provider):
    ids = _ids(historic_provider)
    historic_provider.import_historic_csv(
        pd.DataFrame([_row(species="deer")]), active_project_id=None
    )
    historic_provider.import_historic_csv(
        pd.DataFrame([_row(species="fox")]), active_project_id=None, mode="override"
    )
    detail = historic_provider.get_video_detail(ids["v1"])
    assert len(detail["manual_selections"]) == 1
    assert detail["manual_selections"][0]["species"] == "fox"


def test_import_append_preserves_existing(historic_provider):
    ids = _ids(historic_provider)
    historic_provider.import_historic_csv(
        pd.DataFrame([_row(species="deer")]), active_project_id=None
    )
    historic_provider.import_historic_csv(
        pd.DataFrame([_row(species="fox")]), active_project_id=None, mode="append"
    )
    detail = historic_provider.get_video_detail(ids["v1"])
    assert len(detail["manual_selections"]) == 2


def test_import_fallback_stem_with_camera_match(historic_provider):
    """When folder matches the DB camera_id by substring, cam-stem fallback resolves correctly."""
    ids = _ids(historic_provider)
    # "cam_a" exactly matches the DB camera_id for v1.mp4
    df = pd.DataFrame([_row(folder="cam_a", video="v1", species="deer")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
    assert historic_provider.get_video_detail(ids["v1"])["manual_selections"]


def test_import_fallback_stem_empty_folder_no_match(historic_provider):
    """Empty folder col no longer matches by stem alone — camera substring is required."""
    df = pd.DataFrame([_row(folder="", video="v1", species="deer")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 0


def test_import_species_mapping_applied(historic_provider):
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="chevreuil")])
    result = historic_provider.import_historic_csv(
        df, active_project_id=None, species_mappings={"chevreuil": "deer"}
    )
    assert result["imported"] == 1
    sel = historic_provider.get_video_detail(ids["v1"])["manual_selections"][0]
    assert sel["species"] == "deer"


def test_import_is_blank_col_overrides_species(historic_provider):
    """Explicit is_blank_col=1 should mark video as blank even if species is non-blank."""
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="deer", is_blank_flag="1")])
    result = historic_provider.import_historic_csv(
        df, active_project_id=None, is_blank_col="is_blank_flag"
    )
    assert result["imported"] == 1
    assert historic_provider.get_video_detail(ids["v1"])["is_blank"] == 1


def test_import_is_blank_col_false_allows_species(historic_provider):
    """Explicit is_blank_col=0 should let species processing proceed normally."""
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="deer", is_blank_flag="0")])
    result = historic_provider.import_historic_csv(
        df, active_project_id=None, is_blank_col="is_blank_flag"
    )
    assert result["imported"] == 1
    assert len(historic_provider.get_video_detail(ids["v1"])["manual_selections"]) == 1


def test_import_tag_cols_creates_and_applies_tags(historic_provider):
    """Columns selected as tag_cols with truthy values create custom tags and apply them."""
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="deer", priority="1", camera_issue="0")])
    result = historic_provider.import_historic_csv(
        df, active_project_id=None, tag_cols=["priority", "camera_issue"]
    )
    assert result["imported"] == 1
    tags = set(historic_provider.get_video_tags(ids["v1"]))
    assert "priority" in tags  # truthy value → tag applied
    assert "camera_issue" not in tags  # falsy value → tag not applied


def test_import_tag_cols_append_does_not_remove_existing(historic_provider):
    """In append mode, tag_cols should add tags but not remove ones already set."""
    ids = _ids(historic_provider)
    seed_builtin_tags(historic_provider)
    historic_provider.toggle_video_tag(ids["v1"], "fire")

    df = pd.DataFrame([_row(species="deer", flagged="1")])
    historic_provider.import_historic_csv(
        df, active_project_id=None, tag_cols=["flagged"], mode="append"
    )
    tags = set(historic_provider.get_video_tags(ids["v1"]))
    assert "fire" in tags  # existing tag preserved
    assert "flagged" in tags  # new tag added


def test_import_ambiguous_stem_not_matched_by_stem_fallback(ambiguous_provider):
    """When two videos share the same stem, stem-only fallback must not silently pick one."""
    df = pd.DataFrame(
        [
            {
                "Folder_name_standard": "",
                "Video_name": "clip",
                "Species": "deer",
                "Data_type": "Video",
            }
        ]
    )
    result = ambiguous_provider.import_historic_csv(df, active_project_id=None)
    # With an empty folder the suffix key is "/clip" which won't match either
    # "cam_a/clip.mp4" or "cam_b/clip.mp4", and the stem "clip" is ambiguous so
    # the stem fallback must not match either video.
    assert result["imported"] == 0
    assert len(result["skipped"]) == 1


def test_cross_camera_stem_not_matched(tmp_db, mock_probe):
    """CSV folder C8_Cam002_F2 must not match DB camera P4_Cam003_L1 even with the same stem."""
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    video_dir = tmp_db["video_dir"]
    (video_dir / "P4_Cam003_L1").mkdir()
    (video_dir / "P4_Cam003_L1" / "01180001.mp4").touch()
    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)

    df = pd.DataFrame(
        [
            {
                "Folder_name_standard": "C8_Cam002_F2",
                "Video_name": "01180001.mp4",
                "Species": "deer",
                "Data_type": "Video",
            }
        ]
    )
    result = dp.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 0


def test_matching_camera_substring_resolves(tmp_db, mock_probe):
    """CSV folder C8_Cam003_F2 should match DB camera P4_Cam003_L1 via 'Cam003' substring."""
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    video_dir = tmp_db["video_dir"]
    (video_dir / "P4_Cam003_L1").mkdir()
    (video_dir / "P4_Cam003_L1" / "01180001.mp4").touch()
    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)

    df = pd.DataFrame(
        [
            {
                "Folder_name_standard": "C8_Cam003_F2",
                "Video_name": "01180001.mp4",
                "Species": "deer",
                "Data_type": "Video",
            }
        ]
    )
    result = dp.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
