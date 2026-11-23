import errno
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from review_app.backend.db import migrations
from review_app.backend.db.backup import (
    BACKUP_TIMESTAMP_FORMAT,
    DAILY_RETENTION_DAYS,
    MONTHLY_RETENTION_MONTHS,
    RECENT_KEEP_COUNT,
    WEEKLY_RETENTION_WEEKS,
    BackupCopyError,
    BackupDBNotFoundError,
    BackupError,
    BackupVacuumError,
    RestoreCopyError,
    RestoreCorruptError,
    RestoreFileNotFoundError,
    RestoreRemoveError,
    RestoreSchemaVersionError,
    create_backup,
    get_backup_dir,
    list_backups,
    prune_backups,
    quarantine_broken_db,
    read_schema_version,
    recover_interrupted_restore,
    remove_db_sidecars,
    restore_backup,
)
from review_app.backend.db.migrations import MIGRATIONS, run_migrations
from review_app.backend.db.models import Base
from sqlalchemy import create_engine


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    backup_dir = db_dir / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("review_app.backend.db.backup.get_user_data_dir", lambda: db_dir)
    monkeypatch.setattr("review_app.app.config.get_user_data_dir", lambda: db_dir)

    db_path = db_dir / "review_data.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO test_table (name) VALUES ('hello')")
    con.execute("CREATE TABLE _schema_version (version INTEGER)")
    con.execute("INSERT INTO _schema_version VALUES (3)")
    con.commit()
    con.close()

    yield {"db_dir": db_dir, "db_path": db_path, "backup_dir": backup_dir}


def _make_backup_file(backup_dir, timestamp_str, size_bytes=1024):
    name = f"review_backup_{timestamp_str}.db"
    p = backup_dir / name
    p.write_bytes(os.urandom(size_bytes))
    return p


class TestCreateBackup:
    def test_creates_backup_file(self, workspace):
        result = create_backup(reason="test")
        assert result.exists()
        assert result.name.startswith("review_backup_")
        assert result.name.endswith(".db.gz")

    def test_backup_is_valid_sqlite(self, workspace):
        import gzip
        import tempfile

        result = create_backup(reason="test")
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            with gzip.open(result, "rb") as gz:
                tmp.write(gz.read())
            tmp.flush()
            con = sqlite3.connect(tmp.name)
            try:
                rows = con.execute("SELECT name FROM test_table").fetchall()
                assert rows[0][0] == "hello"
            finally:
                con.close()

    def test_raises_when_db_missing(self, workspace, monkeypatch):
        monkeypatch.setattr(
            "review_app.backend.db.backup.get_user_data_dir",
            lambda: workspace["db_dir"] / "nonexistent",
        )
        with pytest.raises(BackupDBNotFoundError):
            create_backup(reason="test")

    def test_raises_vacuum_error_on_failure(self, workspace, monkeypatch):
        """No fallback path: VACUUM INTO failure surfaces as BackupVacuumError."""

        class BadConn:
            def execute(self, *a, **kw):
                raise RuntimeError("forced vacuum failure")

            def close(self):
                pass

        monkeypatch.setattr(
            "review_app.backend.db.backup.sqlite3.connect", lambda *a, **kw: BadConn()
        )
        with pytest.raises(BackupVacuumError):
            create_backup(reason="test")

    def test_cleans_up_partial_file_on_failure(self, workspace, monkeypatch):
        """A failed VACUUM should not leave a stale empty file behind."""
        before = set(workspace["backup_dir"].iterdir())

        class BadConn:
            def execute(self, sql, params):
                assert sql == "VACUUM INTO ?"
                Path(params[0]).write_bytes(b"")
                assert not list_backups()
                raise RuntimeError("boom")

            def close(self):
                pass

        monkeypatch.setattr(
            "review_app.backend.db.backup.sqlite3.connect", lambda *a, **kw: BadConn()
        )
        with pytest.raises(BackupVacuumError):
            create_backup(reason="test")
        assert set(workspace["backup_dir"].iterdir()) == before

    def test_background_compress_returns_raw_db_then_gzips(self, workspace):
        import time

        result = create_backup(reason="test", compress="background")
        assert result.suffix == ".db", "background mode must return before compression"
        assert result.exists() or result.with_suffix(".db.gz").exists()

        gz_path = Path(str(result) + ".gz")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if gz_path.exists() and not result.exists():
                break
            time.sleep(0.05)
        assert gz_path.exists(), "compression thread should produce the .db.gz"
        assert not result.exists(), "raw .db should be removed after compression"

        # Compressed backup must still be a restorable database
        version = read_schema_version(gz_path)
        assert version == 3

    def test_backup_if_stale_creates_then_skips(self, workspace):
        from review_app.backend.db.backup import backup_if_stale

        assert backup_if_stale(reason="test") is True
        assert backup_if_stale(reason="test") is False, "fresh backup exists — must skip"
        assert backup_if_stale(max_age_seconds=0, reason="test") is True

    def test_backup_if_stale_forwards_compression_mode(self, workspace):
        from review_app.backend.db.backup import backup_if_stale

        with patch("review_app.backend.db.backup.create_backup") as create:
            assert backup_if_stale(reason="pre_update", compress="sync") is True

        create.assert_called_once_with(reason="pre_update", compress="sync")

    def test_backup_if_stale_can_propagate_backup_errors(self, workspace):
        from review_app.backend.db.backup import backup_if_stale

        error = BackupVacuumError("failed")
        with patch("review_app.backend.db.backup.create_backup", side_effect=error):
            assert backup_if_stale(reason="test") is False

        with (
            patch("review_app.backend.db.backup.create_backup", side_effect=error),
            pytest.raises(BackupVacuumError, match="failed"),
        ):
            backup_if_stale(reason="test", raise_on_error=True)

    def test_backup_filename_includes_schema_version(self, workspace):
        result = create_backup(reason="test")
        assert "_v3" in result.name

    def test_user_message_keys(self):
        assert BackupDBNotFoundError("/foo").user_message_key == "backup_error_db_not_found"
        assert BackupCopyError("x").user_message_key == "backup_error_copy_failed"
        assert BackupVacuumError("x").user_message_key == "backup_error_vacuum_failed"
        assert RestoreFileNotFoundError("/foo").user_message_key == "restore_error_file_not_found"
        assert RestoreRemoveError("/foo").user_message_key == "restore_error_remove_failed"
        assert RestoreCopyError("x").user_message_key == "restore_error_copy_failed"
        assert (
            RestoreSchemaVersionError("v5>v4").user_message_key == "restore_error_schema_version"
        )
        assert RestoreCorruptError("x").user_message_key == "restore_error_corrupt"

    def test_exception_hierarchy(self):
        for cls in (
            BackupDBNotFoundError,
            BackupCopyError,
            BackupVacuumError,
            RestoreFileNotFoundError,
            RestoreRemoveError,
            RestoreCopyError,
            RestoreSchemaVersionError,
            RestoreCorruptError,
        ):
            assert issubclass(cls, BackupError)

    def test_exception_carries_detail(self):
        exc = BackupDBNotFoundError("/some/path.db")
        assert exc.detail == "/some/path.db"
        assert str(exc) == "/some/path.db"


class TestListBackups:
    def test_empty_directory(self, workspace):
        assert list_backups() == []

    def test_lists_backups_newest_first(self, workspace):
        ts1 = "20250101_120000"
        ts2 = "20250102_120000"
        _make_backup_file(workspace["backup_dir"], ts1)
        _make_backup_file(workspace["backup_dir"], ts2)

        backups = list_backups()
        assert len(backups) == 2
        assert backups[0]["timestamp"] > backups[1]["timestamp"]
        assert ts2 in backups[0]["name"]
        assert ts1 in backups[1]["name"]

    def test_lists_one_entry_per_snapshot_with_both_formats(self, workspace):
        directory = workspace["backup_dir"]
        raw = _make_backup_file(directory, "20250101_120000_v3")
        compressed = raw.with_suffix(".db.gz")
        raw.rename(compressed)
        raw.write_bytes(b"uncompressed")
        separate = _make_backup_file(directory, "20250101_120100_v3")
        compressed.with_suffix(".gz.tmp").touch()

        assert [item["path"] for item in list_backups()] == [separate, compressed]

    def test_timestamps_are_utc_aware(self, workspace):
        _make_backup_file(workspace["backup_dir"], "20250101_120000")
        backups = list_backups()
        assert backups[0]["timestamp"].tzinfo == timezone.utc

    def test_lists_backups_includes_schema_version(self, workspace):
        _make_backup_file(workspace["backup_dir"], "20250101_120000")
        _make_backup_file(workspace["backup_dir"], "20250101_130000_v4")

        backups = list_backups()
        versions = {b["schema_version"] for b in backups}
        assert versions == {None, 4}

    def test_ignores_malformed_filenames(self, workspace):
        _make_backup_file(workspace["backup_dir"], "20250101_120000")
        (workspace["backup_dir"] / "review_backup_badname.db").write_bytes(b"x")
        assert len(list_backups()) == 1


class TestPruneBackups:
    def _populate(self, backup_dir, now, day_offsets):
        for d in day_offsets:
            ts = (now - timedelta(days=d)).strftime(BACKUP_TIMESTAMP_FORMAT)
            _make_backup_file(backup_dir, ts, size_bytes=64)

    def test_no_pruning_when_under_limit(self, workspace):
        _make_backup_file(workspace["backup_dir"], "20250101_120000")
        assert prune_backups() == 0

    def test_keeps_recent_n(self, workspace):
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 20 backups all clustered today — daily/weekly/monthly only catch one bucket each
        for i in range(20):
            ts = (now - timedelta(minutes=i)).strftime(BACKUP_TIMESTAMP_FORMAT)
            _make_backup_file(workspace["backup_dir"], ts, size_bytes=64)

        with patch("review_app.backend.db.backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime = datetime.strptime
            prune_backups()

        # All 20 are within the same day/week/month → one bucket pick each, plus RECENT_KEEP_COUNT
        # most recent. The most-recent set already covers the bucket picks, so result == RECENT_KEEP_COUNT.
        assert len(list_backups()) == RECENT_KEEP_COUNT

    def test_keeps_daily_milestones(self, workspace):
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # 3 backups per day for DAILY_RETENTION_DAYS + 2 days
        for day in range(DAILY_RETENTION_DAYS + 2):
            for hour in range(3):
                ts = (now - timedelta(days=day, hours=hour * 8)).strftime(BACKUP_TIMESTAMP_FORMAT)
                _make_backup_file(workspace["backup_dir"], ts, size_bytes=64)

        with patch("review_app.backend.db.backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime = datetime.strptime
            prune_backups()

        kept_days = {b["timestamp"].strftime("%Y-%m-%d") for b in list_backups()}
        for day in range(DAILY_RETENTION_DAYS + 1):
            d = (now - timedelta(days=day)).strftime("%Y-%m-%d")
            assert d in kept_days, f"day {d} should be preserved"

    def test_keeps_weekly_and_monthly_tiers(self, workspace):
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # One backup per day for an entire year
        for day in range(365):
            ts = (now - timedelta(days=day)).strftime(BACKUP_TIMESTAMP_FORMAT)
            _make_backup_file(workspace["backup_dir"], ts, size_bytes=64)

        with patch("review_app.backend.db.backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime = datetime.strptime
            prune_backups()

        remaining = list_backups()
        # Bounded: RECENT_KEEP_COUNT + ~DAILY + ~WEEKLY + ~MONTHLY, with overlap
        upper = (
            RECENT_KEEP_COUNT
            + DAILY_RETENTION_DAYS
            + WEEKLY_RETENTION_WEEKS
            + MONTHLY_RETENTION_MONTHS
            + 5
        )
        assert len(remaining) < upper
        # Far older than monthly retention → pruned
        assert len(remaining) < 365

        weeks = {
            f"{b['timestamp'].isocalendar()[0]}-W{b['timestamp'].isocalendar()[1]:02d}"
            for b in remaining
        }
        months = {b["timestamp"].strftime("%Y-%m") for b in remaining}
        assert len(weeks) >= WEEKLY_RETENTION_WEEKS
        assert len(months) >= MONTHLY_RETENTION_MONTHS - 1  # boundary tolerance


class TestRestoreBackup:
    @pytest.fixture(autouse=True)
    def install_review_app_schema(self, workspace):
        engine = create_engine(f"sqlite:///{workspace['db_path']}")
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("DROP TABLE _schema_version")
            Base.metadata.create_all(engine)
            run_migrations(engine)
        finally:
            engine.dispose()

    def test_restores_successfully(self, workspace):
        backup_path = create_backup(reason="pre")
        # Mutate live DB so we can verify restore reverts it
        con = sqlite3.connect(str(workspace["db_path"]))
        con.execute("UPDATE test_table SET name = 'changed'")
        con.commit()
        con.close()

        restore_backup(backup_path, None, reopen=lambda: None)

        con = sqlite3.connect(str(workspace["db_path"]))
        try:
            rows = con.execute("SELECT name FROM test_table").fetchall()
            assert rows[0][0] == "hello"
        finally:
            con.close()

    def test_selected_plain_snapshot_can_finish_compressing(self, workspace):
        compressed = create_backup(reason="test")
        previously_listed = compressed.with_suffix("")
        assert not previously_listed.exists()
        restore_backup(previously_listed, None, reopen=lambda: None)

    def test_raises_when_backup_missing(self, workspace):
        with pytest.raises(RestoreFileNotFoundError):
            restore_backup(Path("/nonexistent/backup.db"), None, reopen=lambda: None)

    def test_pre_restore_safety_backup_created(self, workspace):
        backup_path = create_backup(reason="test")
        initial_count = len(list_backups())
        restore_backup(backup_path, None, reopen=lambda: None)
        assert any(item["path"].suffix == ".db" for item in list_backups())
        assert len(list_backups()) > initial_count

    def test_returns_none_when_no_live_db(self, workspace):
        """Wizard restore: no live DB exists yet."""
        backup_path = create_backup(reason="seed")
        workspace["db_path"].unlink()

        result = restore_backup(
            backup_path, app_schema_version=len(MIGRATIONS), reopen=lambda: None
        )
        assert result is None
        assert workspace["db_path"].exists()

    def test_raises_schema_version_error(self, workspace):
        backup_path = workspace["backup_dir"] / "review_backup_20250101_120000_v99.db"
        con = sqlite3.connect(str(backup_path))
        con.execute("CREATE TABLE _schema_version (version INTEGER)")
        con.execute("INSERT INTO _schema_version VALUES (99)")
        con.commit()
        con.close()

        with pytest.raises(RestoreSchemaVersionError):
            restore_backup(backup_path, None, reopen=lambda: None)

    def test_raises_on_corrupt_backup(self, workspace):
        bad = workspace["backup_dir"] / "review_backup_20250101_120000_v3.db"
        bad.write_bytes(b"not a sqlite file at all")
        with pytest.raises(RestoreCorruptError):
            restore_backup(bad, None, reopen=lambda: None)

    def test_rejects_foreign_key_violations_before_cutover(self, workspace):
        candidate = workspace["backup_dir"] / "invalid.db"
        with sqlite3.connect(candidate) as con:
            con.executescript("""
                CREATE TABLE parent (id INTEGER PRIMARY KEY);
                CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));
                INSERT INTO child VALUES (42);
            """)
        original = workspace["db_path"].read_bytes()

        with pytest.raises(RestoreCorruptError, match="foreign_key_check"):
            restore_backup(candidate, None, reopen=lambda: None)

        assert workspace["db_path"].read_bytes() == original
        assert not list_backups()

    def test_rejects_unrelated_sqlite_database(self, workspace):
        candidate = workspace["backup_dir"] / "unrelated.db"
        with closing(sqlite3.connect(candidate)) as con:
            con.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY)")
        original = workspace["db_path"].read_bytes()

        with pytest.raises(RestoreCorruptError, match="supported Review App schema"):
            restore_backup(candidate, None, reopen=lambda: None)

        assert workspace["db_path"].read_bytes() == original
        assert not list_backups()

    @pytest.mark.parametrize("stamped", [True, False])
    def test_upgrades_legacy_backup_before_reopen(self, workspace, stamped):
        candidate = workspace["backup_dir"] / "legacy.db"
        fixture = Path(__file__).parent / "fixtures" / "schema_v3.sql"
        with closing(sqlite3.connect(candidate)) as con, con:
            con.executescript(fixture.read_text())
            if not stamped:
                con.execute("DROP TABLE _schema_version")
            con.executescript("""
                INSERT INTO projects (id, name, created_at) VALUES ('p', 'Project', '2024-01-01');
                INSERT INTO videos (video_id, project_id, video_path, last_seen_at)
                    VALUES ('v', 'p', 'footage.mp4', '2024-01-01');
                INSERT INTO species (scientific_name, name_en, project_id)
                    VALUES ('Example species', 'Example', 'p');
                INSERT INTO individual_observations
                    (video_id, id, project_id, species, behavior, start_sec, updated_at)
                    VALUES ('v', 7, 'p', 'Example species', 'foraging', 12, '2024-01-01');
            """)
        original = candidate.read_bytes()

        def check_reopened():
            with closing(sqlite3.connect(workspace["db_path"])) as con:
                assert con.execute("SELECT version FROM _schema_version").fetchone() == (
                    len(MIGRATIONS),
                )
                assert con.execute("""
                    SELECT o.id, o.project_id, o.start_sec, s.scientific_name, b.key
                    FROM individual_observations o
                    JOIN species s ON s.id = o.species_id
                    JOIN observation_tags t ON (t.video_id, t.observation_id) = (o.video_id, o.id)
                    JOIN behaviors b ON b.id = t.behavior_id
                """).fetchone() == (7, "p", 12, "Example species", "foraging")

                assert con.execute("""
                    SELECT p.project_id, s.scientific_name
                    FROM project_species p JOIN species s ON p.species_id = s.id
                """).fetchone() == ("p", "Example species")

        restore_backup(candidate, None, reopen=check_reopened)

        assert candidate.read_bytes() == original

    def test_failed_staged_migration_keeps_original_database(self, workspace, monkeypatch):
        candidate = workspace["backup_dir"] / "legacy.db"
        fixture = Path(__file__).parent / "fixtures" / "schema_v3.sql"
        with closing(sqlite3.connect(candidate)) as con:
            con.executescript(fixture.read_text())
        original_candidate = candidate.read_bytes()
        original_db = workspace["db_path"].read_bytes()

        def fail_migration(conn):
            conn.exec_driver_sql("CREATE TABLE partial_upgrade (id INTEGER PRIMARY KEY)")
            raise ValueError("injected migration failure")

        monkeypatch.setattr(
            migrations,
            "MIGRATIONS",
            [(v, fail_migration if v == 4 else step) for v, step in MIGRATIONS],
        )

        def unexpected_reopen():
            pytest.fail("Restore attempted cutover after a failed migration")

        with pytest.raises(RestoreCorruptError, match="injected migration failure"):
            restore_backup(candidate, None, reopen=unexpected_reopen)

        assert workspace["db_path"].read_bytes() == original_db
        assert candidate.read_bytes() == original_candidate
        assert not list_backups()
        assert not list(workspace["db_dir"].glob(".*.restoring"))

    def test_staging_disk_failure_leaves_live_database_intact(self, workspace, monkeypatch):
        candidate = create_backup(reason="test")
        db_path = workspace["db_path"]
        original = db_path.read_bytes()

        def fail_copy(source, target):
            target.write(source.read(16))
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr("review_app.backend.db.backup.shutil.copyfileobj", fail_copy)

        with pytest.raises(RestoreCopyError, match="No space left on device"):
            restore_backup(candidate, None, reopen=lambda: None)

        assert db_path.read_bytes() == original
        assert not list(db_path.parent.glob(".*.restoring"))

    @pytest.mark.parametrize("interrupted", [False, True])
    def test_failed_reopen_recovers_original_wal_records(self, workspace, interrupted):
        candidate = create_backup(reason="test")
        db_path = workspace["db_path"]
        rollback = db_path.with_suffix(".db.rollback")
        failure = KeyboardInterrupt if interrupted else RuntimeError

        def fail_reopen():
            assert rollback.read_bytes().startswith(b"SQLite format 3")
            with closing(sqlite3.connect(db_path)) as reopened:
                assert reopened.execute("SELECT name FROM test_table").fetchone() == ("hello",)
            raise failure("reopen failed")

        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import os
import sqlite3
import sys

con = sqlite3.connect(sys.argv[1])
con.execute("PRAGMA journal_mode=WAL")
con.execute("UPDATE test_table SET name = 'original'")
con.commit()
os._exit(0)
""",
                str(db_path),
            ],
            check=True,
        )
        assert Path(str(db_path) + "-wal").stat().st_size > 0
        with pytest.raises(failure, match="reopen failed"):
            restore_backup(candidate, None, reopen=fail_reopen)

        assert rollback.exists() == interrupted
        recover_interrupted_restore()
        recover_interrupted_restore()
        assert not rollback.exists()
        with sqlite3.connect(db_path) as con:
            assert con.execute("SELECT name FROM test_table").fetchone() == ("original",)

    def test_cleans_sidecars_before_swap(self, workspace):
        backup_path = create_backup(reason="pre")
        # Plant fake sidecars
        Path(str(workspace["db_path"]) + "-wal").write_bytes(b"stale wal")
        Path(str(workspace["db_path"]) + "-shm").write_bytes(b"stale shm")

        restore_backup(backup_path, None, reopen=lambda: None)

        assert not Path(str(workspace["db_path"]) + "-wal").exists()
        assert not Path(str(workspace["db_path"]) + "-shm").exists()

    def test_keeps_live_records_on_swap_failure(self, workspace, monkeypatch):
        """If replacement fails, the original database remains readable."""
        backup_path = create_backup(reason="pre")
        # Sentinel value in the live DB
        con = sqlite3.connect(str(workspace["db_path"]))
        con.execute("UPDATE test_table SET name = 'sentinel'")
        con.commit()
        con.close()

        replace = os.replace

        def fail_candidate_swap(source, destination):
            if source.suffix == ".restoring" and destination == workspace["db_path"]:
                raise OSError("forced swap failure")
            replace(source, destination)

        monkeypatch.setattr("review_app.backend.db.backup.os.replace", fail_candidate_swap)

        with pytest.raises(RestoreCopyError):
            restore_backup(backup_path, None, reopen=lambda: None)

        # Verify the original database still contains the sentinel.
        con = sqlite3.connect(str(workspace["db_path"]))
        try:
            rows = con.execute("SELECT name FROM test_table").fetchall()
            assert rows[0][0] == "sentinel"
        finally:
            con.close()


class TestQuarantineBrokenDb:
    def test_moves_db_to_backup_dir(self, workspace):
        result = quarantine_broken_db()
        assert result is not None
        assert result.parent == workspace["backup_dir"]
        assert not workspace["db_path"].exists()
        # Visible to list_backups so users can inspect/restore from the quarantine
        names = [b["name"] for b in list_backups()]
        assert result.name in names

    def test_returns_none_when_no_db(self, workspace):
        workspace["db_path"].unlink()
        assert quarantine_broken_db() is None

    def test_cleans_sidecars(self, workspace):
        wal = Path(str(workspace["db_path"]) + "-wal")
        wal.write_bytes(b"stale")
        quarantine_broken_db()
        assert not wal.exists()


class TestRemoveDbSidecars:
    def test_removes_all_sidecars(self, tmp_path):
        db = tmp_path / "x.db"
        for ext in ("-wal", "-shm", "-journal"):
            Path(str(db) + ext).write_bytes(b"data")
        remove_db_sidecars(db)
        for ext in ("-wal", "-shm", "-journal"):
            assert not Path(str(db) + ext).exists()

    def test_no_op_when_absent(self, tmp_path):
        # Should not raise
        remove_db_sidecars(tmp_path / "missing.db")


class TestSchemaVersionCheck:
    def test_read_schema_version_returns_none_for_missing_table(self, tmp_path):
        db = tmp_path / "plain.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE foo (id INTEGER)")
        con.close()
        assert read_schema_version(db) is None

    def test_read_schema_version_returns_version(self, tmp_path):
        db = tmp_path / "versioned.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE _schema_version (version INTEGER)")
        con.execute("INSERT INTO _schema_version VALUES (7)")
        con.commit()
        con.close()
        assert read_schema_version(db) == 7


class TestGetBackupDir:
    def test_creates_directory(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "app_data"
        monkeypatch.setattr("review_app.backend.db.backup.get_user_data_dir", lambda: data_dir)
        result = get_backup_dir()
        assert result == data_dir / "backups"
        assert result.exists()
