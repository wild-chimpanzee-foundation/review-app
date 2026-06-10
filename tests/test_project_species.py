import pytest
from review_app.backend.db.models import Base
from review_app.backend.provider.local_data_provider import LocalDataProvider


class MockDataProvider(LocalDataProvider):
    def __init__(self, tmp_path):
        self.db_dir = tmp_path
        self._db_path = self.db_dir / "test.db"
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        self.engine = create_engine(f"sqlite:///{self._db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._seed_data()

    def _seed_data(self):
        from datetime import datetime

        from sqlalchemy import text

        now = datetime.now()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO species (id, scientific_name, name_en, name_fr, is_custom) VALUES ('s1', 'Deer', 'Deer', 'Cerf', 0), ('s2', 'Fox', 'Fox', 'Renard', 0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO behaviors (id, key, name_en, name_fr, is_custom) VALUES ('b1', 'walking', 'Walking', 'Marche', 0), ('b2', 'eating', 'Eating', 'Mange', 0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO projects (id, name, created_at) VALUES ('p1', 'Project 1', :now), ('p2', 'Project 2', :now)"
                ),
                {"now": now},
            )


@pytest.fixture
def dp(tmp_path):
    return MockDataProvider(tmp_path)


def test_get_valid_species_fallback(dp):
    # No project species configured, should return all
    species = dp.get_valid_species("p1")
    assert sorted(species) == ["Deer", "Fox"]


def test_set_and_get_project_species(dp):
    dp.set_project_species("p1", ["Deer"])
    species = dp.get_valid_species("p1")
    assert species == ["Deer"]

    # Other project still falls back or has its own
    assert sorted(dp.get_valid_species("p2")) == ["Deer", "Fox"]


def test_get_species_display_map_project(dp):
    dp.set_project_species("p1", ["Fox"])
    display_map = dp.get_species_display_map(project_id="p1")
    assert list(display_map.keys()) == ["Fox"]


def test_behavior_display_map_global(dp):
    en_map = dp.get_behavior_display_map(lang="en")
    assert en_map["walking"] == "Walking"
    assert en_map["eating"] == "Eating"

    fr_map = dp.get_behavior_display_map(lang="fr")
    assert fr_map["walking"] == "Marche"
    assert fr_map["eating"] == "Mange"


def test_behavior_display_map_ignores_species_and_project(dp):
    # get_behavior_display_map is now global — species_name and project_id are ignored
    en_map = dp.get_behavior_display_map(lang="en", species_name="Deer", project_id="p1")
    assert "walking" in en_map
    assert "eating" in en_map


def _seed_groups_and_inat(dp):
    from sqlalchemy import text

    with dp.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE species SET group_en = 'Cervids', group_fr = 'Cervidés', "
                "inaturalist_url = 'https://www.inaturalist.org/taxa/deer' "
                "WHERE scientific_name = 'Deer'"
            )
        )


def test_species_catalog_project_scoped(dp):
    _seed_groups_and_inat(dp)
    dp.set_project_species("p1", ["Deer"])
    catalog = dp.get_species_catalog("en", project_id="p1")
    assert catalog.display == dp.get_species_display_map("en", project_id="p1")
    assert catalog.display == {"Deer": "Deer (Deer)"}
    assert catalog.groups == {"Deer": "Cervids"}
    assert catalog.global_display == dp.get_species_display_map("en")
    assert sorted(catalog.global_display) == ["Deer", "Fox"]
    assert catalog.inat == {"Deer": "https://www.inaturalist.org/taxa/deer"}

    fr_catalog = dp.get_species_catalog("fr", project_id="p1")
    assert fr_catalog.display == {"Deer": "Cerf (Deer)"}
    assert fr_catalog.groups == {"Deer": "Cervidés"}


def test_species_catalog_falls_back_without_project_species(dp):
    _seed_groups_and_inat(dp)
    # p2 has no species configured: project-scoped maps include everything
    catalog = dp.get_species_catalog("en", project_id="p2")
    assert sorted(catalog.display) == ["Deer", "Fox"]
    assert catalog.groups == {"Deer": "Cervids", "Fox": None}
    assert catalog.display == catalog.global_display
    assert catalog.inat == {"Deer": "https://www.inaturalist.org/taxa/deer"}


def test_species_catalog_without_project_id(dp):
    catalog = dp.get_species_catalog("en")
    assert sorted(catalog.display) == ["Deer", "Fox"]
    assert catalog.display == catalog.global_display
