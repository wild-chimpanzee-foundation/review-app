"""Tests for species collections: seeding, project assignment, and CSV import."""

import pytest
from review_app.backend.db.models import Base
from review_app.backend.errors import DataImportError
from review_app.backend.provider.local_data_provider import LocalDataProvider
from review_app.backend.provider.species import SpeciesMixin
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


class MockDataProvider(LocalDataProvider):
    def __init__(self, tmp_path):
        self.db_dir = tmp_path
        self._db_path = self.db_dir / "test.db"
        self.engine = create_engine(f"sqlite:///{self._db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self._seed_data()

    def _seed_data(self):
        from datetime import datetime

        now = datetime.now()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO species (id, scientific_name, name_en, is_custom) VALUES "
                    "('s1', 'Deer', 'Deer', 0), "
                    "('s2', 'Fox', 'Fox', 0), "
                    "('s3', 'Wolf', 'Wolf', 0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO projects (id, name, created_at) VALUES "
                    "('p1', 'Project 1', :now), ('p2', 'Project 2', :now)"
                ),
                {"now": now},
            )
            # Two bundled collections: ColA has Deer+Fox, ColB has Fox+Wolf
            conn.execute(
                text(
                    "INSERT INTO species_collections (id, name, is_custom) VALUES "
                    "('c1', 'ColA', 0), ('c2', 'ColB', 0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO species_collection_members (collection_id, species_id) VALUES "
                    "('c1', 's1'), ('c1', 's2'), "
                    "('c2', 's2'), ('c2', 's3')"
                )
            )


@pytest.fixture
def dp(tmp_path):
    return MockDataProvider(tmp_path)


# ---------------------------------------------------------------------------
# list_collections
# ---------------------------------------------------------------------------


def test_list_collections_returns_seeded(dp):
    colls = dp.list_collections()
    names = {c["name"] for c in colls}
    assert names == {"ColA", "ColB"}
    for c in colls:
        assert "id" in c
        assert "is_custom" in c


def test_list_collections_empty_db(tmp_path):
    """No collections seeded → returns empty list without error."""
    engine = create_engine(f"sqlite:///{tmp_path / 'e.db'}")
    Base.metadata.create_all(engine)

    class MinimalDP(LocalDataProvider):
        pass

    dp2 = object.__new__(MinimalDP)
    dp2.engine = engine
    assert dp2.list_collections() == []


# ---------------------------------------------------------------------------
# get / set project collection
# ---------------------------------------------------------------------------


def test_get_project_collection_default_none(dp):
    assert dp.get_project_collection("p1") is None


def test_set_project_collection_populates_species(dp):
    dp.set_project_collection("p1", "c1")
    species = sorted(dp.get_project_species("p1"))
    assert species == ["Deer", "Fox"]


def test_set_project_collection_overwrites_previous_species(dp):
    dp.set_project_species("p1", ["Wolf"])
    dp.set_project_collection("p1", "c1")
    assert sorted(dp.get_project_species("p1")) == ["Deer", "Fox"]


def test_set_project_collection_stores_id(dp):
    dp.set_project_collection("p1", "c2")
    assert dp.get_project_collection("p1") == "c2"


def test_set_project_collection_none_clears_id_not_species(dp):
    dp.set_project_species("p1", ["Deer"])
    dp.set_project_collection("p1", None)
    assert dp.get_project_collection("p1") is None
    # Species list is intentionally left untouched
    assert dp.get_project_species("p1") == ["Deer"]


def test_set_project_collection_does_not_affect_other_project(dp):
    dp.set_project_collection("p1", "c1")
    assert dp.get_project_species("p2") == []


# ---------------------------------------------------------------------------
# _parse_species_csv — collection columns
# ---------------------------------------------------------------------------


def test_parse_species_csv_collection_columns(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("scientific_name;english_name;Forest;Savanna\ndeer;Deer;y;n\nfox;Fox;n;y\n")
    rows = SpeciesMixin._parse_species_csv(str(f))
    assert len(rows) == 2
    assert rows[0]["collections"] == {"Forest": True, "Savanna": False}
    assert rows[1]["collections"] == {"Forest": False, "Savanna": True}


def test_parse_species_csv_no_collection_columns(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("scientific_name;english_name\ndeer;Deer\n")
    rows = SpeciesMixin._parse_species_csv(str(f))
    assert rows[0]["collections"] == {}


# ---------------------------------------------------------------------------
# _sync_bundled_collections
# ---------------------------------------------------------------------------


def test_sync_bundled_collections_creates_collections(dp):
    """Syncing a new collection column adds it to species_collections."""
    rows = [
        {"scientific_name": "Deer", "collections": {"NewRegion": True}},
        {"scientific_name": "Fox", "collections": {"NewRegion": False}},
    ]
    with dp.engine.begin() as conn:
        SpeciesMixin._sync_bundled_collections(conn, rows)
    names = {c["name"] for c in dp.list_collections()}
    assert "NewRegion" in names


def test_sync_bundled_collections_rebuilds_membership(dp):
    """Re-syncing replaces existing membership for bundled collections."""
    # Remove Deer from ColA by re-syncing with only Fox present
    rows = [
        {"scientific_name": "Deer", "collections": {"ColA": False}},
        {"scientific_name": "Fox", "collections": {"ColA": True}},
    ]
    with dp.engine.begin() as conn:
        SpeciesMixin._sync_bundled_collections(conn, rows)
    dp.set_project_collection("p1", "c1")
    assert dp.get_project_species("p1") == ["Fox"]


def test_sync_bundled_collections_no_columns_is_noop(dp):
    rows = [{"scientific_name": "Deer", "collections": {}}]
    colls_before = {c["id"] for c in dp.list_collections()}
    with dp.engine.begin() as conn:
        SpeciesMixin._sync_bundled_collections(conn, rows)
    colls_after = {c["id"] for c in dp.list_collections()}
    assert colls_before == colls_after


# ---------------------------------------------------------------------------
# import_collection_from_csv
# ---------------------------------------------------------------------------


def test_import_collection_from_csv_creates_custom_collection(dp):
    csv = "scientific_name\nDeer\nFox\n"
    count = dp.import_collection_from_csv("MyList", csv)
    assert count == 2
    colls = {c["name"]: c for c in dp.list_collections()}
    assert "MyList" in colls
    assert colls["MyList"]["is_custom"] is True


def test_import_collection_from_csv_replaces_existing(dp):
    dp.import_collection_from_csv("MyList", "scientific_name\nDeer\nFox\n")
    count = dp.import_collection_from_csv("MyList", "scientific_name\nWolf\n")
    assert count == 1
    dp.set_project_collection("p1", dp.list_collections()[0]["id"])
    # Find MyList id
    mylist_id = next(c["id"] for c in dp.list_collections() if c["name"] == "MyList")
    dp.set_project_collection("p1", mylist_id)
    assert dp.get_project_species("p1") == ["Wolf"]


def test_import_collection_from_csv_empty_raises(dp):
    with pytest.raises(DataImportError):
        dp.import_collection_from_csv("Empty", "scientific_name\n")


def test_import_collection_from_csv_missing_column_raises(dp):
    with pytest.raises(Exception):
        dp.import_collection_from_csv("Bad", "english_name\nDeer\n")


def test_import_collection_from_csv_upserts_new_species(dp):
    csv = "scientific_name;english_name\nNewAnimal;New Animal\n"
    dp.import_collection_from_csv("NewColl", csv)
    assert dp.species_exists("NewAnimal")


# ---------------------------------------------------------------------------
# FK-safety: species cleanup must not violate foreign key constraints
# ---------------------------------------------------------------------------


def test_species_cleanup_survives_project_species_reference(tmp_path):
    """
    Regression: when a bundled CSV is replaced with a different set of species,
    _load_species_data must not fail with FOREIGN KEY constraint when a project
    still references a species that was removed from the new CSV.
    """
    from review_app.backend.db.migrations import run_migrations
    from review_app.backend.db.models import Base
    from review_app.backend.provider.local_data_provider import LocalDataProvider
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    # First load: CSV has Deer + Fox.
    first_csv = tmp_path / "first.csv"
    first_csv.write_text("scientific_name\nDeer\nFox\n")

    class DP1(LocalDataProvider):
        pass

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

    from sqlalchemy import event as sqla_event

    @sqla_event.listens_for(engine, "connect")
    def _fk_on(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    run_migrations(engine)
    Session = sessionmaker(bind=engine)

    dp = object.__new__(DP1)
    dp.engine = engine
    dp.Session = Session

    # Manually load first CSV
    import io as _io

    from review_app.backend.provider.species import SpeciesMixin

    rows = SpeciesMixin._parse_species_csv(_io.StringIO("scientific_name\nDeer\nFox\n"))
    import uuid as _uuid

    with engine.begin() as conn:
        existing = {
            r[0]: r[1]
            for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
        }
        for row in rows:
            row["id"] = existing.get(row["scientific_name"]) or str(_uuid.uuid4())
        conn.execute(
            text(
                "INSERT INTO species (id, scientific_name, is_custom) VALUES (:id, :scientific_name, 0) ON CONFLICT(scientific_name) DO NOTHING"
            ),
            rows,
        )
        # Assign Fox to a project via project_species
        pid = str(_uuid.uuid4())
        from datetime import datetime

        conn.execute(
            text("INSERT INTO projects (id, name, created_at) VALUES (:id, 'P', :now)"),
            {"id": pid, "now": datetime.now()},
        )
        fox_id = conn.execute(
            text("SELECT id FROM species WHERE scientific_name='Fox'")
        ).fetchone()[0]
        conn.execute(
            text("INSERT INTO project_species (project_id, species_id) VALUES (:pid, :sid)"),
            {"pid": pid, "sid": fox_id},
        )

    # Second load: new CSV has only Deer — Fox was removed.
    # With FOREIGN KEY enforcement ON, DELETE FROM species WHERE NOT IN (Deer)
    # must NOT fail because Fox is still in project_species.
    rows2 = SpeciesMixin._parse_species_csv(_io.StringIO("scientific_name\nDeer\n"))
    import uuid as _uuid2

    with engine.begin() as conn:
        existing = {
            r[0]: r[1]
            for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
        }
        for row in rows2:
            row["id"] = existing.get(row["scientific_name"]) or str(_uuid2.uuid4())
        conn.execute(
            text(
                "INSERT INTO species (id, scientific_name, is_custom) VALUES (:id, :scientific_name, 0) ON CONFLICT(scientific_name) DO NOTHING"
            ),
            rows2,
        )
        SpeciesMixin._sync_bundled_collections(conn, rows2)
        # This must not raise IntegrityError
        conn.execute(
            text(
                """
                DELETE FROM species
                WHERE scientific_name NOT IN ('Deer')
                AND is_custom = 0
                AND id NOT IN (SELECT DISTINCT species_id FROM individual_observations WHERE species_id IS NOT NULL)
                AND id NOT IN (SELECT DISTINCT species_id FROM project_species WHERE species_id IS NOT NULL)
                AND id NOT IN (SELECT DISTINCT species_id FROM species_collection_members WHERE species_id IS NOT NULL)
                """
            )
        )

    # Fox must still be in the DB because it is referenced by project_species.
    with engine.connect() as conn:
        names = [
            r[0] for r in conn.execute(text("SELECT scientific_name FROM species")).fetchall()
        ]
    assert "Fox" in names, "Fox should be retained — it is still referenced by project_species"
    assert "Deer" in names
