from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from review_app.app.config import DEFAULT_DB_FILENAME, get_user_data_dir
from review_app.backend.errors import AppError

logger = logging.getLogger(__name__)

_BACKUP_TS_RE = re.compile(r"^review_backup_(\d{8}_\d{6})(?:_\d+)?\.db$")

MAX_AUTO_BACKUPS = 10
DAILY_RETENTION_DAYS = 14
BACKUP_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


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


def get_backup_dir() -> Path:
    backup_dir = get_user_data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def create_backup(engine, reason: str = "auto", auto_prune: bool = True) -> Path:
    db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
    if not db_path.exists():
        raise BackupDBNotFoundError(str(db_path))

    timestamp = datetime.now().strftime(BACKUP_TIMESTAMP_FORMAT)
    backup_name = f"review_backup_{timestamp}.db"
    backup_path = get_backup_dir() / backup_name

    if backup_path.exists():
        i = 1
        while (get_backup_dir() / f"review_backup_{timestamp}_{i}.db").exists():
            i += 1
        backup_path = get_backup_dir() / f"review_backup_{timestamp}_{i}.db"

    try:
        escaped = str(backup_path).replace("'", "''")
        with engine.connect() as conn:
            conn.execute(text(f"VACUUM INTO '{escaped}'"))
        logger.info("Backup created (%s): %s", reason, backup_path)
        if auto_prune:
            prune_backups()
        return backup_path
    except Exception:
        logger.exception("VACUUM INTO failed, falling back to file copy")
        try:
            _fallback_copy(db_path, backup_path)
            logger.info("Fallback copy backup created (%s): %s", reason, backup_path)
            if auto_prune:
                prune_backups()
            return backup_path
        except Exception as copy_exc:
            logger.exception("Fallback copy also failed")
            raise BackupCopyError(str(copy_exc)) from copy_exc


def _fallback_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("", "-wal", "-shm"):
        s = Path(str(src) + ext)
        d = Path(str(dst) + ext)
        if s.exists():
            shutil.copy2(s, d)


def _parse_backup_timestamp(name: str) -> datetime | None:
    m = _BACKUP_TS_RE.match(name)
    if not m:
        return None
    ts_str = m.group(1)
    try:
        return datetime.strptime(ts_str, BACKUP_TIMESTAMP_FORMAT)
    except ValueError:
        return None


def list_backups() -> list[dict[str, Any]]:
    backup_dir = get_backup_dir()
    backups = []
    for f in sorted(backup_dir.glob("review_backup_*.db"), reverse=True):
        dt = _parse_backup_timestamp(f.name)
        if dt is None:
            continue
        backups.append(
            {
                "path": f,
                "name": f.name,
                "timestamp": dt,
                "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
            }
        )
    return backups


def prune_backups() -> int:
    backups = list_backups()

    daily_milestones: set[str] = set()
    keep: set[Path] = set()

    now = datetime.now()
    for b in backups:
        age_days = (now - b["timestamp"]).days
        day_key = b["timestamp"].strftime("%Y-%m-%d")
        if age_days <= DAILY_RETENTION_DAYS and day_key not in daily_milestones:
            daily_milestones.add(day_key)
            keep.add(b["path"])

    for b in backups[:MAX_AUTO_BACKUPS]:
        keep.add(b["path"])

    to_delete = [b for b in backups if b["path"] not in keep]
    count = 0
    for b in to_delete:
        try:
            b["path"].unlink()
            for ext in ("-wal", "-shm"):
                p = Path(str(b["path"]) + ext)
                if p.exists():
                    p.unlink()
            count += 1
        except OSError:
            logger.warning("Failed to prune backup: %s", b["path"])
    return count


def get_latest_backup_path() -> Path | None:
    backups = list_backups()
    if backups:
        return backups[0]["path"]
    return None


def restore_backup(backup_path: Path, engine) -> None:
    db_path = get_user_data_dir() / DEFAULT_DB_FILENAME
    if not backup_path.exists():
        raise RestoreFileNotFoundError(str(backup_path))

    create_backup(engine, reason="pre_restore", auto_prune=False)

    engine.dispose()

    for ext in ("", "-wal", "-shm", "-journal"):
        p = Path(str(db_path) + ext)
        if p.exists():
            try:
                p.unlink()
            except OSError as exc:
                logger.exception("Failed to remove %s", p)
                raise RestoreRemoveError(str(p)) from exc

    try:
        shutil.copy2(backup_path, db_path)
    except OSError as exc:
        logger.exception("Failed to copy backup %s to %s", backup_path, db_path)
        raise RestoreCopyError(f"{backup_path} -> {db_path}") from exc
    logger.info("Database restored from backup: %s", backup_path)
