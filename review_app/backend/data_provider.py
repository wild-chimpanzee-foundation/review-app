import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, text


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
