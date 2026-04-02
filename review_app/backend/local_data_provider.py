from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    String,
    create_engine,
    delete,
    func,
    select,
)
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

# ---------------------------------------------------------------------------
# Database Schema
# ---------------------------------------------------------------------------

Base = declarative_base()


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(String, default=lambda: str(uuid.uuid4()), primary_key=True)
    video_uid = Column(String, index=True)
    annotation_type = Column(String)
    value_text = Column(String)
    value_num = Column(Float)
    probability = Column(Float)
    t_start_sec = Column(Float)
    t_end_sec = Column(Float)
    model_name = Column(String)
    model_version = Column(String)
    created_at = Column(DateTime, default=func.now())
    session_id = Column(String, index=True)
    individual_id = Column(String)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}
)

CSV_TEMPLATES: dict[str, str] = {
    "annotations": (
        "video_uid,annotation_type,value_text,value_num,probability,t_start_sec,t_end_sec,"
        "model_name,model_version,session_id,individual_id,created_at\n"
        "VIDEO_001,species,deer,0,0.92,0,,species_slowfast_disjoint,v1,,sess-1,\n"
        "VIDEO_002,blank_non_blank,blank,0,0.98,0,,blank_model,v1,,sess-2,\n"
        "VIDEO_003,behavior,reacts_to_camera,0,0.87,12.5,15.0,behavior_model,v1,,sess-3,\n"
    ),
}

DEFAULT_CONFIG: dict[str, Any] = {
    "video_dir": "./videos",
    "db_dir": "./_local_db",
    "species_csv_path": "docs/species.csv",
    "species_column": "Nom_commun_anglais",
    "behaviors": ["reacts_to_camera", "does_not_react", "feeding", "moving", "stationary"],
}


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class LocalDataProvider:
    """SQLite-backed local data provider; mirrors the DataProvider public API."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._load_yaml_config(os.getenv("LOCAL_CONFIG_YAML"))
        self.video_dir = (
            Path(cfg.get("video_dir", DEFAULT_CONFIG["video_dir"])).expanduser().resolve()
        )
        self.db_dir = Path(cfg.get("db_dir", DEFAULT_CONFIG["db_dir"])).expanduser().resolve()
        self.db_dir.mkdir(parents=True, exist_ok=True)

        self._species: list[str] = self._load_species(cfg)
        self._species_behaviors: dict[str, list[str]] = self._load_species_behaviors(cfg)
        self._behaviors: list[str] = cfg.get("behaviors", DEFAULT_CONFIG["behaviors"])

        # Database setup
        db_path = self.db_dir / "review_data.db"
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    @property
    def _app_config_path(self) -> Path:
        return self.db_dir / "config.json"

    def _query_to_df(self, query) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(query, conn)

    # --------------------------------------------------------- internal helpers

    @staticmethod
    def _load_yaml_config(config_path: str | Path | None) -> dict:
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"
        p = Path(config_path)
        print(p)
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
        return {}

    @staticmethod
    def _load_species(cfg: dict) -> list[str]:
        path = cfg.get("species_csv_path", DEFAULT_CONFIG["species_csv_path"])
        column = cfg.get("species_column", DEFAULT_CONFIG["species_column"])

        p = Path(path)
        if not p.exists():
            # Try relative to the app root if not found
            p = Path(__file__).parents[2] / path

        if not p.exists():
            raise FileNotFoundError(
                f"Species CSV file not found at `{path}`. "
                "Species list is mandatory. Please check your config."
            )

        try:
            df = pd.read_csv(p, sep=";")
            if column not in df.columns:
                available_cols = ", ".join(df.columns)
                raise ValueError(
                    f"Column `{column}` not found in species CSV. Available: {available_cols}"
                )

            # Extract species, ensure they are strings, and sort
            species_list = sorted({str(s).strip() for s in df[column].dropna() if str(s).strip()})
            if not species_list:
                raise ValueError(f"No species names found in column `{column}` of `{path}`.")

            return species_list
        except Exception as e:
            if isinstance(e, (FileNotFoundError, ValueError)):
                raise
            raise RuntimeError(f"Failed to load species from CSV: {e}") from e

    @staticmethod
    def _load_species_behaviors(cfg: dict) -> dict[str, list[str]]:
        path = cfg.get("species_behaviors_csv_path", "docs/species_behaviors.csv")
        p = Path(path)
        if not p.exists():
            p = Path(__file__).parents[2] / path

        mapping: dict[str, list[str]] = {}
        if not p.exists():
            return mapping

        try:
            df = pd.read_csv(p, sep=";")
            if "Species" not in df.columns or "Behavior" not in df.columns:
                return mapping

            for _, row in df.iterrows():
                species = str(row["Species"]).strip()
                behavior = str(row["Behavior"]).strip()
                if species and behavior:
                    if species not in mapping:
                        mapping[species] = []
                    mapping[species].append(behavior)
            return mapping
        except Exception as e:
            print(f"Error loading species behaviors from CSV: {e}")
            return mapping

    def get_behaviors_for_species(self, species_name: str) -> list[str]:
        # Defaults for all species
        defaults = ["unlabeled", "reacts_to_camera", "does_not_react"]
        extras = self._species_behaviors.get(species_name, [])
        # Return unique combined list, preserving order
        seen = set()
        result = []
        for b in defaults + extras:
            if b not in seen:
                result.append(b)
                seen.add(b)
        return result

    def _scan_videos(self) -> pd.DataFrame:
        """Walk video_dir and return one row per video file found."""
        if not self.video_dir.exists():
            return pd.DataFrame(columns=["video_id", "video_path", "camera_id", "created_at"])
        rows = []
        for p in sorted(self.video_dir.rglob("*")):
            if p.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            # Use parent-folder name as camera_id (common camera-trap layout).
            camera_id = p.parent.name if p.parent != self.video_dir else "default"
            rows.append(
                {
                    "video_id": p.stem,
                    "video_path": str(p),
                    "camera_id": camera_id,
                    "created_at": datetime.fromtimestamp(
                        p.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return pd.DataFrame(rows)

    # --------------------------------------------------- config / overrides

    def check_db_exists(self) -> bool:
        return self.video_dir.exists() and any(
            p.suffix.lower() in VIDEO_EXTENSIONS for p in self.video_dir.rglob("*")
        )

    def get_valid_species(self) -> list[str]:
        return list(self._species)

    def get_valid_behaviors(self) -> list[str]:
        return list(self._behaviors)

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

    # --------------------------------------------------------- video queries

    def get_all_videos(self) -> pd.DataFrame:
        videos = self._scan_videos()
        if videos.empty:
            return pd.DataFrame()

        with self.engine.connect() as conn:
            annotations = pd.read_sql(select(Annotation), conn)

        df = videos.copy()

        # --- helper: pick the latest annotation per video for a given type/model ---
        def _latest(ann_type: str, model_name: str | None = None) -> pd.DataFrame:
            if annotations.empty or "annotation_type" not in annotations.columns:
                return pd.DataFrame(columns=["video_uid"])
            mask = annotations["annotation_type"] == ann_type
            if model_name:
                mask &= annotations.get("model_name", pd.Series(dtype=str)) == model_name
            sub = annotations[mask].copy()
            if sub.empty:
                return pd.DataFrame(columns=["video_uid"])
            sub["_ts"] = pd.to_datetime(sub["created_at"], errors="coerce")
            return sub.sort_values("_ts", ascending=False).drop_duplicates(
                "video_uid", keep="first"
            )

        def _merge_pred(
            ann_df: pd.DataFrame,
            base: pd.DataFrame,
            pred_col: str,
            prob_col: str,
        ) -> pd.DataFrame:
            if ann_df.empty or "value_text" not in ann_df.columns:
                return base
            sub = ann_df[["video_uid", "value_text", "probability"]].rename(
                columns={"value_text": pred_col, "probability": prob_col}
            )
            return base.merge(sub, left_on="video_id", right_on="video_uid", how="left").drop(
                columns=["video_uid"], errors="ignore"
            )

        # Species model predictions
        df = _merge_pred(
            _latest("species", "species_slowfast_disjoint"),
            df,
            "species_slowfast_disjoint_prediction",
            "species_slowfast_disjoint_prediction_probability",
        )
        df = _merge_pred(
            _latest("species", "species_slowfast_overlapping"),
            df,
            "species_slowfast_overlapping_prediction",
            "species_slowfast_overlapping_prediction_probability",
        )
        df = _merge_pred(
            _latest("species", "species_zamba"),
            df,
            "species_zamba_prediction",
            "species_zamba_prediction_probability",
        )

        # Blank / non-blank
        bnb = _latest("blank_non_blank")
        df = _merge_pred(bnb, df, "blank_non_blank_final_result", "blank_non_blank_probability")
        if "blank_non_blank_final_result" not in df.columns:
            df["blank_non_blank_final_result"] = None
            df["blank_non_blank_probability"] = None

        # Behavior
        beh = _latest("behavior")
        if not beh.empty and "value_text" in beh.columns:
            beh_sub = beh[["video_uid", "value_text"]].rename(
                columns={"value_text": "behavior_prediction"}
            )
            df = df.merge(beh_sub, left_on="video_id", right_on="video_uid", how="left").drop(
                columns=["video_uid"], errors="ignore"
            )
        else:
            df["behavior_prediction"] = None

        # Ensure prediction columns exist even if no annotations imported yet
        for col in [
            "species_slowfast_disjoint_prediction",
            "species_slowfast_overlapping_prediction",
            "species_zamba_prediction",
        ]:
            if col not in df.columns:
                df[col] = None

        # Consensus: all three models must agree
        def _consensus(row: pd.Series) -> str:
            preds = [
                row.get("species_slowfast_disjoint_prediction"),
                row.get("species_slowfast_overlapping_prediction"),
                row.get("species_zamba_prediction"),
            ]
            valid = [str(p) for p in preds if pd.notna(p) and str(p).strip()]
            if len(valid) == 3 and len(set(valid)) == 1:
                return valid[0]
            return "UNKNOWN"

        df["classification_consensus"] = df.apply(_consensus, axis=1)

        def _latest_manual_reviews(ann_df: pd.DataFrame) -> pd.DataFrame:
            manual = ann_df[
                (ann_df["model_name"] == "manual_review")
                & ann_df["annotation_type"].isin({"species", "behavior", "blank_non_blank"})
            ].copy()
            if manual.empty:
                return pd.DataFrame(
                    columns=[
                        "video_uid",
                        "species_behavior_json",
                        "is_blank",
                        "needs_manual_review",
                    ]
                )

            manual["created_at_ts"] = pd.to_datetime(manual["created_at"], errors="coerce")
            manual["session_id"] = manual["session_id"].fillna("")
            manual["individual_id"] = manual["individual_id"].fillna("")

            session_meta = (
                manual.groupby(["video_uid", "session_id"], dropna=False)["created_at_ts"]
                .max()
                .reset_index()
            )

            latest_sessions = (
                session_meta.sort_values("created_at_ts")
                .groupby("video_uid", sort=False)
                .last()
                .reset_index()
            )

            latest_manual = manual.merge(
                latest_sessions[["video_uid", "session_id"]],
                on=["video_uid", "session_id"],
                how="inner",
            )

            session_groups: list[dict[str, Any]] = []
            for video_id, session_rows in latest_manual.groupby("video_uid", sort=False):
                selections: list[dict[str, Any]] = []
                for _, individual_rows in session_rows.groupby("individual_id", sort=False):
                    species_row = individual_rows[
                        individual_rows["annotation_type"] == "species"
                    ]
                    behavior_row = individual_rows[
                        individual_rows["annotation_type"] == "behavior"
                    ]
                    blank_row = individual_rows[
                        individual_rows["annotation_type"] == "blank_non_blank"
                    ]

                    if not species_row.empty:
                        species_val = str(
                            species_row.iloc[-1].get("value_text") or "unknown"
                        )
                        timestamp = (
                            species_row.iloc[-1].get("t_start_sec")
                            or species_row.iloc[-1].get("value_num")
                            or 0.0
                        )
                    elif not blank_row.empty:
                        species_val = "blank"
                        timestamp = (
                            blank_row.iloc[-1].get("t_start_sec")
                            or blank_row.iloc[-1].get("value_num")
                            or 0.0
                        )
                    else:
                        species_val = "unknown"
                        timestamp = 0.0

                    try:
                        timestamp = float(timestamp)
                    except Exception:
                        timestamp = 0.0

                    if not behavior_row.empty:
                        behavior_val = str(behavior_row.iloc[-1].get("value_text") or "unlabeled")
                    else:
                        behavior_val = "unlabeled"

                    selections.append(
                        {
                            "species": species_val,
                            "behavior": behavior_val,
                            "timestamp": timestamp,
                        }
                    )

                if not selections:
                    continue

                session_groups.append(
                    {
                        "video_uid": video_id,
                        "species_behavior_json": json.dumps(selections),
                        "is_blank": any(s["species"] == "blank" for s in selections),
                        "needs_manual_review": False,
                    }
                )

            return pd.DataFrame(session_groups)

        manual_reviews = _latest_manual_reviews(annotations)
        if not manual_reviews.empty:
            df = df.merge(
                manual_reviews, left_on="video_id", right_on="video_uid", how="left"
            ).drop(columns=["video_uid"], errors="ignore")
        else:
            df["species_behavior_json"] = None
            df["is_blank"] = None
            df["needs_manual_review"] = None

        # Coerce needs_manual_review to bool
        df["needs_manual_review"] = df["needs_manual_review"].map(
            lambda x: bool(x) if pd.notna(x) else False
        )

        def _format_selections(val: Any) -> str | None:
            if not val or pd.isna(val):
                return None
            try:
                data = json.loads(val) if isinstance(val, str) else val
                if not isinstance(data, list):
                    return str(data)
                parts = []
                for s in data:
                    species = s.get("species", "unknown")
                    behavior = s.get("behavior", "unlabeled")
                    ts = s.get("timestamp", 0.0)
                    parts.append(f"{species} ({behavior}) @ {ts}s")
                return ", ".join(parts)
            except Exception:
                return str(val)

        # Derived columns expected by the frontend
        df["manual_review_prediction"] = df["species_behavior_json"].apply(_format_selections)
        df["final_species_prediction"] = df["manual_review_prediction"]
        df["current_stage"] = df["needs_manual_review"].map(
            lambda x: "manual_review" if x else "completed"
        )
        df["status"] = df["needs_manual_review"].map(lambda x: "NEEDS_REVIEW" if x else "success")
        df["is_video_valid"] = True
        df["video_validation_details"] = None
        df["depth_estimation_data"] = None
        df["last_updated"] = df["created_at"]

        return df.sort_values("last_updated", ascending=False, na_position="last")

    def get_filtered_videos(self, filters: dict) -> pd.DataFrame:
        df = self.get_all_videos()
        if df.empty:
            return df

        q = (filters.get("search_query") or "").strip().lower()
        if q:
            df = df[
                df["video_id"].fillna("").str.lower().str.contains(q)
                | df["video_path"].fillna("").str.lower().str.contains(q)
            ]

        cam = filters.get("selected_camera", "All")
        if cam != "All":
            df = df[df["camera_id"] == cam]

        sp = filters.get("selected_species", "All")
        if sp != "All":
            df = df[df["final_species_prediction"] == sp]

        poss = filters.get("selected_possible_species", "All")
        if poss != "All":
            cols = [
                c
                for c in [
                    "species_slowfast_overlapping_prediction",
                    "species_slowfast_disjoint_prediction",
                    "species_zamba_prediction",
                ]
                if c in df.columns
            ]
            if cols:
                df = df[df[cols].eq(poss).any(axis=1)]

        rev = filters.get("selected_review", "All")
        if rev == "Needs Review":
            df = df[df["needs_manual_review"] == True]  # noqa: E712
        elif rev == "No Review":
            df = df[df["needs_manual_review"] != True]  # noqa: E712

        bnb = filters.get("selected_blank_non_blank", "All")
        if bnb == "Blank":
            df = df[df["blank_non_blank_final_result"] == "blank"]
        elif bnb == "Non-Blank":
            df = df[df["blank_non_blank_final_result"] == "non_blank"]
        elif bnb == "Unknown":
            df = df[df["blank_non_blank_final_result"].isnull()]

        beh = filters.get("selected_behavior", "All")
        if beh == "Has Behavior":
            df = df[df["behavior_prediction"].fillna("").str.strip() != ""]
        elif beh == "No Behavior":
            df = df[df["behavior_prediction"].fillna("").str.strip() == ""]
        elif beh != "All":
            df = df[df["behavior_prediction"] == beh]

        return df

    def get_videos_for_review(self) -> pd.DataFrame:
        return self.get_filtered_videos({"selected_review": "Needs Review"})

    def get_video_by_id(self, video_id: str) -> dict | None:
        df = self.get_all_videos()
        row = df[df["video_id"] == video_id]
        return row.iloc[0].to_dict() if not row.empty else None

    def get_video_annotations(self, video_id: str) -> pd.DataFrame:
        """Return all model annotations for this video."""
        query = (
            select(Annotation)
            .where(Annotation.video_uid == video_id)
            .order_by(Annotation.created_at.desc())
        )
        return self._query_to_df(query)

    def get_filter_options(self) -> dict:
        df = self.get_all_videos()
        if df.empty:
            return {
                "camera_values": [],
                "species_values": [],
                "possible_species_values": [],
                "behavior_values": [],
            }

        sp_cols = [
            "species_slowfast_overlapping_prediction",
            "species_slowfast_disjoint_prediction",
            "species_zamba_prediction",
        ]
        all_possible: list[str] = []
        for col in sp_cols:
            if col in df.columns:
                all_possible.extend(df[col].dropna().astype(str).tolist())

        def _vals(col: str) -> list[str]:
            if col not in df.columns:
                return []
            return sorted({str(v) for v in df[col].dropna() if str(v).strip()})

        return {
            "camera_values": _vals("camera_id"),
            "species_values": _vals("final_species_prediction"),
            "possible_species_values": sorted({v for v in all_possible if v.strip()}),
            "behavior_values": _vals("behavior_prediction"),
        }

    # --------------------------------------------------------- write operations

    def _utcnow_dt(self) -> datetime:
        return datetime.now(timezone.utc)

    def update_manual_review(
        self,
        video_id: str,
        selections: list[dict],
        annotator: str = "local",
    ) -> None:
        if not selections:
            return
        print("Selections")
        print(selections)

        now = self._utcnow_dt()
        session_id = str(uuid.uuid4())

        with self.Session() as session:
            session.execute(
                delete(Annotation).where(
                    Annotation.video_uid == video_id,
                    Annotation.model_name == "manual_review",
                )
            )
            individual_counter = 0
            for selection in selections:
                species = str(selection.get("species") or "").strip() or "unknown"
                behavior = str(selection.get("behavior") or "").strip() or "unlabeled"
                timestamp = float(selection.get("timestamp") or 0.0)
                individual_id = f"{session_id}-{individual_counter}"
                individual_counter += 1

                def insert_annotation(annotation_type: str, value_text: str | None) -> None:
                    session.add(
                        Annotation(
                            video_uid=video_id,
                            annotation_type=annotation_type,
                            value_text=value_text,
                            value_num=timestamp,
                            probability=None,
                            t_start_sec=timestamp,
                            t_end_sec=None,
                            model_name="manual_review",
                            model_version=annotator,
                            created_at=now,
                            session_id=session_id,
                            individual_id=individual_id,
                        )
                    )

                if species.lower() == "blank":
                    insert_annotation("blank_non_blank", "blank")
                    continue

                insert_annotation("species", species)
                insert_annotation("behavior", behavior)
            session.commit()

    def restore_video_snapshot(self, snapshot: dict) -> None:
        if not snapshot or "video_id" not in snapshot:
            return

        selections = []
        if "species_behavior_json" in snapshot and snapshot["species_behavior_json"]:
            try:
                selections = json.loads(snapshot["species_behavior_json"])
            except Exception:
                pass

        if not selections and "final_species_prediction" in snapshot:
            # Fallback for old snapshots
            selections = [
                {
                    "species": snapshot["final_species_prediction"] or "unknown",
                    "behavior": "unlabeled",
                    "timestamp": 0.0,
                }
            ]

        self.update_manual_review(snapshot["video_id"], selections)

    # --------------------------------------------------------- stats
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

    # ------------------------------------------------- CSV import / validation

    @staticmethod
    def _normalize_annotation_type(annotation_type: str) -> str:
        supported = {"blank_non_blank", "species", "behavior"}
        normalized = (annotation_type or "").strip().lower()
        if normalized not in supported:
            raise ValueError(
                f"Unsupported annotation_type `{annotation_type}`. Use one of {sorted(supported)}"
            )
        return normalized

    @staticmethod
    def _pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
        for col in candidates:
            if col in df.columns:
                return col
        return None

    def validate_model_csv(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        src = df.copy()
        src.columns = [str(c).strip() for c in src.columns]

        required = {"video_uid", "annotation_type"}
        missing = required - set(src.columns)
        if missing:
            raise ValueError(f"CSV must include columns: {', '.join(sorted(missing))}")

        known_videos = set(self._scan_videos()["video_id"].astype(str))
        prepared_rows: list[dict] = []
        errors: list[dict] = []

        for idx, row in src.iterrows():
            row_num = int(idx) + 2
            video_uid = str(row.get("video_uid", "")).strip()
            annotation_type = str(row.get("annotation_type", "")).strip()

            if not video_uid:
                errors.append({"row_number": row_num, "error": "Missing video_uid"})
                continue
            if video_uid not in known_videos:
                errors.append(
                    {"row_number": row_num, "video_uid": video_uid, "error": "Unknown video_uid"}
                )
                continue
            if not annotation_type:
                errors.append(
                    {
                        "row_number": row_num,
                        "video_uid": video_uid,
                        "error": "Missing annotation_type",
                    }
                )
                continue

            t_start = pd.to_numeric(row.get("t_start_sec"), errors="coerce")
            if pd.isna(t_start):
                t_start = 0.0

            t_end = pd.to_numeric(row.get("t_end_sec"), errors="coerce")
            t_end = None if pd.isna(t_end) else float(t_end)

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

            value_text = row.get("value_text")
            if pd.isna(value_text):
                value_text = None
            elif isinstance(value_text, float):
                value_text = None if pd.isna(value_text) else str(value_text)
            else:
                value_text = str(value_text).strip() or None

            value_num = pd.to_numeric(row.get("value_num"), errors="coerce")
            value_num = None if pd.isna(value_num) else float(value_num)

            model_name = str(row.get("model_name") or "").strip() or None
            model_version = str(row.get("model_version") or "").strip() or None
            session_id = str(row.get("session_id") or "").strip()
            if not session_id:
                session_id = str(uuid.uuid4())
            individual_id = str(row.get("individual_id") or "").strip()
            if not individual_id:
                individual_id = str(uuid.uuid4())

            created_at_raw = row.get("created_at")
            if pd.isna(created_at_raw) or created_at_raw is None or created_at_raw == "":
                created_at = self._utcnow_dt()
            else:
                try:
                    created_at = pd.to_datetime(created_at_raw, errors="coerce")
                    if pd.isna(created_at):
                        created_at = self._utcnow_dt()
                    else:
                        created_at = created_at.to_pydatetime()
                except Exception:
                    created_at = self._utcnow_dt()

            prepared = {
                "video_uid": video_uid,
                "annotation_type": annotation_type,
                "value_text": value_text,
                "value_num": value_num,
                "probability": probability,
                "t_start_sec": float(t_start),
                "t_end_sec": t_end,
                "model_name": model_name,
                "model_version": model_version,
                "session_id": session_id,
                "individual_id": individual_id,
                "created_at": created_at,
            }
            prepared_rows.append(prepared)

        return pd.DataFrame(prepared_rows), pd.DataFrame(errors)

    def import_model_csv(
        self,
        cleaned_df: pd.DataFrame,
        model_name: str,
        model_version: str,
        config_version: str | None = None,
    ) -> dict[str, Any]:
        if cleaned_df.empty:
            return {"inserted_rows": 0, "model_run_id": None}

        now = self._utcnow_dt()
        run_name = model_name or "annotations"
        run_version = model_version or "import"
        model_run_id = f"{run_name}__{run_version}__{now.isoformat()}"

        with self.Session() as session:
            for _, row in cleaned_df.iterrows():
                ann = Annotation(
                    video_uid=row["video_uid"],
                    annotation_type=row["annotation_type"],
                    value_text=row.get("value_text"),
                    value_num=row.get("value_num"),
                    probability=row.get("probability"),
                    t_start_sec=row.get("t_start_sec"),
                    t_end_sec=row.get("t_end_sec"),
                    model_name=row.get("model_name") or model_name,
                    model_version=row.get("model_version") or model_version,
                    created_at=row.get("created_at") or now,
                    session_id=row.get("session_id") or str(uuid.uuid4()),
                    individual_id=row.get("individual_id"),
                )
                session.add(ann)
            session.commit()

        return {"inserted_rows": len(cleaned_df), "model_run_id": model_run_id}
