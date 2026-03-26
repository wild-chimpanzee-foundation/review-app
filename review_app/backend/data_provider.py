import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()


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
    "distance": (
        "video_uid,value_num,probability,t_start_sec,t_end_sec\n"
        "VIDEO_001,3.45,0.95,2.0,\n"
        "VIDEO_001,4.10,0.93,4.0,\n"
    ),
}


@dataclass
class RuntimeUser:
    id: str
    email: str


class DataProvider:
    def __init__(self):
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL is required for review-app.")
        # Accept docker-compose style postgres:// and normalize for SQLAlchemy.
        if db_url.startswith("postgres://"):
            db_url = "postgresql+psycopg://" + db_url[len("postgres://") :]
        elif db_url.startswith("postgresql://") and "+psycopg" not in db_url:
            db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

        self.engine = create_engine(db_url)
        self.runtime_user_email = os.getenv("REVIEW_APP_USER_EMAIL", "reviewer@local")

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _ensure_runtime_user(self) -> RuntimeUser:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    text("SELECT id, email FROM users WHERE email = :email LIMIT 1"),
                    {"email": self.runtime_user_email},
                )
                .mappings()
                .first()
            )

            if row:
                return RuntimeUser(id=str(row["id"]), email=str(row["email"]))

            user_id = conn.execute(
                text(
                    """
                    INSERT INTO users (email, display_name, role, is_active, created_at)
                    VALUES (:email, :display_name, 'reviewer', TRUE, now())
                    RETURNING id
                    """
                ),
                {
                    "email": self.runtime_user_email,
                    "display_name": self.runtime_user_email.split("@")[0],
                },
            ).scalar_one()
            return RuntimeUser(id=str(user_id), email=self.runtime_user_email)

    def _videos_base_df(self) -> pd.DataFrame:
        sql = text("""
        WITH latest_paths AS (
            SELECT DISTINCT ON (uvl.video_id)
                uvl.video_id,
                uvl.local_path
            FROM user_video_locations uvl
            ORDER BY uvl.video_id, uvl.last_seen_at DESC NULLS LAST, uvl.id DESC
        ),
        species_preds AS (
            SELECT
                ma.video_id,
                MAX(CASE WHEN mr.model_name = 'species_slowfast_disjoint' THEN ma.value_text END) AS species_slowfast_disjoint_prediction,
                MAX(CASE WHEN mr.model_name = 'species_slowfast_disjoint' THEN ma.probability END) AS species_slowfast_disjoint_prediction_probability,
                MAX(CASE WHEN mr.model_name = 'species_slowfast_overlapping' THEN ma.value_text END) AS species_slowfast_overlapping_prediction,
                MAX(CASE WHEN mr.model_name = 'species_slowfast_overlapping' THEN ma.probability END) AS species_slowfast_overlapping_prediction_probability,
                MAX(CASE WHEN mr.model_name = 'species_zamba' THEN ma.value_text END) AS species_zamba_prediction,
                MAX(CASE WHEN mr.model_name = 'species_zamba' THEN ma.probability END) AS species_zamba_prediction_probability
            FROM model_annotations ma
            JOIN model_runs mr ON mr.id = ma.model_run_id
            WHERE ma.annotation_type = 'species'
            GROUP BY ma.video_id
        ),
        behavior_preds AS (
            SELECT DISTINCT ON (ma.video_id)
                ma.video_id,
                COALESCE(b.label, ma.value_text) AS behavior_prediction
            FROM model_annotations ma
            LEFT JOIN behaviors b ON b.id = ma.behavior_id
            WHERE ma.annotation_type = 'behavior'
            ORDER BY ma.video_id, ma.created_at DESC
        ),
        distance_preds AS (
            SELECT DISTINCT ON (ma.video_id)
                ma.video_id,
                ma.value_num,
                ma.value_text
            FROM model_annotations ma
            WHERE ma.annotation_type = 'distance'
            ORDER BY ma.video_id, ma.created_at DESC
        )
        SELECT
            v.video_uid AS video_id,
            lp.local_path AS video_path,
            c.camera_code AS camera_id,
            v.is_video_valid,
            v.validation_details AS video_validation_details,
            NULL::TIMESTAMPTZ AS video_validation_checked_at,
            CASE
                WHEN vc.needs_manual_review THEN 'manual_review'
                ELSE 'completed'
            END AS current_stage,
            CASE
                WHEN vc.needs_manual_review THEN 'NEEDS_REVIEW'
                ELSE 'success'
            END AS status,
            NULL::DOUBLE PRECISION AS blank_non_blank_probability,
            CASE
                WHEN vc.is_blank THEN 'blank'
                WHEN vc.is_blank = FALSE THEN 'non_blank'
                ELSE NULL
            END AS blank_non_blank_final_result,
            sp.species_slowfast_disjoint_prediction,
            sp.species_slowfast_disjoint_prediction_probability,
            sp.species_slowfast_overlapping_prediction,
            sp.species_slowfast_overlapping_prediction_probability,
            sp.species_zamba_prediction,
            sp.species_zamba_prediction_probability,
            CASE
                WHEN sp.species_slowfast_disjoint_prediction IS NOT NULL
                     AND sp.species_slowfast_disjoint_prediction = sp.species_slowfast_overlapping_prediction
                     AND sp.species_slowfast_disjoint_prediction = sp.species_zamba_prediction
                THEN sp.species_slowfast_disjoint_prediction
                ELSE 'UNKNOWN'
            END AS classification_consensus,
            vc.needs_manual_review,
            ma.manual_review_prediction,
            s.code AS final_species_prediction,
            bp.behavior_prediction,
            COALESCE(CAST(dp.value_num AS TEXT), dp.value_text) AS depth_estimation_data,
            v.created_at,
            vc.updated_at AS last_updated
        FROM videos v
        JOIN cameras c ON c.id = v.camera_id
        LEFT JOIN latest_paths lp ON lp.video_id = v.id
        LEFT JOIN video_consensus vc ON vc.video_id = v.id
        LEFT JOIN species s ON s.id = vc.final_species_id
        LEFT JOIN LATERAL (
            SELECT
                CASE
                    WHEN ma2.is_blank THEN 'blank'
                    ELSE ms.code
                END AS manual_review_prediction
            FROM manual_annotations ma2
            LEFT JOIN species ms ON ms.id = ma2.species_id
            WHERE ma2.video_id = v.id
            ORDER BY ma2.updated_at DESC NULLS LAST, ma2.created_at DESC NULLS LAST
            LIMIT 1
        ) ma ON TRUE
        LEFT JOIN species_preds sp ON sp.video_id = v.id
        LEFT JOIN behavior_preds bp ON bp.video_id = v.id
        LEFT JOIN distance_preds dp ON dp.video_id = v.id
        """)
        return pd.read_sql_query(sql, self.engine)

    def get_config(self):
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT config_json
                    FROM config_versions
                    WHERE is_active = TRUE
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                )
            ).scalar_one_or_none()
        return row or {}

    def save_config(self, config):
        user = self._ensure_runtime_user()
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE config_versions SET is_active = FALSE WHERE is_active = TRUE")
            )
            conn.execute(
                text(
                    """
                    INSERT INTO config_versions (id, config_json, created_by, created_at, is_active)
                    VALUES (uuid_generate_v4(), CAST(:config_json AS JSONB), :created_by, now(), TRUE)
                    """
                ),
                {"config_json": json.dumps(config), "created_by": user.id},
            )

    def get_overrides(self):
        return self.get_config()

    def reapply_thresholds_to_all(self):
        raise NotImplementedError(
            "Threshold reapply for V2 schema is not implemented yet in standalone review-app."
        )

    def check_db_exists(self):
        try:
            df = self.get_all_videos()
            return not df.empty
        except Exception:
            return False

    def get_all_videos(self):
        df = self._videos_base_df()
        return df.sort_values("last_updated", ascending=False, na_position="last")

    def _query_single_column(self, column_name):
        df = self.get_all_videos()
        if column_name not in df.columns:
            return []
        values = df[column_name].dropna().astype(str)
        values = sorted(v for v in values if v.strip())
        return list(dict.fromkeys(values))

    def get_filter_options(self):
        df = self.get_all_videos()
        species_cols = [
            "species_slowfast_overlapping_prediction",
            "species_slowfast_disjoint_prediction",
            "species_zamba_prediction",
        ]
        possible_species = []
        for col in species_cols:
            if col in df.columns:
                possible_species.extend(df[col].dropna().astype(str).tolist())
        possible_species_values = sorted(v for v in set(possible_species) if v.strip())

        return {
            "camera_values": self._query_single_column("camera_id"),
            "species_values": self._query_single_column("final_species_prediction"),
            "possible_species_values": possible_species_values,
            "behavior_values": self._query_single_column("behavior_prediction"),
        }

    def get_filtered_videos(self, filters: dict):
        df = self.get_all_videos()

        search_query = (filters.get("search_query") or "").strip().lower()
        if search_query:
            id_match = df["video_id"].fillna("").str.lower().str.contains(search_query)
            path_match = df["video_path"].fillna("").str.lower().str.contains(search_query)
            df = df[id_match | path_match]

        selected_camera = filters.get("selected_camera", "All")
        if selected_camera != "All":
            df = df[df["camera_id"] == selected_camera]

        selected_species = filters.get("selected_species", "All")
        if selected_species != "All":
            df = df[df["final_species_prediction"] == selected_species]

        selected_possible_species = filters.get("selected_possible_species", "All")
        if selected_possible_species != "All":
            species_cols = [
                "species_slowfast_overlapping_prediction",
                "species_slowfast_disjoint_prediction",
                "species_zamba_prediction",
            ]
            present = [c for c in species_cols if c in df.columns]
            if present:
                mask = df[present].eq(selected_possible_species).any(axis=1)
                df = df[mask]

        selected_validity = filters.get("selected_validity", "All")
        if selected_validity == "Valid Only":
            df = df[df["is_video_valid"] == True]  # noqa: E712
        elif selected_validity == "Invalid Only":
            df = df[df["is_video_valid"] == False]  # noqa: E712
        elif selected_validity == "Unknown":
            df = df[df["is_video_valid"].isnull()]

        selected_review = filters.get("selected_review", "All")
        if selected_review == "Needs Review":
            df = df[df["needs_manual_review"] == True]  # noqa: E712
        elif selected_review == "No Review":
            df = df[df["needs_manual_review"] != True]  # noqa: E712

        selected_blank_non_blank = filters.get("selected_blank_non_blank", "All")
        if selected_blank_non_blank == "Blank":
            df = df[df["blank_non_blank_final_result"] == "blank"]
        elif selected_blank_non_blank == "Non-Blank":
            df = df[df["blank_non_blank_final_result"] == "non_blank"]
        elif selected_blank_non_blank == "Unknown":
            df = df[df["blank_non_blank_final_result"].isnull()]

        selected_behavior = filters.get("selected_behavior", "All")
        if selected_behavior == "Has Behavior":
            df = df[df["behavior_prediction"].fillna("").astype(str).str.strip() != ""]
        elif selected_behavior == "No Behavior":
            df = df[df["behavior_prediction"].fillna("").astype(str).str.strip() == ""]
        elif selected_behavior != "All":
            df = df[df["behavior_prediction"] == selected_behavior]

        distance_min = filters.get("distance_min")
        distance_max = filters.get("distance_max")
        if distance_min is not None or distance_max is not None:
            numeric_dist = pd.to_numeric(df["depth_estimation_data"], errors="coerce")
            if distance_min is not None:
                df = df[numeric_dist >= float(distance_min)]
            if distance_max is not None:
                df = df[numeric_dist <= float(distance_max)]

        return df

    def get_videos_for_review(self):
        return self.get_filtered_videos({"selected_review": "Needs Review"})

    def get_video_by_id(self, video_id):
        df = self.get_all_videos()
        one = df[df["video_id"] == video_id]
        if one.empty:
            return None
        return one.iloc[0].to_dict()

    def get_valid_species(self):
        sql = text("SELECT code FROM species ORDER BY code")
        df = pd.read_sql_query(sql, self.engine)
        return df["code"].astype(str).tolist()

    def update_manual_review(self, video_id, final_species_prediction, needs_manual_review=False):
        user = self._ensure_runtime_user()
        with self.engine.begin() as conn:
            video_row = (
                conn.execute(
                    text("SELECT id FROM videos WHERE video_uid = :video_uid LIMIT 1"),
                    {"video_uid": video_id},
                )
                .mappings()
                .first()
            )
            if not video_row:
                raise ValueError(f"Video not found: {video_id}")
            vid = str(video_row["id"])

            species_id = None
            is_blank = final_species_prediction == "blank"
            if not is_blank and final_species_prediction:
                species_id = conn.execute(
                    text("SELECT id FROM species WHERE code = :code LIMIT 1"),
                    {"code": final_species_prediction},
                ).scalar_one_or_none()

            conn.execute(
                text(
                    """
                    INSERT INTO manual_annotations (
                        video_id, annotator_id, review_state, is_blank, species_id,
                        behavior_id, t_start_sec, t_end_sec, notes, created_at, updated_at
                    ) VALUES (
                        :video_id, :annotator_id, 'approved', :is_blank, :species_id,
                        NULL, 0, NULL, 'Manual review via review-app', now(), now()
                    )
                    """
                ),
                {
                    "video_id": vid,
                    "annotator_id": user.id,
                    "is_blank": is_blank,
                    "species_id": species_id,
                },
            )

            conn.execute(
                text(
                    """
                    INSERT INTO video_consensus (video_id, is_blank, final_species_id, needs_manual_review, consensus_method, updated_at)
                    VALUES (:video_id, :is_blank, :final_species_id, :needs_manual_review, 'manual', now())
                    ON CONFLICT (video_id) DO UPDATE SET
                        is_blank = EXCLUDED.is_blank,
                        final_species_id = EXCLUDED.final_species_id,
                        needs_manual_review = EXCLUDED.needs_manual_review,
                        consensus_method = EXCLUDED.consensus_method,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "video_id": vid,
                    "is_blank": is_blank,
                    "final_species_id": species_id,
                    "needs_manual_review": bool(needs_manual_review),
                },
            )

            conn.execute(
                text(
                    """
                    INSERT INTO review_tasks (video_id, assigned_to, status, priority, claimed_at, completed_at)
                    VALUES (:video_id, :assigned_to, 'completed', 0, now(), now())
                    """
                ),
                {
                    "video_id": vid,
                    "assigned_to": user.id,
                },
            )

            conn.execute(
                text(
                    """
                    INSERT INTO video_history (video_id, actor_type, actor_id, event_type, payload_json, created_at)
                    VALUES (
                        :video_id, 'user', :actor_id, 'manual_review',
                        CAST(:payload_json AS JSONB), now()
                    )
                    """
                ),
                {
                    "video_id": vid,
                    "actor_id": user.id,
                    "payload_json": json.dumps(
                        {
                            "final_species_prediction": final_species_prediction,
                            "needs_manual_review": bool(needs_manual_review),
                        }
                    ),
                },
            )

    def restore_video_snapshot(self, snapshot: dict):
        if not snapshot or "video_id" not in snapshot:
            return
        # Minimal restore: write consensus fields back from snapshot.
        species_code = snapshot.get("final_species_prediction")
        is_blank = snapshot.get("blank_non_blank_final_result") == "blank"
        with self.engine.begin() as conn:
            video_row = (
                conn.execute(
                    text("SELECT id FROM videos WHERE video_uid = :video_uid LIMIT 1"),
                    {"video_uid": snapshot["video_id"]},
                )
                .mappings()
                .first()
            )
            if not video_row:
                return
            vid = str(video_row["id"])
            species_id = None
            if species_code:
                species_id = conn.execute(
                    text("SELECT id FROM species WHERE code = :code LIMIT 1"),
                    {"code": species_code},
                ).scalar_one_or_none()
            conn.execute(
                text(
                    """
                    INSERT INTO video_consensus (video_id, is_blank, final_species_id, needs_manual_review, consensus_method, updated_at)
                    VALUES (:video_id, :is_blank, :final_species_id, :needs_manual_review, 'manual', now())
                    ON CONFLICT (video_id) DO UPDATE SET
                        is_blank = EXCLUDED.is_blank,
                        final_species_id = EXCLUDED.final_species_id,
                        needs_manual_review = EXCLUDED.needs_manual_review,
                        consensus_method = EXCLUDED.consensus_method,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "video_id": vid,
                    "is_blank": is_blank,
                    "final_species_id": species_id,
                    "needs_manual_review": bool(snapshot.get("needs_manual_review") or False),
                },
            )

    def force_update_video(
        self, video_id, stage, status, species, needs_review, blank_result=None
    ):
        # Stage/status are currently derived in V2 and ignored here.
        final_pred = species
        if blank_result == "blank":
            final_pred = "blank"
        self.update_manual_review(video_id, final_pred, needs_manual_review=needs_review)

    def get_pipeline_progress_summary(self):
        all_videos = self.get_all_videos()
        if all_videos.empty:
            return pd.DataFrame(columns=["current_stage", "status", "count"])
        summary = (
            all_videos.groupby(["current_stage", "status"], dropna=False)
            .size()
            .reset_index(name="count")
        )
        return summary

    def get_video_history(self, video_id):
        sql = text("""
            SELECT
                vh.event_type AS stage,
                COALESCE(vh.payload_json->>'status', '') AS status,
                vh.created_at AS timestamp,
                COALESCE(vh.payload_json->>'details', vh.event_type) AS details
            FROM video_history vh
            JOIN videos v ON v.id = vh.video_id
            WHERE v.video_uid = :video_uid
            ORDER BY vh.created_at ASC
        """)
        return pd.read_sql_query(sql, self.engine, params={"video_uid": video_id})

    def get_csv_templates(self) -> dict[str, str]:
        return CSV_TEMPLATES.copy()

    @staticmethod
    def _normalize_annotation_type(annotation_type: str) -> str:
        supported = {"blank_non_blank", "species", "behavior", "distance"}
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
        self, df: pd.DataFrame, annotation_type: str
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        annotation_type = self._normalize_annotation_type(annotation_type)
        source_df = df.copy()
        source_df.columns = [str(c).strip() for c in source_df.columns]

        if "video_uid" not in source_df.columns:
            raise ValueError("CSV must include a `video_uid` column.")

        with self.engine.begin() as conn:
            video_rows = conn.execute(text("SELECT id, video_uid FROM videos")).mappings().all()
            species_rows = conn.execute(text("SELECT id, code FROM species")).mappings().all()
            behavior_rows = conn.execute(text("SELECT id, code FROM behaviors")).mappings().all()

        video_map = {str(r["video_uid"]): str(r["id"]) for r in video_rows}
        species_map = {str(r["code"]): str(r["id"]) for r in species_rows if r["code"] is not None}
        behavior_map = {
            str(r["code"]): str(r["id"]) for r in behavior_rows if r["code"] is not None
        }

        prob_col = self._pick_column(source_df, ["probability", "score", "confidence"])
        start_col = self._pick_column(source_df, ["t_start_sec", "timestamp_sec", "timestamp"])
        end_col = self._pick_column(source_df, ["t_end_sec", "end_sec"])

        value_col = None
        if annotation_type == "blank_non_blank":
            value_col = self._pick_column(
                source_df, ["blank_non_blank", "prediction", "value_text"]
            )
            if not value_col:
                raise ValueError(
                    "Blank/non-blank CSV must include one of: blank_non_blank, prediction, value_text."
                )
        elif annotation_type == "species":
            value_col = self._pick_column(source_df, ["species_code", "prediction", "value_text"])
            if not value_col:
                raise ValueError(
                    "Species CSV must include one of: species_code, prediction, value_text."
                )
        elif annotation_type == "behavior":
            value_col = self._pick_column(
                source_df, ["behavior_code", "behavior", "prediction", "value_text"]
            )
            if not value_col:
                raise ValueError(
                    "Behavior CSV must include one of: behavior_code, behavior, prediction, value_text."
                )
        elif annotation_type == "distance":
            value_col = self._pick_column(source_df, ["value_num", "distance", "distance_m"])
            if not value_col:
                raise ValueError(
                    "Distance CSV must include one of: value_num, distance, distance_m."
                )

        prepared_rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for idx, row in source_df.iterrows():
            row_num = int(idx) + 2
            video_uid = str(row.get("video_uid", "")).strip()
            if not video_uid:
                errors.append({"row_number": row_num, "error": "Missing video_uid"})
                continue

            video_id = video_map.get(video_uid)
            if not video_id:
                errors.append(
                    {"row_number": row_num, "video_uid": video_uid, "error": "Unknown video_uid"}
                )
                continue

            t_start_raw = row.get(start_col) if start_col else 0
            t_end_raw = row.get(end_col) if end_col else None
            probability_raw = row.get(prob_col) if prob_col else None

            t_start_sec = pd.to_numeric(pd.Series([t_start_raw]), errors="coerce").iloc[0]
            if pd.isna(t_start_sec):
                errors.append(
                    {"row_number": row_num, "video_uid": video_uid, "error": "Invalid t_start_sec"}
                )
                continue

            t_end_sec = pd.to_numeric(pd.Series([t_end_raw]), errors="coerce").iloc[0]
            if pd.isna(t_end_sec):
                t_end_sec = None

            probability = pd.to_numeric(pd.Series([probability_raw]), errors="coerce").iloc[0]
            if pd.isna(probability):
                probability = None
            if probability is not None and (float(probability) < 0 or float(probability) > 1):
                errors.append(
                    {
                        "row_number": row_num,
                        "video_uid": video_uid,
                        "error": "probability must be in [0, 1]",
                    }
                )
                continue

            prepared = {
                "video_uid": video_uid,
                "video_id": video_id,
                "annotation_type": annotation_type,
                "species_id": None,
                "behavior_id": None,
                "value_num": None,
                "value_text": None,
                "probability": probability,
                "t_start_sec": float(t_start_sec),
                "t_end_sec": float(t_end_sec) if t_end_sec is not None else None,
            }

            raw_value = row.get(value_col)
            if annotation_type == "blank_non_blank":
                state_raw = str(raw_value or "").strip().lower().replace("-", "_")
                state_map = {
                    "blank": "blank",
                    "non_blank": "non_blank",
                    "nonblank": "non_blank",
                }
                normalized_state = state_map.get(state_raw)
                if not normalized_state:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": "blank_non_blank must be one of: blank, non_blank",
                        }
                    )
                    continue
                prepared["value_text"] = normalized_state

            elif annotation_type == "species":
                species_code = str(raw_value or "").strip()
                if not species_code:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": "Missing species code",
                        }
                    )
                    continue
                species_id = species_map.get(species_code)
                if not species_id:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": f"Unknown species_code `{species_code}`",
                        }
                    )
                    continue
                prepared["species_id"] = species_id
                prepared["value_text"] = species_code

            elif annotation_type == "behavior":
                behavior_code = str(raw_value or "").strip()
                if not behavior_code:
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": "Missing behavior code",
                        }
                    )
                    continue
                behavior_id = behavior_map.get(behavior_code)
                if behavior_id:
                    prepared["behavior_id"] = behavior_id
                prepared["value_text"] = behavior_code

            else:  # distance
                value_num = pd.to_numeric(pd.Series([raw_value]), errors="coerce").iloc[0]
                if pd.isna(value_num):
                    errors.append(
                        {
                            "row_number": row_num,
                            "video_uid": video_uid,
                            "error": "Invalid distance value",
                        }
                    )
                    continue
                prepared["value_num"] = float(value_num)

            prepared_rows.append(prepared)

        cleaned_df = pd.DataFrame(prepared_rows)
        errors_df = pd.DataFrame(errors)
        return cleaned_df, errors_df

    def import_model_csv(
        self,
        cleaned_df: pd.DataFrame,
        model_name: str,
        model_version: str,
        config_version: str | None = None,
    ) -> dict[str, Any]:
        if cleaned_df.empty:
            return {"inserted_rows": 0, "model_run_id": None}

        required = {"video_id", "annotation_type", "t_start_sec", "t_end_sec"}
        missing = required - set(cleaned_df.columns)
        if missing:
            raise ValueError(f"cleaned_df missing required columns: {sorted(missing)}")

        with self.engine.begin() as conn:
            model_run_id = conn.execute(
                text(
                    """
                    INSERT INTO model_runs (
                        model_name, model_version, config_version, started_at, finished_at, status
                    ) VALUES (
                        :model_name, :model_version, :config_version, now(), now(), 'completed'
                    )
                    RETURNING id
                    """
                ),
                {
                    "model_name": model_name.strip(),
                    "model_version": model_version.strip(),
                    "config_version": (config_version or "").strip() or None,
                },
            ).scalar_one()

            insert_rows: list[dict[str, Any]] = []
            for row in cleaned_df.to_dict(orient="records"):
                insert_rows.append(
                    {
                        "video_id": row["video_id"],
                        "model_run_id": str(model_run_id),
                        "annotation_type": row["annotation_type"],
                        "species_id": row.get("species_id"),
                        "behavior_id": row.get("behavior_id"),
                        "value_num": row.get("value_num"),
                        "value_text": row.get("value_text"),
                        "probability": row.get("probability"),
                        "t_start_sec": row["t_start_sec"],
                        "t_end_sec": row.get("t_end_sec"),
                    }
                )

            conn.execute(
                text(
                    """
                    INSERT INTO model_annotations (
                        video_id, model_run_id, annotation_type, species_id, behavior_id,
                        value_num, value_text, probability, t_start_sec, t_end_sec
                    ) VALUES (
                        :video_id, :model_run_id, :annotation_type, :species_id, :behavior_id,
                        :value_num, :value_text, :probability, :t_start_sec, :t_end_sec
                    )
                    """
                ),
                insert_rows,
            )

        return {"inserted_rows": len(insert_rows), "model_run_id": str(model_run_id)}

    def get_flow_data(self):
        # Lightweight fallback until dedicated V2 flow model is implemented.
        all_videos = self.get_all_videos()
        if all_videos.empty:
            return pd.DataFrame(columns=["source", "target", "value"])

        total = len(all_videos)
        needs_review = int((all_videos["needs_manual_review"] == True).sum())  # noqa: E712
        done = total - needs_review
        return pd.DataFrame(
            [
                {"source": "All Videos", "target": "Needs Review", "value": needs_review},
                {"source": "All Videos", "target": "Completed", "value": done},
            ]
        )
