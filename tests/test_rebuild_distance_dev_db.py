"""The one-off converter retains ordinary data, not just its row counts."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime

import pytest
from review_app.backend.db.models import Base
from review_app.backend.provider.local_data_provider import LocalDataProvider
from scripts import rebuild_distance_dev_db as rebuild


@pytest.fixture
def original_database(distance_data, tmp_path):
    data = distance_data
    dp = data["dp"]
    video_id, reference_id = data["videos"]
    dp.update_manual_review(
        video_id,
        [{"species": "deer", "tags": ["grazing"], "count": 3, "labeled_by": "alice"}],
        is_blank=False,
    )
    dp.set_review_later(video_id, True)
    key = dp.create_custom_tag("Évidence spéciale", name_fr="Preuve\nà garder")
    dp.set_tag_individual_eligibility(key, True)
    dp.set_video_tags(video_id, [key])
    dp.set_video_tags(reference_id, ["reference_video"])
    dp.set_setting("custom", "Unicode é / quotes '\" / newline\nretained")
    dp.verify_distance_recording_time(video_id, datetime(2024, 1, 1), "alice")
    dp.set_distance_active_individual(video_id, "alice", data["individual"]["id"])
    dp.save_distance_sample(
        data["individual"]["id"],
        2000,
        reference_frame_id=data["frame"]["id"],
        behavior_ids=[data["behavior_id"]],
        create_infant=True,
        labeled_by="alice",
    )
    with dp.engine.begin() as conn:
        dp.set_assignments_bulk(conn, {video_id: "alice"})
        conn.execute(
            Base.metadata.tables["model_annotations"].insert(),
            {
                "id": "prediction",
                "video_id": video_id,
                "project_id": data["project"].id,
                "annotation_type": "species",
                "model_name": "test-model",
                "value_text": "deer",
                "probability": 0.95,
            },
        )
        conn.execute(
            Base.metadata.tables["species_collections"].insert(),
            {"id": "collection", "name": "Custom", "is_custom": True},
        )
        conn.execute(
            Base.metadata.tables["species_collection_members"].insert(),
            {"collection_id": "collection", "species_id": data["species_id"]},
        )
        conn.execute(
            Base.metadata.tables["project_species"].insert(),
            {"project_id": data["project"].id, "species_id": data["species_id"]},
        )
        conn.exec_driver_sql("UPDATE projects SET collection_id='collection'")
        conn.exec_driver_sql(
            "INSERT INTO distance_individual_tags SELECT ?, id, 'alice', '2024-01-01' "
            "FROM tags WHERE key=?",
            (data["individual"]["id"], key),
        )
    path = tmp_path / "original-v29.db"
    with closing(sqlite3.connect(dp.db_path)) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
        # The private v29 predates parity-only alignment. Do not open this
        # source with the provider.
        dst.execute("ALTER TABLE distance_video_reviews DROP COLUMN alignment_mode")
        dst.execute("UPDATE _schema_version SET version=29")
        dst.commit()
    return path


def _ordinary_contents(path):
    with closing(sqlite3.connect(path)) as conn:
        return {
            name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
            for name in Base.metadata.tables
            if name not in rebuild.DISTANCE_TABLES
        }


def test_dry_run_is_read_only_and_reports_retained_and_discarded(original_database, tmp_path):
    output = tmp_path / "rebuilt.db"
    before = original_database.read_bytes()
    report = rebuild.rebuild_database(original_database, output)
    assert not report["executed"]
    assert not output.exists()
    assert original_database.read_bytes() == before
    assert report["retained"]["individual_observations"]["rows"] == 1
    assert report["discarded"]["distance_individuals"] == 2
    assert report["discarded"]["reference_frames"] == 1


def test_rebuild_preserves_every_ordinary_value_and_resets_distance(original_database, tmp_path):
    output = tmp_path / "rebuilt.db"
    before = original_database.read_bytes()
    report = rebuild.rebuild_database(original_database, output, execute=True)
    assert report["executed"]
    assert original_database.read_bytes() == before
    expected = _ordinary_contents(original_database)
    assert all(expected.values()), "The fixture should exercise every retained table"
    assert _ordinary_contents(output) == expected
    with closing(sqlite3.connect(output)) as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchall() == [
            (len(rebuild.MIGRATIONS),)
        ]
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        for name in rebuild.DISTANCE_TABLES:
            assert conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == 0


def test_result_opens_normally_and_keeps_ordinary_queue(original_database, tmp_path, monkeypatch):
    output = tmp_path / "target" / "review_data.db"
    output.parent.mkdir()
    rebuild.rebuild_database(original_database, output, execute=True)
    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: output.parent
    )
    dp = LocalDataProvider()
    try:
        queue = dp.get_video_queue({}, active_project_id=None)
        assert len(queue) == 2
        with dp.engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT count FROM individual_observations").scalar() == 3
            assert conn.exec_driver_sql("SELECT COUNT(*) FROM distance_individuals").scalar() == 0
            assert (
                conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM video_tags JOIN tags ON tags.id=video_tags.tag_id "
                    "WHERE tags.key='reference_video'"
                ).scalar()
                == 1
            )
    finally:
        dp.engine.dispose()


@pytest.mark.parametrize("execute", [False, True])
def test_source_and_existing_output_are_never_overwritten(original_database, tmp_path, execute):
    with pytest.raises(ValueError, match="different files"):
        rebuild.rebuild_database(original_database, original_database, execute=execute)
    output = tmp_path / "existing.db"
    output.write_bytes(b"not yours to change")
    with pytest.raises(FileExistsError):
        rebuild.rebuild_database(original_database, output, execute=execute)
    assert output.read_bytes() == b"not yours to change"


@pytest.mark.parametrize("unexpected", ["table", "column", "version", "foreign_key"])
def test_unrecognized_or_invalid_source_fails_closed(original_database, tmp_path, unexpected):
    with closing(sqlite3.connect(original_database)) as conn:
        if unexpected == "table":
            conn.execute("CREATE TABLE unrecognized_work (id INTEGER)")
        elif unexpected == "column":
            conn.execute("ALTER TABLE videos ADD COLUMN unrecognized_work TEXT")
        elif unexpected == "version":
            conn.execute("UPDATE _schema_version SET version=28")
        else:
            conn.execute("UPDATE individual_observations SET species_id='missing'")
        conn.commit()
    output = tmp_path / "rebuilt.db"
    with pytest.raises(ValueError):
        rebuild.rebuild_database(original_database, output, execute=True)
    assert not output.exists()


def test_failure_cleans_partial_output_but_keeps_source(original_database, tmp_path, monkeypatch):
    before = original_database.read_bytes()
    output = tmp_path / "rebuilt.db"

    def fail_after_copy(source, target):
        target.execute("INSERT INTO app_settings VALUES ('partial', 'do not keep')")
        raise RuntimeError("copy failed")

    monkeypatch.setattr(rebuild, "_copy_retained", fail_after_copy)
    with pytest.raises(RuntimeError, match="copy failed"):
        rebuild.rebuild_database(original_database, output, execute=True)
    assert not output.exists()
    assert not list(tmp_path.glob("rebuilt.db-*"))
    assert original_database.read_bytes() == before
