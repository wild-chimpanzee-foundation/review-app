from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import pandas as pd
from sqlalchemy import Connection, text

from review_app.backend.db.models import VIDEO_CAMERA_LABEL_SQL, VideoLabel
from review_app.backend.provider.base import ProviderBase
from review_app.backend.provider.manual_review import (
    normalize_manual_selections,
    observation_fields_changed,
    review_blank_status,
)
from review_app.backend.provider.observation_identity import match_observation_id
from review_app.backend.utils import (
    DEFAULT_REVIEW_THRESHOLD,
    bind_id_list,
    needs_browser_transcode,
)

logger = logging.getLogger(__name__)

# How SQLAlchemy's SQLite DATETIME type renders datetimes. Raw-SQL writes must use the
# same format so timestamps stay comparable/sortable next to ORM-written rows.
_SQLITE_DT_FMT = "%Y-%m-%d %H:%M:%S.%f"


def _dt_str(value) -> str | None:
    return None if value is None else value.strftime(_SQLITE_DT_FMT)


class AnnotationMixin(ProviderBase):
    """Video detail queries and manual review writes. Requires self.engine, self.Session, self._utcnow_dt."""

    def get_video_detail(
        self,
        video_id: str,
        blank_threshold: float = DEFAULT_REVIEW_THRESHOLD,
        species_threshold: float = DEFAULT_REVIEW_THRESHOLD,
        obj_detection_threshold: float = DEFAULT_REVIEW_THRESHOLD,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            detail_df = pd.read_sql(
                text(
                    f"""
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
                        {VIDEO_CAMERA_LABEL_SQL} AS camera_id,
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
                    SELECT io.id, io.observation_uuid, s.scientific_name AS species,
                           GROUP_CONCAT(b.key) AS tags,
                           io.count, io.start_sec, io.end_sec, io.labeled_by, io.labeled_at
                    FROM individual_observations io
                    LEFT JOIN species s ON s.id = io.species_id
                    LEFT JOIN observation_tags ot ON ot.video_id = io.video_id AND ot.observation_id = io.id
                    LEFT JOIN behaviors b ON b.id = ot.behavior_id
                    WHERE io.video_id = :video_id
                    GROUP BY io.id, io.observation_uuid, s.scientific_name, io.count, io.start_sec, io.end_sec, io.labeled_by, io.labeled_at
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
                    "observation_uuid": manual.get("observation_uuid"),
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
                text(
                    "DELETE FROM model_annotations WHERE video_id IN (SELECT video_id FROM videos WHERE project_id = :pid)"
                ),
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

    def _load_manual_review_maps(
        self, conn: Connection, active_project_id: str | None
    ) -> tuple[set[str], dict[str, str], dict[str, str]]:
        """(valid species names, scientific_name → species id, behavior key → id)."""
        valid_species = set(self._get_valid_species(conn, active_project_id))
        species_id_map = dict(
            conn.execute(text("SELECT scientific_name, id FROM species")).tuples().all()
        )
        behavior_id_map = dict(conn.execute(text("SELECT key, id FROM behaviors")).tuples().all())
        return valid_species, species_id_map, behavior_id_map

    def apply_manual_reviews(
        self,
        reviews: list[dict],
        active_project_id: str | None = None,
        append: bool = False,
        review_later: dict[str, bool] | None = None,
        assignments: dict[str, str] | None = None,
        video_tags: dict[str, list[str]] | None = None,
    ) -> None:
        """Write one or more clip reviews in one transaction.

        Each review supplies video_id, selections, is_blank, and labeled_by.
        review_later, assignments and video_tags mirror set_review_later, the
        video-assignment restore, and set_video_tags for the same videos.

        Everything is written in one transaction with a handful of executemany
        statements: at 50k videos this replaces ~6 queries + 2-3 transactions per video,
        and a crash mid-import rolls back to the pre-import state instead of leaving a
        half-written database.
        """
        with self.engine.begin() as conn:
            self._apply_manual_reviews(
                conn,
                reviews,
                active_project_id,
                append,
                review_later or {},
                assignments or {},
                video_tags or {},
            )

    def _apply_manual_reviews(
        self,
        conn: Connection,
        reviews: list[dict],
        active_project_id: str | None,
        append: bool,
        review_later: dict[str, bool],
        assignments: dict[str, str],
        video_tags: dict[str, list[str]],
    ) -> None:
        """Write reviewed clips using the caller's transaction."""
        now = self._utcnow_dt().replace(microsecond=0)
        now_str = _dt_str(now)

        if any(review.get("selections") for review in reviews):
            selection_maps = self._load_manual_review_maps(conn, active_project_id)
        else:
            selection_maps = (set(), {}, {})

        review_ids = [str(r["video_id"]) for r in reviews]
        label_ids = list(dict.fromkeys(review_ids + list(review_later)))

        self._validate_video_scope(
            conn,
            list(dict.fromkeys([*label_ids, *assignments, *video_tags])),
            active_project_id,
        )
        # ── Preload current state for every affected video ──────────────────
        existing_obs: dict[str, dict[int, Any]] = {}
        existing_obs_tags: dict[str, dict[int, set[str]]] = {}
        if review_ids:
            params: dict[str, Any] = {}
            id_list = bind_id_list(params, "vids", review_ids)
            for vid, oid, obs_uuid, sp, cnt, start, end, lby in conn.execute(
                text(
                    "SELECT video_id, id, observation_uuid, species_id, count, start_sec,"
                    " end_sec, labeled_by"
                    f" FROM individual_observations WHERE video_id IN {id_list}"
                ),
                params,
            ):
                existing_obs.setdefault(vid, {})[oid] = (obs_uuid, sp, cnt, start, end, lby)
            params = {}
            id_list = bind_id_list(params, "vids", review_ids)
            for vid, oid, bid in conn.execute(
                text(
                    "SELECT video_id, observation_id, behavior_id"
                    f" FROM observation_tags WHERE video_id IN {id_list}"
                ),
                params,
            ):
                existing_obs_tags.setdefault(vid, {}).setdefault(oid, set()).add(bid)

        _no_label = {
            "is_blank": None,
            "labeled_by": None,
            "labeled_at": None,
            "review_later": False,
        }
        labels: dict[str, dict[str, Any]] = {}
        if label_ids:
            params = {}
            id_list = bind_id_list(params, "vids", label_ids)
            for vid, blank, lby, lat, rl in conn.execute(
                text(
                    "SELECT video_id, is_blank, labeled_by, labeled_at, review_later"
                    f" FROM video_labels WHERE video_id IN {id_list}"
                ),
                params,
            ):
                labels[vid] = {
                    "is_blank": blank,
                    "labeled_by": lby,
                    "labeled_at": lat,
                    "review_later": rl,
                }

        # ── Plan all row changes in memory ──────────────────────────────────
        obs_inserts: list[dict] = []
        obs_updates: list[dict] = []
        obs_deletes: list[dict] = []
        tag_deletes: list[dict] = []
        tag_inserts: list[dict] = []
        label_rows: dict[str, dict[str, Any]] = {}

        for review in reviews:
            video_id = str(review["video_id"])
            selections = review.get("selections") or []
            is_blank = review.get("is_blank")
            labeled_by = review.get("labeled_by")
            existing_map = existing_obs.get(video_id, {})
            existing_uuid_map = {
                values[0]: oid for oid, values in existing_map.items() if values[0]
            }
            obs_tags = existing_obs_tags.get(video_id, {})

            # Special case: clearing all annotations (only if not appending)
            if not append and not selections and is_blank is None:
                if video_id in labels:
                    label_rows[video_id] = dict(_no_label)
                    obs_deletes.extend({"vid": video_id, "oid": o} for o in existing_map)
                    tag_deletes.extend({"vid": video_id, "oid": o} for o in obs_tags)
                continue

            normalized = normalize_manual_selections(selections, *selection_maps)
            to_delete = set(existing_map) if not append else set()
            max_id = max(existing_map) if existing_map else 0
            used_ids = set(existing_map)
            newly_added = 0
            obs_tags_to_sync: dict[int, list[str]] = {}

            for row in normalized:
                obs_id = row.get("id")
                observation_uuid = row.get("observation_uuid")
                matched_id = match_observation_id(
                    observation_uuid, obs_id, existing_map, existing_uuid_map
                )
                if matched_id is not None:
                    obs_id = matched_id
                    _ex_uuid, ex_sp, ex_cnt, ex_start, ex_end, ex_lby = existing_map[obs_id]
                    tags_changed = set(row["tag_ids"]) != obs_tags.get(obs_id, set())
                    changed = tags_changed or observation_fields_changed(
                        (ex_sp, ex_cnt, ex_start, ex_end),
                        (row["species_id"], row["count"], row["start_sec"], row["end_sec"]),
                    )
                    if changed:
                        # The UI echoes back the stored name; if it hasn't changed,
                        # use the caller's name instead.
                        echoed_name = row.get("labeled_by")
                        obs_updates.append(
                            {
                                "vid": video_id,
                                "oid": obs_id,
                                "species_id": row["species_id"],
                                "count": row["count"],
                                "start_sec": row["start_sec"],
                                "end_sec": row["end_sec"],
                                "labeled_by": (
                                    labeled_by if echoed_name == ex_lby else echoed_name
                                )
                                or labeled_by,
                                "labeled_at": now_str,
                                "updated_at": now_str,
                            }
                        )
                    to_delete.discard(obs_id)
                    obs_tags_to_sync[obs_id] = row["tag_ids"]
                else:
                    # New record — honour caller-supplied id so re-imports stay stable
                    new_id = obs_id if (obs_id and obs_id not in used_ids) else max_id + 1
                    used_ids.add(new_id)
                    max_id = max(max_id, new_id)
                    new_uuid = observation_uuid or str(uuid.uuid4())
                    newly_added += 1
                    obs_inserts.append(
                        {
                            "vid": video_id,
                            "oid": new_id,
                            "observation_uuid": new_uuid,
                            "species_id": row["species_id"],
                            "count": row["count"],
                            "start_sec": row["start_sec"],
                            "end_sec": row["end_sec"],
                            "labeled_by": row["labeled_by"] or labeled_by,
                            "labeled_at": _dt_str(row.get("labeled_at")) or now_str,
                            "updated_at": now_str,
                        }
                    )
                    obs_tags_to_sync[new_id] = row["tag_ids"]

            obs_deletes.extend({"vid": video_id, "oid": o} for o in to_delete)

            remaining_observations = len(existing_map) - len(to_delete) + newly_added
            is_blank = review_blank_status(is_blank, remaining_observations)

            vals = dict(labels.get(video_id, _no_label))
            # Update labeled_at only if it wasn't labeled before or if blank status changed
            if is_blank is not None and (vals["is_blank"] != is_blank or not vals["labeled_at"]):
                vals["labeled_at"] = now_str
                vals["labeled_by"] = labeled_by
            vals["is_blank"] = is_blank
            label_rows[video_id] = vals

            stale_ids = to_delete | set(obs_tags_to_sync)
            tag_deletes.extend({"vid": video_id, "oid": o} for o in stale_ids)
            tag_inserts.extend(
                {"vid": video_id, "oid": o, "bid": bid}
                for o, tag_ids in obs_tags_to_sync.items()
                for bid in tag_ids
            )

        for vid, value in review_later.items():
            vals = label_rows.get(vid)
            if vals is None:
                vals = dict(labels.get(vid, _no_label))
                label_rows[vid] = vals
            vals["review_later"] = value

        # ── Execute: a handful of executemany statements ────────────────────
        if obs_deletes:
            conn.execute(
                text("DELETE FROM individual_observations WHERE video_id = :vid AND id = :oid"),
                obs_deletes,
            )
        if obs_updates:
            conn.execute(
                text(
                    "UPDATE individual_observations SET species_id = :species_id,"
                    " count = :count, start_sec = :start_sec, end_sec = :end_sec,"
                    " labeled_by = :labeled_by, labeled_at = :labeled_at,"
                    " updated_at = :updated_at WHERE video_id = :vid AND id = :oid"
                ),
                obs_updates,
            )
        if obs_inserts:
            conn.execute(
                text(
                    "INSERT INTO individual_observations (video_id, id, observation_uuid,"
                    " species_id, count, start_sec, end_sec, labeled_by, labeled_at,"
                    " updated_at) VALUES (:vid, :oid, :observation_uuid, :species_id, :count,"
                    " :start_sec, :end_sec, :labeled_by, :labeled_at, :updated_at)"
                ),
                obs_inserts,
            )
        if tag_deletes:
            conn.execute(
                text(
                    "DELETE FROM observation_tags WHERE video_id = :vid AND observation_id = :oid"
                ),
                tag_deletes,
            )
        if tag_inserts:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO observation_tags"
                    " (video_id, observation_id, behavior_id) VALUES (:vid, :oid, :bid)"
                ),
                tag_inserts,
            )
        if label_rows:
            conn.execute(
                text(
                    "INSERT INTO video_labels (video_id, is_blank, labeled_by,"
                    " labeled_at, review_later) VALUES (:vid, :is_blank, :labeled_by,"
                    " :labeled_at, :review_later) ON CONFLICT(video_id) DO UPDATE SET"
                    " is_blank = excluded.is_blank, labeled_by = excluded.labeled_by,"
                    " labeled_at = excluded.labeled_at, review_later = excluded.review_later"
                ),
                [{"vid": vid, **vals} for vid, vals in label_rows.items()],
            )

        if assignments:
            self.set_assignments_bulk(conn, assignments)
        if video_tags:
            self.set_video_tags_bulk(conn, video_tags, append=append)

        logger.log(
            logging.DEBUG if len(reviews) == 1 else logging.INFO,
            "Manual review write: %d videos (%d obs inserted, %d updated, %d deleted,"
            " %d assignments, %d tagged videos)",
            len(reviews),
            len(obs_inserts),
            len(obs_updates),
            len(obs_deletes),
            len(assignments),
            len(video_tags),
        )

    def update_manual_review(
        self,
        video_id: str,
        selections: list[dict] | None,
        is_blank: bool | None = None,
        labeled_by: str | None = None,
        active_project_id: str | None = None,
        append: bool = False,
    ) -> None:
        """Save one clip through the same review writer used by imports."""
        self.apply_manual_reviews(
            [
                {
                    "video_id": video_id,
                    "selections": selections,
                    "is_blank": is_blank,
                    "labeled_by": labeled_by,
                }
            ],
            active_project_id=active_project_id,
            append=append,
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
