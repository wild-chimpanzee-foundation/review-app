from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.backend.db.models import VIDEO_CAMERA_LABEL_SQL, reference_video_sql
from review_app.backend.provider.base import ProviderBase


class StatsMixin(ProviderBase):
    """Overview statistics queries. Requires self.engine."""

    def get_overview_stats(self, active_project_id: str | None = None) -> dict[str, Any]:
        p = {"pid": active_project_id} if active_project_id else {}
        pf = "WHERE project_id = :pid" if active_project_id else ""
        vf = "WHERE v.project_id = :pid" if active_project_id else ""
        model_scope = (
            "AND video_id IN (SELECT video_id FROM videos WHERE project_id = :pid)"
            if active_project_id
            else ""
        )

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
                    COUNT(DISTINCT {VIDEO_CAMERA_LABEL_SQL})                          AS cameras,
                    ROUND(SUM(COALESCE(duration_sec, 0)) / 3600.0, 2) AS total_hours
                FROM videos v {pf}
            """),
                    conn,
                    params=p,
                )
                .iloc[0]
                .to_dict()
            )

            stats["failed_videos"] = pd.read_sql(
                text(
                    f"SELECT v.*, {VIDEO_CAMERA_LABEL_SQL} AS camera_id FROM videos v WHERE is_valid = 0 {'AND project_id = :pid' if active_project_id else ''}"
                ),
                conn,
                params=p,
            )

            stats["missing_videos"] = pd.read_sql(
                text(
                    f"SELECT video_path, {VIDEO_CAMERA_LABEL_SQL} AS camera_id FROM videos v WHERE is_missing = 1 {'AND project_id = :pid' if active_project_id else ''} ORDER BY video_path"
                ),
                conn,
                params=p,
            )

            stats["labeling"] = (
                pd.read_sql(
                    text(f"""
                SELECT
                    COUNT(DISTINCT v.video_id)                                                    AS total_videos,
                    COUNT(DISTINCT CASE WHEN vl.is_blank IS NOT NULL OR {reference_video_sql()}
                        THEN v.video_id END)                                                     AS labeled,
                    COUNT(DISTINCT CASE WHEN vl.is_blank = 1 THEN v.video_id END)                AS blank,
                    COUNT(DISTINCT CASE WHEN vl.is_blank = 0 THEN v.video_id END)                AS non_blank,
                    COUNT(DISTINCT io.video_id)                                                   AS has_observations,
                    COUNT(DISTINCT CASE WHEN vl.review_later = 1 THEN v.video_id END)            AS review_later
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
            labeling = stats["labeling"]
            # Tagged references already carrying a Species label are in blank or non-blank.
            labeling["reference"] = labeling["labeled"] - labeling["blank"] - labeling["non_blank"]

            stats["species_counts"] = pd.read_sql(
                text(f"""
                SELECT
                    s.scientific_name     AS species,
                    COUNT(*)              AS observations,
                    COUNT(DISTINCT io.video_id) AS videos
                FROM individual_observations io
                JOIN species s ON s.id = io.species_id
                JOIN videos v ON v.video_id = io.video_id
                {vf}
                GROUP BY s.scientific_name
                ORDER BY observations DESC
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

            stats["behavior_counts"] = pd.read_sql(
                text(f"""
                SELECT
                    b.key                 AS behavior,
                    COUNT(*)              AS observations,
                    COUNT(DISTINCT ot.video_id) AS videos
                FROM observation_tags ot
                JOIN behaviors b ON b.id = ot.behavior_id
                JOIN individual_observations io ON io.video_id = ot.video_id AND io.id = ot.observation_id
                JOIN videos v ON v.video_id = io.video_id
                {vf}
                GROUP BY b.key
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
                WHERE 1=1 {model_scope}
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
                WHERE annotation_type IN ('species', 'object_detection')
                AND value_text IS NOT NULL
                {model_scope}
                GROUP BY model_name, value_text
                ORDER BY model_name, predictions DESC
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

            deployment_footage = conn.execute(
                text(f"""
                WITH footage AS (
                    SELECT
                        video_id, deployment_id, video_path, duration_sec, is_missing,
                        {reference_video_sql("videos")} AS is_reference
                    FROM videos
                    {pf}
                )
                SELECT
                    v.deployment_id,
                    COUNT(*)                                               AS total_videos,
                    SUM(v.is_reference)                                    AS reference_videos,
                    SUM(CASE WHEN vl.is_blank IS NOT NULL OR v.is_reference THEN 1 ELSE 0 END) AS labeled,
                    SUM(CASE WHEN vl.is_blank = 1 THEN 1 ELSE 0 END)         AS blank,
                    ROUND(SUM(COALESCE(v.duration_sec,0))/3600.0, 2)         AS hours,
                    COALESCE(
                        MIN(CASE WHEN NOT COALESCE(v.is_missing, 0) AND NOT v.is_reference
                            THEN v.video_path END),
                        MIN(CASE WHEN NOT COALESCE(v.is_missing, 0) THEN v.video_path END)
                    )                                                      AS sample_video_path
                FROM footage v
                LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                GROUP BY v.deployment_id
                ORDER BY total_videos DESC, v.deployment_id
            """),
                p,
            ).mappings()
            # Preserve SQL NULL as None, not pandas string-column NaN (a float).
            stats["deployment_footage"] = [dict(row) for row in deployment_footage]

        return stats

    def get_video_locations(self, active_project_id: str | None = None) -> list[dict]:
        """Return one marker per camera_id (avg lat/lon) for all geotagged videos."""
        p = {"pid": active_project_id} if active_project_id else {}
        pf = "WHERE latitude IS NOT NULL AND longitude IS NOT NULL" + (
            " AND project_id = :pid" if active_project_id else ""
        )
        with self.engine.connect() as conn:
            return pd.read_sql(
                text(f"""
                SELECT
                    ROUND(AVG(latitude), 6)  AS latitude,
                    ROUND(AVG(longitude), 6) AS longitude,
                    {VIDEO_CAMERA_LABEL_SQL} AS camera_id,
                    COUNT(*)                 AS video_count
                FROM videos v
                {pf}
                GROUP BY camera_id
                ORDER BY video_count DESC
                """),
                conn,
                params=p,
            ).to_dict(orient="records")
