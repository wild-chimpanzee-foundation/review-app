"""
local_data_provider.py
======================
Drop-in replacement for DataProvider that needs no database.

Storage layout
--------------
video_dir/               ← point at your folder of video files
_local_db/               ← created automatically
    annotations.csv      ← model predictions imported via CSV
    manual_reviews.csv   ← human labels (append-only; latest row wins)
    history.csv          ← audit trail
    config.json          ← app config / overrides

Configuration
-------------
Place a `local_config.yaml` next to this file (or pass config_path=):

    video_dir: ./videos
    db_dir:    ./_local_db
    species:   [blank, unknown, deer, fox, ...]
    behaviors: [reacts_to_camera, does_not_react, ...]

Wiring into your existing frontend
-----------------------------------
In review_app/frontend/data_access.py, replace:

    from review_app.data_provider import DataProvider
    data_provider = DataProvider()

with:

    from local_data_provider import LocalDataProvider
    data_provider = LocalDataProvider()          # reads local_config.yaml
    # or: LocalDataProvider(config_path="/path/to/config.yaml")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
import os
from dotenv import load_dotenv

load_dotenv()
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}
)

CSV_TEMPLATES: dict[str, str] = {
    "blank_non_blank": (
        "video_uid,blank_non_blank,probability,t_start_sec,t_end_sec\n"
        "VIDEO_001,blank,0.97,0,\n"
        "VIDEO_002,non_blank,0.89,0,\n"
    ),
    "species": (
        "video_uid,species_code,probability,t_start_sec,t_end_sec\n"
        "VIDEO_001,deer,0.92,0,\n"
        "VIDEO_002,fox,0.81,0,\n"
    ),
    "behavior": (
        "video_uid,behavior_code,probability,t_start_sec,t_end_sec\n"
        "VIDEO_001,reacts_to_camera,0.87,12.5,15.0\n"
        "VIDEO_002,does_not_react,0.91,0,\n"
    ),
}

DEFAULT_CONFIG: dict[str, Any] = {
    "video_dir": "./videos",
    "db_dir": "./_local_db",
    "species_csv_path": "docs/species.csv",
    "species_column": "Nom_commun_anglais",
    "behaviors": ["reacts_to_camera", "does_not_react", "feeding", "moving", "stationary"],
}

# CSV column schemas used when creating empty files
_SCHEMA: dict[str, list[str]] = {
    "annotations": [
        "video_uid",
        "annotation_type",
        "value_text",
        "value_num",
        "probability",
        "t_start_sec",
        "t_end_sec",
        "model_name",
        "model_version",
        "created_at",
    ],
    "manual_reviews": [
        "video_uid",
        "final_species_prediction",
        "is_blank",
        "needs_manual_review",
        "annotator",
        "created_at",
        "updated_at",
    ],
    "history": [
        "video_uid",
        "event_type",
        "details",
        "payload_json",
        "created_at",
    ],
}


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class LocalDataProvider:
    """CSV-backed local data provider; mirrors the DataProvider public API."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._load_yaml_config(os.getenv("LOCAL_CONFIG_YAML"))
        self.video_dir = (
            Path(cfg.get("video_dir", DEFAULT_CONFIG["video_dir"])).expanduser().resolve()
        )
        self.db_dir = Path(cfg.get("db_dir", DEFAULT_CONFIG["db_dir"])).expanduser().resolve()
        self.db_dir.mkdir(parents=True, exist_ok=True)

        self._species: list[str] = self._load_species(cfg)
        self._behaviors: list[str] = cfg.get("behaviors", DEFAULT_CONFIG["behaviors"])

        for name, cols in _SCHEMA.items():
            self._ensure_csv(self.db_dir / f"{name}.csv", cols)

    # ------------------------------------------------------------------ paths

    @property
    def _annotations_path(self) -> Path:
        return self.db_dir / "annotations.csv"

    @property
    def _reviews_path(self) -> Path:
        return self.db_dir / "manual_reviews.csv"

    @property
    def _history_path(self) -> Path:
        return self.db_dir / "history.csv"

    @property
    def _app_config_path(self) -> Path:
        return self.db_dir / "config.json"

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
    def _ensure_csv(path: Path, columns: list[str]) -> None:
        if not path.exists():
            pd.DataFrame(columns=columns).to_csv(path, index=False)

    @staticmethod
    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _read_csv(self, path: Path) -> pd.DataFrame:
        try:
            df = pd.read_csv(path, dtype=str)
            return df if not df.empty else pd.DataFrame(columns=df.columns)
        except (pd.errors.EmptyDataError, FileNotFoundError):
            return pd.DataFrame()

    def _append_rows(self, path: Path, rows: list[dict]) -> None:
        if not rows:
            return
        new_df = pd.DataFrame(rows)
        existing = self._read_csv(path)
        combined = (
            pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
        )
        combined.to_csv(path, index=False)

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

        annotations = self._read_csv(self._annotations_path)
        reviews = self._read_csv(self._reviews_path)

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

        # Manual reviews — latest row per video wins
        if not reviews.empty and "video_uid" in reviews.columns:
            rev_latest = (
                reviews.assign(_ts=pd.to_datetime(reviews["updated_at"], errors="coerce"))
                .sort_values("_ts", ascending=False)
                .drop_duplicates("video_uid", keep="first")
            )[["video_uid", "final_species_prediction", "is_blank", "needs_manual_review"]]
            df = df.merge(rev_latest, left_on="video_id", right_on="video_uid", how="left").drop(
                columns=["video_uid"], errors="ignore"
            )
        else:
            df["final_species_prediction"] = None
            df["is_blank"] = None
            df["needs_manual_review"] = None

        # Coerce needs_manual_review to bool
        df["needs_manual_review"] = df["needs_manual_review"].map(
            lambda x: str(x).lower() == "true" if pd.notna(x) else False
        )

        # Derived columns expected by the frontend
        df["manual_review_prediction"] = df["final_species_prediction"]
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

    def update_manual_review(
        self,
        video_id: str,
        final_species_prediction: str,
        needs_manual_review: bool = False,
    ) -> None:
        now = self._utcnow()
        is_blank = final_species_prediction == "blank"

        self._append_rows(
            self._reviews_path,
            [
                {
                    "video_uid": video_id,
                    "final_species_prediction": final_species_prediction,
                    "is_blank": is_blank,
                    "needs_manual_review": needs_manual_review,
                    "annotator": "local",
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        )

        self._append_rows(
            self._history_path,
            [
                {
                    "video_uid": video_id,
                    "event_type": "manual_review",
                    "details": f"Labelled as: {final_species_prediction}",
                    "payload_json": json.dumps(
                        {
                            "final_species_prediction": final_species_prediction,
                            "needs_manual_review": needs_manual_review,
                        }
                    ),
                    "created_at": now,
                }
            ],
        )

    def restore_video_snapshot(self, snapshot: dict) -> None:
        if not snapshot or "video_id" not in snapshot:
            return
        self.update_manual_review(
            snapshot["video_id"],
            snapshot.get("final_species_prediction") or "",
            bool(snapshot.get("needs_manual_review") or False),
        )

    # --------------------------------------------------------- history / stats

    def get_video_history(self, video_id: str) -> pd.DataFrame:
        history = self._read_csv(self._history_path)
        if history.empty or "video_uid" not in history.columns:
            return pd.DataFrame(columns=["stage", "status", "timestamp", "details"])
        sub = history[history["video_uid"] == video_id].copy()
        sub = sub.rename(columns={"event_type": "stage", "created_at": "timestamp"})
        sub["status"] = ""
        return sub[["stage", "status", "timestamp", "details"]].reset_index(drop=True)

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

    def validate_model_csv(
        self,
        df: pd.DataFrame,
        annotation_type: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        annotation_type = self._normalize_annotation_type(annotation_type)
        src = df.copy()
        src.columns = [str(c).strip() for c in src.columns]

        if "video_uid" not in src.columns:
            raise ValueError("CSV must include a `video_uid` column.")

        known_videos = set(self._scan_videos()["video_id"].astype(str))
        species_set = set(self._species)

        prob_col = self._pick_column(src, ["probability", "score", "confidence"])
        start_col = self._pick_column(src, ["t_start_sec", "timestamp_sec", "timestamp"])
        end_col = self._pick_column(src, ["t_end_sec", "end_sec"])

        # Determine value column based on type
        value_col_candidates = {
            "blank_non_blank": ["blank_non_blank", "prediction", "value_text"],
            "species": ["species_code", "prediction", "value_text"],
            "behavior": ["behavior_code", "behavior", "prediction", "value_text"],
        }
        value_col = self._pick_column(src, value_col_candidates[annotation_type])
        if not value_col:
            raise ValueError(
                f"{annotation_type} CSV must include one of: "
                f"{', '.join(value_col_candidates[annotation_type])}"
            )

        prepared_rows: list[dict] = []
        errors: list[dict] = []

        for idx, row in src.iterrows():
            row_num = int(idx) + 2
            video_uid = str(row.get("video_uid", "")).strip()

            if not video_uid:
                errors.append({"row_number": row_num, "error": "Missing video_uid"})
                continue
            if video_uid not in known_videos:
                errors.append(
                    {"row_number": row_num, "video_uid": video_uid, "error": "Unknown video_uid"}
                )
                continue

            t_start_sec = pd.to_numeric(
                pd.Series([row.get(start_col) if start_col else 0]), errors="coerce"
            ).iloc[0]
            if pd.isna(t_start_sec):
                errors.append(
                    {"row_number": row_num, "video_uid": video_uid, "error": "Invalid t_start_sec"}
                )
                continue

            t_end_sec = pd.to_numeric(
                pd.Series([row.get(end_col) if end_col else None]), errors="coerce"
            ).iloc[0]
            t_end_sec = None if pd.isna(t_end_sec) else float(t_end_sec)

            probability = pd.to_numeric(
                pd.Series([row.get(prob_col) if prob_col else None]), errors="coerce"
            ).iloc[0]
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

            prepared: dict[str, Any] = {
                "video_uid": video_uid,
                "annotation_type": annotation_type,
                "value_text": None,
                "value_num": None,
                "probability": probability,
                "t_start_sec": float(t_start_sec),
                "t_end_sec": t_end_sec,
            }

            raw = row.get(value_col)

            if annotation_type == "blank_non_blank":
                state = str(raw or "").strip().lower().replace("-", "_")
                normalized = {
                    "blank": "blank",
                    "non_blank": "non_blank",
                    "nonblank": "non_blank",
                }.get(state)
                if not normalized:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": "value must be blank or non_blank",
                        }
                    )
                    continue
                prepared["value_text"] = normalized

            elif annotation_type == "species":
                code = str(raw or "").strip()
                if not code:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": "Missing species code",
                        }
                    )
                    continue
                if code not in species_set:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": f"Unknown species_code `{code}`",
                        }
                    )
                    continue
                prepared["value_text"] = code

            elif annotation_type == "behavior":
                code = str(raw or "").strip()
                if not code:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": "Missing behavior code",
                        }
                    )
                    continue
                # Behaviors are soft-validated — unknown codes are accepted but flagged
                if code not in set(self._behaviors):
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": f"Unrecognised behavior `{code}` (accepted anyway)",
                            "warning": True,
                        }
                    )
                prepared["value_text"] = code

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

        now = self._utcnow()
        model_run_id = f"{model_name}__{model_version}__{now}"

        rows = [
            {
                **{
                    k: row.get(k)
                    for k in (
                        "video_uid",
                        "annotation_type",
                        "value_text",
                        "value_num",
                        "probability",
                        "t_start_sec",
                        "t_end_sec",
                    )
                },
                "model_name": model_name,
                "model_version": model_version,
                "created_at": now,
            }
            for row in cleaned_df.to_dict(orient="records")
        ]

        self._append_rows(self._annotations_path, rows)
        return {"inserted_rows": len(rows), "model_run_id": model_run_id}

    # ----------------------------------------------------------------- no-ops
    # These exist only to keep the interface identical to DataProvider.

    def reapply_thresholds_to_all(self) -> None:  # pragma: no cover
        raise NotImplementedError("Not applicable for local CSV provider.")

    def force_update_video(
        self, video_id, stage, status, species, needs_review, blank_result=None
    ):
        final = "blank" if blank_result == "blank" else species
        self.update_manual_review(video_id, final, needs_manual_review=needs_review)
