from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

Base = declarative_base()

# ---------------------------------------------------------------------------
# Maximum threads for parallel ffprobe calls.  Tune to your I/O concurrency.
# ---------------------------------------------------------------------------
_FFPROBE_MAX_WORKERS: int = int(os.getenv("FFPROBE_MAX_WORKERS", "16"))
_FFPROBE_TIMEOUT_SEC: int = int(os.getenv("FFPROBE_TIMEOUT_SEC", "10"))


class Video(Base):
    __tablename__ = "videos"

    video_id = Column(String, primary_key=True)
    video_path = Column(String, nullable=False)
    camera_id = Column(String, index=True)
    created_at = Column(DateTime, nullable=True)
    duration_sec = Column(Float, nullable=True)
    last_seen_at = Column(DateTime, nullable=False, default=func.now())
    # Populated by ffprobe on first ingest; never overwritten for existing rows.
    is_valid = Column(Boolean, nullable=True)
    validation_error = Column(String, nullable=True)


class VideoLabel(Base):
    __tablename__ = "video_labels"

    video_id = Column(String, ForeignKey("videos.video_id"), primary_key=True)
    is_blank = Column(Boolean, nullable=True)
    labeled_by = Column(String, nullable=True)
    labeled_at = Column(DateTime, nullable=False, default=func.now())


class IndividualObservation(Base):
    __tablename__ = "individual_observations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    species = Column(String, nullable=False)
    behavior = Column(String, nullable=False)
    start_sec = Column(Float, nullable=False, default=0.0)
    end_sec = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class ModelAnnotation(Base):
    __tablename__ = "model_annotations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    annotation_type = Column(String, nullable=False, index=True)
    model_name = Column(String, nullable=False, index=True)
    value_text = Column(String, nullable=True)
    value_num = Column(Float, nullable=True)
    probability = Column(Float, nullable=True)
    t_start_sec = Column(Float, nullable=True)
    t_end_sec = Column(Float, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "video_id", "model_name", "annotation_type", name="uq_model_ann_identity"
        ),
    )


class VideoPriority(Base):
    __tablename__ = "video_priority"

    video_id = Column(String, ForeignKey("videos.video_id"), primary_key=True)
    annotation_importance_score = Column(Float, nullable=False, index=True)


Index(
    "idx_individual_video_species", IndividualObservation.video_id, IndividualObservation.species
)
Index(
    "idx_individual_video_behavior", IndividualObservation.video_id, IndividualObservation.behavior
)
Index("idx_individual_video_time", IndividualObservation.video_id, IndividualObservation.start_sec)


VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}
)

CSV_TEMPLATES: dict[str, str] = {
    "model_annotations": (
        "video_uid,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec\n"
        "CAM01/VIDEO_001.mp4,species,species_model_a,deer,,0.92,0,12.0\n"
        "CAM01/VIDEO_001.mp4,behavior,behavior_model_a,reacts_to_camera,,0.83,0,12.0\n"
        "CAM01/VIDEO_002.mp4,blank_non_blank,blank_model,blank,,0.98,0,\n"
    )
}

REPO_ROOT = Path(__file__).parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_DB_FILENAME = "review_data.db"


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------


def _find_ffprobe() -> str | None:
    """Return the ffprobe executable path, or None if not installed."""
    return shutil.which("ffprobe")


def _probe_video(path: Path) -> tuple[float | None, bool, str | None]:
    """
    Run ffprobe on *path* and return ``(duration_sec, is_valid, error_message)``.

    Uses a single fast JSON query on the *format* section only – no stream
    decoding, so it completes in milliseconds even for large files.

    Returns:
        duration_sec   – float seconds, or None if not parseable
        is_valid       – True when the container is readable by ffprobe
        error_message  – human-readable string when is_valid is False, else None
    """
    ffprobe = _find_ffprobe()
    if ffprobe is None:
        # ffprobe not installed: treat all videos as valid with unknown duration.
        return None, True, None

    cmd = [
        ffprobe,
        "-v",
        "error",  # suppress all non-error output
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_FFPROBE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return None, False, f"ffprobe timed out after {_FFPROBE_TIMEOUT_SEC}s"
    except OSError as exc:
        return None, False, f"ffprobe OS error: {exc}"

    if result.returncode != 0:
        stderr_text = result.stderr.decode(errors="replace").strip()
        # Keep only the first 200 chars to avoid bloating the DB.
        return None, False, stderr_text[:200] or "ffprobe returned non-zero exit code"

    try:
        data = json.loads(result.stdout)
        raw_duration = data.get("format", {}).get("duration")
        duration = float(raw_duration) if raw_duration is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, False, "ffprobe returned unparseable JSON"

    return duration, True, None


def _probe_many(
    paths: list[Path],
    max_workers: int = _FFPROBE_MAX_WORKERS,
) -> dict[Path, tuple[float | None, bool, str | None]]:
    """
    Probe *paths* in parallel and return a mapping of
    ``path -> (duration_sec, is_valid, error_message)``.

    Falls back gracefully when ffprobe is absent.
    """
    if not paths:
        return {}

    results: dict[Path, tuple[float | None, bool, str | None]] = {}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(paths))) as pool:
        future_to_path = {pool.submit(_probe_video, p): p for p in paths}
        for future in as_completed(future_to_path):
            path = future_to_path[future]
            try:
                results[path] = future.result()
            except Exception as exc:  # pragma: no cover – safety net
                results[path] = (None, False, str(exc))

    return results


# ---------------------------------------------------------------------------


class LocalDataProvider:
    """SQLite-backed local data provider for manual review + constrained model imports."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._load_yaml_config(config_path)
        self.video_dir = self._required_path(cfg, "video_dir")
        self.db_dir = self._required_path(cfg, "db_dir")
        self.db_dir.mkdir(parents=True, exist_ok=True)

        self._species: list[str] = self._load_species(cfg)
        self._species_behaviors: dict[str, list[str]] = self._load_species_behaviors(cfg)
        behavior_defaults = cfg.get("behavior_defaults")
        self._behavior_defaults: list[str] = self._normalize_string_list(
            behavior_defaults, "behaviors"
        )
        self._priority_csv_path: Path | None = self._optional_path(cfg.get("priority_csv_path"))
        self._consensus_min_probability: float = float(cfg.get("consensus_min_probability", 0.0))

        db_filename = str(cfg.get("db_filename") or DEFAULT_DB_FILENAME).strip()
        if not db_filename:
            raise ValueError("`db_filename` cannot be empty.")

        self._db_path = self.db_dir / db_filename
        recreate_on_start = bool(cfg.get("recreate_db_on_start", False)) or (
            str(os.getenv("REVIEW_APP_RECREATE_DB", "")).lower() in {"1", "true", "yes"}
        )
        if recreate_on_start and self._db_path.exists():
            try:
                self._db_path.unlink()
            except PermissionError as exc:
                raise RuntimeError(
                    f"Cannot recreate sqlite DB at `{self._db_path}` due to permissions: {exc}"
                ) from exc

        self.engine = create_engine(f"sqlite:///{self._db_path}")
        if self._needs_schema_reset():
            self.engine.dispose()
            if self._db_path.exists():
                try:
                    self._db_path.unlink()
                except PermissionError as exc:
                    raise RuntimeError(
                        f"Cannot reset incompatible sqlite DB at `{self._db_path}` due to permissions: {exc}"
                    ) from exc
            self.engine = create_engine(f"sqlite:///{self._db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self._sync_videos_table()
        self._sync_priority_table()

    def _needs_schema_reset(self) -> bool:
        """
        Detect incompatible legacy schemas and trigger full DB recreation.
        """
        if not self._db_path.exists():
            return False

        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute("PRAGMA table_info(videos)").fetchall()
                if not rows:
                    return False
                columns = {str(r[1]) for r in rows}
        except Exception:
            return True

        required = {"is_valid", "validation_error"}
        return not required.issubset(columns)

    @property
    def _app_config_path(self) -> Path:
        return self.db_dir / "config.json"

    @staticmethod
    def _resolve_path(raw_path: str | Path) -> Path:
        p = Path(raw_path).expanduser()
        if p.is_absolute():
            return p
        return (REPO_ROOT / p).resolve()

    @staticmethod
    def _optional_path(raw_path: Any) -> Path | None:
        if raw_path is None:
            return None
        txt = str(raw_path).strip()
        if not txt:
            return None
        return LocalDataProvider._resolve_path(txt)

    @staticmethod
    def _load_yaml_config(config_path: str | Path | None) -> dict[str, Any]:
        if config_path is None:
            env_path = os.getenv("LOCAL_CONFIG_YAML")
            config_path = env_path if env_path else DEFAULT_CONFIG_PATH
        p = LocalDataProvider._resolve_path(config_path)
        if not p.exists():
            raise FileNotFoundError(
                f"Config file not found: `{p}`. "
                "Set LOCAL_CONFIG_YAML or pass config_path to LocalDataProvider."
            )
        with open(p) as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file `{p}` must be a YAML mapping.")
        return loaded

    @staticmethod
    def _required_path(cfg: dict[str, Any], key: str) -> Path:
        raw = cfg.get(key)
        if not raw:
            raise ValueError(f"Missing required config key `{key}`.")
        return LocalDataProvider._resolve_path(raw)

    @staticmethod
    def _normalize_string_list(values: Any, key_name: str) -> list[str]:
        if not isinstance(values, list):
            raise ValueError(f"`{key_name}` must be a list of strings.")
        normalized = [str(v).strip() for v in values if str(v).strip()]
        if not normalized:
            raise ValueError(f"`{key_name}` must contain at least one non-empty value.")
        return normalized

    @staticmethod
    def _load_species(cfg: dict[str, Any]) -> list[str]:
        path = cfg.get("species_csv_path")
        column = cfg.get("species_column")
        if not path or not column:
            raise ValueError(
                "Config must define either `species` or both `species_csv_path` and `species_column`."
            )

        p = LocalDataProvider._resolve_path(path)
        if not p.exists():
            raise FileNotFoundError(f"Species CSV file not found at `{path}`.")

        df = pd.read_csv(p, sep=";")
        if column not in df.columns:
            available_cols = ", ".join(df.columns)
            raise ValueError(
                f"Column `{column}` not found in species CSV. Available: {available_cols}"
            )

        species_list = sorted({str(s).strip() for s in df[column].dropna() if str(s).strip()})
        if not species_list:
            raise ValueError(f"No species names found in column `{column}` of `{path}`.")
        return species_list

    @staticmethod
    def _load_species_behaviors(cfg: dict[str, Any]) -> dict[str, list[str]]:
        path = cfg.get("species_behaviors_csv_path")
        if not path:
            return {}

        p = LocalDataProvider._resolve_path(path)
        if not p.exists():
            return {}

        try:
            df = pd.read_csv(p, sep=";")
            if "Species" not in df.columns or "Behavior" not in df.columns:
                return {}

            mapping: dict[str, list[str]] = {}
            for _, row in df.iterrows():
                species = str(row["Species"]).strip()
                behavior = str(row["Behavior"]).strip()
                if species and behavior:
                    mapping.setdefault(species, []).append(behavior)
            return mapping
        except Exception:
            return {}

    @staticmethod
    def _utcnow_dt() -> datetime:
        return datetime.now(timezone.utc)

    def _video_id_from_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.video_dir))
        except ValueError:
            return str(path.name)

    def _scan_videos(self) -> pd.DataFrame:
        if not self.video_dir.exists():
            return pd.DataFrame(
                columns=["video_id", "video_path", "camera_id", "created_at", "duration_sec"]
            )

        rows: list[dict[str, Any]] = []
        for p in sorted(self.video_dir.rglob("*")):
            if p.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            camera_id = p.parent.name if p.parent != self.video_dir else "default"
            created_at = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            rows.append(
                {
                    "video_id": self._video_id_from_path(p),
                    "video_path": str(p),
                    "camera_id": camera_id,
                    "created_at": created_at,
                    # duration/validity filled in by _sync_videos_table for new rows
                    "duration_sec": None,
                }
            )
        return pd.DataFrame(rows)

    def _sync_videos_table(self) -> None:
        """
        Sync the filesystem scan into the ``videos`` table.

        New videos are probed with ffprobe **in parallel** before the DB write.
        Existing rows are updated (path/camera/timestamp) but their
        ``duration_sec``, ``is_valid``, and ``validation_error`` are preserved
        so we never re-probe files that are already known.
        """
        scanned = self._scan_videos()
        now = self._utcnow_dt()

        with self.Session() as session:
            if scanned.empty:
                session.commit()
                return

            # ----------------------------------------------------------------
            # Determine which video_ids are genuinely new (not in DB yet).
            # ----------------------------------------------------------------
            all_existing_ids: set[str] = {
                row[0] for row in session.execute(select(Video.video_id)).fetchall()
            }
            new_rows = scanned[~scanned["video_id"].isin(all_existing_ids)]

            # ----------------------------------------------------------------
            # Probe only the new videos – in parallel for speed.
            # ----------------------------------------------------------------
            probe_results: dict[str, tuple[float | None, bool, str | None]] = {}
            if not new_rows.empty:
                path_map: dict[Path, str] = {
                    Path(row["video_path"]): row["video_id"] for _, row in new_rows.iterrows()
                }
                raw = _probe_many(list(path_map.keys()))
                probe_results = {path_map[p]: result for p, result in raw.items()}

            # ----------------------------------------------------------------
            # Upsert all scanned rows.
            # ----------------------------------------------------------------
            for _, row in scanned.iterrows():
                vid_id: str = row["video_id"]
                existing = session.get(Video, vid_id)

                if existing is None:
                    # Brand-new video – attach probe result.
                    duration, is_valid, validation_error = probe_results.get(
                        vid_id, (None, None, None)
                    )
                    session.add(
                        Video(
                            video_id=vid_id,
                            video_path=row["video_path"],
                            camera_id=row["camera_id"],
                            created_at=row["created_at"],
                            duration_sec=duration,
                            last_seen_at=now,
                            is_valid=is_valid,
                            validation_error=validation_error,
                        )
                    )
                else:
                    # Existing video – refresh filesystem metadata only.
                    existing.video_path = row["video_path"]
                    existing.camera_id = row["camera_id"]
                    existing.created_at = row["created_at"]
                    existing.last_seen_at = now
                    # duration / is_valid / validation_error are intentionally
                    # NOT overwritten so we don't re-probe on every startup.

            session.commit()

    def reprobe_video(self, video_id: str) -> None:
        """
        Force a fresh ffprobe run for a single video and persist the result.
        Useful after a file is replaced or repaired.
        """
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise ValueError(f"Unknown video_id: {video_id!r}")
            duration, is_valid, validation_error = _probe_video(Path(video.video_path))
            video.duration_sec = duration
            video.is_valid = is_valid
            video.validation_error = validation_error
            session.commit()

    def reprobe_invalid_videos(self) -> dict[str, Any]:
        """
        Re-probe all videos currently marked as invalid (e.g. after a bulk
        file repair).  Returns a summary dict.
        """
        with self.Session() as session:
            invalid_videos = (
                session.query(Video)
                .filter((Video.is_valid == False) | (Video.is_valid.is_(None)))  # noqa: E712
                .all()
            )
            if not invalid_videos:
                return {"re_probed": 0, "now_valid": 0, "still_invalid": 0}

            path_map: dict[Path, str] = {Path(v.video_path): v.video_id for v in invalid_videos}
            probe_results = _probe_many(list(path_map.keys()))

            now_valid = 0
            still_invalid = 0
            for path, (duration, is_valid, validation_error) in probe_results.items():
                vid_id = path_map[path]
                video = session.get(Video, vid_id)
                if video is None:
                    continue
                video.duration_sec = duration
                video.is_valid = is_valid
                video.validation_error = validation_error
                if is_valid:
                    now_valid += 1
                else:
                    still_invalid += 1
            session.commit()

        return {
            "re_probed": len(invalid_videos),
            "now_valid": now_valid,
            "still_invalid": still_invalid,
        }

    def _sync_priority_table(self) -> None:
        with self.Session() as session:
            session.query(VideoPriority).delete(synchronize_session=False)
            if self._priority_csv_path and self._priority_csv_path.exists():
                try:
                    df = pd.read_csv(self._priority_csv_path)
                    required = {"video_id", "annotation_importance_score"}
                    if required.issubset(set(df.columns)):
                        for _, row in df.iterrows():
                            vid = str(row.get("video_id") or "").strip()
                            score = pd.to_numeric(
                                row.get("annotation_importance_score"), errors="coerce"
                            )
                            if not vid or pd.isna(score):
                                continue
                            session.add(
                                VideoPriority(
                                    video_id=vid,
                                    annotation_importance_score=float(score),
                                )
                            )
                except Exception:
                    pass
            session.commit()

    def check_db_exists(self) -> bool:
        return self.video_dir.exists() and any(
            p.suffix.lower() in VIDEO_EXTENSIONS for p in self.video_dir.rglob("*")
        )

    def get_valid_species(self) -> list[str]:
        return list(self._species)

    def get_config(self) -> dict:
        if self._app_config_path.exists():
            with open(self._app_config_path) as f:
                return json.load(f)
        return {}

    def save_config(self, config: dict) -> None:
        with open(self._app_config_path, "w") as f:
            json.dump(config, f, indent=2)

    def get_overrides(self) -> dict:
        return self.get_config()

    def get_csv_templates(self) -> dict[str, str]:
        return CSV_TEMPLATES.copy()

    def get_behaviors_for_species(self, species_name: str) -> list[str]:
        defaults = ["unlabeled"] + self._behavior_defaults
        extras = self._species_behaviors.get(species_name, [])
        seen = set()
        result = []
        for b in defaults + extras:
            if b not in seen:
                result.append(b)
                seen.add(b)
        return result

    def _get_model_annotations_df(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(select(ModelAnnotation), conn)

    def _get_individuals_df(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(select(IndividualObservation), conn)

    def _get_labels_df(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(select(VideoLabel), conn)

    def get_queue_filter_options(self) -> dict[str, list[str]]:
        self._sync_videos_table()
        self._sync_priority_table()
        with self.engine.connect() as conn:
            camera_values = (
                pd.read_sql(
                    text(
                        "SELECT DISTINCT camera_id FROM videos WHERE camera_id IS NOT NULL ORDER BY camera_id"
                    ),
                    conn,
                )["camera_id"]
                .astype(str)
                .tolist()
            )
            species_values = (
                pd.read_sql(
                    text(
                        "SELECT DISTINCT species FROM individual_observations "
                        "WHERE species IS NOT NULL AND TRIM(species) <> '' ORDER BY species"
                    ),
                    conn,
                )["species"]
                .astype(str)
                .tolist()
            )
            behavior_values = (
                pd.read_sql(
                    text(
                        "SELECT DISTINCT behavior FROM individual_observations "
                        "WHERE behavior IS NOT NULL AND TRIM(behavior) <> '' ORDER BY behavior"
                    ),
                    conn,
                )["behavior"]
                .astype(str)
                .tolist()
            )
            possible_species_values = (
                pd.read_sql(
                    text(
                        "SELECT DISTINCT value_text FROM model_annotations "
                        "WHERE annotation_type='species' AND value_text IS NOT NULL "
                        "AND TRIM(value_text) <> '' ORDER BY value_text"
                    ),
                    conn,
                )["value_text"]
                .astype(str)
                .tolist()
            )

        return {
            "camera_values": camera_values,
            "species_values": species_values,
            "possible_species_values": possible_species_values,
            "behavior_values": behavior_values,
        }

    def get_video_queue(self, filters: dict) -> list[str]:
        self._sync_videos_table()
        self._sync_priority_table()

        params: dict[str, Any] = {
            "search_query": f"%{(filters.get('search_query') or '').strip().lower()}%",
            "selected_camera": filters.get("selected_camera", "All"),
            "selected_species": filters.get("selected_species", "All"),
            "selected_possible_species": filters.get("selected_possible_species", "All"),
            "selected_blank_non_blank": filters.get("selected_blank_non_blank", "All"),
            "selected_behavior": filters.get("selected_behavior", "All"),
            "selected_review": filters.get("selected_review", "All"),
            "include_unranked": 1 if bool(filters.get("include_unranked", False)) else 0,
        }

        sql = text(
            """
            WITH model_blank AS (
                SELECT
                    ma.video_id,
                    ma.value_text AS blank_non_blank_model_result,
                    ma.probability,
                    ROW_NUMBER() OVER (
                        PARTITION BY ma.video_id
                        ORDER BY COALESCE(ma.probability, -1.0) DESC, ma.updated_at DESC
                    ) AS rn
                FROM model_annotations ma
                WHERE ma.annotation_type = 'blank_non_blank'
            ),
            effective_blank AS (
                SELECT
                    v.video_id,
                    CASE
                        WHEN vl.is_blank IS NOT NULL THEN CASE WHEN vl.is_blank = 1 THEN 'blank' ELSE 'non_blank' END
                        WHEN mb.rn = 1 AND LOWER(TRIM(mb.blank_non_blank_model_result)) IN ('blank', 'non_blank')
                            THEN LOWER(TRIM(mb.blank_non_blank_model_result))
                        ELSE NULL
                    END AS blank_non_blank_final_result
                FROM videos v
                LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                LEFT JOIN model_blank mb ON mb.video_id = v.video_id
            ),
            review_state AS (
                SELECT
                    v.video_id,
                    CASE
                        WHEN vl.is_blank IS NULL
                             AND NOT EXISTS (
                                 SELECT 1 FROM individual_observations io WHERE io.video_id = v.video_id
                             )
                        THEN 1 ELSE 0
                    END AS needs_manual_review
                FROM videos v
                LEFT JOIN video_labels vl ON vl.video_id = v.video_id
            ),
            priority_counts AS (
                SELECT COUNT(*) AS cnt FROM video_priority
            )
            SELECT v.video_id
            FROM videos v
            LEFT JOIN video_priority vp ON vp.video_id = v.video_id
            LEFT JOIN effective_blank eb ON eb.video_id = v.video_id
            LEFT JOIN review_state rs ON rs.video_id = v.video_id
            CROSS JOIN priority_counts pc
            WHERE
                (
                    :search_query = '%%'
                    OR LOWER(v.video_id) LIKE :search_query
                    OR LOWER(v.video_path) LIKE :search_query
                )
                AND (:selected_camera = 'All' OR v.camera_id = :selected_camera)
                AND (
                    :selected_species = 'All'
                    OR EXISTS (
                        SELECT 1
                        FROM individual_observations io
                        WHERE io.video_id = v.video_id AND io.species = :selected_species
                    )
                )
                AND (
                    :selected_possible_species = 'All'
                    OR EXISTS (
                        SELECT 1
                        FROM model_annotations ma
                        WHERE ma.video_id = v.video_id
                          AND ma.annotation_type = 'species'
                          AND ma.value_text = :selected_possible_species
                    )
                )
                AND (
                    :selected_blank_non_blank = 'All'
                    OR (:selected_blank_non_blank = 'Blank' AND eb.blank_non_blank_final_result = 'blank')
                    OR (:selected_blank_non_blank = 'Non-Blank' AND eb.blank_non_blank_final_result = 'non_blank')
                    OR (:selected_blank_non_blank = 'Unknown' AND eb.blank_non_blank_final_result IS NULL)
                )
                AND (
                    :selected_behavior = 'All'
                    OR (
                        :selected_behavior = 'Has Behavior'
                        AND EXISTS (
                            SELECT 1 FROM individual_observations io
                            WHERE io.video_id = v.video_id
                              AND io.behavior IS NOT NULL
                              AND TRIM(io.behavior) <> ''
                        )
                    )
                    OR (
                        :selected_behavior = 'No Behavior'
                        AND NOT EXISTS (
                            SELECT 1 FROM individual_observations io
                            WHERE io.video_id = v.video_id
                              AND io.behavior IS NOT NULL
                              AND TRIM(io.behavior) <> ''
                        )
                    )
                    OR (
                        :selected_behavior NOT IN ('All', 'Has Behavior', 'No Behavior')
                        AND EXISTS (
                            SELECT 1 FROM individual_observations io
                            WHERE io.video_id = v.video_id
                              AND io.behavior = :selected_behavior
                        )
                    )
                )
                AND (
                    :selected_review = 'All'
                    OR (:selected_review = 'Needs Review' AND rs.needs_manual_review = 1)
                    OR (:selected_review = 'No Review' AND rs.needs_manual_review = 0)
                )
                AND (
                    :include_unranked = 1
                    OR pc.cnt = 0
                    OR vp.video_id IS NOT NULL
                )
            ORDER BY
                CASE
                    WHEN pc.cnt > 0 THEN CASE WHEN vp.video_id IS NULL THEN 1 ELSE 0 END
                    ELSE 0
                END,
                CASE WHEN pc.cnt > 0 THEN vp.annotation_importance_score END DESC,
                v.created_at DESC,
                v.video_id ASC
            """
        )

        with self.engine.connect() as conn:
            rows = pd.read_sql(sql, conn, params=params)
        if rows.empty:
            return []
        return rows["video_id"].astype(str).tolist()

    def get_video_detail(self, video_id: str) -> dict | None:
        self._sync_videos_table()
        with self.engine.connect() as conn:
            detail_df = pd.read_sql(
                text(
                    """
                    WITH model_blank AS (
                        SELECT
                            ma.video_id,
                            ma.value_text AS blank_non_blank_model_result,
                            ma.probability,
                            ROW_NUMBER() OVER (
                                PARTITION BY ma.video_id
                                ORDER BY COALESCE(ma.probability, -1.0) DESC, ma.updated_at DESC
                            ) AS rn
                        FROM model_annotations ma
                        WHERE ma.annotation_type = 'blank_non_blank'
                    ),
                    model_species_consensus AS (
                        SELECT
                            ma.video_id,
                            CASE
                                WHEN COUNT(DISTINCT ma.value_text) = 1 THEN MAX(ma.value_text)
                                ELSE 'UNKNOWN'
                            END AS classification_consensus
                        FROM model_annotations ma
                        WHERE ma.annotation_type = 'species'
                          AND COALESCE(ma.probability, 0.0) >= :min_prob
                          AND ma.value_text IS NOT NULL
                          AND TRIM(ma.value_text) <> ''
                        GROUP BY ma.video_id
                    ),
                    manual_summary AS (
                        SELECT
                            io.video_id,
                            GROUP_CONCAT(DISTINCT io.behavior) AS behavior_prediction,
                            COUNT(*) AS individual_count
                        FROM individual_observations io
                        GROUP BY io.video_id
                    ),
                    review_state AS (
                        SELECT
                            v.video_id,
                            CASE
                                WHEN vl.is_blank IS NULL
                                     AND NOT EXISTS (
                                         SELECT 1 FROM individual_observations io2
                                         WHERE io2.video_id = v.video_id
                                     )
                                THEN 1 ELSE 0
                            END AS needs_manual_review
                        FROM videos v
                        LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    )
                    SELECT
                        v.video_id,
                        v.video_path,
                        v.camera_id,
                        v.duration_sec,
                        v.created_at,
                        v.is_valid AS is_video_valid,
                        v.validation_error AS video_validation_details,
                        vl.is_blank,
                        vl.labeled_at,
                        ms.behavior_prediction,
                        ms.individual_count,
                        COALESCE(mcs.classification_consensus, 'UNKNOWN') AS classification_consensus,
                        CASE
                            WHEN vl.is_blank IS NOT NULL THEN CASE WHEN vl.is_blank = 1 THEN 'blank' ELSE 'non_blank' END
                            WHEN mb.rn = 1 AND LOWER(TRIM(mb.blank_non_blank_model_result)) IN ('blank', 'non_blank')
                                THEN LOWER(TRIM(mb.blank_non_blank_model_result))
                            ELSE NULL
                        END AS blank_non_blank_final_result,
                        rs.needs_manual_review
                    FROM videos v
                    LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    LEFT JOIN model_blank mb ON mb.video_id = v.video_id
                    LEFT JOIN manual_summary ms ON ms.video_id = v.video_id
                    LEFT JOIN review_state rs ON rs.video_id = v.video_id
                    WHERE v.video_id = :video_id
                    """
                ),
                conn,
                params={"video_id": video_id, "min_prob": self._consensus_min_probability},
            )

            manual_rows = pd.read_sql(
                text(
                    """
                    SELECT species, behavior, start_sec, end_sec
                    FROM individual_observations
                    WHERE video_id = :video_id
                    ORDER BY COALESCE(start_sec, 0.0), species
                    """
                ),
                conn,
                params={"video_id": video_id},
            )

        if detail_df.empty:
            return None

        row = detail_df.iloc[0].to_dict()
        selections = []
        for _, manual in manual_rows.iterrows():
            selections.append(
                {
                    "species": str(manual.get("species") or "unknown"),
                    "behavior": str(manual.get("behavior") or "unlabeled"),
                    "start_sec": float(manual.get("start_sec") or 0.0),
                    "end_sec": None
                    if pd.isna(manual.get("end_sec"))
                    else float(manual.get("end_sec")),
                }
            )
        row["manual_selections"] = selections
        row["species_behavior_json"] = json.dumps(selections) if selections else None
        row["manual_review_prediction"] = (
            ", ".join(
                [
                    (
                        f"{s['species']} ({s['behavior']}) @ {s['start_sec']}s"
                        if s["end_sec"] is None
                        else f"{s['species']} ({s['behavior']}) {s['start_sec']}s-{s['end_sec']}s"
                    )
                    for s in selections
                ]
            )
            if selections
            else None
        )
        row["final_species_prediction"] = row["manual_review_prediction"]
        row["current_stage"] = (
            "manual_review" if bool(row.get("needs_manual_review")) else "completed"
        )
        row["status"] = "NEEDS_REVIEW" if bool(row.get("needs_manual_review")) else "success"
        row["is_video_valid"] = (
            True if row.get("is_video_valid") is None else bool(row.get("is_video_valid"))
        )
        return row

    def get_model_annotations(self, video_id: str) -> pd.DataFrame:
        with self.engine.connect() as conn:
            model_df = pd.read_sql(
                text(
                    """
                    SELECT
                        model_name,
                        annotation_type,
                        value_text,
                        probability,
                        updated_at AS created_at
                    FROM model_annotations
                    WHERE video_id = :video_id
                    ORDER BY updated_at DESC
                    """
                ),
                conn,
                params={"video_id": video_id},
            )
        if model_df.empty:
            return pd.DataFrame(
                columns=[
                    "model_name",
                    "annotation_type",
                    "value_text",
                    "probability",
                    "created_at",
                ]
            )
        model_df["created_at"] = pd.to_datetime(model_df["created_at"], errors="coerce")
        return model_df

    def update_manual_review(
        self,
        video_id: str,
        selections: list[dict] | None,
        annotator: str = "local",
    ) -> None:
        if selections is None:
            return

        now = self._utcnow_dt()
        session_id = str(uuid.uuid4())

        normalized: list[dict[str, Any]] = []
        for selection in selections:
            species = str(selection.get("species") or "").strip() or "unknown"
            behavior = str(selection.get("behavior") or "").strip() or "unlabeled"
            if "start_sec" in selection:
                start_sec = pd.to_numeric(selection.get("start_sec"), errors="coerce")
            else:
                start_sec = pd.to_numeric(selection.get("timestamp"), errors="coerce")
            if pd.isna(start_sec):
                start_sec = 0.0

            end_sec_raw = selection.get("end_sec")
            end_sec = pd.to_numeric(end_sec_raw, errors="coerce")
            end_sec_val: float | None = None if pd.isna(end_sec) else float(end_sec)

            normalized.append(
                {
                    "species": species,
                    "behavior": behavior,
                    "start_sec": float(start_sec),
                    "end_sec": end_sec_val,
                }
            )

        if not normalized:
            is_blank = None
        else:
            is_blank = len(normalized) == 1 and normalized[0]["species"].lower() == "blank"

        with self.Session() as session:
            label = session.get(VideoLabel, video_id)
            if label is None:
                label = VideoLabel(video_id=video_id)
                session.add(label)
            label.is_blank = is_blank
            label.labeled_by = annotator
            label.labeled_at = now

            session.query(IndividualObservation).filter(
                IndividualObservation.video_id == video_id
            ).delete(synchronize_session=False)

            if is_blank is False:
                for row in normalized:
                    session.add(
                        IndividualObservation(
                            video_id=video_id,
                            session_id=session_id,
                            species=row["species"],
                            behavior=row["behavior"],
                            start_sec=row["start_sec"],
                            end_sec=row["end_sec"],
                            created_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()

    def restore_video_snapshot(self, snapshot: dict) -> None:
        if not snapshot or "video_id" not in snapshot:
            return

        selections: list[dict[str, Any]] = []
        raw = snapshot.get("species_behavior_json")
        if raw:
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    for item in data:
                        start = item.get("start_sec", item.get("timestamp", 0.0))
                        selections.append(
                            {
                                "species": item.get("species", "unknown"),
                                "behavior": item.get("behavior", "unlabeled"),
                                "start_sec": start,
                                "end_sec": item.get("end_sec"),
                            }
                        )
            except Exception:
                selections = []

        if not selections and snapshot.get("blank_non_blank_final_result") == "blank":
            selections = [
                {"species": "blank", "behavior": "unlabeled", "start_sec": 0.0, "end_sec": None}
            ]

        if not selections and snapshot.get("final_species_prediction"):
            selections = [
                {
                    "species": snapshot["final_species_prediction"],
                    "behavior": "unlabeled",
                    "start_sec": 0.0,
                    "end_sec": snapshot.get("duration_sec"),
                }
            ]

        if selections:
            self.update_manual_review(snapshot["video_id"], selections)

    def get_pipeline_progress_summary(self) -> pd.DataFrame:
        df = self.get_all_videos()
        if df.empty:
            return pd.DataFrame(columns=["current_stage", "status", "count"])
        return (
            df.groupby(["current_stage", "status"], dropna=False).size().reset_index(name="count")
        )

    def get_flow_data(self) -> pd.DataFrame:
        df = self.get_all_videos()
        if df.empty:
            return pd.DataFrame(columns=["source", "target", "value"])
        needs = int((df["needs_manual_review"] == True).sum())  # noqa: E712
        done = len(df) - needs
        return pd.DataFrame(
            [
                {"source": "All Videos", "target": "Needs Review", "value": needs},
                {"source": "All Videos", "target": "Completed", "value": done},
            ]
        )

    @staticmethod
    def _normalize_annotation_type(annotation_type: str) -> str:
        supported = {"blank_non_blank", "species", "behavior"}
        normalized = (annotation_type or "").strip().lower()
        if normalized not in supported:
            raise ValueError(
                f"Unsupported annotation_type `{annotation_type}`. Use one of {sorted(supported)}"
            )
        return normalized

    def validate_model_csv(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        src = df.copy()
        src.columns = [str(c).strip() for c in src.columns]

        required = {"video_uid", "annotation_type", "model_name"}
        missing = required - set(src.columns)
        if missing:
            raise ValueError(f"CSV must include columns: {', '.join(sorted(missing))}")

        known_videos = set(self.get_all_videos()["video_id"].astype(str))
        prepared_rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for idx, row in src.iterrows():
            row_num = int(idx) + 2
            video_uid = str(row.get("video_uid", "")).strip()
            model_name = str(row.get("model_name", "")).strip()
            raw_type = str(row.get("annotation_type", "")).strip()

            if not video_uid:
                errors.append({"row_number": row_num, "error": "Missing video_uid"})
                continue
            if video_uid not in known_videos:
                errors.append(
                    {"row_number": row_num, "video_uid": video_uid, "error": "Unknown video_uid"}
                )
                continue
            if not model_name:
                errors.append(
                    {"row_number": row_num, "video_uid": video_uid, "error": "Missing model_name"}
                )
                continue

            try:
                annotation_type = self._normalize_annotation_type(raw_type)
            except ValueError as exc:
                errors.append({"row_number": row_num, "video_uid": video_uid, "error": str(exc)})
                continue

            probability = pd.to_numeric(row.get("probability"), errors="coerce")
            probability = None if pd.isna(probability) else float(probability)
            if probability is not None and not (0.0 <= probability <= 1.0):
                errors.append(
                    {
                        "row_number": row_num,
                        "video_uid": video_uid,
                        "error": "probability must be in [0, 1]",
                    }
                )
                continue

            t_start = pd.to_numeric(row.get("t_start_sec"), errors="coerce")
            t_start = None if pd.isna(t_start) else float(t_start)
            t_end = pd.to_numeric(row.get("t_end_sec"), errors="coerce")
            t_end = None if pd.isna(t_end) else float(t_end)

            value_text = row.get("value_text")
            value_text = None if pd.isna(value_text) else (str(value_text).strip() or None)
            value_num = pd.to_numeric(row.get("value_num"), errors="coerce")
            value_num = None if pd.isna(value_num) else float(value_num)

            prepared_rows.append(
                {
                    "video_id": video_uid,
                    "annotation_type": annotation_type,
                    "model_name": model_name,
                    "value_text": value_text,
                    "value_num": value_num,
                    "probability": probability,
                    "t_start_sec": t_start,
                    "t_end_sec": t_end,
                }
            )

        return pd.DataFrame(prepared_rows), pd.DataFrame(errors)

    def import_model_csv(self, cleaned_df: pd.DataFrame) -> dict[str, Any]:
        if cleaned_df.empty:
            return {"inserted_rows": 0, "upserted_rows": 0}

        upserted = 0
        with self.Session() as session:
            for _, row in cleaned_df.iterrows():
                existing = (
                    session.query(ModelAnnotation)
                    .filter(
                        ModelAnnotation.video_id == row["video_id"],
                        ModelAnnotation.model_name == row["model_name"],
                        ModelAnnotation.annotation_type == row["annotation_type"],
                    )
                    .one_or_none()
                )

                if existing is None:
                    existing = ModelAnnotation(
                        video_id=row["video_id"],
                        model_name=row["model_name"],
                        annotation_type=row["annotation_type"],
                    )
                    session.add(existing)

                existing.value_text = row.get("value_text")
                existing.value_num = row.get("value_num")
                existing.probability = row.get("probability")
                existing.t_start_sec = row.get("t_start_sec")
                existing.t_end_sec = row.get("t_end_sec")
                existing.updated_at = self._utcnow_dt()
                upserted += 1

            session.commit()

        return {"inserted_rows": int(len(cleaned_df)), "upserted_rows": int(upserted)}
