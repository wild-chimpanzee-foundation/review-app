from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import text


class StatsMixin:
    """Overview statistics queries. Requires self.engine."""

    def get_overview_stats(self, active_project_id: str | None = None) -> dict[str, Any]:
        p = {"pid": active_project_id} if active_project_id else {}
        pf = "WHERE project_id = :pid" if active_project_id else ""
        vf = "WHERE v.project_id = :pid" if active_project_id else ""
        af = "AND project_id = :pid" if active_project_id else ""

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
                    f"SELECT * FROM videos WHERE is_valid = 0 {'AND project_id = :pid' if active_project_id else ''}"
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

            stats["species_counts"] = pd.read_sql(
                text(f"""
                SELECT
                    s.scientific_name     AS species,
                    COUNT(*)              AS observations,
                    COUNT(DISTINCT io.video_id) AS videos
                FROM individual_observations io
                JOIN species s ON s.id = io.species_id
                {pf.replace("project_id", "io.project_id") if pf else ""}
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
                    COUNT(DISTINCT io.video_id) AS videos
                FROM individual_observations io
                JOIN behaviors b ON b.id = io.behavior_id
                {pf.replace("project_id", "io.project_id") if pf else ""}
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
                {af}
                GROUP BY model_name, value_text
                ORDER BY model_name, predictions DESC
            """),
                conn,
                params=p,
            ).to_dict(orient="records")

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
                    WHERE annotation_type = 'species' {af}
                ),
                manual AS (
                    SELECT DISTINCT io.video_id, s.scientific_name AS manual_species
                    FROM individual_observations io
                    JOIN species s ON s.id = io.species_id
                    WHERE 1=1 {af.replace("project_id", "io.project_id") if af else ""}
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
