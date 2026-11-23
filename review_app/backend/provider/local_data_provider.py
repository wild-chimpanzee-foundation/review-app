from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from review_app.app.config import DEFAULT_DB_FILENAME, get_user_data_dir
from review_app.backend.db.backup import RestoreCopyError, RestoreRecoveryError, restore_backup
from review_app.backend.db.migrations import MIGRATIONS, initialize_database, upgrade_database
from review_app.backend.provider.annotation_repository import AnnotationMixin
from review_app.backend.provider.assignment_handoff import AssignmentHandoffMixin
from review_app.backend.provider.assignment_service import AssignmentMixin
from review_app.backend.provider.deployments import DeploymentMixin
from review_app.backend.provider.distance_repository import DistanceMixin
from review_app.backend.provider.distance_transfer import DistanceTransferMixin
from review_app.backend.provider.import_service import ImportMixin
from review_app.backend.provider.project_repository import ProjectMixin
from review_app.backend.provider.species import SpeciesMixin
from review_app.backend.provider.stats_service import StatsMixin
from review_app.backend.provider.tag_repository import TagMixin
from review_app.backend.provider.video import VideoMixin
from review_app.backend.provider.video_queue import QueueMixin

logger = logging.getLogger(__name__)

# Python 3.12 deprecated the built-in sqlite3 datetime adapters; register explicit replacements.
sqlite3.register_adapter(datetime, lambda val: val.isoformat())
sqlite3.register_adapter(date, lambda val: val.isoformat())


def restore_local_database(backup_path: Path) -> LocalDataProvider:
    """Restore with no live provider connections and return the verified provider."""
    return restore_backup(
        backup_path,
        len(MIGRATIONS),
        reopen=LocalDataProvider,
        close=lambda provider: provider.engine.dispose(),
    )


class LocalDataProvider(
    DeploymentMixin,
    VideoMixin,
    SpeciesMixin,
    ProjectMixin,
    QueueMixin,
    AnnotationMixin,
    ImportMixin,
    StatsMixin,
    TagMixin,
    AssignmentMixin,
    AssignmentHandoffMixin,
    DistanceMixin,
    DistanceTransferMixin,
):
    """SQLite-backed local data provider for manual review + constrained model imports."""

    def __init__(self) -> None:
        self.db_dir = get_user_data_dir()
        self.db_dir.mkdir(parents=True, exist_ok=True)

        self._consensus_min_probability: float = 0.0

        self._db_path = self.db_dir / DEFAULT_DB_FILENAME

        fresh = not self._db_path.exists()
        self.engine = create_engine(
            f"sqlite:///{self._db_path}",
            connect_args={"check_same_thread": False},
            pool_size=20,
            max_overflow=0,
        )

        # Validate before applying the provider's persistent SQLite pragmas.
        try:
            if fresh:
                initialize_database(self.engine)
            else:
                upgrade_database(self.engine)
        finally:
            self.engine.dispose()

        self._restore_paused = threading.Event()

        @event.listens_for(self.engine, "connect")
        @event.listens_for(self.engine, "checkout")
        def reject_checkout_during_restore(*_):
            if self._restore_paused.is_set():
                raise RestoreCopyError("Database connections are paused for restore")

        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA cache_size=-64000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")

        logger.info("Opening database at %s", self._db_path)
        try:
            self.Session = sessionmaker(bind=self.engine)

            self._load_species_data()
            self._load_species_behaviors()
            self._ensure_reference_video_tag()
        except Exception:
            self.engine.dispose()
            raise

    def restore_from_backup(self, backup_path: Path) -> LocalDataProvider:
        """Stop new checkouts and drain active requests before swapping the database."""
        self._restore_paused.set()
        try:
            pool = self.engine.pool
            assert isinstance(pool, QueuePool)
            deadline = time.monotonic() + 5
            while pool.checkedout():
                if time.monotonic() >= deadline:
                    raise RestoreCopyError("Active database connections did not drain")
                time.sleep(0.05)
            self.engine.dispose()
            return restore_local_database(backup_path)
        except RestoreRecoveryError:
            # Keep the provider offline until startup can recover the pending copy.
            raise
        except Exception:
            self._restore_paused.clear()
            raise

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _utcnow_dt() -> datetime:
        return datetime.now(timezone.utc)

    # ── App settings (key-value store in DB) ─────────────────────────────────

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM app_settings WHERE key = :k"), {"k": key}
            ).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO app_settings (key, value) VALUES (:k, :v) ON CONFLICT(key) DO UPDATE SET value = :v"
                ),
                {"k": key, "v": str(value) if value is not None else None},
            )

    # ── Video sync ────────────────────────────────────────────────────────────

    def sync_videos(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        video_dir: Path | None = None,
        active_project_id: str | None = None,
    ) -> dict[str, int]:
        return self._sync_videos_table(
            progress_callback, video_dir=video_dir, active_project_id=active_project_id
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    def has_videos_in_db(self, active_project_id: str | None) -> bool:
        if not self._db_path.exists():
            return False
        with self.engine.connect() as conn:
            if active_project_id:
                result = conn.execute(
                    text("SELECT COUNT(*) FROM videos WHERE project_id = :pid"),
                    {"pid": active_project_id},
                ).fetchone()
            else:
                result = conn.execute(text("SELECT COUNT(*) FROM videos")).fetchone()
            return result[0] > 0 if result else False
