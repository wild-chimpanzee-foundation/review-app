"""Model-prediction CSV: normalization, validation, import, and export."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.app.config import CSV_TEMPLATES
from review_app.backend.errors import DataImportError
from review_app.backend.path_matching import resolve_video_path
from review_app.backend.provider.import_service._shared import ImportSharedMixin

logger = logging.getLogger(__name__)


class ModelCsvMixin(ImportSharedMixin):
    """Import of model annotation CSVs and export of stored model annotations."""

    def get_csv_templates(self) -> dict[str, str]:
        with self.engine.connect() as conn:
            videos_df = pd.read_sql(text("SELECT video_path FROM videos LIMIT 10"), conn)

        if not videos_df.empty:
            sample_paths = videos_df["video_path"].tolist()
            rows = [f"{p},species,species_model_a,deer,,0.92,0,12.0" for p in sample_paths[:3]]
            rows.append(
                f"{sample_paths[0] if sample_paths else 'path/to/video.mp4'},behavior,behavior_model_a,reacts_to_camera,,0.83,0,12.0"
            )
            rows.append(
                f"{sample_paths[1] if len(sample_paths) > 1 else 'path/to/video2.mp4'},blank_non_blank,blank_model,blank,,0.98,0,"
            )
            template = "video_path,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec\n"
            template += "\n".join(rows)
        else:
            template = CSV_TEMPLATES["model_annotations"]

        return {"model_annotations": template}

    def normalize_model_csv_with_mapping(
        self,
        df: pd.DataFrame,
        path_col: str,
        ann_mappings: list[dict[str, str]],
        active_project_id: str | None,
        filename_match: bool = False,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Normalize an arbitrary CSV to the long format expected by validate_model_csv, using
        an explicit column mapping provided by the user.

        Matching: tries parent_dir/filename (suffix) first, falls back to unambiguous stem.
        ann_mappings: list of {model_name, annotation_type, value_col, prob_col}
        """
        lookup = self._build_video_path_lookup(active_project_id)

        rows: list[dict[str, Any]] = []
        matched_suffix = 0
        matched_cam_stem = 0
        matched_filename = 0
        unmatched_paths: list[str] = []

        for _, src_row in df.iterrows():
            raw_path = str(src_row.get(path_col, "")).strip()

            video_id, tier = resolve_video_path(
                raw_path, lookup, use_filename_match=filename_match
            )
            if video_id:
                if tier == "cam_stem":
                    matched_cam_stem += 1
                elif tier == "filename":
                    matched_filename += 1
                else:
                    matched_suffix += 1

            if video_id is None:
                unmatched_paths.append(raw_path)
                continue

            for m in ann_mappings:
                model_name = m.get("model_name", "").strip()
                ann_type = m.get("annotation_type", "species")
                value_col = m.get("value_col", "").strip()
                prob_col = m.get("prob_col", "").strip()
                count_col = m.get("count_col", "").strip()

                if not model_name:
                    continue

                value_text: str | None = None
                if value_col and value_col in src_row.index:
                    v = src_row[value_col]
                    if not pd.isna(v):
                        value_text = str(v).strip() or None

                if ann_type == "blank_non_blank" and value_text is None:
                    value_text = "blank"

                probability: float | None = None
                if prob_col and prob_col in src_row.index:
                    pv = pd.to_numeric(src_row[prob_col], errors="coerce")
                    probability = None if pd.isna(pv) else float(pv)

                value_num: float | None = None
                if count_col and count_col in src_row.index:
                    cv = pd.to_numeric(src_row[count_col], errors="coerce")
                    value_num = None if pd.isna(cv) else float(cv)

                if value_text is not None or probability is not None or value_num is not None:
                    rows.append(
                        {
                            "video_path": video_id,
                            "annotation_type": ann_type,
                            "model_name": model_name,
                            "value_text": value_text,
                            "probability": probability,
                            "value_num": value_num,
                        }
                    )

        empty = pd.DataFrame(
            columns=[
                "video_path",
                "annotation_type",
                "model_name",
                "value_text",
                "probability",
                "value_num",
            ]
        )
        stats: dict[str, Any] = {
            "total_rows": len(df),
            "matched": matched_suffix + matched_cam_stem + matched_filename,
            "matched_by_suffix": matched_suffix,
            "matched_by_cam_stem": matched_cam_stem,
            "matched_by_filename": matched_filename,
            "unmatched": len(unmatched_paths),
            "unmatched_sample": unmatched_paths[:10],
        }
        return pd.DataFrame(rows) if rows else empty, stats

    def validate_model_csv(
        self,
        df: pd.DataFrame,
        mappings: dict[str, str] | None = None,
        active_project_id: str | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], list[dict]]:
        src = df.copy()
        src.columns = [str(c).strip() for c in src.columns]

        # Normalize path column aliases → video_path
        _path_aliases = {"path", "filepath", "review_filename", "original_filepath"}
        if "video_path" not in src.columns:
            for alias in _path_aliases:
                if alias in src.columns:
                    src = src.rename(columns={alias: "video_path"})
                    break

        mappings = mappings or {}

        required = {"video_path", "annotation_type", "model_name"}
        missing = required - set(src.columns)
        if missing:
            raise DataImportError(
                user_message_key="csv_error_missing_columns",
                detail=f"CSV must include columns: {', '.join(sorted(missing))}",
            )

        video_map = self._known_video_map(active_project_id)
        known_videos = set(video_map.keys())
        path_to_id = {v.lower(): k for k, v in video_map.items()}

        lookup = self._build_video_path_lookup(active_project_id)

        def _resolve_path(raw: str) -> str:
            vid, _ = resolve_video_path(raw, lookup, known_videos, path_to_id)
            return vid or raw

        src["video_path"] = src["video_path"].astype(str).str.strip().map(_resolve_path)

        species_mask = (
            src["annotation_type"].str.strip().str.lower().isin({"species", "object_detection"})
        )
        unique_species = (
            src.loc[species_mask, "value_text"].dropna().astype(str).str.strip().unique()
        )
        unique_species = {str(s) for s in unique_species if str(s).strip()}

        variant_map = self._build_species_variant_map()
        species_fuzzy_cache: dict[str, tuple[bool, str | None]] = {
            s: self._validate_species_fuzzy(s, variant_map) for s in unique_species
        }

        prepared_rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        species_mappings: list[dict[str, str]] = []
        unmapped_species: set[str] = set()

        for idx, row in src.iterrows():
            row_num = int(idx) + 1
            video_id = str(row.get("video_path", "")).strip()
            model_name = str(row.get("model_name", "")).strip()
            raw_type = str(row.get("annotation_type", "")).strip()

            if not video_id:
                errors.append({"row_number": row_num, "error": "error_missing_path"})
                continue
            video_path = video_map.get(video_id, video_id)
            if video_id not in known_videos:
                errors.append(
                    {
                        "row_number": row_num,
                        "video_path": video_path,
                        "error": "error_unknown_path",
                    }
                )
                continue
            if not model_name:
                errors.append(
                    {
                        "row_number": row_num,
                        "video_path": video_path,
                        "error": "error_missing_model_name",
                    }
                )
                continue

            try:
                annotation_type = self._normalize_annotation_type(raw_type)
            except DataImportError as exc:
                errors.append(
                    {
                        "row_number": row_num,
                        "video_path": video_path,
                        "error": exc.user_message_key,
                    }
                )
                continue

            probability = pd.to_numeric(row.get("probability"), errors="coerce")
            probability = None if pd.isna(probability) else float(probability)
            if probability is not None and not (0.0 <= probability <= 1.0):
                errors.append(
                    {
                        "row_number": row_num,
                        "video_path": video_path,
                        "error": "error_invalid_probability",
                    }
                )
                continue

            t_start = pd.to_numeric(row.get("t_start_sec"), errors="coerce")
            t_start = None if pd.isna(t_start) else float(t_start)
            t_end = pd.to_numeric(row.get("t_end_sec"), errors="coerce")
            t_end = None if pd.isna(t_end) else float(t_end)

            value_text = row.get("value_text")
            value_text = None if pd.isna(value_text) else (str(value_text).strip() or None)
            value_num = pd.to_numeric(row.get("value_num"), errors="coerce")
            value_num = None if pd.isna(value_num) else float(value_num)

            if annotation_type in {"species", "object_detection"} and value_text:
                original_value = value_text
                mapped_to = mappings.get(original_value)
                if mapped_to:
                    value_text = mapped_to
                else:
                    is_valid, best_match = species_fuzzy_cache.get(original_value, (False, None))
                    if not is_valid:
                        unmapped_species.add(original_value)
                        errors.append(
                            {
                                "row_number": row_num,
                                "video_path": video_path,
                                "error": "error_species_needs_mapping",
                            }
                        )
                        continue
                    if best_match != original_value:
                        species_mappings.append(
                            {"original": original_value, "mapped_to": best_match}
                        )
                    value_text = best_match

            prepared_rows.append(
                {
                    "video_id": video_id,
                    "video_path": video_path,
                    "annotation_type": annotation_type,
                    "model_name": model_name,
                    "value_text": value_text,
                    "value_num": value_num,
                    "probability": probability,
                    "t_start_sec": t_start,
                    "t_end_sec": t_end,
                }
            )

        unmapped_species_list = [{"original": s} for s in sorted(unmapped_species)]
        return (
            pd.DataFrame(prepared_rows),
            pd.DataFrame(errors),
            species_mappings,
            unmapped_species_list,
        )

    def import_model_csv(
        self, cleaned_df: pd.DataFrame, active_project_id: str | None
    ) -> dict[str, Any]:
        if cleaned_df.empty:
            return {"imported": 0}

        self._safety_backup()
        logger.info(
            "Importing %d model annotation rows (project=%s)", len(cleaned_df), active_project_id
        )

        now = self._utcnow_dt()
        rows = [
            {
                "id": str(uuid.uuid4()),
                "project_id": active_project_id,
                "video_id": row["video_id"],
                "annotation_type": row["annotation_type"],
                "model_name": row["model_name"],
                "value_text": row.get("value_text"),
                "value_num": row.get("value_num"),
                "probability": row.get("probability"),
                "t_start_sec": row.get("t_start_sec"),
                "t_end_sec": row.get("t_end_sec"),
                "updated_at": now,
            }
            for _, row in cleaned_df.iterrows()
        ]

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO model_annotations
                        (id, project_id, video_id, annotation_type, model_name,
                         value_text, value_num, probability, t_start_sec, t_end_sec, updated_at)
                    VALUES
                        (:id, :project_id, :video_id, :annotation_type, :model_name,
                         :value_text, :value_num, :probability, :t_start_sec, :t_end_sec, :updated_at)
                    -- value_text is part of the conflict key, so it cannot be updated here.
                    -- To correct a misspelled value_text, delete the row first then re-import.
                    ON CONFLICT(video_id, model_name, annotation_type, COALESCE(value_text, '')) DO UPDATE SET
                        value_num   = excluded.value_num,
                        probability = excluded.probability,
                        t_start_sec = excluded.t_start_sec,
                        t_end_sec   = excluded.t_end_sec,
                        updated_at  = excluded.updated_at
                """),
                rows,
            )

        logger.info("Model CSV import complete: %d rows upserted", len(rows))
        return {"imported": len(rows)}

    def export_model_annotations_csv(
        self,
        active_project_id: str | None,
        camera_ids: list[str] | None = None,
        video_ids: list[str] | None = None,
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"pid": active_project_id} if active_project_id else {}
        v_pid = "AND v.project_id = :pid" if active_project_id else ""
        cam_filter = self._camera_video_sql_filter(params, camera_ids, video_ids)

        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(f"""
                    SELECT
                        v.video_path,
                        ma.model_name,
                        ma.annotation_type,
                        ma.value_text,
                        ma.value_num,
                        ma.probability,
                        ma.t_start_sec,
                        ma.t_end_sec
                    FROM model_annotations ma
                    JOIN videos v ON v.video_id = ma.video_id
                    WHERE 1=1 {v_pid} {cam_filter}
                    ORDER BY v.video_path, ma.model_name, ma.annotation_type
                """),
                conn,
                params=params,
            )
        return df
