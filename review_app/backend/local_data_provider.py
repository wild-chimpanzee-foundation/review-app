from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv

from review_app.app.config import get_app_dir, get_config_path
from review_app.backend.utils import (
    get_default_species_from_annotations,
    needs_browser_transcode,
)
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
    event,
    func,
    inspect as sa_inspect,
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
    is_web_safe = Column(Boolean, nullable=True)
    validation_error = Column(String, nullable=True)
    transcoded_path = Column(String, nullable=True)


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

Index("idx_videos_is_valid", Video.is_valid)
Index("idx_model_ann_type_value", ModelAnnotation.annotation_type, ModelAnnotation.value_text)
# Covers: WHERE annotation_type='species' AND value_text=:ps  (possible_species filter)
Index(
    "idx_model_ann_type_text_video",
    ModelAnnotation.annotation_type,
    ModelAnnotation.value_text,
    ModelAnnotation.video_id,
)

# Covers: WHERE annotation_type='blank_non_blank' inside effective_blank CTE
Index(
    "idx_model_ann_blank_probe",
    ModelAnnotation.annotation_type,
    ModelAnnotation.video_id,
    ModelAnnotation.probability,
)

# Covers: WHERE video_id=? AND behavior=?  (behavior filter EXISTS)
# (video_id + species already exists; behavior composite is missing)
Index(
    "idx_individual_behavior_video",
    IndividualObservation.behavior,
    IndividualObservation.video_id,
)

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

if getattr(sys, "frozen", False):
    REPO_ROOT = Path(sys.executable).parent
else:
    REPO_ROOT = Path(__file__).parents[2]


DEFAULT_CONFIG_PATH = get_config_path()
DEFAULT_DB_FILENAME = "review_data.db"


# ---------------------------------------------------------------------------
# subprocess helpers
# ---------------------------------------------------------------------------


def _subprocess_env() -> dict:
    """Return an environment safe for subprocesses when running frozen.

    PyInstaller prepends _internal/ to LD_LIBRARY_PATH so its bundled libs
    are found by Python. Subprocesses (ffprobe, ffmpeg) inherit this and can
    crash when the bundled libs conflict with system libs they link against.
    Restoring the original value fixes that.
    """
    env = os.environ.copy()
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        orig = env.get("LD_LIBRARY_PATH_ORIG", "")
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------


def _find_ffprobe() -> str | None:
    """Return the ffprobe executable path, or None if not installed."""
    return shutil.which("ffprobe")


def _probe_video(path: Path) -> tuple[float | None, bool, bool, str | None]:
    """
    Run ffprobe on *path* and return ``(duration_sec, is_valid, is_web_safe, error_message)``.

    Uses a single fast JSON query on the *format* section only – no stream
    decoding, so it completes in milliseconds even for large files.

    Returns:
        duration_sec   – float seconds, or None if not parseable
        is_valid       – True when the container is readable by ffprobe
        is_web_safe    – True if codec/container are known to work in browsers
        error_message  – human-readable string when is_valid is False, else None
    """
    ffprobe = _find_ffprobe()
    if ffprobe is None:
        # ffprobe not installed: treat all videos as valid with unknown duration.
        return None, False, False, "ffprobe executable not found"

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:stream=duration,codec_name,codec_type",
        "-of",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT_SEC,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return None, False, False, f"ffprobe timed out after {_FFPROBE_TIMEOUT_SEC}s"
    except OSError as exc:
        return None, False, False, f"ffprobe OS error: {exc}"

    if result.returncode != 0:
        stderr_text = result.stderr.strip()
        # Keep only the first 200 chars to avoid bloating the DB.
        return None, False, False, stderr_text[:200] or "ffprobe returned non-zero exit code"

    try:
        data = json.loads(result.stdout)
        # Try format first, then fallback to the first stream
        raw_duration = data.get("format", {}).get("duration")

        if raw_duration is None:
            streams = data.get("streams", [])
            if streams:
                raw_duration = streams[0].get("duration")

        # Web safe check
        format_name = data.get("format", {}).get("format_name", "").lower()
        streams = data.get("streams", [])
        video_codec = next(
            (s.get("codec_name", "") for s in streams if s.get("codec_type") == "video"), ""
        ).lower()

        # Simple web-safe logic: MP4/MOV/WebM with H.264/VP8/VP9/AV1
        safe_formats = {"mp4", "mov", "webm", "ogg"}
        safe_codecs = {"h264", "vp8", "vp9", "av1", "theora"}

        formats = {f.strip() for f in format_name.split(",")}
        is_web_safe = bool(formats & safe_formats) and video_codec in safe_codecs

    except (json.JSONDecodeError, ValueError, TypeError):
        return None, False, False, "ffprobe returned unparseable JSON"

    return raw_duration, True, is_web_safe, None


def _probe_many(
    paths: list[Path], max_workers: int = _FFPROBE_MAX_WORKERS, progress_callback: callable = None
) -> dict[Path, tuple[float | None, bool, bool, str | None]]:
    """
    Probe *paths* in parallel and return a mapping of
    ``path -> (duration_sec, is_valid, is_web_safe, error_message)``.

    Falls back gracefully when ffprobe is absent.
    """
    if not paths:
        return {}

    results: dict[Path, tuple[float | None, bool, bool, str | None]] = {}

    total = len(paths)

    with ThreadPoolExecutor(max_workers=min(max_workers, len(paths))) as pool:
        future_to_path = {pool.submit(_probe_video, p): p for p in paths}
        for i, future in enumerate(as_completed(future_to_path)):
            path = future_to_path[future]
            try:
                results[path] = future.result()
            except Exception as exc:  # pragma: no cover – safety net
                results[path] = (None, False, False, str(exc))
            if progress_callback:
                progress_callback(i + 1, total, path.name)

    return results


# ---------------------------------------------------------------------------


class LocalDataProvider:
    """SQLite-backed local data provider for manual review + constrained model imports."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._load_yaml_config(config_path)
        self.video_dir = self._required_path(cfg, "video_dir")

        # Handle empty or relative db_dir
        raw_db_dir = cfg.get("db_dir", "")
        if not raw_db_dir or raw_db_dir == ".":
            self.db_dir = get_app_dir()
        else:
            self.db_dir = self._resolve_path(raw_db_dir)

        self.db_dir.mkdir(parents=True, exist_ok=True)

        self._species: list[str] = self._load_species(cfg)
        self._species_behaviors: dict[str, list[str]] = self._load_species_behaviors(cfg)

        behavior_defaults = cfg.get("behavior_defaults")
        self._behavior_defaults: list[str] = self._normalize_string_list(
            behavior_defaults, "behavior_defaults"
        )

        self._priority_csv_path: Path | None = self._optional_path(cfg.get("priority_csv_path"))
        self._consensus_min_probability: float = float(cfg.get("consensus_min_probability", 0.0))
        self._fuzzy_match_threshold: int = int(cfg.get("fuzzy_match_threshold", 80))

        db_filename = str(cfg.get("db_filename") or DEFAULT_DB_FILENAME).strip()
        if not db_filename:
            raise ValueError("`db_filename` cannot be empty.")

        self._db_path = self.db_dir / db_filename
        recreate_on_start = bool(cfg.get("recreate_db_on_start", False)) or (
            # Compatibility with environment variable
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

        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL
            conn.execute("PRAGMA cache_size=-64000")  # 64 MB page cache
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")  # 256 MB memory-mapped I/O

        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        self.Session = sessionmaker(bind=self.engine)

    def sync_videos(self, progress_callback):
        self._sync_videos_table(progress_callback)
        self._sync_priority_table()

    def _needs_schema_reset(self) -> bool:
        """Detect incompatible legacy schemas and trigger full DB recreation."""
        if not self._db_path.exists():
            return False
        try:
            inspector = sa_inspect(self.engine)
            if not inspector.has_table("videos"):
                return False
            columns = {col["name"] for col in inspector.get_columns("videos")}
        except Exception:
            return True
        required = {"is_valid", "validation_error"}
        return not required.issubset(columns)

    def _migrate_schema(self) -> None:
        """Add new nullable columns to existing databases without dropping data."""
        inspector = sa_inspect(self.engine)
        cols = {col["name"] for col in inspector.get_columns("videos")}
        if "transcoded_path" not in cols:
            with self.engine.connect() as conn:
                conn.execute(text("ALTER TABLE videos ADD COLUMN transcoded_path TEXT"))
                conn.commit()

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
            config_path = get_config_path()
        p = LocalDataProvider._resolve_path(config_path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: `{p}`. ")
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
        # Standardized key name: species_behaviors_csv_path
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

    def _validate_species_fuzzy(self, value_text: str) -> tuple[bool, str | None]:
        """
        Validate a species name against the known species list using fuzzy matching.

        Args:
            value_text: The species name to validate.

        Returns:
            A tuple of (is_valid, best_match). If is_valid is True, best_match is
            the validated species name. If is_valid is False, best_match is the
            closest match or None.
        """
        from thefuzz import process

        if not value_text:
            return False, None

        value_text = str(value_text).strip()

        if value_text in self._species:
            return True, value_text

        match, score = process.extractOne(value_text, self._species)
        if score >= self._fuzzy_match_threshold:
            return True, match

        return False, None

    @staticmethod
    def _utcnow_dt() -> datetime:
        return datetime.now(timezone.utc)

    def _video_id_from_path(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.video_dir)
            parent = rel.parent.name if rel.parent != Path(".") else "default"
            return f"{parent}/{path.stem}"
        except ValueError:
            parent = path.parent.name if path.parent.name else "default"
            return f"{parent}/{path.stem}"

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

    def _sync_videos_table(self, progress_callback=None) -> None:
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

            # Single query for all existing IDs + their probe status
            existing_rows = {
                row[0]: row
                for row in session.execute(
                    select(
                        Video.video_id, Video.is_valid, Video.duration_sec, Video.validation_error
                    )
                ).fetchall()
            }

            new_video_ids = [r for r in scanned["video_id"] if r not in existing_rows]
            new_rows = scanned[scanned["video_id"].isin(new_video_ids)]

            # Probe only new videos
            probe_results: dict[str, tuple] = {}
            if not new_rows.empty:
                path_map = {
                    Path(row["video_path"]): row["video_id"] for _, row in new_rows.iterrows()
                }
                probe_results = {
                    path_map[p]: result
                    for p, result in _probe_many(
                        list(path_map), progress_callback=progress_callback
                    ).items()
                }

            # Bulk-insert new videos
            if new_video_ids:
                session.execute(
                    Video.__table__.insert(),
                    [
                        {
                            "video_id": row["video_id"],
                            "video_path": row["video_path"],
                            "camera_id": row["camera_id"],
                            "created_at": row["created_at"].to_pydatetime(),
                            "last_seen_at": now,
                            **dict(
                                zip(
                                    (
                                        "duration_sec",
                                        "is_valid",
                                        "is_web_safe",
                                        "validation_error",
                                    ),
                                    probe_results.get(row["video_id"], (None, None, None, None)),
                                )
                            ),
                        }
                        for _, row in new_rows.iterrows()
                    ],
                )

            # Bulk-update existing rows (path/camera/timestamp only)
            existing_df = scanned[scanned["video_id"].isin(existing_rows)]
            if not existing_df.empty:
                session.execute(
                    text("""
                        UPDATE videos
                        SET video_path = :video_path,
                            camera_id  = :camera_id,
                            created_at = :created_at,
                            last_seen_at = :last_seen_at
                        WHERE video_id = :video_id
                    """),
                    [
                        {
                            "video_id": row["video_id"],
                            "video_path": row["video_path"],
                            "camera_id": row["camera_id"],
                            "created_at": row["created_at"].to_pydatetime(),
                            "last_seen_at": now,
                        }
                        for _, row in existing_df.iterrows()
                    ],
                )

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
            duration, is_valid, is_web_safe, validation_error = _probe_video(
                Path(video.video_path)
            )
            video.duration_sec = duration
            video.is_valid = is_valid
            video.is_web_safe = is_web_safe
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
            for path, (duration, is_valid, is_web_safe, validation_error) in probe_results.items():
                vid_id = path_map[path]
                video = session.get(Video, vid_id)
                if video is None:
                    continue
                video.duration_sec = duration
                video.is_valid = is_valid
                video.is_web_safe = is_web_safe
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

    def transcode_video(self, video_id: str) -> dict[str, Any]:
        """
        Transcode a video to web-safe H.264 MP4 using ffmpeg.

        Output is written to a sidecar cache under video_dir/_transcoded/ so
        the original file is never modified.  On success, ``transcoded_path``
        is set on the DB row and can be used for playback instead of the
        original.
        """
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise ValueError(f"Unknown video_id: {video_id!r}")

            input_path = Path(video.video_path)
            if not input_path.exists():
                raise FileNotFoundError(f"Video file not found: {input_path}")

            tmp_dir = Path(tempfile.gettempdir()) / "video_review_transcoded"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            safe_name = video_id.replace("/", "_").replace("\\", "_").replace(":", "_")
            sidecar_path = tmp_dir / f"{safe_name}.mp4"

            if sidecar_path.exists():
                video.transcoded_path = str(sidecar_path)
                session.commit()
                return {"success": True, "new_path": str(sidecar_path)}

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(sidecar_path),
            ]

            try:
                subprocess.run(
                    cmd, capture_output=True, text=True, check=True, env=_subprocess_env()
                )
            except subprocess.CalledProcessError as exc:
                if sidecar_path.exists():
                    sidecar_path.unlink()
                return {"success": False, "error": f"ffmpeg failed: {exc.stderr}"}

            video.transcoded_path = str(sidecar_path)
            video.is_web_safe = True
            session.commit()

            return {"success": True, "new_path": str(sidecar_path)}

    def check_db_exists(self) -> bool:
        return self.video_dir.exists() and any(
            p.suffix.lower() in VIDEO_EXTENSIONS for p in self.video_dir.rglob("*")
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    def has_videos_in_db(self) -> bool:
        if not self._db_path.exists():
            return False
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM videos")).fetchone()
            return result[0] > 0 if result else False

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
        with self.engine.connect() as conn:
            videos_df = pd.read_sql(
                text("SELECT video_id FROM videos LIMIT 10"),
                conn,
            )

        if not videos_df.empty:
            sample_video_ids = videos_df["video_id"].tolist()
            rows = [
                f"{vid},species,species_model_a,deer,,0.92,0,12.0" for vid in sample_video_ids[:3]
            ]
            rows.append(
                f"{sample_video_ids[0] if sample_video_ids else 'VIDEO_001'},behavior,behavior_model_a,reacts_to_camera,,0.83,0,12.0"
            )
            rows.append(
                f"{sample_video_ids[1] if len(sample_video_ids) > 1 else 'VIDEO_002'},blank_non_blank,blank_model,blank,,0.98,0,"
            )
            template = "video_uid,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec\n"
            template += "\n".join(rows)
        else:
            template = CSV_TEMPLATES["model_annotations"]

        return {"model_annotations": template}

    def get_behaviors_for_species(self, species_name: str) -> list[str]:
        # "unlabeled" is now part of _behavior_defaults in config
        defaults = list(self._behavior_defaults)
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
        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(
                    """
                    SELECT 'camera' AS source, camera_id AS val FROM videos WHERE camera_id IS NOT NULL GROUP BY camera_id
                    UNION ALL
                    SELECT 'species', species FROM individual_observations WHERE species IS NOT NULL AND TRIM(species) <> '' GROUP BY species
                    UNION ALL
                    SELECT 'behavior', behavior FROM individual_observations WHERE behavior IS NOT NULL AND TRIM(behavior) <> '' GROUP BY behavior
                    UNION ALL
                    SELECT 'possible_species', value_text FROM model_annotations
                    WHERE annotation_type = 'species' AND value_text IS NOT NULL AND TRIM(value_text) <> '' GROUP BY value_text
                    """
                ),
                conn,
            )

        result: dict[str, list[str]] = {
            "camera_values": [],
            "species_values": [],
            "behavior_values": [],
            "possible_species_values": [],
        }
        for _, row in df.iterrows():
            source = str(row["source"])
            val = str(row["val"])
            if source == "camera":
                result["camera_values"].append(val)
            elif source == "species":
                result["species_values"].append(val)
            elif source == "behavior":
                result["behavior_values"].append(val)
            elif source == "possible_species":
                result["possible_species_values"].append(val)

        result["camera_values"].sort()
        result["species_values"].sort()
        result["behavior_values"].sort()
        result["possible_species_values"].sort()

        return result

    def get_video_queue(self, filters: dict) -> list[str]:
        search_raw = (filters.get("search_query") or "").strip().lower()
        selected_camera = filters.get("selected_camera", "All")
        selected_species = filters.get("selected_species", "All")
        selected_possible_species = filters.get("selected_possible_species", "All")
        selected_blank_non_blank = filters.get("selected_blank_non_blank", "All")
        selected_behavior = filters.get("selected_behavior", "All")
        selected_review_status = filters.get("selected_review_status", "All")
        selected_sort = filters.get("selected_sort", "priority")
        selected_sort_direction = filters.get("selected_sort_direction", "desc")
        sort_dir = "DESC" if selected_sort_direction == "desc" else "ASC"
        sort_dir_inv = "ASC" if selected_sort_direction == "desc" else "DESC"
        include_unranked = bool(filters.get("include_unranked", False))
        web_safe_only = bool(filters.get("web_safe_only", False))

        params: dict[str, Any] = {}

        # ── 1. Resolve priority count once in Python, not inside every result row ──
        with self.engine.connect() as conn:
            priority_count: int = (
                conn.execute(text("SELECT COUNT(*) FROM video_priority")).scalar() or 0
            )
        has_priority = priority_count > 0

        # ── 2. CTEs — only emit effective_blank when the filter is actually used ──
        ctes: list[str] = []
        need_blank_filter = selected_blank_non_blank not in ("All",)
        if need_blank_filter:
            ctes.append("""
            effective_blank AS (
                SELECT
                    v.video_id,
                    CASE
                        WHEN vl.is_blank IS NOT NULL
                            THEN CASE WHEN vl.is_blank = 1 THEN 'blank' ELSE 'non_blank' END
                        WHEN mb.value_text IS NOT NULL
                        AND LOWER(TRIM(mb.value_text)) IN ('blank', 'non_blank')
                            THEN LOWER(TRIM(mb.value_text))
                        ELSE NULL
                    END AS blank_non_blank_final_result
                FROM videos v
                LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                LEFT JOIN (
                    SELECT video_id, value_text,
                        ROW_NUMBER() OVER (
                            PARTITION BY video_id
                            ORDER BY COALESCE(probability, -1.0) DESC, updated_at DESC
                        ) AS rn
                    FROM model_annotations
                    WHERE annotation_type = 'blank_non_blank'
                ) mb ON mb.video_id = v.video_id AND mb.rn = 1
            )""")

        # ── 3. JOINs — use INNER JOIN when include_unranked=False to let SQLite prune early ──
        joins: list[str] = []
        if has_priority:
            if include_unranked:
                joins.append("LEFT JOIN video_priority vp ON vp.video_id = v.video_id")
            else:
                joins.append("JOIN video_priority vp ON vp.video_id = v.video_id")
        if need_blank_filter:
            joins.append("LEFT JOIN effective_blank eb ON eb.video_id = v.video_id")

        # ── 4. WHERE clauses — only emit conditions for active filters ──
        where: list[str] = []

        if search_raw:
            params["sq"] = f"%{search_raw}%"
            where.append("(LOWER(v.video_id) LIKE :sq OR LOWER(v.video_path) LIKE :sq)")

        if selected_camera != "All":
            params["camera"] = selected_camera
            where.append("v.camera_id = :camera")

        if selected_species != "All":
            params["species"] = selected_species
            where.append("""
                EXISTS (
                    SELECT 1 FROM individual_observations io
                    WHERE io.video_id = v.video_id AND io.species = :species
                )""")

        if selected_possible_species != "All":
            params["ps"] = selected_possible_species
            where.append("""
                EXISTS (
                    SELECT 1 FROM model_annotations ma
                    WHERE ma.video_id = v.video_id
                    AND ma.annotation_type = 'species'
                    AND ma.value_text = :ps
                )""")

        if selected_blank_non_blank == "Blank":
            where.append("eb.blank_non_blank_final_result = 'blank'")
        elif selected_blank_non_blank == "Non-Blank":
            where.append("eb.blank_non_blank_final_result = 'non_blank'")
        elif selected_blank_non_blank == "Unknown":
            where.append("eb.blank_non_blank_final_result IS NULL")

        if selected_behavior == "Has Behavior":
            where.append("""
                EXISTS (
                    SELECT 1 FROM individual_observations io
                    WHERE io.video_id = v.video_id
                    AND io.behavior IS NOT NULL AND TRIM(io.behavior) <> ''
                )""")
        elif selected_behavior == "No Behavior":
            where.append("""
                NOT EXISTS (
                    SELECT 1 FROM individual_observations io
                    WHERE io.video_id = v.video_id
                    AND io.behavior IS NOT NULL AND TRIM(io.behavior) <> ''
                )""")
        elif selected_behavior not in ("All", "Has Behavior", "No Behavior"):
            params["behavior"] = selected_behavior
            where.append("""
                EXISTS (
                    SELECT 1 FROM individual_observations io
                    WHERE io.video_id = v.video_id AND io.behavior = :behavior
                )""")

        if web_safe_only:
            where.append("v.is_web_safe = 1")

        if selected_review_status == "Reviewed":
            where.append("""
                EXISTS (
                    SELECT 1 FROM video_labels vl2
                    WHERE vl2.video_id = v.video_id AND vl2.is_blank IS NOT NULL
                )""")
        elif selected_review_status == "Unreviewed":
            where.append("""
                NOT EXISTS (
                    SELECT 1 FROM video_labels vl2
                    WHERE vl2.video_id = v.video_id AND vl2.is_blank IS NOT NULL
                )""")

        # ── 5. ORDER BY ──
        if selected_sort == "priority":
            if has_priority and include_unranked:
                order_by = f"""ORDER BY
                    CASE WHEN vp.video_id IS NULL THEN 1 ELSE 0 END,
                    vp.annotation_importance_score {sort_dir},
                    v.video_id ASC"""
            elif has_priority:
                order_by = f"""ORDER BY
                    vp.annotation_importance_score {sort_dir},
                    v.video_id ASC"""
            else:
                order_by = "ORDER BY v.video_id ASC"
        elif selected_sort == "camera":
            order_by = f"ORDER BY v.camera_id {sort_dir}, v.video_id ASC"
        elif selected_sort == "unreviewed_first":
            order_by = f"""ORDER BY
                CASE WHEN EXISTS (
                    SELECT 1 FROM video_labels vl2
                    WHERE vl2.video_id = v.video_id AND vl2.is_blank IS NOT NULL
                ) THEN 1 ELSE 0 END {sort_dir_inv},
                v.video_id ASC"""
        elif selected_sort == "species_prob":
            species_sort_filter = (
                selected_possible_species
                if selected_possible_species != "All"
                else selected_species
                if selected_species != "All"
                else None
            )
            if species_sort_filter is not None:
                params["species_sort_val"] = species_sort_filter
                ctes.append("""
            species_max_prob AS (
                SELECT video_id, SUM(COALESCE(probability, 0)) AS max_species_prob
                FROM model_annotations
                WHERE annotation_type = 'species'
                AND value_text = :species_sort_val
                GROUP BY video_id
            )""")
            else:
                ctes.append("""
            species_max_prob AS (
                SELECT video_id, MAX(prob_sum) AS max_species_prob
                FROM (
                    SELECT video_id, SUM(COALESCE(probability, 0)) AS prob_sum
                    FROM model_annotations
                    WHERE annotation_type = 'species'
                    GROUP BY video_id, value_text
                ) _sp
                GROUP BY video_id
            )""")
            joins.append("LEFT JOIN species_max_prob smp ON smp.video_id = v.video_id")
            order_by = f"ORDER BY smp.max_species_prob {sort_dir} NULLS LAST, v.video_id ASC"
        elif selected_sort == "random":
            order_by = "ORDER BY RANDOM()"
        else:
            order_by = "ORDER BY v.video_id ASC"

        # ── Assemble ──
        cte_sql = ("WITH " + ",\n".join(ctes)) if ctes else ""
        join_sql = "\n".join(joins)
        where_sql = ("WHERE " + "\nAND ".join(where)) if where else ""

        sql = text(f"""
            {cte_sql}
            SELECT v.video_id
            FROM videos v
            {join_sql}
            {where_sql}
            {order_by}
        """)

        with self.engine.connect() as conn:
            rows = pd.read_sql(sql, conn, params=params)

        return [] if rows.empty else rows["video_id"].astype(str).tolist()

    def get_video_detail(self, video_id: str) -> dict | None:
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
                    avg_species_conf AS (
                        SELECT video_id, AVG(COALESCE(probability, 0.0)) AS avg_species_confidence
                        FROM model_annotations
                        WHERE annotation_type = 'species'
                        GROUP BY video_id
                    ),
                    model_behavior AS (
                        SELECT video_id, value_text AS behavior_prediction
                        FROM (
                            SELECT
                                ma.video_id,
                                ma.value_text,
                                ma.probability,
                                ROW_NUMBER() OVER (
                                    PARTITION BY ma.video_id
                                    ORDER BY COALESCE(ma.probability, 0.0) DESC
                                ) AS rn
                            FROM model_annotations ma
                            WHERE ma.annotation_type = 'behavior'
                                AND COALESCE(ma.probability, 0.0) >= :min_prob
                                AND ma.value_text IS NOT NULL
                                AND TRIM(ma.value_text) <> ''
                        ) ranked
                        WHERE rn = 1
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
                        v.is_web_safe,
                        v.transcoded_path,
                        v.validation_error AS video_validation_details,
                        vl.is_blank,
                        vl.labeled_by,
                        vl.labeled_at,
                        ms.behavior_prediction,
                        ms.individual_count,
                        COALESCE(msc.classification_consensus, 'UNKNOWN') AS classification_consensus,
                        COALESCE(mbe.behavior_prediction, 'unlabeled') AS model_behavior_prediction,
                        CASE
                            WHEN vl.is_blank IS NOT NULL THEN CASE WHEN vl.is_blank = 1 THEN 'blank' ELSE 'non_blank' END
                            WHEN mb.rn = 1 AND LOWER(TRIM(mb.blank_non_blank_model_result)) IN ('blank', 'non_blank')
                                THEN LOWER(TRIM(mb.blank_non_blank_model_result))
                            ELSE NULL
                        END AS blank_non_blank_final_result,
                        CASE WHEN LOWER(TRIM(mb.blank_non_blank_model_result)) = 'blank'
                             THEN mb.probability ELSE NULL
                        END AS blank_model_probability,
                        COALESCE(asc_.avg_species_confidence, 0.0) AS avg_species_confidence,
                        rs.needs_manual_review
                    FROM videos v
                    LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    LEFT JOIN model_blank mb ON mb.video_id = v.video_id AND mb.rn = 1
                    LEFT JOIN manual_summary ms ON ms.video_id = v.video_id
                    LEFT JOIN model_species_consensus msc ON msc.video_id = v.video_id
                    LEFT JOIN model_behavior mbe ON mbe.video_id = v.video_id
                    LEFT JOIN avg_species_conf asc_ ON asc_.video_id = v.video_id
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
            "\n".join(
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

        # Pre-calculate fields for the UI to improve separation of concerns
        row["needs_transcode"] = needs_browser_transcode(row)
        model_ann = self.get_model_annotations(video_id)
        row["default_species"] = get_default_species_from_annotations(
            model_ann, self.get_valid_species(), None
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
        is_blank: bool | None = None,
    ) -> None:
        if selections is None:
            selections = []

        now = self._utcnow_dt()
        session_id = str(uuid.uuid4())

        normalized: list[dict[str, Any]] = []
        for selection in selections:
            species = str(selection.get("species") or "").strip() or "unknown"
            if species.lower() == "blank":
                continue
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

        if is_blank is None:
            # Fallback to legacy detection if not explicitly provided
            if not normalized:
                is_blank = None
            else:
                # Still check if user passed a list containing only "blank" species
                is_blank = (
                    len(selections) == 1 and str(selections[0].get("species")).lower() == "blank"
                )

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

    @staticmethod
    def _normalize_annotation_type(annotation_type: str) -> str:
        supported = {"blank_non_blank", "species", "behavior"}
        normalized = (annotation_type or "").strip().lower()
        if normalized not in supported:
            raise ValueError(
                f"Unsupported annotation_type `{annotation_type}`. Use one of {sorted(supported)}"
            )
        return normalized

    def validate_model_csv(
        self, df: pd.DataFrame, mappings: dict[str, str] | None = None
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], list[dict]]:
        src = df.copy()
        src.columns = [str(c).strip() for c in src.columns]

        mappings = mappings or {}

        required = {"video_uid", "annotation_type", "model_name"}
        missing = required - set(src.columns)
        if missing:
            raise ValueError(f"CSV must include columns: {', '.join(sorted(missing))}")

        known_videos = set(self.get_video_queue(filters={}))

        species_mask = src["annotation_type"].str.strip().str.lower() == "species"
        unique_species = src.loc[species_mask, "value_text"].dropna().str.strip().unique()
        unique_species = {str(s) for s in unique_species if str(s).strip()}

        species_fuzzy_cache: dict[str, tuple[bool, str | None]] = {}
        for species_val in unique_species:
            species_fuzzy_cache[species_val] = self._validate_species_fuzzy(species_val)

        prepared_rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        species_mappings: list[dict[str, str]] = []
        unmapped_species: set[str] = set()

        for idx, row in src.iterrows():
            row_num = int(idx) + 1
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

            if annotation_type == "species" and value_text:
                original_value = value_text
                # First, check if there is an explicit user mapping
                mapped_to = mappings.get(original_value)
                if mapped_to:
                    value_text = mapped_to
                else:
                    is_valid, best_match = species_fuzzy_cache.get(original_value, (False, None))
                    if not is_valid:
                        unmapped_species.add(original_value)
                        errors.append(
                            {
                                "row_number": row_num,
                                "video_uid": video_uid,
                                "error": f"Species name '{original_value}' needs mapping",
                            }
                        )
                        continue
                    if best_match != original_value:
                        species_mappings.append(
                            {"original": original_value, "mapped_to": best_match}
                        )
                    value_text = best_match

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

        unmapped_species_list = [{"original": s} for s in sorted(unmapped_species)]
        return (
            pd.DataFrame(prepared_rows),
            pd.DataFrame(errors),
            species_mappings,
            unmapped_species_list,
        )

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

    def get_overview_stats(self) -> dict[str, Any]:
        """
        Single-query overview for dashboards. All counts in one round-trip.
        """
        with self.engine.connect() as conn:
            stats = {}

            # ── Videos ──────────────────────────────────────────────────────
            stats["videos"] = (
                pd.read_sql(
                    text("""
                SELECT
                    COUNT(*)                                            AS total,
                    SUM(CASE WHEN is_valid = 1  THEN 1 ELSE 0 END)    AS valid,
                    SUM(CASE WHEN is_valid = 0  THEN 1 ELSE 0 END)    AS invalid,
                    SUM(CASE WHEN is_valid IS NULL THEN 1 ELSE 0 END)  AS unprobed,
                    COUNT(DISTINCT camera_id)                          AS cameras,
                    ROUND(SUM(COALESCE(duration_sec, 0)) / 3600.0, 2) AS total_hours
                FROM videos
            """),
                    conn,
                )
                .iloc[0]
                .to_dict()
            )

            stats["failed_videos"] = pd.read_sql(
                text("""
                    SELECT * FROM videos
                    WHERE is_valid = 0
                    """),
                conn,
            )

            # ── Label / review progress ──────────────────────────────────────
            stats["labeling"] = (
                pd.read_sql(
                    text("""
                SELECT
                    COUNT(DISTINCT v.video_id)                                         AS total_videos,
                    COUNT(DISTINCT vl.video_id)                                        AS labeled,
                    COUNT(DISTINCT v.video_id) - COUNT(DISTINCT vl.video_id)           AS unlabeled,
                    SUM(CASE WHEN vl.is_blank = 1 THEN 1 ELSE 0 END)                  AS blank,
                    SUM(CASE WHEN vl.is_blank = 0 THEN 1 ELSE 0 END)                  AS non_blank,
                    COUNT(DISTINCT io.video_id)                                        AS has_observations
                FROM videos v
                LEFT JOIN video_labels     vl ON vl.video_id = v.video_id
                LEFT JOIN individual_observations io ON io.video_id = v.video_id
            """),
                    conn,
                )
                .iloc[0]
                .to_dict()
            )

            # ── Manual observations: species breakdown ───────────────────────
            stats["species_counts"] = pd.read_sql(
                text("""
                SELECT
                    species,
                    COUNT(*)              AS observations,
                    COUNT(DISTINCT video_id) AS videos
                FROM individual_observations
                GROUP BY species
                ORDER BY observations DESC
            """),
                conn,
            ).to_dict(orient="records")

            # ── Manual observations: behavior breakdown ──────────────────────
            stats["behavior_counts"] = pd.read_sql(
                text("""
                SELECT
                    behavior,
                    COUNT(*)              AS observations,
                    COUNT(DISTINCT video_id) AS videos
                FROM individual_observations
                GROUP BY behavior
                ORDER BY observations DESC
            """),
                conn,
            ).to_dict(orient="records")

            # ── Model annotation coverage ────────────────────────────────────
            stats["model_coverage"] = pd.read_sql(
                text("""
                SELECT
                    model_name,
                    annotation_type,
                    COUNT(DISTINCT video_id)              AS videos_covered,
                    ROUND(AVG(probability), 3)            AS avg_probability,
                    ROUND(MIN(probability), 3)            AS min_probability,
                    ROUND(MAX(probability), 3)            AS max_probability
                FROM model_annotations
                GROUP BY model_name, annotation_type
                ORDER BY model_name, annotation_type
            """),
                conn,
            ).to_dict(orient="records")

            # ── Model species predictions ────────────────────────────────────
            stats["model_species_dist"] = pd.read_sql(
                text("""
                SELECT
                    model_name,
                    value_text           AS predicted_species,
                    COUNT(*)             AS predictions,
                    ROUND(AVG(probability), 3) AS avg_confidence
                FROM model_annotations
                WHERE annotation_type = 'species'
                AND value_text IS NOT NULL
                GROUP BY model_name, value_text
                ORDER BY model_name, predictions DESC
            """),
                conn,
            ).to_dict(orient="records")

            # ── Agreement: model vs manual ───────────────────────────────────
            # Where a manual label exists, how often does the top model agree?
            stats["model_human_agreement"] = pd.read_sql(
                text("""
                WITH top_model AS (
                    SELECT
                        video_id,
                        model_name,
                        value_text AS predicted_species,
                        ROW_NUMBER() OVER (
                            PARTITION BY video_id, model_name
                            ORDER BY COALESCE(probability, 0) DESC
                        ) AS rn
                    FROM model_annotations
                    WHERE annotation_type = 'species'
                ),
                manual AS (
                    SELECT DISTINCT video_id, species AS manual_species
                    FROM individual_observations
                )
                SELECT
                    tm.model_name,
                    COUNT(*)                                              AS compared,
                    SUM(CASE WHEN tm.predicted_species = m.manual_species
                            THEN 1 ELSE 0 END)                         AS agreed,
                    ROUND(
                        100.0 * SUM(CASE WHEN tm.predicted_species = m.manual_species
                                        THEN 1 ELSE 0 END) / COUNT(*), 1
                    )                                                     AS agreement_pct
                FROM top_model tm
                JOIN manual m ON m.video_id = tm.video_id
                WHERE tm.rn = 1
                GROUP BY tm.model_name
            """),
                conn,
            ).to_dict(orient="records")

            # ── Per-camera breakdown ─────────────────────────────────────────
            stats["camera_summary"] = pd.read_sql(
                text("""
                SELECT
                    v.camera_id,
                    COUNT(*)                                               AS total_videos,
                    SUM(CASE WHEN vl.video_id IS NOT NULL THEN 1 ELSE 0 END) AS labeled,
                    SUM(CASE WHEN vl.is_blank = 1 THEN 1 ELSE 0 END)         AS blank,
                    ROUND(SUM(COALESCE(v.duration_sec,0))/3600.0, 2)         AS hours
                FROM videos v
                LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                GROUP BY v.camera_id
                ORDER BY total_videos DESC
            """),
                conn,
            ).to_dict(orient="records")

        return stats
