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
