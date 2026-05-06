import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from review_app.backend.db.backup import (
    BACKUP_TIMESTAMP_FORMAT,
    DAILY_RETENTION_DAYS,
    MAX_AUTO_BACKUPS,
    BackupCopyError,
    BackupDBNotFoundError,
    BackupError,
    RestoreCopyError,
    RestoreFileNotFoundError,
    RestoreRemoveError,
    _fallback_copy,
    create_backup,
    get_backup_dir,
    list_backups,
    prune_backups,
    restore_backup,
)
from sqlalchemy import create_engine, text


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    backup_dir = db_dir / "backups"
    backup_dir.mkdir()

    monkeypatch.setattr("review_app.backend.db.backup.get_user_data_dir", lambda: db_dir)
    monkeypatch.setattr("review_app.app.config.get_user_data_dir", lambda: db_dir)

    db_path = db_dir / "review_data.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO test_table (name) VALUES ('hello')"))
        conn.commit()

    yield {"db_dir": db_dir, "db_path": db_path, "backup_dir": backup_dir, "engine": engine}

    engine.dispose()


def _make_backup_file(backup_dir, timestamp_str, size_bytes=1024):
    name = f"review_backup_{timestamp_str}.db"
    p = backup_dir / name
    p.write_bytes(os.urandom(size_bytes))
    return p


class TestCreateBackup:
    def test_creates_backup_file(self, workspace):
        result = create_backup(workspace["engine"], reason="test")
        assert result is not None
        assert result.exists()
        assert result.name.startswith("review_backup_")
        assert result.name.endswith(".db")

    def test_backup_is_valid_sqlite(self, workspace):
        result = create_backup(workspace["engine"], reason="test")
        check_engine = create_engine(f"sqlite:///{result}")
        try:
            with check_engine.connect() as conn:
                rows = conn.execute(text("SELECT name FROM test_table")).fetchall()
                assert rows[0][0] == "hello"
        finally:
            check_engine.dispose()

    def test_raises_when_db_missing(self, workspace, monkeypatch):
        monkeypatch.setattr(
            "review_app.backend.db.backup.get_user_data_dir",
            lambda: workspace["db_dir"] / "nonexistent",
        )
        with pytest.raises(BackupDBNotFoundError):
            create_backup(workspace["engine"], reason="test")

    def test_fallback_copy_on_vacuum_failure(self, workspace, monkeypatch):
        call_count = 0
        original_connect = workspace["engine"].connect

        def bad_connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError("forced vacuum failure")
            return original_connect(*a, **kw)

        monkeypatch.setattr(workspace["engine"], "connect", bad_connect)
        result = create_backup(workspace["engine"], reason="test_fallback")
        assert result is not None
        assert result.exists()

    def test_raises_backup_copy_error_when_both_fail(self, workspace, monkeypatch):
        monkeypatch.setattr(
            "review_app.backend.db.backup._fallback_copy",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("copy failed")),
        )
        monkeypatch.setattr(
            "review_app.backend.db.backup.get_user_data_dir",
            lambda: workspace["db_dir"],
        )
        with patch("review_app.backend.db.backup.text", side_effect=RuntimeError("fail")):
            with pytest.raises(BackupCopyError):
                create_backup(workspace["engine"], reason="test")

    def test_user_message_keys(self):
        assert BackupDBNotFoundError("/foo").user_message_key == "backup_error_db_not_found"
        assert BackupCopyError("x").user_message_key == "backup_error_copy_failed"
        assert RestoreFileNotFoundError("/foo").user_message_key == "restore_error_file_not_found"
        assert RestoreRemoveError("/foo").user_message_key == "restore_error_remove_failed"
        assert RestoreCopyError("x").user_message_key == "restore_error_copy_failed"

    def test_exception_hierarchy(self):
        assert issubclass(BackupDBNotFoundError, BackupError)
        assert issubclass(BackupCopyError, BackupError)
        assert issubclass(RestoreFileNotFoundError, BackupError)
        assert issubclass(RestoreRemoveError, BackupError)
        assert issubclass(RestoreCopyError, BackupError)

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
        assert backups[0]["name"] == f"review_backup_{ts2}.db"
        assert backups[1]["name"] == f"review_backup_{ts1}.db"

    def test_ignores_malformed_filenames(self, workspace):
        _make_backup_file(workspace["backup_dir"], "20250101_120000")
        bad_file = workspace["backup_dir"] / "review_backup_badname.db"
        bad_file.write_bytes(b"x" * 10)

        backups = list_backups()
        assert len(backups) == 1

    def test_includes_size(self, workspace):
        _make_backup_file(workspace["backup_dir"], "20250101_120000", size_bytes=2 * 1024 * 1024)
        backups = list_backups()
        assert backups[0]["size_mb"] >= 1.0


class TestPruneBackups:
    def test_prunes_excess_backups(self, workspace):
        now = datetime(2025, 1, 20, 12, 0, 0)

        for i in range(MAX_AUTO_BACKUPS + 10):
            ts = (now - timedelta(days=i)).strftime(BACKUP_TIMESTAMP_FORMAT)
            _make_backup_file(workspace["backup_dir"], ts)

        with patch("review_app.backend.db.backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime = lambda s, fmt: datetime.strptime(s, fmt)
            result = prune_backups()

        total = MAX_AUTO_BACKUPS + 10
        kept = total - result
        assert kept <= MAX_AUTO_BACKUPS + DAILY_RETENTION_DAYS
        assert result == total - kept

    def test_keeps_daily_milestones(self, workspace):
        now = datetime(2025, 1, 10, 12, 0, 0)

        for day in range(DAILY_RETENTION_DAYS + 1):
            for hour in range(3):
                ts = (now - timedelta(days=day, hours=hour * 8)).strftime(BACKUP_TIMESTAMP_FORMAT)
                _make_backup_file(workspace["backup_dir"], ts, size_bytes=64)

        with patch("review_app.backend.db.backup.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.strptime = lambda s, fmt: datetime.strptime(s, fmt)
            prune_backups()

        remaining = list_backups()

        kept_days = set()
        for b in remaining:
            kept_days.add(b["timestamp"].strftime("%Y-%m-%d"))

        for day in range(DAILY_RETENTION_DAYS + 1):
            d = (now - timedelta(days=day)).strftime("%Y-%m-%d")
            assert d in kept_days, f"Expected backup for day {d} to be preserved"

    def test_no_pruning_when_under_limit(self, workspace):
        _make_backup_file(workspace["backup_dir"], "20250101_120000")
        result = prune_backups()
        assert result == 0


class TestRestoreBackup:
    def test_restores_successfully(self, workspace):
        backup_path = create_backup(workspace["engine"], reason="pre")
        workspace["engine"].dispose()

        with patch("review_app.backend.db.backup.get_user_data_dir", lambda: workspace["db_dir"]):
            restore_backup(backup_path, workspace["engine"])

        assert workspace["db_path"].exists()
        check_engine = create_engine(f"sqlite:///{workspace['db_path']}")
        try:
            with check_engine.connect() as conn:
                rows = conn.execute(text("SELECT name FROM test_table")).fetchall()
                assert rows[0][0] == "hello"
        finally:
            check_engine.dispose()

    def test_raises_when_backup_missing(self, workspace):
        with pytest.raises(RestoreFileNotFoundError):
            with patch(
                "review_app.backend.db.backup.get_user_data_dir", lambda: workspace["db_dir"]
            ):
                restore_backup(Path("/nonexistent/backup.db"), workspace["engine"])

    def test_raises_when_cannot_remove_existing_db(self, workspace, monkeypatch):
        backup_path = create_backup(workspace["engine"], reason="pre")
        workspace["engine"].dispose()

        def fail_unlink(self, *args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", fail_unlink)

        with patch("review_app.backend.db.backup.get_user_data_dir", lambda: workspace["db_dir"]):
            engine2 = create_engine(f"sqlite:///{workspace['db_path']}")
            with pytest.raises(RestoreRemoveError):
                restore_backup(backup_path, engine2)
            engine2.dispose()

    def test_pre_restore_backup_is_created(self, workspace):
        backup1 = create_backup(workspace["engine"], reason="test")
        workspace["engine"].dispose()

        initial_count = len(list_backups())

        with patch("review_app.backend.db.backup.get_user_data_dir", lambda: workspace["db_dir"]):
            engine2 = create_engine(f"sqlite:///{workspace['db_path']}")
            restore_backup(backup1, engine2)
            engine2.dispose()

        assert len(list_backups()) > initial_count


class TestFallbackCopy:
    def test_copies_main_file(self, tmp_path):
        src = tmp_path / "source.db"
        src.write_bytes(b"test_data_12345")
        dst = tmp_path / "dest.db"

        _fallback_copy(src, dst)
        assert dst.exists()
        assert dst.read_bytes() == b"test_data_12345"

    def test_copies_wal_and_shm(self, tmp_path):
        src = tmp_path / "source.db"
        src.write_bytes(b"main")
        src_wal = tmp_path / "source.db-wal"
        src_wal.write_bytes(b"wal")
        src_shm = tmp_path / "source.db-shm"
        src_shm.write_bytes(b"shm")

        dst = tmp_path / "dest.db"

        _fallback_copy(src, dst)
        assert dst.exists()
        assert (tmp_path / "dest.db-wal").exists()
        assert (tmp_path / "dest.db-shm").exists()
        assert (tmp_path / "dest.db-wal").read_bytes() == b"wal"
        assert (tmp_path / "dest.db-shm").read_bytes() == b"shm"

    def test_creates_parent_dirs(self, tmp_path):
        src = tmp_path / "source.db"
        src.write_bytes(b"data")
        dst = tmp_path / "nested" / "dir" / "dest.db"

        _fallback_copy(src, dst)
        assert dst.exists()


class TestGetBackupDir:
    def test_creates_directory(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "app_data"
        monkeypatch.setattr("review_app.backend.db.backup.get_user_data_dir", lambda: data_dir)
        result = get_backup_dir()
        assert result == data_dir / "backups"
        assert result.exists()

    def test_idempotent(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "app_data"
        monkeypatch.setattr("review_app.backend.db.backup.get_user_data_dir", lambda: data_dir)
        get_backup_dir()
        result = get_backup_dir()
        assert result.exists()
