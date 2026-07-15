"""Tests for validate_model_csv and import_model_csv.

Most of these started as characterisation tests, written before validate_model_csv was
split into a mapping-independent pass and a cheap mapping pass, to pin down exactly what
it did at the time. The sentinel tests have since been rewritten: the importer now
resolves IGNORE_SENTINEL and BLANK_SENTINEL itself rather than leaving them in value_text
for the import page to filter.

validate_model_csv returns (cleaned_df, errors_df, species_mappings, unmapped_species).
"""

import pandas as pd
import pytest
from review_app.backend.errors import DataImportError
from review_app.backend.provider.import_service import BLANK_SENTINEL, IGNORE_SENTINEL
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.fixture
def dp_videos(tmp_db, mock_probe):
    """Provider with two known videos. Species catalog is deer (Red Deer) + fox (Red Fox)."""
    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    (video_dir / "cam_a" / "v1.mp4").touch()
    (video_dir / "cam_a" / "v2.mp4").touch()
    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    ids = dp.get_video_queue({}, active_project_id=None)
    paths = {dp.get_video_detail(v)["video_path"]: v for v in ids}
    v1 = next(p for p in paths if p.endswith("v1.mp4"))
    v2 = next(p for p in paths if p.endswith("v2.mp4"))
    return dp, {"v1": v1, "v2": v2, "ids": paths}


def _row(video_path, **kw):
    base = {
        "video_path": video_path,
        "annotation_type": "species",
        "model_name": "m1",
        "value_text": "deer",
    }
    base.update(kw)
    return base


def _errors(errors_df):
    return list(errors_df["error"]) if not errors_df.empty else []


# ── Happy path ────────────────────────────────────────────────────────────────


def test_valid_species_row_is_prepared(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], probability=0.9, t_start_sec=0.0, t_end_sec=5.0)])

    cleaned, errors, mappings, unmapped = dp.validate_model_csv(df, None, None)

    assert errors.empty
    assert len(cleaned) == 1
    row = cleaned.iloc[0]
    assert row["video_id"] == v["ids"][v["v1"]]
    assert row["video_path"] == v["v1"]
    assert row["annotation_type"] == "species"
    assert row["model_name"] == "m1"
    assert row["value_text"] == "deer"
    assert row["probability"] == 0.9
    assert row["t_start_sec"] == 0.0
    assert row["t_end_sec"] == 5.0
    assert mappings == []
    assert unmapped == []


def test_cleaned_columns_are_stable(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"])])
    cleaned, _, _, _ = dp.validate_model_csv(df, None, None)
    assert list(cleaned.columns) == [
        "video_id",
        "video_path",
        "annotation_type",
        "model_name",
        "value_text",
        "value_num",
        "probability",
        "t_start_sec",
        "t_end_sec",
    ]


# ── Row-level errors ──────────────────────────────────────────────────────────


def test_missing_path_is_an_error(dp_videos):
    dp, _ = dp_videos
    df = pd.DataFrame([_row("")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert cleaned.empty
    assert _errors(errors) == ["error_missing_path"]


def test_unknown_path_is_an_error(dp_videos):
    dp, _ = dp_videos
    df = pd.DataFrame([_row("nowhere/absent.mp4")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert cleaned.empty
    assert _errors(errors) == ["error_unknown_path"]
    # Unresolvable paths are echoed back untouched so the user can recognise them.
    assert errors.iloc[0]["video_path"] == "nowhere/absent.mp4"


def test_missing_model_name_is_an_error(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], model_name="")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert cleaned.empty
    assert _errors(errors) == ["error_missing_model_name"]


def test_invalid_annotation_type_is_an_error(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], annotation_type="nonsense")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert cleaned.empty
    assert _errors(errors) == ["error_invalid_annotation_type"]


@pytest.mark.parametrize("prob", [-0.1, 1.1, 42])
def test_probability_outside_0_1_is_an_error(dp_videos, prob):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], probability=prob)])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert cleaned.empty
    assert _errors(errors) == ["error_invalid_probability"]


def test_unparseable_probability_becomes_none_not_an_error(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], probability="not-a-number")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert errors.empty
    assert cleaned.iloc[0]["probability"] is None


def test_row_number_follows_the_frame_index_not_position(dp_videos):
    """row_number is index + 1, so a non-default index shifts the reported rows."""
    dp, _ = dp_videos
    df = pd.DataFrame([_row(""), _row("")], index=[10, 11])
    _, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert list(errors["row_number"]) == [11, 12]


# ── Annotation type / value handling ──────────────────────────────────────────


def test_annotation_type_is_normalised_to_lowercase(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], annotation_type="  SPECIES  ")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert errors.empty
    assert cleaned.iloc[0]["annotation_type"] == "species"


def test_blank_type_skips_species_resolution(dp_videos):
    """Non-species types keep value_text verbatim — no catalog check, no mapping."""
    dp, v = dp_videos
    df = pd.DataFrame(
        [_row(v["v1"], annotation_type="blank_non_blank", value_text="total_nonsense")]
    )
    cleaned, errors, _, unmapped = dp.validate_model_csv(df, None, None)
    assert errors.empty
    assert cleaned.iloc[0]["value_text"] == "total_nonsense"
    assert unmapped == []


def test_empty_species_value_is_kept_as_none(dp_videos):
    """A species row with a blank value_text bypasses species checks entirely."""
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="   ")])
    cleaned, errors, _, unmapped = dp.validate_model_csv(df, None, None)
    assert errors.empty
    assert cleaned.iloc[0]["value_text"] is None
    assert unmapped == []


def test_value_text_is_stripped(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="  deer  ")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert errors.empty
    assert cleaned.iloc[0]["value_text"] == "deer"


# ── Species resolution ────────────────────────────────────────────────────────


def test_unknown_species_needs_mapping(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="tyrannosaurus")])
    cleaned, errors, _, unmapped = dp.validate_model_csv(df, None, None)
    assert cleaned.empty
    assert _errors(errors) == ["error_species_needs_mapping"]
    assert unmapped == [{"original": "tyrannosaurus"}]


def test_english_name_resolves_to_scientific_name(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="Red Deer")])
    cleaned, errors, mappings, unmapped = dp.validate_model_csv(df, None, None)
    assert errors.empty
    assert cleaned.iloc[0]["value_text"] == "deer"
    assert unmapped == []
    # A resolution that changes the value is reported back as a suggested mapping.
    assert mappings == [{"original": "Red Deer", "mapped_to": "deer"}]


def test_exact_match_reports_no_suggested_mapping(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="deer")])
    _, _, mappings, _ = dp.validate_model_csv(df, None, None)
    assert mappings == []


def test_suggested_mappings_repeat_once_per_row(dp_videos):
    """Wart: species_mappings is appended per row, so it holds duplicates.

    Callers happen to collapse it into a dict, which is why this has gone unnoticed.
    """
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="Red Deer") for _ in range(3)])
    _, _, mappings, _ = dp.validate_model_csv(df, None, None)
    assert mappings == [{"original": "Red Deer", "mapped_to": "deer"}] * 3


def test_explicit_mapping_wins_over_catalog_lookup(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="tyrannosaurus")])
    cleaned, errors, _, unmapped = dp.validate_model_csv(df, {"tyrannosaurus": "fox"}, None)
    assert errors.empty
    assert cleaned.iloc[0]["value_text"] == "fox"
    assert unmapped == []


def test_explicit_mapping_is_not_itself_validated(dp_videos):
    """Wart: a mapping target is trusted verbatim, even if it is not a known species."""
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="tyrannosaurus")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, {"tyrannosaurus": "not_a_species"}, None)
    assert errors.empty
    assert cleaned.iloc[0]["value_text"] == "not_a_species"


def test_empty_mapping_value_falls_through_to_catalog(dp_videos):
    """A falsy mapping means 'not mapped yet', so the row still needs mapping."""
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="tyrannosaurus")])
    cleaned, errors, _, unmapped = dp.validate_model_csv(df, {"tyrannosaurus": ""}, None)
    assert cleaned.empty
    assert _errors(errors) == ["error_species_needs_mapping"]
    assert unmapped == [{"original": "tyrannosaurus"}]


def test_ignore_sentinel_drops_the_rows_without_erroring(dp_videos):
    """Rows mapped to IGNORE are dropped — the user asked for that, so they aren't errors."""
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="tyrannosaurus")])
    cleaned, errors, _, unmapped = dp.validate_model_csv(
        df, {"tyrannosaurus": IGNORE_SENTINEL}, None
    )
    assert errors.empty
    assert cleaned.empty
    assert unmapped == []


def test_blank_sentinel_becomes_a_blank_prediction(dp_videos):
    """Mapping a species to blank restates the row as the same model's blank prediction."""
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="tyrannosaurus")])
    cleaned, errors, _, _ = dp.validate_model_csv(df, {"tyrannosaurus": BLANK_SENTINEL}, None)
    assert errors.empty
    assert cleaned.iloc[0]["annotation_type"] == "blank_non_blank"
    assert cleaned.iloc[0]["value_text"] == "blank"


def test_mapping_applies_to_object_detection_too(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame(
        [_row(v["v1"], annotation_type="object_detection", value_text="tyrannosaurus")]
    )
    cleaned, errors, _, _ = dp.validate_model_csv(df, {"tyrannosaurus": "fox"}, None)
    assert errors.empty
    assert cleaned.iloc[0]["value_text"] == "fox"


def test_unmapped_species_are_sorted_and_deduplicated(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame(
        [
            _row(v["v1"], value_text="zebra_thing"),
            _row(v["v1"], value_text="aardvark_thing"),
            _row(v["v2"], value_text="zebra_thing"),
        ]
    )
    _, _, _, unmapped = dp.validate_model_csv(df, None, None)
    assert unmapped == [{"original": "aardvark_thing"}, {"original": "zebra_thing"}]


# ── Frame-level behaviour ─────────────────────────────────────────────────────


@pytest.mark.parametrize("alias", ["path", "filepath", "review_filename", "original_filepath"])
def test_path_column_aliases_are_accepted(dp_videos, alias):
    dp, v = dp_videos
    row = _row(v["v1"])
    row[alias] = row.pop("video_path")
    cleaned, errors, _, _ = dp.validate_model_csv(pd.DataFrame([row]), None, None)
    assert errors.empty
    assert len(cleaned) == 1


def test_video_path_column_wins_over_an_alias(dp_videos):
    dp, v = dp_videos
    row = _row(v["v1"])
    row["path"] = "nowhere/absent.mp4"
    cleaned, errors, _, _ = dp.validate_model_csv(pd.DataFrame([row]), None, None)
    assert errors.empty
    assert len(cleaned) == 1


def test_column_names_are_stripped(dp_videos):
    dp, v = dp_videos
    row = {f"  {k}  ": val for k, val in _row(v["v1"]).items()}
    cleaned, errors, _, _ = dp.validate_model_csv(pd.DataFrame([row]), None, None)
    assert errors.empty
    assert len(cleaned) == 1


@pytest.mark.parametrize(
    "drop, missing",
    [
        ("video_path", "video_path"),
        ("annotation_type", "annotation_type"),
        ("model_name", "model_name"),
    ],
)
def test_missing_required_column_raises(dp_videos, drop, missing):
    dp, v = dp_videos
    row = _row(v["v1"])
    row.pop(drop)
    with pytest.raises(DataImportError) as exc:
        dp.validate_model_csv(pd.DataFrame([row]), None, None)
    assert exc.value.user_message_key == "csv_error_missing_columns"
    assert missing in exc.value.detail


def test_optional_columns_may_be_absent_entirely(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame(
        [
            {
                "video_path": v["v1"],
                "annotation_type": "species",
                "model_name": "m1",
                "value_text": "deer",
            }
        ]
    )
    cleaned, errors, _, _ = dp.validate_model_csv(df, None, None)
    assert errors.empty
    row = cleaned.iloc[0]
    assert row["probability"] is None
    assert row["value_num"] is None
    assert row["t_start_sec"] is None
    assert row["t_end_sec"] is None


def test_input_frame_is_not_mutated(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"])])
    before = df.copy(deep=True)
    dp.validate_model_csv(df, None, None)
    pd.testing.assert_frame_equal(df, before)


def test_valid_and_invalid_rows_are_partitioned(dp_videos):
    dp, v = dp_videos
    df = pd.DataFrame(
        [
            _row(v["v1"]),
            _row("nowhere/absent.mp4"),
            _row(v["v2"], value_text="tyrannosaurus"),
            _row(v["v2"], model_name=""),
        ]
    )
    cleaned, errors, _, unmapped = dp.validate_model_csv(df, None, None)
    assert len(cleaned) == 1
    assert _errors(errors) == [
        "error_unknown_path",
        "error_species_needs_mapping",
        "error_missing_model_name",
    ]
    assert unmapped == [{"original": "tyrannosaurus"}]


# ── import_model_csv ──────────────────────────────────────────────────────────


def test_import_registers_add_new_species(dp_videos):
    """ "Add as a new species" must reach the catalog, or value_text matches nothing."""
    dp, v = dp_videos
    assert not dp.species_exists("Novum inventum")
    df = pd.DataFrame([_row(v["v1"], value_text="tyrannosaurus")])
    cleaned, _, _, _ = dp.validate_model_csv(df, {"tyrannosaurus": "Novum inventum"}, None)

    result = dp.import_model_csv(cleaned_df=cleaned, active_project_id=None)

    assert result["imported"] == 1
    assert dp.species_exists("Novum inventum")


def test_import_does_not_register_fuzzy_matched_species(dp_videos):
    """A row the catalog already matched must not create anything."""
    dp, v = dp_videos
    df = pd.DataFrame([_row(v["v1"], value_text="deer")])
    cleaned, _, _, _ = dp.validate_model_csv(df, None, None)

    before = set(dp.get_valid_species(None))
    dp.import_model_csv(cleaned_df=cleaned, active_project_id=None)

    assert set(dp.get_valid_species(None)) == before
