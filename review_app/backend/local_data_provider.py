from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from review_app.app.config import (
    CSV_TEMPLATES,
    DEFAULT_DB_FILENAME,
    REPO_ROOT,
    get_config_path,
    get_user_data_dir,
)
from review_app.app.state import get_active_project_id
from review_app.backend.migrations import run_migrations
from review_app.backend.models import (
    Base,
    IndividualObservation,
    ModelAnnotation,
    Project,
    ProjectDir,
    VideoLabel,
)
from review_app.backend.species import SpeciesMixin
from review_app.backend.utils import get_default_species_from_annotations, needs_browser_transcode
from review_app.backend.video import VideoMixin

load_dotenv()


class LocalDataProvider(VideoMixin, SpeciesMixin):
    """SQLite-backed local data provider for manual review + constrained model imports."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._load_yaml_config(config_path)

        self.db_dir = get_user_data_dir()

        self.db_dir.mkdir(parents=True, exist_ok=True)

        self._behavior_defaults: list[str] = self._normalize_string_list(
            cfg.get("behavior_defaults"), "behavior_defaults"
        )
        self._consensus_min_probability: float = float(cfg.get("consensus_min_probability", 0.0))
        self._fuzzy_match_threshold: int = int(cfg.get("fuzzy_match_threshold", 80))

        db_filename = DEFAULT_DB_FILENAME

        self._db_path = self.db_dir / db_filename

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

        self._load_species_data(cfg)
        self._load_species_behaviors(cfg)

    # ── Config helpers ────────────────────────────────────────────────────────

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
            raise FileNotFoundError(f"Config file not found: `{p}`.")
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
        if values is None:
            return []
        if not isinstance(values, list):
            raise ValueError(f"`{key_name}` must be a list of strings.")
        return [str(v).strip() for v in values if str(v).strip()]

    @staticmethod
    def _utcnow_dt() -> datetime:
        return datetime.now(timezone.utc)

    @property
    def _app_config_path(self) -> Path:
        return self.db_dir / "config.json"

    # ── Video sync ────────────────────────────────────────────────────────────

    def sync_videos(self, progress_callback, video_dir: Path | None = None) -> dict:
        return self._sync_videos_table(progress_callback, video_dir=video_dir)

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

    # ── Config persistence ────────────────────────────────────────────────────

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

    # ── Project management ────────────────────────────────────────────────────

    def create_project(self, name: str, video_dir: str) -> Project:
        project = Project(id=str(__import__("uuid").uuid4()), name=name)
        with self.Session() as s:
            s.add(project)
            s.flush()
            if video_dir:
                s.add(
                    ProjectDir(
                        id=str(__import__("uuid").uuid4()),
                        project_id=project.id,
                        path=str(video_dir),
                        sort_order=0,
                    )
                )
            s.commit()
            s.refresh(project)
            return project

    def list_projects(self) -> list[Project]:
        with self.Session() as s:
            return s.query(Project).order_by(Project.created_at).all()

    def get_project(self, project_id: str) -> Project | None:
        with self.Session() as s:
            return s.query(Project).filter_by(id=project_id).first()

    def update_project_name(self, project_id: str, name: str) -> None:
        with self.Session() as s:
            project = s.query(Project).filter_by(id=project_id).first()
            if project:
                project.name = name
                s.commit()

    def touch_project(self, project_id: str) -> None:
        with self.Session() as s:
            project = s.query(Project).filter_by(id=project_id).first()
            if project:
                project.last_opened = self._utcnow_dt()
                s.commit()

    def get_project_dirs(self, project_id: str) -> list[ProjectDir]:
        with self.Session() as s:
            return (
                s.query(ProjectDir)
                .filter_by(project_id=project_id)
                .order_by(ProjectDir.sort_order)
                .all()
            )

    def add_project_dir(self, project_id: str, path: str) -> ProjectDir:
        with self.Session() as s:
            existing = s.query(ProjectDir).filter_by(project_id=project_id).all()
            sort_order = max((d.sort_order for d in existing), default=-1) + 1
            d = ProjectDir(
                id=str(__import__("uuid").uuid4()),
                project_id=project_id,
                path=str(path),
                sort_order=sort_order,
            )
            s.add(d)
            s.commit()
            s.refresh(d)
            return d

    def remove_project_dir(self, dir_id: str) -> None:
        # TODO deal with videos from that dir
        with self.Session() as s:
            d = s.query(ProjectDir).filter_by(id=dir_id).first()
            if d:
                s.delete(d)
                s.commit()

    # ── CSV templates ─────────────────────────────────────────────────────────

    def get_csv_templates(self) -> dict[str, str]:
        with self.engine.connect() as conn:
            videos_df = pd.read_sql(text("SELECT video_id FROM videos LIMIT 10"), conn)

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

    # ── Queue ─────────────────────────────────────────────────────────────────

    def _get_model_annotations_df(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(select(ModelAnnotation), conn)

    def _get_individuals_df(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(select(IndividualObservation), conn)

    def _get_labels_df(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(select(VideoLabel), conn)

    def get_queue_filter_options(self, active_project_id: str | None) -> dict[str, list[str]]:
        pid_clause = "AND project_id = :pid" if active_project_id else ""
        params = {"pid": active_project_id} if active_project_id else {}
        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(f"""
                    SELECT 'camera' AS source, camera_id AS val FROM videos
                    WHERE camera_id IS NOT NULL {pid_clause} GROUP BY camera_id
                    UNION ALL
                    SELECT 'species', species FROM individual_observations
                    WHERE species IS NOT NULL AND TRIM(species) <> '' {pid_clause} GROUP BY species
                    UNION ALL
                    SELECT 'behavior', behavior FROM individual_observations
                    WHERE behavior IS NOT NULL AND TRIM(behavior) <> '' {pid_clause} GROUP BY behavior
                    UNION ALL
                    SELECT 'possible_species', value_text FROM model_annotations
                    WHERE annotation_type = 'species' AND value_text IS NOT NULL AND TRIM(value_text) <> '' {pid_clause} GROUP BY value_text
                    UNION ALL
                    SELECT 'model_behavior', value_text FROM model_annotations
                    WHERE annotation_type = 'behavior' AND value_text IS NOT NULL AND TRIM(value_text) <> '' {pid_clause} GROUP BY value_text
                """),
                conn,
                params=params,
            )

        result: dict[str, list[str]] = {
            "camera_values": [],
            "species_values": [],
            "behavior_values": [],
            "possible_species_values": [],
            "model_behavior_values": [],
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
            elif source == "model_behavior":
                result["model_behavior_values"].append(val)

        result["camera_values"].sort()
        result["species_values"].sort()
        result["behavior_values"].sort()
        result["possible_species_values"].sort()
        result["model_behavior_values"].sort()

        return result

    def get_video_queue(self, filters: dict, active_project_id: str | None) -> list[str]:
        search_raw = (filters.get("search_query") or "").strip().lower()
        selected_camera = filters.get("selected_camera", "All")
        selected_species = filters.get("selected_species", "All")
        selected_possible_species = filters.get("selected_possible_species", "All")
        selected_manual_blank = filters.get("selected_manual_blank", "All")
        selected_model_blank = filters.get("selected_model_blank", "All")
        selected_model_behavior = filters.get("selected_model_behavior", "All")
        selected_behavior = filters.get("selected_behavior", "All")
        selected_annotation_status = filters.get("selected_annotation_status", "All")
        selected_sort = filters.get("selected_sort", "camera")
        selected_sort_direction = filters.get("selected_sort_direction", "desc")
        sort_dir = "DESC" if selected_sort_direction == "desc" else "ASC"
        sort_dir_inv = "ASC" if selected_sort_direction == "desc" else "DESC"
        web_safe_only = bool(filters.get("web_safe_only", False))
        selected_needs_review = filters.get("selected_needs_review", "All")
        blank_threshold = float(filters.get("blank_threshold", 0.75))
        species_threshold = float(filters.get("species_threshold", 0.75))

        params: dict[str, Any] = {}
        ctes: list[str] = []
        joins: list[str] = []
        where: list[str] = []

        if active_project_id:
            params["pid"] = active_project_id
            where.append("v.project_id = :pid")

        need_needs_review_filter = selected_needs_review != "All"
        if need_needs_review_filter:
            params["blank_thr"] = blank_threshold
            params["species_thr"] = species_threshold
            ctes.append("""
            nr_blank AS (
                SELECT video_id,
                    MAX(CASE WHEN LOWER(TRIM(value_text)) = 'blank'
                             THEN COALESCE(probability, 0.0) ELSE 0.0 END) AS blank_prob
                FROM model_annotations
                WHERE annotation_type = 'blank_non_blank'
                GROUP BY video_id
            ),
            nr_species AS (
                SELECT video_id,
                    MAX(COALESCE(probability, 0.0))   AS max_sp,
                    COUNT(DISTINCT value_text)         AS distinct_top1,
                    COUNT(*)                           AS model_count
                FROM model_annotations
                WHERE annotation_type = 'species'
                GROUP BY video_id
            ),
            needs_review AS (
                SELECT v.video_id,
                    CASE
                        WHEN COALESCE(nb.blank_prob, 0.0) >= :blank_thr
                         AND COALESCE(ns.max_sp, 0.0) < :species_thr THEN 0
                        WHEN ns.distinct_top1 = 1 AND ns.model_count >= 1 THEN 0
                        ELSE 1
                    END AS needs_review_flag
                FROM videos v
                LEFT JOIN nr_blank nb ON nb.video_id = v.video_id
                LEFT JOIN nr_species ns ON ns.video_id = v.video_id
            )""")

        need_model_blank_filter = selected_model_blank != "All"
        if need_model_blank_filter:
            ctes.append("""
            model_blank AS (
                SELECT video_id, LOWER(TRIM(value_text)) AS result
                FROM (
                    SELECT video_id, value_text,
                        ROW_NUMBER() OVER (
                            PARTITION BY video_id
                            ORDER BY COALESCE(probability, -1.0) DESC, updated_at DESC
                        ) AS rn
                    FROM model_annotations
                    WHERE annotation_type = 'blank_non_blank'
                ) mb WHERE rn = 1
            )""")

        if need_needs_review_filter:
            joins.append("LEFT JOIN needs_review nr ON nr.video_id = v.video_id")
        if need_model_blank_filter:
            joins.append("LEFT JOIN model_blank mb_f ON mb_f.video_id = v.video_id")

        if search_raw:
            params["sq"] = f"%{search_raw}%"
            where.append("LOWER(v.video_path) LIKE :sq")

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

        if selected_manual_blank == "Blank":
            where.append(
                "EXISTS (SELECT 1 FROM video_labels vl2 WHERE vl2.video_id = v.video_id AND vl2.is_blank = 1)"
            )
        elif selected_manual_blank == "Non-Blank":
            where.append(
                "EXISTS (SELECT 1 FROM video_labels vl2 WHERE vl2.video_id = v.video_id AND vl2.is_blank = 0)"
            )
        elif selected_manual_blank == "Unlabeled":
            where.append(
                "NOT EXISTS (SELECT 1 FROM video_labels vl2 WHERE vl2.video_id = v.video_id AND vl2.is_blank IS NOT NULL)"
            )

        if selected_model_blank == "Blank":
            where.append("mb_f.result = 'blank'")
        elif selected_model_blank == "Non-Blank":
            where.append("mb_f.result = 'non_blank'")
        elif selected_model_blank == "Unknown":
            where.append("mb_f.video_id IS NULL")

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

        if selected_model_behavior != "All":
            params["model_behavior"] = selected_model_behavior
            where.append("""
                EXISTS (
                    SELECT 1 FROM model_annotations ma
                    WHERE ma.video_id = v.video_id
                    AND ma.annotation_type = 'behavior'
                    AND ma.value_text = :model_behavior
                )""")

        if web_safe_only:
            where.append("v.is_web_safe = 1")

        if selected_needs_review == "Needs Review":
            where.append("nr.needs_review_flag = 1")
        elif selected_needs_review == "No Review":
            where.append("nr.needs_review_flag = 0")

        if selected_annotation_status == "Annotated":
            where.append("""
                EXISTS (
                    SELECT 1 FROM video_labels vl2
                    WHERE vl2.video_id = v.video_id AND vl2.is_blank IS NOT NULL
                )""")
        elif selected_annotation_status == "Not Annotated":
            where.append("""
                NOT EXISTS (
                    SELECT 1 FROM video_labels vl2
                    WHERE vl2.video_id = v.video_id AND vl2.is_blank IS NOT NULL
                )""")

        if selected_sort == "camera":
            order_by = f"ORDER BY v.camera_id {sort_dir}, v.video_path ASC"
        elif selected_sort == "unreviewed_first":
            order_by = f"""ORDER BY
                CASE WHEN EXISTS (
                    SELECT 1 FROM video_labels vl2
                    WHERE vl2.video_id = v.video_id AND vl2.is_blank IS NOT NULL
                ) THEN 1 ELSE 0 END {sort_dir_inv},
                v.video_path ASC"""
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
            order_by = f"ORDER BY smp.max_species_prob {sort_dir} NULLS LAST, v.video_path ASC"
        elif selected_sort == "random":
            order_by = "ORDER BY RANDOM()"
        else:
            order_by = "ORDER BY v.video_path ASC"

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

    # ── Video detail ──────────────────────────────────────────────────────────

    def get_video_detail(
        self,
        video_id: str,
        blank_threshold: float = 0.75,
        species_threshold: float = 0.75,
    ) -> dict | None:
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
                    max_species_conf AS (
                        SELECT video_id, MAX(COALESCE(probability, 0.0)) AS max_species_confidence
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
                        vl.labeled_by AS blank_labeled_by,
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
                        COALESCE(msc_.max_species_confidence, 0.0) AS max_species_confidence,
                        rs.needs_manual_review
                    FROM videos v
                    LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    LEFT JOIN model_blank mb ON mb.video_id = v.video_id AND mb.rn = 1
                    LEFT JOIN manual_summary ms ON ms.video_id = v.video_id
                    LEFT JOIN model_species_consensus msc ON msc.video_id = v.video_id
                    LEFT JOIN model_behavior mbe ON mbe.video_id = v.video_id
                    LEFT JOIN max_species_conf msc_ ON msc_.video_id = v.video_id
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
                    SELECT species, behavior, start_sec, end_sec, labeled_by, labeled_at
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
                    "labeled_by": manual.get("labeled_by"),
                    "labeled_at": manual.get("labeled_at"),
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
        row["needs_transcode"] = needs_browser_transcode(row)
        model_ann = self.get_model_annotations(video_id)
        row["default_species"] = get_default_species_from_annotations(
            model_ann, self.get_valid_species(), None
        )

        blank_prob = row.get("blank_model_probability") or 0.0
        max_sp = row.get("max_species_confidence") or 0.0
        row["predicted_blank"] = (
            row.get("is_blank") is None
            and not selections
            and blank_prob >= blank_threshold
            and max_sp < species_threshold
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

    # ── Manual review ─────────────────────────────────────────────────────────

    def update_manual_review(
        self,
        video_id: str,
        selections: list[dict] | None,
        is_blank: bool | None = None,
        labeled_by: str | None = None,
        active_project_id: str | None = None,
    ) -> None:
        if selections is None:
            selections = []

        now = self._utcnow_dt()

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
                    "labeled_by": selection.get("labeled_by"),
                }
            )

        if is_blank is None:
            if not normalized:
                is_blank = None
            else:
                is_blank = (
                    len(selections) == 1 and str(selections[0].get("species")).lower() == "blank"
                )

        with self.Session() as session:
            label = session.get(VideoLabel, video_id)
            if label is None:
                label = VideoLabel(video_id=video_id)
                session.add(label)
            label.is_blank = is_blank
            if is_blank:
                label.labeled_by = labeled_by
                label.labeled_at = now

            session.query(IndividualObservation).filter(
                IndividualObservation.video_id == video_id
            ).delete(synchronize_session=False)

            if is_blank is False:
                for obs_id, row in enumerate(normalized, start=1):
                    session.add(
                        IndividualObservation(
                            video_id=video_id,
                            id=obs_id,
                            project_id=active_project_id,
                            species=row["species"],
                            behavior=row["behavior"],
                            start_sec=row["start_sec"],
                            end_sec=row["end_sec"],
                            labeled_by=row.get("labeled_by"),
                            labeled_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()

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
                        project_id=self.active_project_id,
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

    # ── Annotation export / import ────────────────────────────────────────────

    def export_annotations_csv(self) -> pd.DataFrame:
        pid_clause = "AND v.project_id = :pid" if self.active_project_id else ""
        params = {"pid": self.active_project_id} if self.active_project_id else {}
        with self.engine.connect() as conn:
            return pd.read_sql(
                text(f"""
                    SELECT
                        v.video_id,
                        v.video_path,
                        v.camera_id,
                        v.created_at              AS recorded_at,
                        v.duration_sec,
                        CAST(vl.is_blank AS INTEGER) AS is_blank,
                        COALESCE(io.labeled_by, vl.labeled_by) AS annotator,
                        COALESCE(io.labeled_at, vl.labeled_at) AS labeled_at,
                        io.id                     AS observation_id,
                        io.species,
                        io.behavior,
                        io.start_sec,
                        io.end_sec
                    FROM videos v
                    JOIN video_labels vl ON vl.video_id = v.video_id
                    LEFT JOIN individual_observations io ON io.video_id = v.video_id
                    WHERE 1=1 {pid_clause}
                    ORDER BY v.camera_id, v.video_path, io.start_sec
                """),
                conn,
                params=params,
            )

    def import_annotations_csv(
        self, df: pd.DataFrame, active_project_id: str | None
    ) -> dict[str, Any]:
        required = {"video_id", "is_blank"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        with self.engine.connect() as conn:
            if active_project_id:
                known_ids = set(
                    pd.read_sql(
                        text("SELECT video_id FROM videos WHERE project_id = :pid"),
                        conn,
                        params={"pid": active_project_id},
                    )["video_id"]
                )
            else:
                known_ids = set(pd.read_sql(text("SELECT video_id FROM videos"), conn)["video_id"])

        imported = 0
        skipped: list[str] = []

        for video_id, group in df.groupby("video_id", sort=False):
            if video_id not in known_ids:
                skipped.append(str(video_id))
                continue

            first = group.iloc[0]
            is_blank_raw = first["is_blank"]
            is_blank = bool(int(is_blank_raw)) if pd.notna(is_blank_raw) else None

            if is_blank:
                self.update_manual_review(
                    str(video_id), [], is_blank=True, active_project_id=active_project_id
                )
            else:
                selections = []
                for _, row in group.iterrows():
                    sp = str(row.get("species") or "").strip()
                    if not sp:
                        continue
                    beh = str(row.get("behavior") or "unlabeled").strip() or "unlabeled"
                    labeled_by = (
                        str(row["annotator"])
                        if "annotator" in group.columns and pd.notna(row.get("annotator"))
                        else None
                    )
                    selections.append(
                        {
                            "species": sp,
                            "behavior": beh,
                            "start_sec": row.get("start_sec"),
                            "end_sec": row.get("end_sec"),
                            "labeled_by": labeled_by,
                        }
                    )
                if selections:
                    self.update_manual_review(
                        str(video_id), selections, active_project_id=active_project_id
                    )

            imported += 1

        return {"imported": imported, "skipped": skipped}

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_overview_stats(self) -> dict[str, Any]:
        p = {"pid": self.active_project_id} if self.active_project_id else {}
        pf = "WHERE project_id = :pid" if self.active_project_id else ""
        vf = "WHERE v.project_id = :pid" if self.active_project_id else ""

        with self.engine.connect() as conn:
            stats = {}

            stats["videos"] = (
                pd.read_sql(
                    text(f"""
                SELECT
                    COUNT(*)                                            AS total,
                    SUM(CASE WHEN is_valid = 1  THEN 1 ELSE 0 END)    AS valid,
                    SUM(CASE WHEN is_valid = 0  THEN 1 ELSE 0 END)    AS invalid,
                    SUM(CASE WHEN is_valid IS NULL THEN 1 ELSE 0 END)  AS unprobed,
                    COUNT(DISTINCT camera_id)                          AS cameras,
                    ROUND(SUM(COALESCE(duration_sec, 0)) / 3600.0, 2) AS total_hours
                FROM videos {pf}
            """),
                    conn,
                    params=p,
                )
                .iloc[0]
                .to_dict()
            )

            stats["failed_videos"] = pd.read_sql(
                text(
                    f"SELECT * FROM videos WHERE is_valid = 0 {'AND project_id = :pid' if self.active_project_id else ''}"
                ),
                conn,
                params=p,
            )

            stats["labeling"] = (
                pd.read_sql(
                    text(f"""
                SELECT
                    COUNT(DISTINCT v.video_id)                                                    AS total_videos,
                    COUNT(DISTINCT CASE WHEN vl.is_blank IS NOT NULL THEN v.video_id END)         AS labeled,
                    COUNT(DISTINCT CASE WHEN vl.is_blank = 1 THEN v.video_id END)                AS blank,
                    COUNT(DISTINCT CASE WHEN vl.is_blank = 0 THEN v.video_id END)                AS non_blank,
                    COUNT(DISTINCT io.video_id)                                                   AS has_observations
                FROM videos v
                LEFT JOIN video_labels     vl ON vl.video_id = v.video_id
                LEFT JOIN individual_observations io ON io.video_id = v.video_id
                {vf}
            """),
                    conn,
                    params=p,
                )
                .iloc[0]
                .to_dict()
            )

            stats["species_counts"] = pd.read_sql(
                text(f"""
                SELECT
                    species,
                    COUNT(*)              AS observations,
                    COUNT(DISTINCT video_id) AS videos
                FROM individual_observations
                {pf}
                GROUP BY species
                ORDER BY observations DESC
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

            stats["behavior_counts"] = pd.read_sql(
                text(f"""
                SELECT
                    behavior,
                    COUNT(*)              AS observations,
                    COUNT(DISTINCT video_id) AS videos
                FROM individual_observations
                {pf}
                GROUP BY behavior
                ORDER BY observations DESC
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

            stats["model_coverage"] = pd.read_sql(
                text(f"""
                SELECT
                    model_name,
                    annotation_type,
                    COUNT(DISTINCT video_id)              AS videos_covered,
                    ROUND(AVG(probability), 3)            AS avg_probability,
                    ROUND(MIN(probability), 3)            AS min_probability,
                    ROUND(MAX(probability), 3)            AS max_probability
                FROM model_annotations
                {pf}
                GROUP BY model_name, annotation_type
                ORDER BY model_name, annotation_type
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

            stats["model_species_dist"] = pd.read_sql(
                text(f"""
                SELECT
                    model_name,
                    value_text           AS predicted_species,
                    COUNT(*)             AS predictions,
                    ROUND(AVG(probability), 3) AS avg_confidence
                FROM model_annotations
                WHERE annotation_type = 'species'
                AND value_text IS NOT NULL
                {"AND project_id = :pid" if self.active_project_id else ""}
                GROUP BY model_name, value_text
                ORDER BY model_name, predictions DESC
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

            ma_pid = "AND project_id = :pid" if self.active_project_id else ""
            io_pid = "AND project_id = :pid" if self.active_project_id else ""
            stats["model_human_agreement"] = pd.read_sql(
                text(f"""
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
                    WHERE annotation_type = 'species' {ma_pid}
                ),
                manual AS (
                    SELECT DISTINCT video_id, species AS manual_species
                    FROM individual_observations
                    WHERE 1=1 {io_pid}
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
                params=p,
            ).to_dict(orient="records")

            stats["camera_summary"] = pd.read_sql(
                text(f"""
                SELECT
                    v.camera_id,
                    COUNT(*)                                               AS total_videos,
                    SUM(CASE WHEN vl.video_id IS NOT NULL THEN 1 ELSE 0 END) AS labeled,
                    SUM(CASE WHEN vl.is_blank = 1 THEN 1 ELSE 0 END)         AS blank,
                    ROUND(SUM(COALESCE(v.duration_sec,0))/3600.0, 2)         AS hours
                FROM videos v
                LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                {vf}
                GROUP BY v.camera_id
                ORDER BY total_videos DESC
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

        return stats
