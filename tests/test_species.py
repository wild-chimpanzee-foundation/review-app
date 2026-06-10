"""
Tests for SpeciesMixin (review_app/backend/species.py):
CSV parsing, fuzzy matching, custom species/behaviors, project overrides,
and CSV-driven project import.
"""

import pytest
from review_app.backend.errors import DataImportError, SpeciesError
from review_app.backend.provider.local_data_provider import LocalDataProvider
from review_app.backend.provider.species import SpeciesMixin

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
    with pytest.raises(SpeciesError, match="scientific_name"):
        SpeciesMixin._parse_species_csv(str(f))


def test_parse_species_csv_inaturalist_column(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text(
        "scientific_name;english_name;inaturalist\n"
        "deer;Red Deer; https://www.inaturalist.org/taxa/42 \n"
        "fox;Red Fox;\n"
        "boar;Wild Boar;   \n"
    )
    rows = SpeciesMixin._parse_species_csv(str(f))
    assert rows[0]["inaturalist_url"] == "https://www.inaturalist.org/taxa/42"
    assert rows[1]["inaturalist_url"] is None  # empty cell (NaN)
    assert rows[2]["inaturalist_url"] is None  # whitespace-only cell
    # inaturalist is a base column, not a collection membership flag
    assert all(r["collections"] == {} for r in rows)


def test_parse_species_csv_without_inaturalist_column(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("scientific_name;english_name\ndeer;Red Deer\n")
    rows = SpeciesMixin._parse_species_csv(str(f))
    assert rows[0]["inaturalist_url"] is None


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


def test_behavior_display_map_global_always(tmp_db):
    dp = LocalDataProvider()
    # species_name is ignored — map is always global
    m = dp.get_behavior_display_map(lang="en", species_name="deer")
    assert "grazing" in m
    assert "running" in m  # all behaviors are now global


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
    with pytest.raises(DataImportError, match="No valid rows"):
        dp.import_project_species_from_csv(project.id, "scientific_name\n")


# ---------------------------------------------------------------------------
# import_project_behaviors_from_csv
# ---------------------------------------------------------------------------


def test_import_project_behaviors_adds_custom_behaviors(provider_with_project):
    """import_project_behaviors_from_csv adds unknown keys as custom global behaviors."""
    dp, project, _ = provider_with_project

    csv_content = "scientific_name;key;name_en\n*;stalking;Stalking\n"
    added = dp.import_project_behaviors_from_csv(project.id, csv_content)
    assert added == 1

    m = dp.get_behavior_display_map(lang="en")
    assert "stalking" in m


def test_import_project_behaviors_skips_existing(provider_with_project):
    dp, project, _ = provider_with_project

    # grazing and running are seeded in tmp_db fixture
    csv_content = "scientific_name;key;name_en\n*;grazing;Grazing\n"
    added = dp.import_project_behaviors_from_csv(project.id, csv_content)
    assert added == 0  # already exists


def test_import_project_behaviors_empty_csv_raises(provider_with_project):
    dp, project, _ = provider_with_project
    with pytest.raises(DataImportError, match="No valid rows"):
        dp.import_project_behaviors_from_csv(project.id, "scientific_name;key\n")


# ---------------------------------------------------------------------------
# iNaturalist URL loading from the bundled CSV
# ---------------------------------------------------------------------------


def test_load_species_data_upserts_inaturalist_url(tmp_db):
    # The tmp_db fixture CSV has no inaturalist column.
    dp = LocalDataProvider()
    assert dp.get_species_catalog("en").inat == {}
    with dp.engine.connect() as conn:
        from sqlalchemy import text

        deer_id_before = conn.execute(
            text("SELECT id FROM species WHERE scientific_name = 'deer'")
        ).scalar()

    # Add the column to the bundled CSV; the next provider init must upsert it.
    (tmp_db["root"] / "species.csv").write_text(
        "scientific_name;english_name;inaturalist\n"
        "deer;Red Deer;https://www.inaturalist.org/taxa/42\n"
        "fox;Red Fox;\n"
    )
    dp2 = LocalDataProvider()
    catalog = dp2.get_species_catalog("en")
    assert catalog.inat == {"deer": "https://www.inaturalist.org/taxa/42"}

    with dp2.engine.connect() as conn:
        from sqlalchemy import text

        deer_id_after = conn.execute(
            text("SELECT id FROM species WHERE scientific_name = 'deer'")
        ).scalar()
    assert deer_id_after == deer_id_before, "upsert must preserve existing species IDs"


def test_load_species_data_clears_removed_inaturalist_url(tmp_db):
    (tmp_db["root"] / "species.csv").write_text(
        "scientific_name;english_name;inaturalist\n"
        "deer;Red Deer;https://www.inaturalist.org/taxa/42\n"
        "fox;Red Fox;\n"
    )
    dp = LocalDataProvider()
    assert dp.get_species_catalog("en").inat == {"deer": "https://www.inaturalist.org/taxa/42"}

    # Removing the URL from the CSV must clear it on the next load.
    (tmp_db["root"] / "species.csv").write_text(
        "scientific_name;english_name;inaturalist\ndeer;Red Deer;\nfox;Red Fox;\n"
    )
    dp2 = LocalDataProvider()
    assert dp2.get_species_catalog("en").inat == {}
