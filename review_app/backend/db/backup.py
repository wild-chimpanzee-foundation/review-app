from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from review_app.app.config import DEFAULT_DB_FILENAME, get_user_data_dir
from review_app.backend.errors import AppError

logger = logging.getLogger(__name__)

_BACKUP_TS_RE = re.compile(r"^review_backup_(\d{8}_\d{6})(?:_v(\d+))?(?:_\d+)?\.db(?:\.gz)?$")

RECENT_KEEP_COUNT = 5
DAILY_RETENTION_DAYS = 14
WEEKLY_RETENTION_WEEKS = 8
MONTHLY_RETENTION_MONTHS = 3
BACKUP_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

# Level 1 is ~5-10x faster than the gzip default (9) at ~15% larger files. Backups
# on multi-GB databases block imports, so speed wins over size here.
BACKUP_GZIP_LEVEL = 1


class BackupError(AppError):
    user_message_key: str = "backup_failed"

    def __init__(self, detail: str = ""):
        self.detail = detail
        super().__init__(detail)


class BackupDBNotFoundError(BackupError):
    user_message_key: str = "backup_error_db_not_found"


class BackupVacuumError(BackupError):
    user_message_key: str = "backup_error_vacuum_failed"


class BackupCopyError(BackupError):
    user_message_key: str = "backup_error_copy_failed"


class RestoreFileNotFoundError(BackupError):
    user_message_key: str = "restore_error_file_not_found"


class RestoreRemoveError(BackupError):
    user_message_key: str = "restore_error_remove_failed"


class RestoreCopyError(BackupError):
    user_message_key: str = "restore_error_copy_failed"


class RestoreSchemaVersionError(BackupError):
    user_message_key: str = "restore_error_schema_version"


class RestoreCorruptError(BackupError):
    user_message_key: str = "restore_error_corrupt"


def get_backup_dir() -> Path:
    backup_dir = get_user_data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


@contextmanager
def _as_db_file(path: Path):
    """Yield a readable .db path. Decompresses .db.gz to a temp file if needed."""
    if path.suffix == ".gz":
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".db")
        tmp_path = Path(tmp_name)
        try:
            os.close(tmp_fd)
            with gzip.open(path, "rb") as f_in, open(tmp_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        yield path


def read_schema_version(db_path: Path) -> int | None:
    """Read _schema_version from an arbitrary SQLite file (plain or gzipped). Returns None if unreadable."""
    try:
        with _as_db_file(db_path) as p:
            con = sqlite3.connect(str(p))
            try:
                row = con.execute("SELECT version FROM _schema_version").fetchone()
            finally:
                con.close()
        return row[0] if row else None
    except Exception as exc:
        logger.warning("Could not read schema version from %s: %s", db_path, exc)
        return None


def _validate_backup_path(path: Path) -> None:
    s = str(path)
    if "\n" in s or "\0" in s:
        raise BackupCopyError(f"invalid backup path: {path!r}")
    backup_dir = get_backup_dir().resolve()
    if path.resolve().parent != backup_dir:
        raise BackupCopyError(f"backup path outside backup dir: {path}")


def _validate_restore_source(path: Path) -> None:
    """Lightweight validation for restore sources. The path is application-controlled
    (list_backups or a tempfile) so this is defense in depth, not access control."""
    s = str(path)
    if "\n" in s or "\0" in s:
        raise RestoreFileNotFoundError(f"invalid backup path: {path!r}")


SIDE_CAR_EXTS: tuple[str, ...] = ("-wal", "-shm", "-journal")


def remove_db_sidecars(db_path: Path) -> None:
    """Remove SQLite sidecar files (-wal, -shm, -journal) for db_path. Best-effort."""
    for ext in SIDE_CAR_EXTS:
        p = Path(str(db_path) + ext)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                logger.warning("Failed to remove sidecar %s", p)


def _compress_backup(raw_db: Path, backup_path: Path, auto_prune: bool, atomic: bool) -> Path:
    """gzip raw_db into backup_path and remove the raw file. Returns the final path.

    atomic=True writes to a .tmp file first and renames — required in the background
    thread so list_backups/restore never see a half-written .gz. On compression
    failure the raw .db is kept — it is already a valid backup (list_backups and
    restore handle both .db and .db.gz)."""
    target = Path(str(backup_path) + ".tmp") if atomic else backup_path
    try:
        with (
            open(raw_db, "rb") as f_in,
            gzip.open(target, "wb", compresslevel=BACKUP_GZIP_LEVEL) as f_out,
        ):
            shutil.copyfileobj(f_in, f_out)
        if atomic:
            os.replace(target, backup_path)
        raw_db.unlink(missing_ok=True)
        result = backup_path
    except Exception:
        logger.exception("Backup compression failed; keeping uncompressed backup %s", raw_db)
        target.unlink(missing_ok=True)
        result = raw_db
    if auto_prune:
        try:
            prune_backups()
        except Exception:
            logger.exception("Backup pruning failed")
    return result


def create_backup(reason: str = "auto", auto_prune: bool = True, compress: str = "sync") -> Path:
    """Create a VACUUM INTO backup of the live DB. Raises on any failure.

    compress:
      "sync"       – gzip before returning; returns the .db.gz path.
      "background" – return the plain .db right after VACUUM INTO; gzip + prune run
                     in a daemon thread. If the app exits mid-compression the raw
                     .db stays behind as a valid backup.
    """
    _t0 = time.monotonic()
    db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
    if not db_path.exists():
        raise BackupDBNotFoundError(str(db_path))

    schema_version = read_schema_version(db_path) or 0
    timestamp = datetime.now(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)
    backup_dir = get_backup_dir()

    # Clear leftovers from a compression thread killed by a previous shutdown
    for stale in backup_dir.glob("review_backup_*.tmp"):
        stale.unlink(missing_ok=True)

    backup_path = backup_dir / f"review_backup_{timestamp}_v{schema_version}.db.gz"
    if backup_path.exists() or backup_path.with_suffix("").exists():
        i = 1
        while (backup_dir / f"review_backup_{timestamp}_v{schema_version}_{i}.db.gz").exists():
            i += 1
        backup_path = backup_dir / f"review_backup_{timestamp}_v{schema_version}_{i}.db.gz"

    _validate_backup_path(backup_path)
    raw_db = backup_path.with_suffix("")  # .db.gz → .db for VACUUM INTO
    escaped = str(raw_db).replace("'", "''")

    con = None
    try:
        con = sqlite3.connect(str(db_path))
        con.execute(f"VACUUM INTO '{escaped}'")
    except Exception as exc:
        logger.exception("VACUUM INTO failed")
        raw_db.unlink(missing_ok=True)
        raise BackupVacuumError(str(exc)) from exc
    finally:
        if con is not None:
            con.close()

    vacuum_secs = time.monotonic() - _t0

    if compress == "background":
        threading.Thread(
            target=_compress_backup,
            args=(raw_db, backup_path, auto_prune, True),
            name="backup-compress",
            daemon=True,
        ).start()
        logger.info(
            "Backup created (%s): %s (vacuum %.1fs, compressing in background)",
            reason,
            raw_db,
            vacuum_secs,
        )
        return raw_db

    result = _compress_backup(raw_db, backup_path, auto_prune, atomic=False)
    if result != backup_path:
        raise BackupVacuumError(f"compression failed, uncompressed backup kept at {result}")
    logger.info(
        "Backup created (%s): %s (vacuum %.1fs, gzip %.1fs)",
        reason,
        backup_path,
        vacuum_secs,
        time.monotonic() - _t0 - vacuum_secs,
    )
    return backup_path


def _parse_backup_meta(name: str) -> tuple[datetime, int | None] | None:
    """Return (timestamp UTC, schema_version) parsed from a backup filename, or None."""
    m = _BACKUP_TS_RE.match(name)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), BACKUP_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    version = int(m.group(2)) if m.group(2) is not None else None
    return dt, version


def list_backups() -> list[dict[str, Any]]:
    backup_dir = get_backup_dir()
    backups = []
    all_files = sorted(
        list(backup_dir.glob("review_backup_*.db.gz"))
        + list(backup_dir.glob("review_backup_*.db")),
        key=lambda p: p.name,
        reverse=True,
    )
    for f in all_files:
        meta = _parse_backup_meta(f.name)
        if meta is None:
            continue
        dt, schema_version = meta
        try:
            size_mb = round(f.stat().st_size / (1024 * 1024), 2)
        except FileNotFoundError:
            continue
        backups.append(
            {
                "path": f,
                "name": f.name,
                "timestamp": dt,
                "size_mb": size_mb,
                "schema_version": schema_version,
            }
        )
    return backups


def prune_backups() -> int:
    """GFS retention: N most recent + 1/day for D days + 1/week for W weeks + 1/month for M months.
    Each tier picks the newest backup in its bucket; tiers overlap rather than partition."""
    backups = list_backups()

    keep: set[Path] = set()
    daily_seen: set[str] = set()
    weekly_seen: set[str] = set()
    monthly_seen: set[str] = set()

    for b in backups[:RECENT_KEEP_COUNT]:
        keep.add(b["path"])

    now = datetime.now(timezone.utc)
    for b in backups:
        ts = b["timestamp"]
        age_days = (now - ts).days

        if age_days <= DAILY_RETENTION_DAYS:
            day_key = ts.strftime("%Y-%m-%d")
            if day_key not in daily_seen:
                daily_seen.add(day_key)
                keep.add(b["path"])

        if age_days <= WEEKLY_RETENTION_WEEKS * 7:
            iso = ts.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"
            if week_key not in weekly_seen:
                weekly_seen.add(week_key)
                keep.add(b["path"])

        if age_days <= MONTHLY_RETENTION_MONTHS * 31:
            month_key = ts.strftime("%Y-%m")
            if month_key not in monthly_seen:
                monthly_seen.add(month_key)
                keep.add(b["path"])

    to_delete = [b for b in backups if b["path"] not in keep]
    count = 0
    for b in to_delete:
        try:
            b["path"].unlink()
            count += 1
        except OSError:
            logger.warning("Failed to prune backup: %s", b["path"])
    return count


def backup_if_stale(max_age_seconds: int = 1800, reason: str = "auto") -> bool:
    """Create a backup only if no backup exists within max_age_seconds. Returns True if one was created.

    Safety-net path (pre-import, pre-update, shutdown): compression runs in the
    background so the caller only waits for the VACUUM INTO."""
    backups = list_backups()
    if backups:
        age = (datetime.now(timezone.utc) - backups[0]["timestamp"]).total_seconds()
        if age < max_age_seconds:
            return False
    try:
        create_backup(reason=reason, compress="background")
        return True
    except BackupError:
        return False


def _check_restore_schema_version(backup_path: Path, current_version: int | None) -> None:
    """Raise RestoreSchemaVersionError if the backup is from a newer schema.
    Skips the check if current_version is None (current schema unknown)."""
    if current_version is None:
        return
    backup_version = read_schema_version(backup_path)
    if backup_version is not None and backup_version > current_version:
        raise RestoreSchemaVersionError(
            f"backup schema v{backup_version} > app schema v{current_version}"
        )


def _check_restore_integrity(backup_path: Path) -> None:
    try:
        with _as_db_file(backup_path) as p:
            con = sqlite3.connect(str(p))
            try:
                result = con.execute("PRAGMA integrity_check").fetchone()
            finally:
                con.close()
        if result and result[0] != "ok":
            raise RestoreCorruptError(f"integrity_check: {result[0]}")
    except RestoreCorruptError:
        raise
    except Exception as exc:
        raise RestoreCorruptError(str(exc)) from exc


def _emergency_rollback(pre_restore_path: Path | None, db_path: Path) -> bool:
    """Best-effort restore of db_path from pre_restore_path. Cleans sidecars first.
    Returns True on success. No-op if pre_restore_path is None."""
    if pre_restore_path is None:
        return False
    try:
        if pre_restore_path.exists():
            remove_db_sidecars(db_path)
            if pre_restore_path.suffix == ".gz":
                with gzip.open(pre_restore_path, "rb") as f_in, open(db_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            else:
                shutil.copy2(pre_restore_path, db_path)
            logger.warning("Emergency rollback restored db from %s", pre_restore_path)
            return True
    except Exception:
        logger.exception("Emergency rollback failed")
    return False


def restore_backup(backup_path: Path, app_schema_version: int | None = None) -> Path | None:
    """Restore a backup over the live DB atomically (write-to-staging + os.replace).
    The caller MUST dispose any open SQLAlchemy engine on the live DB first.

    If app_schema_version is provided, it caps the allowed backup schema version (use this
    when there is no live DB to read from, e.g. setup wizard). Otherwise the live DB's
    schema version is used.

    Returns the pre-restore safety backup path, or None if no live DB existed."""
    db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
    if not backup_path.exists():
        raise RestoreFileNotFoundError(str(backup_path))

    _validate_restore_source(backup_path)
    _check_restore_integrity(backup_path)

    max_version = app_schema_version
    if max_version is None and db_path.exists():
        max_version = read_schema_version(db_path)
    _check_restore_schema_version(backup_path, max_version)

    pre_restore_path: Path | None = None
    if db_path.exists():
        pre_restore_path = create_backup(reason="pre_restore", auto_prune=False)

    staging = Path(str(db_path) + ".restoring")
    try:
        if backup_path.suffix == ".gz":
            with gzip.open(backup_path, "rb") as f_in, open(staging, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy2(backup_path, staging)
    except OSError as exc:
        logger.exception("Failed to stage backup at %s", staging)
        staging.unlink(missing_ok=True)
        _emergency_rollback(pre_restore_path, db_path)
        raise RestoreCopyError(f"{backup_path} -> {staging}") from exc

    remove_db_sidecars(db_path)

    try:
        os.replace(staging, db_path)
    except OSError as exc:
        logger.exception("Failed to swap staging file into %s", db_path)
        staging.unlink(missing_ok=True)
        _emergency_rollback(pre_restore_path, db_path)
        raise RestoreCopyError(str(exc)) from exc

    logger.info("Database restored from backup: %s", backup_path)
    return pre_restore_path


def quarantine_broken_db() -> Path | None:
    """Move a corrupt live DB into the backup directory so it remains visible in
    list_backups() and can be inspected later. Sidecars are cleaned up.
    Returns the quarantine path, or None if no live DB existed."""
    db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
    if not db_path.exists():
        return None

    schema_version = read_schema_version(db_path) or 0
    timestamp = datetime.now(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)
    backup_dir = get_backup_dir()
    target = backup_dir / f"review_backup_{timestamp}_v{schema_version}.db.gz"
    if target.exists():
        i = 1
        while (backup_dir / f"review_backup_{timestamp}_v{schema_version}_{i}.db.gz").exists():
            i += 1
        target = backup_dir / f"review_backup_{timestamp}_v{schema_version}_{i}.db.gz"

    with (
        open(db_path, "rb") as f_in,
        gzip.open(target, "wb", compresslevel=BACKUP_GZIP_LEVEL) as f_out,
    ):
        shutil.copyfileobj(f_in, f_out)
    db_path.unlink()
    remove_db_sidecars(db_path)
    logger.warning("Quarantined broken DB to %s", target)
    return target
