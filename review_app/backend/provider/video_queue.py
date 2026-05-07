from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select, text

from review_app.backend.db.models import IndividualObservation, ModelAnnotation, VideoLabel
from review_app.backend.provider.base import ProviderBase


class QueueMixin(ProviderBase):
    """Video queue building and filter options. Requires self.engine."""

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
        params: dict = {}
        vid_filter = ""
        io_exists = ""
        ma_exists = ""
        if active_project_id:
            params["pid"] = active_project_id
            vid_filter = "AND project_id = :pid"
            io_exists = "AND EXISTS (SELECT 1 FROM videos v WHERE v.video_id = io.video_id AND v.project_id = :pid)"
            ma_exists = "AND EXISTS (SELECT 1 FROM videos v WHERE v.video_id = ma.video_id AND v.project_id = :pid)"

        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(f"""
                    SELECT 'camera' AS source, camera_id AS val FROM videos
                    WHERE camera_id IS NOT NULL {vid_filter} GROUP BY camera_id
                    UNION ALL
                    SELECT 'species', s.scientific_name FROM individual_observations io
                    JOIN species s ON s.id = io.species_id
                    WHERE io.species_id IS NOT NULL {io_exists}
                    GROUP BY s.scientific_name
                    UNION ALL
                    SELECT 'behavior', b.key FROM individual_observations io
                    JOIN behaviors b ON b.id = io.behavior_id
                    WHERE io.behavior_id IS NOT NULL {io_exists}
                    GROUP BY b.key
                    UNION ALL
                    SELECT 'possible_species', ma.value_text FROM model_annotations ma
                    WHERE ma.annotation_type = 'species' AND ma.value_text IS NOT NULL AND TRIM(ma.value_text) <> '' {ma_exists}
                    GROUP BY ma.value_text
                    UNION ALL
                    SELECT 'model_behavior', ma.value_text FROM model_annotations ma
                    WHERE ma.annotation_type = 'behavior' AND ma.value_text IS NOT NULL AND TRIM(ma.value_text) <> '' {ma_exists}
                    GROUP BY ma.value_text
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

    def get_video_queue(self, filters: dict[str, Any], active_project_id: str | None) -> list[str]:
        # Safety invariant: only hardcoded SQL fragments and pre-validated keywords (ASC/DESC)
        # are interpolated into the query via f-strings. All user-supplied values must go
        # through bind params (the `params` dict). Do not interpolate filter values directly.
        search_raw = (filters.get("search_query") or "").strip().lower()
        selected_camera = filters.get("selected_camera", "All")
        selected_species = filters.get("selected_species", "All")
        selected_possible_species = filters.get("selected_possible_species", "All")
        selected_manual_blank = filters.get("selected_manual_blank", "All")
        selected_model_blank = filters.get("selected_model_blank", "All")
        selected_model_behavior = filters.get("selected_model_behavior", "All")
        selected_behavior = filters.get("selected_behavior", "All")
        selected_annotation_status = filters.get("selected_annotation_status", "All")
        selected_is_review_later = filters.get("selected_is_review_later", False)
        selected_sort = filters.get("selected_sort", "camera")
        selected_sort_direction = filters.get("selected_sort_direction", "desc")
        sort_dir = "DESC" if selected_sort_direction == "desc" else "ASC"
        sort_dir_inv = "ASC" if selected_sort_direction == "desc" else "DESC"
        assert sort_dir in ("ASC", "DESC") and sort_dir_inv in (
            "ASC",
            "DESC",
        )  # interpolated into SQL
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
                    JOIN species s ON s.id = io.species_id
                    WHERE io.video_id = v.video_id AND s.scientific_name = :species
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
                    WHERE io.video_id = v.video_id AND io.behavior_id IS NOT NULL
                )""")
        elif selected_behavior == "No Behavior":
            where.append("""
                NOT EXISTS (
                    SELECT 1 FROM individual_observations io
                    WHERE io.video_id = v.video_id AND io.behavior_id IS NOT NULL
                )""")
        elif selected_behavior not in ("All", "Has Behavior", "No Behavior"):
            params["behavior"] = selected_behavior
            where.append("""
                EXISTS (
                    SELECT 1 FROM individual_observations io
                    JOIN behaviors b ON b.id = io.behavior_id
                    WHERE io.video_id = v.video_id AND b.key = :behavior
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
        if selected_is_review_later:
            where.append(
                "EXISTS (SELECT 1 FROM video_labels vl2 WHERE vl2.video_id = v.video_id AND vl2.review_later = 1)"
            )

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
