"""
Tests for SpeciesMixin (review_app/backend/species.py):
CSV parsing, fuzzy matching, custom species/behaviors, project overrides,
and CSV-driven project import.
"""

import pytest
from review_app.backend.local_data_provider import LocalDataProvider
from review_app.backend.species import SpeciesMixin

# ---------------------------------------------------------------------------
# _parse_species_csv (static, no DB needed)
# ---------------------------------------------------------------------------


def test_parse_species_csv_basic(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text(
        "scientific_name;english_name;french_name\ndeer;Red Deer;Cerf\nfox;Red Fox;Renard\n"
    )
    rows = SpeciesMixin._parse_species_csv(str(f))
    assert len(rows) == 2
    assert rows[0]["scientific_name"] == "deer"
    assert rows[0]["name_en"] == "Red Deer"
    assert rows[0]["name_fr"] == "Cerf"
    assert rows[1]["name_fr"] == "Renard"


def test_parse_species_csv_no_optional_columns(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("scientific_name\ndeer\nfox\n")
    rows = SpeciesMixin._parse_species_csv(str(f))
    assert len(rows) == 2
    assert rows[0]["name_en"] is None
    assert rows[0]["name_fr"] is None


def test_parse_species_csv_skips_na_rows(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("scientific_name;english_name\nNA;\nnan;\n;empty\ndeer;Red Deer\n")
    rows = SpeciesMixin._parse_species_csv(str(f))
    assert [r["scientific_name"] for r in rows] == ["deer"]


def test_parse_species_csv_missing_required_column_raises(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("english_name\nRed Deer\n")
    with pytest.raises(ValueError, match="scientific_name"):
        SpeciesMixin._parse_species_csv(str(f))


# ---------------------------------------------------------------------------
# _parse_behaviors_csv (static, no DB needed)
# ---------------------------------------------------------------------------


def test_parse_behaviors_csv_basic(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text(
        "scientific_name;key;name_en;name_fr\n*;grazing;Grazing;Brouter\ndeer;running;Running;Courir\n"
    )
    rows = SpeciesMixin._parse_behaviors_csv(str(f))
    assert len(rows) == 2
    assert rows[0] == {
        "scientific_name": "*",
        "key": "grazing",
        "name_en": "Grazing",
        "name_fr": "Brouter",
    }
    assert rows[1] == {
        "scientific_name": "deer",
        "key": "running",
        "name_en": "Running",
        "name_fr": "Courir",
    }


def test_parse_behaviors_csv_no_name_fr_column(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("scientific_name;key;name_en\n*;grazing;Grazing\n")
    rows = SpeciesMixin._parse_behaviors_csv(str(f))
    assert rows[0]["name_fr"] is None


def test_parse_behaviors_csv_missing_required_columns_returns_empty(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text("name_en\nGrazing\n")
    assert SpeciesMixin._parse_behaviors_csv(str(f)) == []


def test_parse_behaviors_csv_parse_error_returns_empty(tmp_path):
    f = tmp_path / "b.csv"
    f.write_bytes(b"\xff\xfe invalid binary")
    # Should not raise; returns []
    result = SpeciesMixin._parse_behaviors_csv(str(f))
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Fuzzy species matching
# ---------------------------------------------------------------------------


def test_fuzzy_exact_match(tmp_db):
    dp = LocalDataProvider()
    ok, matched = dp._validate_species_fuzzy("deer")
    assert ok is True
    assert matched == "deer"


def test_fuzzy_case_insensitive_exact(tmp_db):
    dp = LocalDataProvider()
    ok, matched = dp._validate_species_fuzzy("DEER")
    assert ok is True
    assert matched == "deer"


def test_fuzzy_english_name_match(tmp_db):
    dp = LocalDataProvider()
    ok, matched = dp._validate_species_fuzzy("Red Deer")
    assert ok is True
    assert matched == "deer"


def test_fuzzy_typo_match(tmp_db):
    dp = LocalDataProvider()
    ok, matched = dp._validate_species_fuzzy("deerr")  # one extra letter
    assert ok is True
    assert matched == "deer"


def test_fuzzy_no_match_below_threshold(tmp_db):
    dp = LocalDataProvider()
    ok, matched = dp._validate_species_fuzzy("xyzzy_unknown_animal_zzz")
    assert ok is False
    assert matched is None


def test_fuzzy_empty_string(tmp_db):
    dp = LocalDataProvider()
    ok, matched = dp._validate_species_fuzzy("")
    assert ok is False
    assert matched is None


# ---------------------------------------------------------------------------
# Custom species
# ---------------------------------------------------------------------------


def test_add_custom_species_creates_entry(tmp_db):
    dp = LocalDataProvider()
    result = dp.add_custom_species("Panthera leo", "Lion", "Lion", "Carnivore", "Carnivore")
    assert result is True
    assert dp.species_exists("Panthera leo")
    assert "Panthera leo" in dp.get_valid_species()


def test_add_custom_species_duplicate_returns_false(tmp_db):
    dp = LocalDataProvider()
    dp.add_custom_species("Panthera leo", "Lion", "Lion", "Carnivore", "Carnivore")
    result = dp.add_custom_species("Panthera leo", "Lion 2", "Lion 2", "Carnivore", "Carnivore")
    assert result is False


def test_custom_species_survives_reload(tmp_db):
    """Custom species must not be deleted when bundled CSV is reloaded."""
    dp = LocalDataProvider()
    dp.add_custom_species("Panthera leo", "Lion", "Lion", "Carnivore", "Carnivore")
    dp._load_species_data()  # simulates app restart
    assert dp.species_exists("Panthera leo")


def test_species_exists_false_for_unknown(tmp_db):
    dp = LocalDataProvider()
    assert dp.species_exists("ghost_species_xyz") is False


# ---------------------------------------------------------------------------
# Custom behaviors
# ---------------------------------------------------------------------------


def test_add_custom_behavior_creates_entry(tmp_db):
    dp = LocalDataProvider()
    result = dp.add_custom_behavior("stalking", "Stalking", "En embuscade")
    assert result is True
    assert dp.behavior_exists("stalking")


def test_add_custom_behavior_duplicate_returns_false(tmp_db):
    dp = LocalDataProvider()
    dp.add_custom_behavior("stalking", "Stalking")
    result = dp.add_custom_behavior("stalking", "Stalking again")
    assert result is False


def test_get_all_behaviors_includes_custom(tmp_db):
    dp = LocalDataProvider()
    dp.add_custom_behavior("stalking", "Stalking", "En embuscade")
    behaviors = dp.get_all_behaviors()
    keys = [b["key"] for b in behaviors]
    assert "stalking" in keys
    assert "grazing" in keys  # from bundled CSV


def test_behavior_exists_false_for_unknown(tmp_db):
    dp = LocalDataProvider()
    assert dp.behavior_exists("ghost_behavior_xyz") is False


# ---------------------------------------------------------------------------
# Display maps
# ---------------------------------------------------------------------------


def test_species_display_map_english(tmp_db):
    dp = LocalDataProvider()
    m = dp.get_species_display_map(lang="en")
    assert "deer" in m
    assert "Red Deer" in m["deer"]  # format: "Red Deer (deer)"


def test_behavior_display_map_filtered_by_species(tmp_db):
    dp = LocalDataProvider()
    m = dp.get_behavior_display_map(lang="en", species_name="deer")
    assert "grazing" in m
    assert "running" not in m  # fox-only behavior


def test_behavior_display_map_all(tmp_db):
    dp = LocalDataProvider()
    m = dp.get_behavior_display_map(lang="en")
    assert "grazing" in m
    assert "running" in m


# ---------------------------------------------------------------------------
# import_project_species_from_csv
# ---------------------------------------------------------------------------


def test_import_project_species_from_csv(provider_with_project):
    dp, project, _ = provider_with_project
    csv_content = "scientific_name;english_name\ndeer;Red Deer\n"
    count = dp.import_project_species_from_csv(project.id, csv_content)
    assert count == 1
    assert dp.get_project_species(project.id) == ["deer"]


def test_import_project_species_upserts_new_species(provider_with_project):
    dp, project, _ = provider_with_project
    csv_content = "scientific_name;english_name\nPanthera leo;Lion\n"
    dp.import_project_species_from_csv(project.id, csv_content)
    assert dp.species_exists("Panthera leo")
    assert "Panthera leo" in dp.get_project_species(project.id)


def test_import_project_species_empty_csv_raises(provider_with_project):
    dp, project, _ = provider_with_project
    with pytest.raises(ValueError, match="No valid rows"):
        dp.import_project_species_from_csv(project.id, "scientific_name\n")


# ---------------------------------------------------------------------------
# import_project_behaviors_from_csv
# ---------------------------------------------------------------------------


def test_import_project_behaviors_global_rows(provider_with_project):
    """Behaviors with scientific_name='*' apply to all project species."""
    dp, project, _ = provider_with_project
    dp.set_project_species(project.id, ["deer", "fox"])

    csv_content = "scientific_name;key;name_en\n*;stalking;Stalking\n"
    updated = dp.import_project_behaviors_from_csv(project.id, csv_content)
    assert updated == 2  # deer and fox both updated

    assert "stalking" in dp.get_project_species_behaviors(project.id, "deer")
    assert "stalking" in dp.get_project_species_behaviors(project.id, "fox")


def test_import_project_behaviors_species_specific(provider_with_project):
    dp, project, _ = provider_with_project
    dp.set_project_species(project.id, ["deer", "fox"])

    csv_content = "scientific_name;key;name_en\ndeer;stalking;Stalking\n"
    dp.import_project_behaviors_from_csv(project.id, csv_content)

    assert "stalking" in dp.get_project_species_behaviors(project.id, "deer")
    assert "stalking" not in dp.get_project_species_behaviors(project.id, "fox")


def test_import_project_behaviors_empty_csv_raises(provider_with_project):
    dp, project, _ = provider_with_project
    with pytest.raises(ValueError, match="No valid rows"):
        dp.import_project_behaviors_from_csv(project.id, "scientific_name;key\n")
