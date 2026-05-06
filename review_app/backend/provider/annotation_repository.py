from __future__ import annotations

import json
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.backend.db.models import IndividualObservation, VideoLabel
from review_app.backend.errors import SpeciesError
from review_app.backend.utils import needs_browser_transcode


class AnnotationMixin:
    """Video detail queries and manual review writes. Requires self.engine, self.Session, self._utcnow_dt."""

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
                        SELECT video_id, value_text AS classification_consensus
                        FROM (
                            SELECT
                                video_id,
                                value_text,
                                ROW_NUMBER() OVER (
                                    PARTITION BY video_id
                                    ORDER BY avg_prob DESC, model_count DESC
                                ) AS rn
                            FROM (
                                SELECT video_id, value_text,
                                       AVG(COALESCE(probability, 0.0)) AS avg_prob,
                                       COUNT(*) AS model_count
                                FROM model_annotations
                                WHERE annotation_type = 'species'
                                  AND COALESCE(probability, 0.0) >= :min_prob
                                  AND value_text IS NOT NULL
                                  AND TRIM(value_text) <> ''
                                GROUP BY video_id, value_text
                            ) species_avgs
                        ) ranked
                        WHERE rn = 1
                    ),
                    max_species_conf AS (
                        SELECT video_id, MAX(prob_sum) AS max_species_confidence
                        FROM (
                            SELECT video_id, AVG(COALESCE(probability, 0.0)) AS prob_sum
                            FROM model_annotations
                            WHERE annotation_type = 'species'
                            GROUP BY video_id, value_text
                        ) _sp
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
                            GROUP_CONCAT(DISTINCT b.key) AS behavior_prediction,
                            COUNT(*) AS individual_count
                        FROM individual_observations io
                        LEFT JOIN behaviors b ON b.id = io.behavior_id
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
                        vl.review_later,
                        vl.labeled_by AS blank_labeled_by,
                        vl.labeled_at AS blank_labeled_at,
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
                    SELECT s.scientific_name AS species, b.key AS behavior,
                           io.start_sec, io.end_sec, io.labeled_by, io.labeled_at
                    FROM individual_observations io
                    LEFT JOIN species s ON s.id = io.species_id
                    LEFT JOIN behaviors b ON b.id = io.behavior_id
                    WHERE io.video_id = :video_id
                    ORDER BY COALESCE(io.start_sec, 0.0), s.scientific_name
                    """
                ),
                conn,
                params={"video_id": video_id},
            )

        if detail_df.empty:
            return None

        return self._build_video_detail_row(
            detail_df.iloc[0].to_dict(), manual_rows, blank_threshold, species_threshold
        )

    def _build_video_detail_row(
        self,
        row: dict,
        manual_rows: pd.DataFrame,
        blank_threshold: float,
        species_threshold: float,
    ) -> dict:
        selections = []
        for _, manual in manual_rows.iterrows():
            selections.append(
                {
                    "species": str(manual.get("species")),
                    "behavior": str(manual.get("behavior")),
                    "start_sec": float(manual.get("start_sec")),
                    "end_sec": float(manual.get("end_sec")),
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
        if selections == [] and is_blank is None:
            with self.Session() as session:
                if session.get(VideoLabel, video_id) is not None:
                    session.query(VideoLabel).filter(VideoLabel.video_id == video_id).update(
                        {"is_blank": None, "labeled_by": None, "labeled_at": None}
                    )
                    session.query(IndividualObservation).filter(
                        IndividualObservation.video_id == video_id
                    ).delete(synchronize_session=False)
                    session.commit()
            return

        now = self._utcnow_dt()
        valid_species = set(self.get_valid_species(active_project_id))

        with self.engine.connect() as conn:
            species_id_map = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
            }
            behavior_id_map = {
                r[0]: r[1] for r in conn.execute(text("SELECT key, id FROM behaviors")).fetchall()
            }

        normalized: list[dict[str, Any]] = []
        for selection in selections:
            species = str(selection.get("species") or "").strip() or "unknown"
            if species not in valid_species:
                raise SpeciesError(
                    user_message_key="species_error_unknown",
                    detail=f"Unknown species: {species!r}",
                )
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
                    "species_id": species_id_map.get(species),
                    "behavior_id": behavior_id_map.get(behavior),
                    "start_sec": float(start_sec),
                    "end_sec": end_sec_val,
                    "labeled_by": selection.get("labeled_by"),
                }
            )

        if is_blank is None:
            is_blank = False if normalized else None

        with self.Session() as session:
            label = session.get(VideoLabel, video_id)
            if label is None:
                label = VideoLabel(video_id=video_id)
                session.add(label)
            label.is_blank = is_blank
            label.review_later = False
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
                            species_id=row["species_id"],
                            behavior_id=row["behavior_id"],
                            start_sec=row["start_sec"],
                            end_sec=row["end_sec"],
                            labeled_by=row.get("labeled_by"),
                            labeled_at=now,
                            updated_at=now,
                        )
                    )
            session.commit()

    def set_review_later(self, video_id: str, value: bool = True) -> None:
        with self.Session() as session:
            label = session.get(VideoLabel, video_id)
            if label is None:
                label = VideoLabel(video_id=video_id)
                session.add(label)
            label.review_later = value
            session.commit()
