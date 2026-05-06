from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.app.config import CSV_TEMPLATES
from review_app.backend.errors import DataImportError

logger = logging.getLogger(__name__)


class ImportMixin:
    """CSV import, export, and validation. Requires self.engine, self.Session, self._utcnow_dt."""

    # ── Shared path/video lookups ─────────────────────────────────────────────

    def _known_video_ids(self, active_project_id: str | None) -> set[str]:
        with self.engine.connect() as conn:
            q = text(
                "SELECT video_id FROM videos"
                + (" WHERE project_id = :pid" if active_project_id else "")
            )
            p = {"pid": active_project_id} if active_project_id else {}
            return set(conn.execute(q, p).scalars())

    def _known_video_map(self, active_project_id: str | None) -> dict[str, str]:
        """Returns {video_id: video_path} for all videos in the project."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT video_id, video_path FROM videos"
                    + (" WHERE project_id = :pid" if active_project_id else "")
                ),
                {"pid": active_project_id} if active_project_id else {},
            ).fetchall()
        return {str(r[0]): str(r[1]) for r in rows}

    # ── CSV templates ─────────────────────────────────────────────────────────

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
            template = "path,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec\n"
            template += "\n".join(rows)
        else:
            template = CSV_TEMPLATES["model_annotations"]

        return {"model_annotations": template}

    # ── Path matching helpers ─────────────────────────────────────────────────

    def _build_video_path_lookup(
        self, active_project_id: str | None
    ) -> tuple[dict[str, str], dict[str, str]]:
        """
        Build two lookups for matching CSV file paths to DB video_ids.
        by_suffix keys include both parent/name (with ext) and parent/stem (without ext)
        so that CSV paths match regardless of whether the extension is present or differs.
        - by_suffix: {(parent_dir/name).lower() -> video_id, (parent_dir/stem).lower() -> video_id}
        - by_stem:   {stem.lower() -> video_id}  — fallback, only for unambiguous stems
        """
        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(
                    "SELECT video_id, video_path FROM videos"
                    + (" WHERE project_id = :pid" if active_project_id else "")
                ),
                conn,
                params={"pid": active_project_id} if active_project_id else {},
            )
        by_suffix: dict[str, str] = {}
        stem_to_id: dict[str, str] = {}
        stem_count: dict[str, int] = {}
        for _, row in df.iterrows():
            p = Path(str(row["video_path"]))
            vid = str(row["video_id"])
            by_suffix[f"{p.parent.name}/{p.name}".lower()] = vid
            by_suffix[f"{p.parent.name}/{p.stem}".lower()] = vid
            stem = p.stem.lower()
            stem_to_id[stem] = vid
            stem_count[stem] = stem_count.get(stem, 0) + 1
        by_stem = {s: vid for s, vid in stem_to_id.items() if stem_count[s] == 1}
        return by_suffix, by_stem

    # ── Annotation type validation ────────────────────────────────────────────

    @staticmethod
    def _normalize_annotation_type(annotation_type: str) -> str:
        supported = {"blank_non_blank", "species", "behavior"}
        normalized = (annotation_type or "").strip().lower()
        if normalized not in supported:
            raise DataImportError(
                user_message_key="error_invalid_annotation_type",
                detail=f"Invalid annotation type: {normalized!r}",
            )
        return normalized

    # ── Model CSV import ──────────────────────────────────────────────────────

    def normalize_model_csv_with_mapping(
        self,
        df: pd.DataFrame,
        path_col: str,
        match_strategy: str,
        ann_mappings: list[dict[str, str]],
        active_project_id: str | None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Normalize an arbitrary CSV to the long format expected by validate_model_csv, using
        an explicit column mapping provided by the user.

        match_strategy: "suffix" (parent_dir/stem) or "stem" (filename only, unambiguous)
        ann_mappings: list of {model_name, annotation_type, value_col, prob_col}
        """
        by_suffix, by_stem = self._build_video_path_lookup(active_project_id)

        rows: list[dict[str, Any]] = []
        matched_suffix = 0
        matched_stem = 0
        unmatched_paths: list[str] = []

        for _, src_row in df.iterrows():
            raw_path = str(src_row.get(path_col, "")).strip()
            p = Path(raw_path)

            video_id: str | None = None
            if match_strategy == "suffix":
                video_id = by_suffix.get(f"{p.parent.name}/{p.name}".lower()) or by_suffix.get(
                    f"{p.parent.name}/{p.stem}".lower()
                )
                if video_id:
                    matched_suffix += 1
            elif match_strategy == "stem":
                video_id = by_stem.get(p.stem.lower())
                if video_id:
                    matched_stem += 1

            if video_id is None:
                unmatched_paths.append(raw_path)
                continue

            for m in ann_mappings:
                model_name = m.get("model_name", "").strip()
                ann_type = m.get("annotation_type", "species")
                value_col = m.get("value_col", "").strip()
                prob_col = m.get("prob_col", "").strip()

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

                if value_text is not None or probability is not None:
                    rows.append(
                        {
                            "path": video_id,
                            "annotation_type": ann_type,
                            "model_name": model_name,
                            "value_text": value_text,
                            "probability": probability,
                        }
                    )

        empty = pd.DataFrame(
            columns=["path", "annotation_type", "model_name", "value_text", "probability"]
        )
        stats: dict[str, Any] = {
            "total_rows": len(df),
            "matched": matched_suffix + matched_stem,
            "matched_by_suffix": matched_suffix,
            "matched_by_stem": matched_stem,
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

        mappings = mappings or {}

        required = {"path", "annotation_type", "model_name"}
        missing = required - set(src.columns)
        if missing:
            raise DataImportError(
                user_message_key="csv_error_missing_columns",
                detail=f"CSV must include columns: {', '.join(sorted(missing))}",
            )

        video_map = self._known_video_map(active_project_id)
        known_videos = set(video_map.keys())

        by_suffix, by_stem = self._build_video_path_lookup(active_project_id)

        def _resolve_path(raw: str) -> str:
            if raw in known_videos:
                return raw
            p = Path(raw)
            return (
                by_suffix.get(f"{p.parent.name}/{p.name}".lower())
                or by_suffix.get(f"{p.parent.name}/{p.stem}".lower())
                or by_stem.get(p.stem.lower())
                or raw
            )

        src["path"] = src["path"].astype(str).str.strip().map(_resolve_path)

        species_mask = src["annotation_type"].str.strip().str.lower() == "species"
        unique_species = src.loc[species_mask, "value_text"].dropna().str.strip().unique()
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
            video_id = str(row.get("path", "")).strip()
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

            if annotation_type == "species" and value_text:
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
            return {"inserted_rows": 0}

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
                    ON CONFLICT(video_id, model_name, annotation_type) DO UPDATE SET
                        value_text  = excluded.value_text,
                        value_num   = excluded.value_num,
                        probability = excluded.probability,
                        t_start_sec = excluded.t_start_sec,
                        t_end_sec   = excluded.t_end_sec,
                        updated_at  = excluded.updated_at
                """),
                rows,
            )

        logger.info("Model CSV import complete: %d rows upserted", len(rows))
        return {"inserted_rows": len(rows)}

    # ── Annotation export / import ────────────────────────────────────────────

    def export_annotations_csv(
        self, active_project_id: str | None, lang: str = "en"
    ) -> pd.DataFrame:
        params = {"pid": active_project_id} if active_project_id else {}
        vid_pid = "AND v.project_id = :pid" if active_project_id else ""
        ma_pid = "AND project_id = :pid" if active_project_id else ""
        with self.engine.connect() as conn:
            base_df = pd.read_sql(
                text(f"""
                    SELECT
                        v.video_id,
                        p.name                    AS project_name,
                        v.video_path,
                        v.camera_id,
                        v.created_at              AS recorded_at,
                        v.duration_sec,
                        CAST(vl.is_blank AS INTEGER)      AS is_blank,
                        CAST(vl.review_later AS INTEGER)  AS review_later,
                        CASE WHEN vl.is_blank IS NOT NULL THEN 1 ELSE 0 END AS is_annotated,
                        COALESCE(io.labeled_by, vl.labeled_by) AS annotator,
                        COALESCE(io.labeled_at, vl.labeled_at) AS labeled_at,
                        io.id                     AS observation_id,
                        s.scientific_name         AS species,
                        b.key                     AS behavior,
                        io.start_sec,
                        io.end_sec
                    FROM videos v
                    LEFT JOIN projects p ON p.id = v.project_id
                    LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    LEFT JOIN individual_observations io ON io.video_id = v.video_id
                    LEFT JOIN species s ON s.id = io.species_id
                    LEFT JOIN behaviors b ON b.id = io.behavior_id
                    WHERE 1=1 {vid_pid}
                    ORDER BY v.camera_id, v.video_path, io.start_sec
                """),
                conn,
                params=params,
            )

            model_df = pd.read_sql(
                text(f"""
                    SELECT video_id, model_name, annotation_type, value_text, probability
                    FROM model_annotations
                    WHERE 1=1 {ma_pid}
                """),
                conn,
                params=params,
            )

        if not model_df.empty:
            model_df["col"] = model_df["model_name"] + "__" + model_df["annotation_type"]
            value_wide = model_df.pivot_table(
                index="video_id", columns="col", values="value_text", aggfunc="first"
            )
            prob_wide = model_df.pivot_table(
                index="video_id", columns="col", values="probability", aggfunc="first"
            )
            prob_wide.columns = [f"{c}__prob" for c in prob_wide.columns]
            model_wide = value_wide.join(prob_wide, how="outer").reset_index()

            # needs_review: flag=1 when models disagree on species or blank signal is weak
            all_video_ids = model_df["video_id"].unique()
            species_agg = (
                model_df[model_df["annotation_type"] == "species"]
                .groupby("video_id")
                .agg(distinct_top1=("value_text", "nunique"), model_count=("value_text", "count"))
                .reset_index()
            )
            blank_probs = (
                model_df[
                    (model_df["annotation_type"] == "blank_non_blank")
                    & (model_df["value_text"].str.lower().str.strip() == "blank")
                ]
                .groupby("video_id")["probability"]
                .max()
                .rename("blank_prob")
                .reset_index()
            )
            nr = pd.DataFrame({"video_id": all_video_ids})
            nr = nr.merge(species_agg, on="video_id", how="left")
            nr = nr.merge(blank_probs, on="video_id", how="left")
            nr[["blank_prob", "distinct_top1", "model_count"]] = nr[
                ["blank_prob", "distinct_top1", "model_count"]
            ].fillna(0.0)
            thr = 0.75
            nr["needs_review"] = (
                ~(
                    (nr["blank_prob"] >= thr)
                    | ((nr["distinct_top1"] == 1) & (nr["model_count"] >= 1))
                )
            ).astype(int)
            model_wide = model_wide.merge(
                nr[["video_id", "needs_review"]], on="video_id", how="left"
            )

            base_df = base_df.merge(model_wide, on="video_id", how="left")

        if "behavior" in base_df.columns:
            behavior_map = self.get_behavior_display_map(lang=lang)
            base_df["behavior"] = base_df["behavior"].map(
                lambda b: behavior_map.get(b, b) if pd.notna(b) else b
            )

        base_df = base_df.drop(columns=["video_id"], errors="ignore")
        return base_df

    def import_annotations_csv(
        self, df: pd.DataFrame, active_project_id: str | None
    ) -> dict[str, Any]:
        logger.info("Importing annotations CSV: %d rows (project=%s)", len(df), active_project_id)
        has_path = "video_path" in df.columns
        has_id = "video_id" in df.columns
        if not has_path and not has_id:
            raise DataImportError(
                user_message_key="csv_error_missing_column_video_path",
                detail="Missing required column: video_path (or video_id)",
            )
        if "is_blank" not in df.columns:
            raise DataImportError(
                user_message_key="csv_error_missing_column_is_blank",
                detail="Missing required column: is_blank",
            )

        path_to_id = {v: k for k, v in self._known_video_map(active_project_id).items()}
        known_ids = self._known_video_ids(active_project_id)

        if has_path and not has_id:
            df = df.copy()
            df["video_id"] = df["video_path"].map(path_to_id)

        imported = 0
        skipped: list[str] = []

        for video_id, group in df.groupby("video_id", sort=False):
            if pd.isna(video_id) or video_id not in known_ids:
                label = group["video_path"].iloc[0] if has_path else str(video_id)
                skipped.append(str(label))
                continue

            first = group.iloc[0]
            is_blank_raw = first["is_blank"]
            is_blank = bool(int(is_blank_raw)) if pd.notna(is_blank_raw) else None

            if is_blank:
                self.update_manual_review(
                    str(video_id), [], is_blank=True, active_project_id=active_project_id
                )
            else:
                selections = []
                for _, row in group.iterrows():
                    sp_raw = row.get("species")
                    sp = str(sp_raw).strip() if pd.notna(sp_raw) else ""
                    if not sp:
                        continue
                    beh = str(row.get("behavior") or "unlabeled").strip() or "unlabeled"
                    labeled_by = (
                        str(row["annotator"])
                        if "annotator" in group.columns and pd.notna(row.get("annotator"))
                        else None
                    )
                    selections.append(
                        {
                            "species": sp,
                            "behavior": beh,
                            "start_sec": row.get("start_sec"),
                            "end_sec": row.get("end_sec"),
                            "labeled_by": labeled_by,
                        }
                    )
                if selections:
                    self.update_manual_review(
                        str(video_id), selections, active_project_id=active_project_id
                    )

            imported += 1

        if skipped:
            logger.warning(
                "Annotations CSV: %d videos not found in DB (project=%s): %s",
                len(skipped),
                active_project_id,
                skipped[:10],
            )
        logger.info(
            "Annotations CSV import complete: imported=%d skipped=%d", imported, len(skipped)
        )
        return {"imported": imported, "skipped": skipped}
