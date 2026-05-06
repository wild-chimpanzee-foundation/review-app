from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from review_app.app.config import DEFAULT_DB_FILENAME, get_user_data_dir
from review_app.backend.db.migrations import run_migrations
from review_app.backend.db.models import Base
from review_app.backend.provider.annotation_repository import AnnotationMixin
from review_app.backend.provider.import_service import ImportMixin
from review_app.backend.provider.project_repository import ProjectMixin
from review_app.backend.provider.species import SpeciesMixin
from review_app.backend.provider.stats_service import StatsMixin
from review_app.backend.provider.video import VideoMixin
from review_app.backend.provider.video_queue import QueueMixin

logger = logging.getLogger(__name__)


class LocalDataProvider(
    VideoMixin,
    SpeciesMixin,
    ProjectMixin,
    QueueMixin,
    AnnotationMixin,
    ImportMixin,
    StatsMixin,
):
    """SQLite-backed local data provider for manual review + constrained model imports."""

    def __init__(self) -> None:
        self.db_dir = get_user_data_dir()
        self.db_dir.mkdir(parents=True, exist_ok=True)

        self._consensus_min_probability: float = 0.0

        self._db_path = self.db_dir / DEFAULT_DB_FILENAME

        self.engine = create_engine(f"sqlite:///{self._db_path}")

        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")

        Base.metadata.create_all(self.engine)
        run_migrations(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self._load_species_data()
        self._load_species_behaviors()

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
        progress_callback,
        video_dir: Path | None = None,
        active_project_id: str | None = None,
    ) -> dict:
        return self._sync_videos_table(
            progress_callback, video_dir=video_dir, active_project_id=active_project_id
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    def has_videos_in_db(self, active_project_id) -> bool:
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
