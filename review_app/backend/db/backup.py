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
import zlib
from collections.abc import Callable
from contextlib import closing, contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from review_app.app.config import DEFAULT_DB_FILENAME, get_user_data_dir
from review_app.backend.db.migrations import (
    SchemaVersionError,
    upgrade_database,
)
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
BackupCompression = Literal["sync", "background"]


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


class RestoreRecoveryError(RestoreCopyError):
    user_message_key: str = "restore_rollback_failed"


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


_restore_lock = threading.Lock()


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


def _compress_backup(raw_db: Path, backup_path: Path, auto_prune: bool) -> Path:
    """Publish completed gzip output and keep the raw snapshot if compression fails."""
    target = backup_path.with_suffix(".gz.tmp")
    try:
        with (
            raw_db.open("rb") as f_in,
            gzip.open(target, "wb", compresslevel=BACKUP_GZIP_LEVEL) as f_out,
        ):
            shutil.copyfileobj(f_in, f_out)
        os.replace(target, backup_path)
    except Exception:
        logger.exception("Backup compression failed; keeping uncompressed backup %s", raw_db)
        target.unlink(missing_ok=True)
        result = raw_db
    else:
        try:
            raw_db.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove uncompressed copy of %s", backup_path)
        result = backup_path
    if auto_prune:
        try:
            prune_backups(backup_dir=backup_path.parent)
        except Exception:
            logger.exception("Backup pruning failed")
    return result


def create_backup(
    reason: str = "auto", auto_prune: bool = True, compress: BackupCompression = "sync"
) -> Path:
    """Create a VACUUM INTO backup of the live DB. Raises on any failure.

    compress:
      "sync"       – gzip before returning; returns the .db.gz path.
      "background" – return the plain .db right after VACUUM INTO; gzip + prune run
                     in a daemon thread. If the app exits mid-compression the raw
                     .db stays behind as a valid backup.
    """
    _t0 = time.monotonic()
    with _restore_lock:
        db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
        if not db_path.exists():
            raise BackupDBNotFoundError(str(db_path))

        schema_version = read_schema_version(db_path) or 0
        timestamp = datetime.now(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)
        backup_dir = get_backup_dir()

        base_name = f"review_backup_{timestamp}_v{schema_version}"
        backup_path = backup_dir / f"{base_name}.db.gz"
        sequence = 0
        while backup_path.exists() or backup_path.with_suffix("").exists():
            sequence += 1
            backup_path = backup_dir / f"{base_name}_{sequence}.db.gz"

        _validate_backup_path(backup_path)
        raw_db = backup_path.with_suffix("")
        snapshot = raw_db.with_suffix(".db.snapshot.tmp")
        try:
            snapshot.unlink(missing_ok=True)
            with closing(sqlite3.connect(str(db_path))) as con:
                con.execute("VACUUM INTO ?", (str(snapshot),))
            os.replace(snapshot, raw_db)
        except Exception as exc:
            logger.exception("VACUUM INTO failed")
            snapshot.unlink(missing_ok=True)
            raise BackupVacuumError(str(exc)) from exc

    vacuum_secs = time.monotonic() - _t0

    if compress == "background":
        threading.Thread(
            target=_compress_backup,
            args=(raw_db, backup_path, auto_prune),
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

    result = _compress_backup(raw_db, backup_path, auto_prune)
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


def _list_backup_files(backup_dir: Path) -> list[dict[str, Any]]:
    backups = []
    all_files = sorted(
        backup_dir.glob("review_backup_*.db*"),
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


def _snapshot_path(path: Path) -> Path:
    return path.with_suffix("") if path.suffix == ".gz" else path


def _preferred_backups(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = {}
    for backup in files:
        # The reverse filename sort puts the gzip artifact first within each pair.
        snapshots.setdefault(_snapshot_path(backup["path"]), backup)
    return list(snapshots.values())


def list_backups() -> list[dict[str, Any]]:
    """List one entry per snapshot, preferring its compressed copy if available."""
    return _preferred_backups(_list_backup_files(get_backup_dir()))


def prune_backups(*, backup_dir: Path | None = None) -> int:
    """GFS retention: N most recent + 1/day for D days + 1/week for W weeks + 1/month for M months.
    Each tier picks the newest backup in its bucket; tiers overlap rather than partition."""
    files = _list_backup_files(backup_dir if backup_dir is not None else get_backup_dir())
    backups = _preferred_backups(files)

    keep: set[Path] = set()
    daily_seen: set[str] = set()
    weekly_seen: set[str] = set()
    monthly_seen: set[str] = set()

    for b in backups[:RECENT_KEEP_COUNT]:
        keep.add(_snapshot_path(b["path"]))

    now = datetime.now(timezone.utc)
    for b in backups:
        ts = b["timestamp"]
        age_days = (now - ts).days

        if age_days <= DAILY_RETENTION_DAYS:
            day_key = ts.strftime("%Y-%m-%d")
            if day_key not in daily_seen:
                daily_seen.add(day_key)
                keep.add(_snapshot_path(b["path"]))

        if age_days <= WEEKLY_RETENTION_WEEKS * 7:
            iso = ts.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"
            if week_key not in weekly_seen:
                weekly_seen.add(week_key)
                keep.add(_snapshot_path(b["path"]))

        if age_days <= MONTHLY_RETENTION_MONTHS * 31:
            month_key = ts.strftime("%Y-%m")
            if month_key not in monthly_seen:
                monthly_seen.add(month_key)
                keep.add(_snapshot_path(b["path"]))

    to_delete = [b for b in files if _snapshot_path(b["path"]) not in keep]
    count = 0
    for b in to_delete:
        try:
            b["path"].unlink()
            count += 1
        except OSError:
            logger.warning("Failed to prune backup: %s", b["path"])
    return count


def backup_if_stale(
    max_age_seconds: int = 1800,
    reason: str = "auto",
    compress: BackupCompression = "background",
    raise_on_error: bool = False,
) -> bool:
    """Create a backup only if no backup exists within max_age_seconds.

    Returns True if one was created. Safety-net callers use background compression by
    default. UI callers can request synchronous compression so the progress dialog stays
    open until the final backup file is ready, and can opt into seeing backup failures.

    Safety-net path (pre-import, pre-update, shutdown): compression runs in the
    background so the caller only waits for the VACUUM INTO unless ``compress`` is
    explicitly set to ``"sync"``."""
    backups = list_backups()
    if backups:
        age = (datetime.now(timezone.utc) - backups[0]["timestamp"]).total_seconds()
        if age < max_age_seconds:
            return False
    try:
        create_backup(reason=reason, compress=compress)
        return True
    except BackupError:
        if raise_on_error:
            raise
        return False


def _check_restore_integrity(staging: Path) -> None:
    try:
        with staging.open("rb") as source:
            if source.read(16) != b"SQLite format 3\0":
                raise RestoreCorruptError("Invalid SQLite header")
        with closing(
            sqlite3.connect(f"{staging.resolve().as_uri()}?immutable=1", uri=True)
        ) as con:
            result = con.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise RestoreCorruptError(f"integrity_check: {result}")
            violation = con.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise RestoreCorruptError(f"foreign_key_check: {violation}")
    except (sqlite3.Error, OSError) as exc:
        raise RestoreCorruptError(str(exc)) from exc


def _prepare_restore(staging: Path, maximum_version: int | None) -> None:
    _check_restore_integrity(staging)
    engine = create_engine(URL.create("sqlite", database=str(staging)))
    try:
        upgraded = upgrade_database(engine, maximum_version=maximum_version)
    except SchemaVersionError as exc:
        raise RestoreSchemaVersionError(str(exc)) from exc
    except (ValueError, SQLAlchemyError) as exc:
        raise RestoreCorruptError(str(exc)) from exc
    finally:
        engine.dispose()

    if upgraded:
        _check_restore_integrity(staging)
        try:
            with staging.open("rb+") as candidate:
                os.fsync(candidate.fileno())
        except OSError as exc:
            raise RestoreCopyError(str(exc)) from exc


def _open_restore_source(path: Path):
    try:
        return path.open("rb")
    except FileNotFoundError as exc:
        # A listed raw snapshot may finish compressing before the user selects it.
        managed_dir = get_user_data_dir() / "backups"
        if (
            path.suffix == ".db"
            and _parse_backup_meta(path.name) is not None
            and path.parent.resolve() == managed_dir.resolve()
        ):
            try:
                return path.with_suffix(".db.gz").open("rb")
            except FileNotFoundError:
                pass
        raise RestoreFileNotFoundError(str(path)) from exc


@contextmanager
def _staged_restore(source: Path, db_path: Path):
    _validate_restore_source(source)
    staging = None
    try:
        try:
            with (
                _open_restore_source(source) as original,
                tempfile.NamedTemporaryFile(
                    dir=db_path.parent,
                    prefix=f".{db_path.name}.",
                    suffix=".restoring",
                    delete=False,
                ) as target,
            ):
                staging = Path(target.name)
                compressed = original.read(2) == b"\x1f\x8b"
                original.seek(0)
                stream = gzip.GzipFile(fileobj=original) if compressed else nullcontext(original)
                with stream as contents:
                    shutil.copyfileobj(contents, target)
                target.flush()
                os.fsync(target.fileno())
        except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
            raise RestoreCorruptError(str(exc)) from exc
        except OSError as exc:
            raise RestoreCopyError(str(exc)) from exc
        yield staging
    finally:
        if staging is not None:
            remove_db_sidecars(staging)
            staging.unlink(missing_ok=True)


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _install_staged_db(staging: Path, db_path: Path) -> None:
    try:
        for ext in SIDE_CAR_EXTS:
            Path(str(db_path) + ext).unlink(missing_ok=True)
        os.replace(staging, db_path)
        _sync_directory(db_path.parent)
    except OSError as exc:
        raise RestoreCopyError(str(exc)) from exc


def _rollback_path(db_path: Path) -> Path:
    return db_path.with_suffix(".db.rollback")


def recover_interrupted_restore() -> None:
    """Restore the original database before opening any app connections."""
    db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
    rollback = _rollback_path(db_path)
    if rollback.exists():
        _install_staged_db(rollback, db_path)
        logger.warning("Reinstated the database after an incomplete restore")


def _preserve_live_database(db_path: Path, rollback: Path) -> None:
    try:
        if any(Path(str(db_path) + ext).exists() for ext in ("-wal", "-journal")):
            with closing(sqlite3.connect(str(db_path))) as con:
                con.execute("SELECT count(*) FROM sqlite_schema").fetchone()
                busy, _, _ = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if busy:
                    raise RestoreCopyError("Live database WAL is busy")
        with _staged_restore(db_path, db_path) as snapshot:
            os.replace(snapshot, rollback)
            _sync_directory(db_path.parent)
    except (OSError, sqlite3.Error) as exc:
        raise RestoreCopyError(str(exc)) from exc


def restore_backup[T](
    backup_path: Path,
    app_schema_version: int | None,
    *,
    reopen: Callable[[], T],
    close: Callable[[T], None] | None = None,
) -> T:
    """Validate, install and reopen a backup with the live connections closed."""
    with _restore_lock:
        db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
        rollback = _rollback_path(db_path)
        if rollback.exists():
            raise RestoreRecoveryError("An interrupted restore must be recovered before retrying")

        with _staged_restore(backup_path, db_path) as staging:
            _prepare_restore(staging, app_schema_version)
            live_exists = db_path.exists()
            live_version = read_schema_version(db_path) if live_exists else None

            archive = None
            if live_exists:
                try:
                    timestamp = datetime.now(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)
                    archive = get_backup_dir() / (
                        f"review_backup_{timestamp}_v{live_version or 0}_{time.time_ns()}.db"
                    )
                except OSError as exc:
                    raise RestoreCopyError(str(exc)) from exc

            try:
                if live_exists:
                    _preserve_live_database(db_path, rollback)
                _install_staged_db(staging, db_path)
                reopened = reopen()
            except Exception:
                try:
                    recover_interrupted_restore()
                    if not live_exists:
                        for ext in SIDE_CAR_EXTS:
                            Path(str(db_path) + ext).unlink(missing_ok=True)
                        db_path.unlink(missing_ok=True)
                        _sync_directory(db_path.parent)
                except (BackupError, OSError) as exc:
                    logger.exception("Could not roll back the failed restore")
                    raise RestoreRecoveryError(str(exc)) from exc
                raise

            if archive is not None:
                try:
                    os.replace(rollback, archive)
                except OSError as exc:
                    try:
                        if close is not None:
                            close(reopened)
                        recover_interrupted_restore()
                    except Exception as recovery_error:
                        logger.exception("Could not roll back the unfinalized restore")
                        raise RestoreRecoveryError(str(recovery_error)) from recovery_error
                    raise RestoreCopyError(str(exc)) from exc
                try:
                    _sync_directory(archive.parent)
                    _sync_directory(db_path.parent)
                except OSError:
                    logger.exception("Could not sync the archived restore safety copy")

        logger.info("Database restored from backup: %s", backup_path)
        return reopened


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
