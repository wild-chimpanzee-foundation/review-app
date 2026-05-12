"""
Tests for validate_historic_csv and import_historic_csv.
"""

import uuid
from pathlib import Path

import pandas as pd
import pytest
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import text


def _seed_builtin_tags(dp) -> None:
    builtin = [
        ("fire", "Fire", "Feu", "deep-orange", "local_fire_department"),
        ("nice_shot", "Nice Shot", "Belle image", "amber", "star"),
        ("broken_metadata", "Broken Metadata", "Métadonnées corrompues", "red", "report_problem"),
    ]
    with dp.engine.begin() as conn:
        for key, name_en, name_fr, color, icon in builtin:
            if not conn.execute(text("SELECT 1 FROM tags WHERE key=:k"), {"k": key}).fetchone():
                conn.execute(
                    text(
                        "INSERT INTO tags (id,key,name_en,name_fr,color,icon,is_custom) "
                        "VALUES (:id,:key,:name_en,:name_fr,:color,:icon,0)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "key": key,
                        "name_en": name_en,
                        "name_fr": name_fr,
                        "color": color,
                        "icon": icon,
                    },
                )


_BLANK_SENTINEL = "__blank__"


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
    """NA/blank behaviour should not crash; stored as null since 'unlabeled' has no DB entry."""
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(species="deer", Behaviour="NA")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
    assert historic_provider.get_video_detail(ids["v1"])["manual_selections"]


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
        df, active_project_id=None, species_mappings={"unknown_sp": _BLANK_SENTINEL}
    )
    assert result["imported"] == 1
    assert historic_provider.get_video_detail(ids["v1"])["is_blank"] == 1


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


def test_import_fallback_stem_matching(historic_provider):
    """Empty folder col should still match via video stem when stem is unambiguous."""
    ids = _ids(historic_provider)
    df = pd.DataFrame([_row(folder="", video="v1", species="deer")])
    result = historic_provider.import_historic_csv(df, active_project_id=None)
    assert result["imported"] == 1
    assert historic_provider.get_video_detail(ids["v1"])["manual_selections"]


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
    _seed_builtin_tags(historic_provider)
    historic_provider.toggle_video_tag(ids["v1"], "fire")

    df = pd.DataFrame([_row(species="deer", flagged="1")])
    historic_provider.import_historic_csv(
        df, active_project_id=None, tag_cols=["flagged"], mode="append"
    )
    tags = set(historic_provider.get_video_tags(ids["v1"]))
    assert "fire" in tags  # existing tag preserved
    assert "flagged" in tags  # new tag added
