from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.backend.db.models import IndividualObservation, ObservationTag, VideoLabel
from review_app.backend.errors import SpeciesError
from review_app.backend.provider.base import ProviderBase
from review_app.backend.utils import needs_browser_transcode

logger = logging.getLogger(__name__)


class AnnotationMixin(ProviderBase):
    """Video detail queries and manual review writes. Requires self.engine, self.Session, self._utcnow_dt."""

    def get_video_detail(
        self,
        video_id: str,
        blank_threshold: float = 0.75,
        species_threshold: float = 0.75,
        obj_detection_threshold: float = 0.75,
    ) -> dict[str, Any] | None:
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
                        SELECT video_id, value_text AS classification_consensus, consensus_count
                        FROM (
                            SELECT
                                video_id,
                                value_text,
                                avg_count AS consensus_count,
                                ROW_NUMBER() OVER (
                                    PARTITION BY video_id
                                    ORDER BY is_obj_high_conf DESC, avg_prob DESC, model_count DESC
                                ) AS rn
                            FROM (
                                SELECT video_id, value_text,
                                       AVG(COALESCE(probability, 0.0)) AS avg_prob,
                                       COUNT(*) AS model_count,
                                       -- object_detection rows above threshold take priority over species in consensus ranking
                                       MAX(CASE WHEN annotation_type = 'object_detection' AND COALESCE(probability, 0.0) >= :obj_thr THEN 1 ELSE 0 END) AS is_obj_high_conf,
                                       AVG(value_num) AS avg_count
                                FROM model_annotations
                                WHERE annotation_type IN ('species', 'object_detection')
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
                            WHERE annotation_type IN ('species', 'object_detection')
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
                        LEFT JOIN observation_tags ot ON ot.video_id = io.video_id AND ot.observation_id = io.id
                        LEFT JOIN behaviors b ON b.id = ot.behavior_id
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
                        v.latitude,
                        v.longitude,
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
                        msc.classification_consensus,
                        msc.consensus_count,
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
                        va.assigned_to,
                        COALESCE(msc_.max_species_confidence, 0.0) AS max_species_confidence,
                        rs.needs_manual_review
                    FROM videos v
                    LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    LEFT JOIN video_assignments va ON va.video_id = v.video_id
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
                params={
                    "video_id": video_id,
                    "min_prob": self._consensus_min_probability,
                    "obj_thr": obj_detection_threshold,
                },
            )

            manual_rows = pd.read_sql(
                text(
                    """
                    SELECT io.id, s.scientific_name AS species,
                           GROUP_CONCAT(b.key) AS tags,
                           io.count, io.start_sec, io.end_sec, io.labeled_by, io.labeled_at
                    FROM individual_observations io
                    LEFT JOIN species s ON s.id = io.species_id
                    LEFT JOIN observation_tags ot ON ot.video_id = io.video_id AND ot.observation_id = io.id
                    LEFT JOIN behaviors b ON b.id = ot.behavior_id
                    WHERE io.video_id = :video_id
                    GROUP BY io.id, s.scientific_name, io.count, io.start_sec, io.end_sec, io.labeled_by, io.labeled_at
                    ORDER BY COALESCE(io.start_sec, 0.0), s.scientific_name
                    """
                ),
                conn,
                params={"video_id": video_id},
            )

            tag_rows = conn.execute(
                text(
                    "SELECT t.key FROM video_tags vt "
                    "JOIN tags t ON t.id = vt.tag_id "
                    "WHERE vt.video_id = :video_id"
                ),
                {"video_id": video_id},
            ).fetchall()

            suggestion_rows = pd.read_sql(
                text(
                    """
                    SELECT value_text AS species, annotation_type,
                           AVG(COALESCE(probability, 0.0)) AS avg_prob,
                           AVG(value_num) AS avg_count
                    FROM model_annotations
                    WHERE video_id = :video_id
                      AND annotation_type IN ('species', 'object_detection')
                      AND COALESCE(probability, 0.0) >= :min_prob
                      AND value_text IS NOT NULL AND TRIM(value_text) != ''
                    GROUP BY value_text, annotation_type
                    ORDER BY avg_prob DESC
                    """
                ),
                conn,
                params={"video_id": video_id, "min_prob": self._consensus_min_probability},
            )

        if detail_df.empty:
            return None

        # Collect all candidates keyed by species, keeping highest probability
        suggestion_candidates: dict[str, dict] = {}

        def _add_candidate(species: str, prob: float, count_val) -> None:
            count = max(1, int(round(count_val))) if pd.notna(count_val) else 1
            if (
                species not in suggestion_candidates
                or prob > suggestion_candidates[species]["probability"]
            ):
                suggestion_candidates[species] = {
                    "species": species,
                    "probability": prob,
                    "count": count,
                }

        if not suggestion_rows.empty:
            all_sp_rows = suggestion_rows[suggestion_rows["annotation_type"] == "species"]
            sp_rows = all_sp_rows[all_sp_rows["avg_prob"] >= species_threshold]
            # Only recommend when every species model predicts the same species
            # (exactly 1 distinct species at any confidence) AND it passes threshold
            if len(all_sp_rows) == 1 and len(sp_rows) == 1:
                r = sp_rows.iloc[0]
                _add_candidate(r["species"], float(r["avg_prob"]), r.get("avg_count"))
            obj_rows = suggestion_rows[
                (suggestion_rows["annotation_type"] == "object_detection")
                & (suggestion_rows["avg_prob"] >= obj_detection_threshold)
            ]
            for _, r in obj_rows.iterrows():
                _add_candidate(r["species"], float(r["avg_prob"]), r.get("avg_count"))

        suggestions = list(suggestion_candidates.values())

        video_tag_keys = [r[0] for r in tag_rows]
        row_dict = detail_df.iloc[0].to_dict()
        row_dict["model_suggestions"] = suggestions
        return self._build_video_detail_row(
            row_dict,
            manual_rows,
            blank_threshold,
            species_threshold,
            video_tag_keys,
        )

    def _build_video_detail_row(
        self,
        row: dict[str, Any],
        manual_rows: pd.DataFrame,
        blank_threshold: float,
        species_threshold: float,
        video_tag_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        def _parse_tags(raw) -> list[str]:
            if not raw or (isinstance(raw, float) and pd.isna(raw)):
                return []
            return [t.strip() for t in str(raw).split(",") if t.strip()]

        selections = []
        for _, manual in manual_rows.iterrows():
            selections.append(
                {
                    "id": int(manual.get("id")) if pd.notna(manual.get("id")) else None,
                    "species": str(manual.get("species")),
                    "tags": _parse_tags(manual.get("tags")),
                    "count": int(manual.get("count")) if pd.notna(manual.get("count")) else None,
                    "start_sec": float(manual.get("start_sec")),
                    "end_sec": float(manual.get("end_sec"))
                    if pd.notna(manual.get("end_sec"))
                    else None,
                    "labeled_by": manual.get("labeled_by"),
                    "labeled_at": manual.get("labeled_at"),
                }
            )
        row["manual_selections"] = selections
        row["video_tags"] = video_tag_keys or []
        row["species_behavior_json"] = json.dumps(selections) if selections else None
        row["manual_review_prediction"] = (
            "\n".join(
                [
                    (
                        f"{s['species']} @ {s['start_sec']}s"
                        if s["end_sec"] is None
                        else f"{s['species']} {s['start_sec']}s-{s['end_sec']}s"
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
        row["is_blank"] = None if row.get("is_blank") is None else bool(row.get("is_blank"))
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

    def delete_model_annotations(self, project_id: str | None) -> int:
        if not project_id:
            raise ValueError("project_id is required to delete model annotations")
        with self.engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM model_annotations WHERE project_id = :pid"),
                {"pid": project_id},
            )
        return result.rowcount

    def get_model_annotations(self, video_id: str) -> pd.DataFrame:
        with self.engine.connect() as conn:
            model_df = pd.read_sql(
                text(
                    """
                    SELECT
                        model_name,
                        annotation_type,
                        value_text,
                        value_num,
                        probability,
                        t_start_sec,
                        t_end_sec,
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
                    "value_num",
                    "probability",
                    "t_start_sec",
                    "t_end_sec",
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
        append: bool = False,
    ) -> None:
        if selections is None:
            selections = []

        # Special case: Clearing all annotations (only if not appending)
        if not append and not selections and is_blank is None:
            logger.info("Clearing annotations for video %s", video_id)
            with self.Session() as session:
                if session.get(VideoLabel, video_id) is not None:
                    session.query(VideoLabel).filter(VideoLabel.video_id == video_id).update(
                        {
                            "is_blank": None,
                            "labeled_by": None,
                            "labeled_at": None,
                            "review_later": False,
                        }
                    )
                    session.query(ObservationTag).filter(
                        ObservationTag.video_id == video_id
                    ).delete(synchronize_session=False)
                    session.query(IndividualObservation).filter(
                        IndividualObservation.video_id == video_id
                    ).delete(synchronize_session=False)
                    session.commit()
            return

        now = self._utcnow_dt().replace(microsecond=0)
        valid_species = set(self.get_valid_species(active_project_id))

        with self.engine.connect() as conn:
            species_id_map = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
            }
            behavior_id_map = {
                r[0]: r[1] for r in conn.execute(text("SELECT key, id FROM behaviors")).fetchall()
            }

        def _parse_tag_ids(selection: dict) -> list[str]:
            raw = selection.get("tags")
            if raw is None:
                # Legacy import: fall back to single "behavior" key
                raw = selection.get("behavior", "")
            if isinstance(raw, list):
                keys = raw
            else:
                keys = [t.strip() for t in str(raw).split(",") if t.strip()]
            # Filter out legacy sentinel values
            keys = [k for k in keys if k not in ("unlabeled", "does_not_react", "")]
            return [behavior_id_map[k] for k in keys if k in behavior_id_map]

        normalized: list[dict[str, Any]] = []
        for selection in selections:
            species = str(selection.get("species") or "").strip() or "unknown"
            if species not in valid_species:
                raise SpeciesError(
                    user_message_key="species_error_unknown",
                    detail=f"Unknown species: {species!r}",
                    name=species,
                )

            # Support both 'start_sec' and 'timestamp' keys
            start_sec_raw = selection.get("start_sec")
            if start_sec_raw is None:
                start_sec_raw = selection.get("timestamp")
            start_sec = pd.to_numeric(start_sec_raw, errors="coerce")
            if pd.isna(start_sec):
                start_sec = 0.0

            end_sec_raw = selection.get("end_sec")
            end_sec = pd.to_numeric(end_sec_raw, errors="coerce")
            end_sec_val: float | None = None if pd.isna(end_sec) else float(end_sec)

            obs_id = selection.get("id")
            if obs_id is not None:
                try:
                    obs_id = int(obs_id)
                except (ValueError, TypeError):
                    obs_id = None

            count_raw = selection.get("count")
            count_val: int | None = int(count_raw) if count_raw is not None else None

            normalized.append(
                {
                    "id": obs_id,
                    "species_id": species_id_map.get(species),
                    "tag_ids": _parse_tag_ids(selection),
                    "count": count_val,
                    "start_sec": float(start_sec),
                    "end_sec": end_sec_val,
                    "labeled_by": selection.get("labeled_by"),
                    "labeled_at": selection.get("labeled_at"),
                }
            )

        if is_blank is None and normalized:
            is_blank = False

        # obs_id -> tag_ids for all observations we touch (for tag sync after commit)
        obs_tags_to_sync: dict[int, list[str]] = {}

        # Load current tags so we can detect changes without a separate query per observation
        with self.engine.connect() as conn:
            existing_tag_rows = conn.execute(
                text(
                    "SELECT observation_id, behavior_id FROM observation_tags WHERE video_id = :vid"
                ),
                {"vid": video_id},
            ).fetchall()
        existing_obs_tags: dict[int, set[str]] = {}
        for oid, bid in existing_tag_rows:
            existing_obs_tags.setdefault(oid, set()).add(bid)

        with self.Session() as session:
            # 1. Update IndividualObservations surgically
            existing = (
                session.query(IndividualObservation)
                .filter(IndividualObservation.video_id == video_id)
                .all()
            )
            existing_map = {obs.id: obs for obs in existing}
            to_delete = set(existing_map.keys()) if not append else set()

            max_id = max(existing_map.keys()) if existing_map else 0
            newly_added_count = 0

            for row in normalized:
                obs_id = row.get("id")
                if obs_id and obs_id in existing_map:
                    # Update existing record
                    obs = existing_map[obs_id]
                    end_sec_changed = (obs.end_sec is None) != (row["end_sec"] is None) or (
                        obs.end_sec is not None
                        and row["end_sec"] is not None
                        and abs(obs.end_sec - row["end_sec"]) > 0.001
                    )
                    tags_changed = set(row["tag_ids"]) != existing_obs_tags.get(obs_id, set())
                    changed = (
                        obs.species_id != row["species_id"]
                        or obs.count != row["count"]
                        or abs((obs.start_sec or 0.0) - row["start_sec"]) > 0.001
                        or end_sec_changed
                        or tags_changed
                    )
                    if changed:
                        obs.species_id = row["species_id"]
                        obs.count = row["count"]
                        obs.start_sec = row["start_sec"]
                        obs.end_sec = row["end_sec"]
                        # The UI echoes back the stored name; if it hasn't changed, use the caller's name instead.
                        echoed_name = row.get("labeled_by")
                        obs.labeled_by = (
                            labeled_by if echoed_name == obs.labeled_by else echoed_name
                        ) or labeled_by
                        obs.labeled_at = now
                        obs.updated_at = now
                    if obs_id in to_delete:
                        to_delete.remove(obs_id)
                    obs_tags_to_sync[obs_id] = row["tag_ids"]
                else:
                    # New record
                    max_id += 1
                    newly_added_count += 1
                    session.add(
                        IndividualObservation(
                            video_id=video_id,
                            id=max_id,
                            project_id=active_project_id,
                            species_id=row["species_id"],
                            count=row["count"],
                            start_sec=row["start_sec"],
                            end_sec=row["end_sec"],
                            labeled_by=row["labeled_by"] or labeled_by,
                            labeled_at=row.get("labeled_at") or now,
                            updated_at=now,
                        )
                    )
                    obs_tags_to_sync[max_id] = row["tag_ids"]

            if to_delete:
                session.query(IndividualObservation).filter(
                    IndividualObservation.video_id == video_id,
                    IndividualObservation.id.in_(to_delete),
                ).delete(synchronize_session=False)

            # Determine if any observations exist after this operation
            has_observations = (len(existing_map) - len(to_delete) + newly_added_count) > 0
            if has_observations:
                is_blank = False

            # 2. Update VideoLabel
            label = session.get(VideoLabel, video_id)
            if label is None:
                label = VideoLabel(video_id=video_id)
                session.add(label)

            # Update labeled_at only if it wasn't labeled before or if blank status changed
            if is_blank is not None and (label.is_blank != is_blank or not label.labeled_at):
                label.labeled_at = now
                label.labeled_by = labeled_by

            label.is_blank = is_blank

            session.commit()
            logger.info(
                "Annotation saved: video=%s is_blank=%s observations=%d labeled_by=%s",
                video_id,
                is_blank,
                len(normalized),
                labeled_by,
            )

        # 3. Sync observation_tags (outside the ORM session, in a separate transaction)
        with self.engine.begin() as conn:
            # Clear tags for deleted observations
            for obs_id in to_delete:
                conn.execute(
                    text(
                        "DELETE FROM observation_tags WHERE video_id = :vid AND observation_id = :oid"
                    ),
                    {"vid": video_id, "oid": obs_id},
                )
            # Sync tags for each observation we touched
            for obs_id, tag_ids in obs_tags_to_sync.items():
                conn.execute(
                    text(
                        "DELETE FROM observation_tags WHERE video_id = :vid AND observation_id = :oid"
                    ),
                    {"vid": video_id, "oid": obs_id},
                )
                for tag_id in tag_ids:
                    conn.execute(
                        text(
                            "INSERT OR IGNORE INTO observation_tags"
                            " (video_id, observation_id, behavior_id) VALUES (:vid, :oid, :bid)"
                        ),
                        {"vid": video_id, "oid": obs_id, "bid": tag_id},
                    )

    def set_review_later(self, video_id: str, value: bool = True) -> None:
        with self.Session() as session:
            label = session.get(VideoLabel, video_id)
            if label is None:
                label = VideoLabel(video_id=video_id)
                session.add(label)
            label.review_later = value
            session.commit()
        logger.debug("review_later=%s for video %s", value, video_id)
