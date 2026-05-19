from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.app.config import CSV_TEMPLATES
from review_app.backend.errors import DataImportError
from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)

BLANK_SENTINEL = "__blank__"
_FALSY = {"", "0", "false", "False", "nan", "none", "None", "no"}


def _apply_historic_tags(
    provider, video_id: str, row: dict, tag_cols: list[str], append: bool
) -> None:
    """Create missing custom tags for truthy tag_cols values, then apply them to video_id."""
    if not tag_cols:
        return
    active_cols = [col for col in tag_cols if str(row.get(col, "")).strip() not in _FALSY]
    if not active_cols:
        return
    # create_custom_tag is idempotent and returns the normalised key
    tag_keys = [provider.create_custom_tag(name_en=col) for col in active_cols]
    provider.set_video_tags(video_id, tag_keys, append=append)


def _extract_tag_keys(row: dict | Any, columns) -> list[str] | None:
    """Parse tag_* and custom_tags columns from an exported CSV row.

    Returns None if no tag columns are present in the CSV (so callers can skip
    set_video_tags entirely and leave existing tags untouched).
    """
    tag_cols = [c for c in columns if c.startswith("tag_")]
    has_custom = "custom_tags" in columns
    if not tag_cols and not has_custom:
        return None
    keys: list[str] = []
    for col in tag_cols:
        val = row.get(col)
        if pd.notna(val) and str(val).strip() == "1":
            keys.append(col[4:])  # strip "tag_" prefix
    if has_custom:
        raw = row.get("custom_tags")
        if pd.notna(raw) and str(raw).strip():
            keys.extend(k.strip() for k in str(raw).split(",") if k.strip())
    return keys


class ImportMixin(ProviderBase):
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

        - by_suffix: keyed by both the full relative path from a project scan dir AND
          the legacy parent_dir/name form, so CSVs produced by the pipeline (which use the
          full relative path) and manually-edited CSVs (which may use just parent/name) both
          match. Extension-less variants are included so the extension need not be present.
        - by_stem: {stem.lower() -> video_id} — last-resort fallback, only for unique stems.
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
            scan_dirs: list[Path] = []
            if active_project_id:
                rows = conn.execute(
                    text("SELECT path FROM project_dirs WHERE project_id = :pid"),
                    {"pid": active_project_id},
                ).fetchall()
                scan_dirs = [Path(r[0]) for r in rows]

        by_suffix: dict[str, str] = {}
        stem_to_id: dict[str, str] = {}
        stem_count: dict[str, int] = {}
        for _, row in df.iterrows():
            p = Path(str(row["video_path"]))
            vid = str(row["video_id"])

            # Full relative path from each project scan dir (primary match)
            for scan_dir in scan_dirs:
                try:
                    rel = p.relative_to(scan_dir)
                    by_suffix[str(rel).lower()] = vid
                    by_suffix[str(rel.with_suffix("")).lower()] = vid
                except ValueError:
                    continue

            # Legacy parent_dir/name fallback
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
        supported = {"blank_non_blank", "species", "behavior", "object_detection"}
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
        ann_mappings: list[dict[str, str]],
        active_project_id: str | None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """
        Normalize an arbitrary CSV to the long format expected by validate_model_csv, using
        an explicit column mapping provided by the user.

        Matching: tries parent_dir/filename (suffix) first, falls back to unambiguous stem.
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

            video_id = (
                by_suffix.get(raw_path.lower())
                or by_suffix.get(str(p.with_suffix("")).lower())
                or by_suffix.get(f"{p.parent.name}/{p.name}".lower())
                or by_suffix.get(f"{p.parent.name}/{p.stem}".lower())
                or by_stem.get(p.stem.lower())
            )
            if video_id:
                if by_stem.get(p.stem.lower()) == video_id and not (
                    by_suffix.get(raw_path.lower())
                    or by_suffix.get(str(p.with_suffix("")).lower())
                    or by_suffix.get(f"{p.parent.name}/{p.name}".lower())
                    or by_suffix.get(f"{p.parent.name}/{p.stem}".lower())
                ):
                    matched_stem += 1
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
                            "path": video_id,
                            "annotation_type": ann_type,
                            "model_name": model_name,
                            "value_text": value_text,
                            "probability": probability,
                            "value_num": value_num,
                        }
                    )

        empty = pd.DataFrame(
            columns=["path", "annotation_type", "model_name", "value_text", "probability", "value_num"]
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
                by_suffix.get(raw.lower())
                or by_suffix.get(str(p.with_suffix("")).lower())
                or by_suffix.get(f"{p.parent.name}/{p.name}".lower())
                or by_suffix.get(f"{p.parent.name}/{p.stem}".lower())
                or by_stem.get(p.stem.lower())
                or raw
            )

        src["path"] = src["path"].astype(str).str.strip().map(_resolve_path)

        species_mask = src["annotation_type"].str.strip().str.lower().isin({"species", "object_detection"})
        unique_species = src.loc[species_mask, "value_text"].dropna().astype(str).str.strip().unique()
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
        return {"inserted_rows": len(rows)}

    # ── Video metadata CSV import ─────────────────────────────────────────────

    def import_video_metadata_csv(
        self, df: pd.DataFrame, active_project_id: str | None
    ) -> dict[str, Any]:
        """
        Update video rows from a CSV with columns: path (required), created_at, latitude, longitude.
        Uses the same path-matching logic as model annotation import.
        Returns {"updated": int, "skipped": list[str]}.
        """
        if "path" not in df.columns:
            raise DataImportError(
                user_message_key="csv_error_missing_column_video_path",
                detail="Metadata CSV must contain a 'path' column",
            )

        by_suffix, by_stem = self._build_video_path_lookup(active_project_id)
        has_created_at = "created_at" in df.columns
        has_latitude = "latitude" in df.columns
        has_longitude = "longitude" in df.columns

        updated = 0
        skipped: list[str] = []

        with self.engine.begin() as conn:
            for _, row in df.iterrows():
                raw_path = str(row.get("path", "")).strip()
                p = Path(raw_path)
                video_id = (
                    by_suffix.get(f"{p.parent.name}/{p.name}".lower())
                    or by_suffix.get(f"{p.parent.name}/{p.stem}".lower())
                    or by_stem.get(p.stem.lower())
                )
                if video_id is None:
                    skipped.append(raw_path)
                    continue

                fields: dict[str, Any] = {}
                if has_created_at and not pd.isna(row["created_at"]):
                    try:
                        fields["created_at"] = pd.to_datetime(
                            row["created_at"], utc=True
                        ).to_pydatetime()
                    except Exception:
                        pass
                if has_latitude and not pd.isna(row["latitude"]):
                    try:
                        fields["latitude"] = float(str(row["latitude"]).replace(",", "."))
                    except (ValueError, TypeError):
                        pass
                if has_longitude and not pd.isna(row["longitude"]):
                    try:
                        fields["longitude"] = float(str(row["longitude"]).replace(",", "."))
                    except (ValueError, TypeError):
                        pass

                if not fields:
                    continue

                set_clause = ", ".join(f"{k} = :{k}" for k in fields)
                fields["video_id"] = video_id
                fields["pid"] = active_project_id
                pid_clause = " AND project_id = :pid" if active_project_id else ""
                conn.execute(
                    text(f"UPDATE videos SET {set_clause} WHERE video_id = :video_id{pid_clause}"),
                    fields,
                )
                updated += 1

        logger.info("Video metadata import: %d updated, %d skipped", updated, len(skipped))
        return {"updated": updated, "skipped": skipped}

    # ── Annotation export / import ────────────────────────────────────────────

    def export_annotations_csv(self, active_project_id: str | None) -> pd.DataFrame:
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
                        v.latitude,
                        v.longitude,
                        v.duration_sec,
                        CAST(vl.is_blank AS INTEGER)      AS is_blank,
                        CAST(vl.review_later AS INTEGER)  AS review_later,
                        CASE WHEN vl.is_blank IS NOT NULL THEN 1 ELSE 0 END AS is_annotated,
                        COALESCE(io.labeled_by, vl.labeled_by) AS annotator,
                        COALESCE(io.labeled_at, vl.labeled_at) AS labeled_at,
                        io.id                     AS observation_id,
                        s.scientific_name         AS species,
                        b.key                     AS behavior,
                        io.count,
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

            tags_df = pd.read_sql(
                text(f"""
                    SELECT vt.video_id, t.key, t.is_custom
                    FROM video_tags vt
                    JOIN tags t ON t.id = vt.tag_id
                    WHERE EXISTS (SELECT 1 FROM videos v WHERE v.video_id = vt.video_id {vid_pid})
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

        if not tags_df.empty:
            builtin_keys = tags_df.loc[tags_df["is_custom"] == 0, "key"].unique().tolist()
            # One 0/1 column per built-in tag
            for key in sorted(builtin_keys):
                col = f"tag_{key}"
                flagged = tags_df.loc[tags_df["key"] == key, "video_id"]
                base_df[col] = base_df["video_id"].isin(flagged).astype(int)
            # Custom tags combined in one column
            custom_tags = tags_df[tags_df["is_custom"] == 1].copy()
            if not custom_tags.empty:
                custom_agg = (
                    custom_tags.groupby("video_id")["key"]
                    .apply(lambda s: ",".join(sorted(s)))
                    .reset_index(name="custom_tags")
                )
                base_df = base_df.merge(custom_agg, on="video_id", how="left")
            else:
                base_df["custom_tags"] = None
        else:
            for key in ["fire", "nice_shot", "broken_metadata"]:
                base_df[f"tag_{key}"] = 0
            base_df["custom_tags"] = None

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
                model_df[model_df["annotation_type"].isin({"species", "object_detection"})]
                .groupby("video_id")
                .agg(distinct_top1=("value_text", "nunique"), model_count=("value_text", "count"))
                .reset_index()
            )
            blank_probs = (
                model_df[model_df["annotation_type"] == "blank_non_blank"]
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

        base_df = base_df.drop(columns=["video_id"], errors="ignore")

        # Format float timestamp columns as fixed-decimal strings so that to_csv() writes them
        # quoted. Without this, LibreOffice with European locale settings misreads "60.085" as
        # the integer 60085 (treating "." as a thousands separator).
        for col in ("duration_sec", "start_sec", "end_sec"):
            if col in base_df.columns:
                base_df[col] = base_df[col].apply(lambda v: f"{v:.3f}" if pd.notna(v) else "")

        return base_df

    def import_annotations_csv(
        self, df: pd.DataFrame, active_project_id: str | None, mode: str = "override"
    ) -> dict[str, Any]:
        logger.info(
            "Importing annotations CSV: %d rows (project=%s, mode=%s)",
            len(df),
            active_project_id,
            mode,
        )
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

        # Build video path lookup: exact match first, then fuzzy suffix fallback for cross-machine sharing.
        path_to_id = {v: k for k, v in self._known_video_map(active_project_id).items()}
        by_suffix, _by_stem = self._build_video_path_lookup(active_project_id)
        known_ids = self._known_video_ids(active_project_id)

        def _resolve_path(path_str: str) -> str | None:
            vid = path_to_id.get(path_str)
            if vid:
                return vid
            p = Path(path_str)
            return by_suffix.get(f"{p.parent.name}/{p.name}".lower()) or by_suffix.get(
                f"{p.parent.name}/{p.stem}".lower()
            )

        if has_path and not has_id:
            df = df.copy()
            df["video_id"] = df["video_path"].map(_resolve_path)

        imported = 0
        skipped: list[str] = []
        append = mode == "append"

        for video_id, group in df.groupby("video_id", sort=False, dropna=False):
            if pd.isna(video_id) or video_id not in known_ids:
                label = group["video_path"].iloc[0] if has_path else str(video_id)
                skipped.append(str(label))
                continue

            first = group.iloc[0]
            is_blank_raw = first["is_blank"]
            is_blank = bool(int(is_blank_raw)) if pd.notna(is_blank_raw) else None
            blank_labeled_by = (
                str(first["annotator"])
                if "annotator" in group.columns and pd.notna(first.get("annotator"))
                else None
            )

            if is_blank:
                self.update_manual_review(
                    str(video_id),
                    [],
                    is_blank=True,
                    labeled_by=blank_labeled_by,
                    active_project_id=active_project_id,
                    append=append,
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
                    labeled_at = (
                        pd.to_datetime(row["labeled_at"], errors="coerce")
                        if "labeled_at" in group.columns and pd.notna(row.get("labeled_at"))
                        else None
                    )
                    if labeled_at is pd.NaT:
                        labeled_at = None
                    # Ignore observation_id in append mode to ensure we never override existing records.
                    obs_id_raw = row.get("observation_id")
                    obs_id = int(obs_id_raw) if pd.notna(obs_id_raw) and mode != "append" else None
                    count_raw = row.get("count")
                    count_val = int(count_raw) if pd.notna(count_raw) else None

                    selections.append(
                        {
                            "id": obs_id,
                            "species": sp,
                            "behavior": beh,
                            "count": count_val,
                            "start_sec": row.get("start_sec"),
                            "end_sec": row.get("end_sec"),
                            "labeled_by": labeled_by,
                            "labeled_at": labeled_at,
                        }
                    )
                if selections:
                    self.update_manual_review(
                        str(video_id),
                        selections,
                        active_project_id=active_project_id,
                        append=append,
                    )

            # Restore review_later
            rl_raw = first.get("review_later") if "review_later" in group.columns else None
            if pd.notna(rl_raw) and bool(int(rl_raw)):
                self.set_review_later(str(video_id), True)

            # Restore tags
            tag_keys = _extract_tag_keys(first, df.columns)
            if tag_keys is not None:
                self.set_video_tags(str(video_id), tag_keys, append=append)

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

    # ── Historic CSV import ───────────────────────────────────────────────────

    def _filter_and_group_historic(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        folder_col: str,
        video_col: str,
        data_type_col: str,
        data_type_val: str = "",
    ) -> tuple[pd.DataFrame, int, dict[str, list[dict]], list[str]]:
        """Filter rows by data_type_col == data_type_val (when both are set), build path lookup, group matched rows by video_id."""
        if data_type_col in df.columns and data_type_val:
            video_df = df[df[data_type_col].astype(str).str.strip() == data_type_val].copy()
            skipped_installation = len(df) - len(video_df)
        else:
            video_df = df.copy()
            skipped_installation = 0

        by_suffix, by_stem = self._build_video_path_lookup(active_project_id)
        groups: dict[str, list[dict]] = {}
        skipped: list[str] = []

        for _, row in video_df.iterrows():
            folder = str(row.get(folder_col, "")).strip() if folder_col in video_df.columns else ""
            video = str(row.get(video_col, "")).strip() if video_col in video_df.columns else ""
            video_id = by_suffix.get(f"{folder}/{video}".lower()) or by_stem.get(video.lower())
            if video_id is None:
                skipped.append(f"{folder}/{video}")
            else:
                groups.setdefault(video_id, []).append(dict(row))

        return video_df, skipped_installation, groups, skipped

    def validate_historic_csv(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        folder_col: str = "Folder_name_standard",
        video_col: str = "Video_name",
        species_col: str = "Species",
        data_type_col: str = "Data_type",
        data_type_val: str = "Video",
        species_mappings: dict[str, str] | None = None,
        is_blank_col: str = "",
        tag_cols: list[str] | None = None,
    ) -> dict[str, Any]:
        species_mappings = species_mappings or {}
        video_df, skipped_installation, groups, skipped = self._filter_and_group_historic(
            df, active_project_id, folder_col, video_col, data_type_col, data_type_val
        )
        variant_map = self._build_species_variant_map()

        unknown_species: set[str] = set()
        seen_unmatched: set[str] = set()
        unmatched_paths: list[str] = []

        for path in skipped:
            if path not in seen_unmatched:
                seen_unmatched.add(path)
                unmatched_paths.append(path)

        if species_col in video_df.columns:
            for rows in groups.values():
                for row in rows:
                    sp = str(row.get(species_col, "")).strip()
                    if not sp or sp in ("Vide", "NA", "nan"):
                        continue
                    if sp in species_mappings:
                        mapped = species_mappings[sp]
                        # Blank sentinel is always valid; empty means not yet mapped
                        if mapped and mapped != BLANK_SENTINEL:
                            is_valid, _ = self._validate_species_fuzzy(mapped, variant_map)
                            if not is_valid:
                                unknown_species.add(sp)
                    else:
                        is_valid, _ = self._validate_species_fuzzy(sp, variant_map)
                        if not is_valid:
                            unknown_species.add(sp)

        logger.info(
            "Historic CSV validation: total=%d matched=%d unmatched=%d unknown_species=%d",
            len(video_df),
            len(groups),
            len(seen_unmatched),
            len(unknown_species),
        )
        return {
            "total_rows": len(video_df),
            "skipped_installation": skipped_installation,
            "matched": len(groups),
            "unmatched": len(seen_unmatched),
            "unmatched_paths": unmatched_paths,
            "unknown_species": sorted(unknown_species),
        }

    def import_historic_csv(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        folder_col: str = "Folder_name_standard",
        video_col: str = "Video_name",
        species_col: str = "Species",
        data_type_col: str = "Data_type",
        data_type_val: str = "Video",
        behavior_col: str = "Behaviour",
        count_col: str = "Number",
        observer_col: str = "Observer",
        timestamp_col: str = "timestamp",
        mode: str = "override",
        species_mappings: dict[str, str] | None = None,
        is_blank_col: str = "",
        tag_cols: list[str] | None = None,
    ) -> dict[str, Any]:
        species_mappings = species_mappings or {}
        tag_cols = tag_cols or []
        _, _, groups, skipped = self._filter_and_group_historic(
            df, active_project_id, folder_col, video_col, data_type_col, data_type_val
        )
        variant_map = self._build_species_variant_map()
        append = mode == "append"

        imported = 0
        skipped_observations: list[dict[str, Any]] = []

        for video_id, rows in groups.items():
            first = rows[0]
            labeled_by: str | None = str(first.get(observer_col, "")).strip() or None

            # Explicit is_blank_col takes priority over species-based blank detection
            force_blank = (
                is_blank_col
                and is_blank_col in first
                and str(first.get(is_blank_col, "")).strip() not in _FALSY
            )

            non_blank = (
                []
                if force_blank
                else [
                    r
                    for r in rows
                    if str(r.get(species_col, "")).strip() not in ("Vide", "NA", "nan", "")
                    and species_mappings.get(str(r.get(species_col, "")).strip()) != BLANK_SENTINEL
                ]
            )

            if not non_blank:
                self.update_manual_review(
                    video_id,
                    [],
                    is_blank=True,
                    labeled_by=labeled_by,
                    active_project_id=active_project_id,
                    append=append,
                )
                _apply_historic_tags(self, video_id, first, tag_cols, append)
                imported += 1
                continue

            selections: list[dict[str, Any]] = []
            for r in non_blank:
                sp = str(r.get(species_col, "")).strip()
                sp = species_mappings.get(sp, sp)
                is_valid, best_match = self._validate_species_fuzzy(sp, variant_map)
                if not is_valid:
                    skipped_observations.append({"video_id": video_id, "species": sp})
                    continue
                sp = best_match or sp

                beh = str(r.get(behavior_col, "")).strip() if behavior_col in r else ""
                if beh in ("NA", "nan", ""):
                    beh = "unlabeled"

                count_raw = r.get(count_col)
                count_val: int | None = None
                if count_raw is not None and not (
                    isinstance(count_raw, float) and pd.isna(count_raw)
                ):
                    try:
                        count_val = int(count_raw)
                    except (ValueError, TypeError):
                        pass

                row_ts = r.get(timestamp_col)
                row_labeled_at = None
                if row_ts is not None and not (isinstance(row_ts, float) and pd.isna(row_ts)):
                    try:
                        row_labeled_at = pd.to_datetime(row_ts)
                    except Exception:
                        pass

                selections.append(
                    {
                        "species": sp,
                        "behavior": beh,
                        "count": count_val,
                        "labeled_by": str(r.get(observer_col, "")).strip() or None,
                        "labeled_at": row_labeled_at,
                    }
                )

            if selections:
                self.update_manual_review(
                    video_id,
                    selections,
                    active_project_id=active_project_id,
                    append=append,
                )
            _apply_historic_tags(self, video_id, first, tag_cols, append)
            imported += 1

        logger.info(
            "Historic CSV import complete: imported=%d skipped=%d skipped_obs=%d",
            imported,
            len(skipped),
            len(skipped_observations),
        )
        return {
            "imported": imported,
            "skipped": list(dict.fromkeys(skipped)),
            "skipped_observations": skipped_observations,
        }
