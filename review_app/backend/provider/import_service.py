from __future__ import annotations

import logging
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from pyproj import Transformer
from sqlalchemy import text

from review_app.app.config import CSV_TEMPLATES
from review_app.backend.errors import DataImportError
from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)

BLANK_SENTINEL = "__blank__"
_FALSY = {"", "0", "false", "False", "nan", "none", "None", "no"}
_BLANK_SPECIES = {"Vide", "Video vide", "Indetermine", "Espece indeterminee", "NA", "nan", ""}


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


def _extract_builtin_tag_keys(row: dict | Any, columns) -> list[str] | None:
    """Parse tag_* columns from an exported CSV row.

    Returns None if no tag_* columns are present (so callers can skip
    set_video_tags entirely and leave existing tags untouched).
    Custom tags are handled separately so their keys can be normalized
    via create_custom_tag before being passed to set_video_tags.
    """
    tag_cols = [c for c in columns if c.startswith("tag_")]
    if not tag_cols:
        return None
    keys: list[str] = []
    for col in tag_cols:
        val = row.get(col)
        if pd.notna(val) and str(val).strip() == "1":
            keys.append(col[4:])  # strip "tag_" prefix
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
            template = "video_path,annotation_type,model_name,value_text,value_num,probability,t_start_sec,t_end_sec\n"
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
        # cam_prefix/stem fallback: e.g. "BDR72_681625_.../DCIM/.../DSCF0001.mp4" → "bdr72/dscf0001"
        cam_stem_to_id: dict[str, str] = {}
        cam_stem_count: dict[str, int] = {}
        for _, row in df.iterrows():
            p = Path(str(row["video_path"]))
            vid = str(row["video_id"])

            # Full relative path from each project scan dir (primary match)
            for scan_dir in scan_dirs:
                try:
                    rel = p.relative_to(scan_dir)
                    by_suffix[str(rel).lower()] = vid
                    by_suffix[str(rel.with_suffix("")).lower()] = vid
                    # Short cam-ID prefix (first _-segment of the top-level folder)
                    if rel.parts:
                        cam_prefix = rel.parts[0].split("_")[0].lower()
                        key = f"{cam_prefix}/{p.stem.lower()}"
                        cam_stem_to_id[key] = vid
                        cam_stem_count[key] = cam_stem_count.get(key, 0) + 1
                except ValueError:
                    continue

            # Legacy parent_dir/name fallback
            by_suffix[f"{p.parent.name}/{p.name}".lower()] = vid
            by_suffix[f"{p.parent.name}/{p.stem}".lower()] = vid

            stem = p.stem.lower()
            stem_to_id[stem] = vid
            stem_count[stem] = stem_count.get(stem, 0) + 1

        by_stem = {s: vid for s, vid in stem_to_id.items() if stem_count[s] == 1}
        # Merge unambiguous cam-prefix entries into by_suffix
        for key, vid in cam_stem_to_id.items():
            if cam_stem_count[key] == 1:
                by_suffix.setdefault(key, vid)
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

        required = {"video_path", "annotation_type", "model_name"}
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

    def validate_metadata_csv(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        folder_col: str = "",
        file_col: str = "",
    ) -> dict[str, Any]:
        """Dry-run path matching; returns {total, matched, unmatched} without writing to the DB."""
        by_suffix, by_stem = self._build_video_path_lookup(active_project_id)
        matched = 0
        unmatched_paths: list[str] = []
        for _, row in df.iterrows():
            raw_path = self._meta_resolve_path(row, folder_col, file_col)
            p = Path(raw_path)
            video_id = (
                by_suffix.get(raw_path.lower())
                or by_suffix.get(f"{p.parent.name}/{p.name}".lower())
                or by_suffix.get(f"{p.parent.name}/{p.stem}".lower())
                or by_stem.get(p.stem.lower())
            )
            if video_id:
                matched += 1
            else:
                unmatched_paths.append(raw_path)
        return {
            "total": len(df),
            "matched": matched,
            "unmatched": len(unmatched_paths),
            "unmatched_paths": unmatched_paths[:50],
        }

    @staticmethod
    def _meta_resolve_path(row: Any, folder_col: str, file_col: str) -> str:
        if folder_col and file_col:
            folder = str(row.get(folder_col, "")).strip()
            file_ = str(row.get(file_col, "")).strip()
            return f"{folder}/{file_}"
        return str(row.get("path") or row.get("video_path") or "").strip()

    def import_video_metadata_csv(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        folder_col: str = "",
        file_col: str = "",
        datetime_col: str = "created_at",
        lat_col: str = "latitude",
        lon_col: str = "longitude",
        source_epsg: int | None = None,
    ) -> dict[str, Any]:
        """
        Update video rows from a CSV with columns for path, created_at, latitude, longitude.
        When folder_col and file_col are provided the path is constructed as folder/file;
        otherwise the 'path' column is used.
        When source_epsg is given, coordinates are reprojected to WGS84 before storing.
        Returns {"updated": int, "skipped": list[str]}.
        """
        use_mapped_path = bool(folder_col and file_col)
        if not use_mapped_path and "path" not in df.columns and "video_path" not in df.columns:
            raise DataImportError(
                user_message_key="csv_error_missing_column_video_path",
                detail="Metadata CSV must contain a 'path' or 'video_path' column",
            )

        transformer: Transformer | None = None
        if source_epsg:
            transformer = Transformer.from_crs(source_epsg, 4326, always_xy=True)

        by_suffix, by_stem = self._build_video_path_lookup(active_project_id)
        has_created_at = datetime_col and datetime_col in df.columns
        has_latitude = lat_col and lat_col in df.columns
        has_longitude = lon_col and lon_col in df.columns
        has_assignment = "assigned_to" in df.columns

        if has_assignment:
            all_annotators = {
                str(a).strip() for a in df["assigned_to"].dropna().unique() if str(a).strip()
            }
            for a in all_annotators:
                self.add_annotator(a)

        path_to_id = {v.lower(): k for k, v in self._known_video_map(active_project_id).items()}

        updated = 0
        skipped: list[str] = []
        now_str = self._utcnow_dt().isoformat()

        with self.engine.begin() as conn:
            for _, row in df.iterrows():
                raw_path = self._meta_resolve_path(row, folder_col, file_col)
                p = Path(raw_path)
                video_id = (
                    path_to_id.get(raw_path.lower())
                    or by_suffix.get(raw_path.lower())
                    or by_suffix.get(f"{p.parent.name}/{p.name}".lower())
                    or by_suffix.get(f"{p.parent.name}/{p.stem}".lower())
                    or by_stem.get(p.stem.lower())
                )
                if video_id is None:
                    skipped.append(raw_path)
                    continue

                fields: dict[str, Any] = {}
                if has_created_at and not pd.isna(row[datetime_col]):
                    try:
                        fields["created_at"] = pd.to_datetime(
                            row[datetime_col], utc=True
                        ).to_pydatetime()
                    except Exception:
                        pass
                if (
                    has_latitude
                    and has_longitude
                    and not pd.isna(row[lat_col])
                    and not pd.isna(row[lon_col])
                ):
                    try:
                        raw_lat = float(str(row[lat_col]).replace(",", "."))
                        raw_lon = float(str(row[lon_col]).replace(",", "."))
                        if transformer:
                            wgs_lon, wgs_lat = transformer.transform(raw_lon, raw_lat)
                            fields["latitude"] = wgs_lat
                            fields["longitude"] = wgs_lon
                        else:
                            fields["latitude"] = raw_lat
                            fields["longitude"] = raw_lon
                    except (ValueError, TypeError):
                        pass
                elif has_latitude and not pd.isna(row[lat_col]):
                    try:
                        fields["latitude"] = float(str(row[lat_col]).replace(",", "."))
                    except (ValueError, TypeError):
                        pass
                elif has_longitude and not pd.isna(row[lon_col]):
                    try:
                        fields["longitude"] = float(str(row[lon_col]).replace(",", "."))
                    except (ValueError, TypeError):
                        pass

                if fields:
                    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
                    fields["video_id"] = video_id
                    fields["pid"] = active_project_id
                    pid_clause = " AND project_id = :pid" if active_project_id else ""
                    conn.execute(
                        text(
                            f"UPDATE videos SET {set_clause} WHERE video_id = :video_id{pid_clause}"
                        ),
                        fields,
                    )
                    updated += 1

                if has_assignment:
                    annotator = (
                        str(row["assigned_to"]).strip() if pd.notna(row["assigned_to"]) else None
                    )
                    if annotator:
                        conn.execute(
                            text("""
                                INSERT OR REPLACE INTO video_assignments (video_id, assigned_to, assigned_at)
                                VALUES (:video_id, :annotator, :now)
                            """),
                            {"video_id": video_id, "annotator": annotator, "now": now_str},
                        )

        logger.info("Video metadata import: %d updated, %d skipped", updated, len(skipped))
        return {"updated": updated, "skipped": skipped}

    # ── Annotation export / import ────────────────────────────────────────────

    def export_annotations_csv(self, active_project_id: str | None) -> pd.DataFrame:
        params = {"pid": active_project_id} if active_project_id else {}
        vid_pid = "AND v.project_id = :pid" if active_project_id else ""
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
                        va.assigned_to,
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
                    LEFT JOIN video_assignments va ON va.video_id = v.video_id
                    LEFT JOIN individual_observations io ON io.video_id = v.video_id
                    LEFT JOIN species s ON s.id = io.species_id
                    LEFT JOIN behaviors b ON b.id = io.behavior_id
                    WHERE 1=1 {vid_pid}
                    ORDER BY v.camera_id, v.video_path, v.created_at
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

        all_builtin_keys = sorted(t["key"] for t in self.get_all_tags() if not t["is_custom"])
        # One 0/1 column per built-in tag — always present even if no video has it
        for key in all_builtin_keys:
            flagged = (
                tags_df.loc[tags_df["key"] == key, "video_id"]
                if not tags_df.empty
                else pd.Series([], dtype=str)
            )
            base_df[f"tag_{key}"] = base_df["video_id"].isin(flagged).astype(int)
        # Custom tags combined in one column
        if not tags_df.empty:
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
            base_df["custom_tags"] = None

        base_df = base_df.drop(columns=["video_id"], errors="ignore")

        # Move tag columns to the end
        tag_cols = [c for c in base_df.columns if c.startswith("tag_") or c == "custom_tags"]
        other_cols = [c for c in base_df.columns if c not in tag_cols]
        base_df = base_df[other_cols + tag_cols]

        # Format float timestamp columns as fixed-decimal strings so that to_csv() writes them
        # quoted. Without this, LibreOffice with European locale settings misreads "60.085" as
        # the integer 60085 (treating "." as a thousands separator).
        for col in "duration_sec":
            if col in base_df.columns:
                base_df[col] = base_df[col].apply(lambda v: f"{v:.3f}" if pd.notna(v) else "")

        return base_df

    def export_model_annotations_csv(
        self, active_project_id: str | None, camera_ids: list[str] | None = None
    ) -> pd.DataFrame:
        params: dict[str, Any] = {"pid": active_project_id} if active_project_id else {}
        v_pid = "AND v.project_id = :pid" if active_project_id else ""
        cam_filter = ""
        if camera_ids:
            placeholders = ", ".join(f":c{i}" for i in range(len(camera_ids)))
            for i, c in enumerate(camera_ids):
                params[f"c{i}"] = c
            cam_filter = f"AND v.camera_id IN ({placeholders})"

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
        observations_by_annotator: Counter[str] = Counter()
        all_custom_keys: set[str] = set()
        now_str = self._utcnow_dt().isoformat()

        for video_id, group in df.groupby("video_id", sort=False, dropna=False):
            if pd.isna(video_id) or video_id not in known_ids:
                label = group["video_path"].iloc[0] if has_path else str(video_id)
                skipped.append(str(label))
                continue

            first = group.iloc[0]

            # Restore video assignment
            if "assigned_to" in group.columns:
                annotator = (
                    str(first["assigned_to"]).strip() if pd.notna(first["assigned_to"]) else None
                )
                if annotator:
                    self.add_annotator(annotator)
                    with self.engine.begin() as conn:
                        conn.execute(
                            text("""
                                INSERT OR REPLACE INTO video_assignments (video_id, assigned_to, assigned_at)
                                VALUES (:video_id, :annotator, :now)
                            """),
                            {"video_id": video_id, "annotator": annotator, "now": now_str},
                        )

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
                observations_by_annotator[blank_labeled_by or ""] += 1
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
                    for sel in selections:
                        observations_by_annotator[sel.get("labeled_by") or ""] += 1

            # Restore review_later
            rl_raw = first.get("review_later") if "review_later" in group.columns else None
            if pd.notna(rl_raw) and bool(int(rl_raw)):
                self.set_review_later(str(video_id), True)

            # Auto-create missing custom tags and collect their normalized keys so
            # set_video_tags receives keys that actually exist in the DB.
            normalized_custom_keys: list[str] = []
            if "custom_tags" in df.columns:
                raw = first.get("custom_tags")
                if pd.notna(raw) and str(raw).strip():
                    for k in str(raw).split(","):
                        k = k.strip()
                        if k:
                            normalized_custom_keys.append(self.create_custom_tag(name_en=k))
            builtin_tag_keys = _extract_builtin_tag_keys(first, df.columns)
            if builtin_tag_keys is not None or normalized_custom_keys:
                self.set_video_tags(
                    str(video_id),
                    (builtin_tag_keys or []) + normalized_custom_keys,
                    append=append,
                )
            all_custom_keys.update(normalized_custom_keys)

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
        return {
            "imported": imported,
            "skipped": skipped,
            "by_annotator": dict(observations_by_annotator),
            "custom_tags": len(all_custom_keys),
        }

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
        valid_for_project = set(self.get_valid_species(active_project_id))
        variant_map = {
            k: v for k, v in self._build_species_variant_map().items() if v in valid_for_project
        }

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
                    if not sp or sp in _BLANK_SPECIES:
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
        valid_for_project = set(self.get_valid_species(active_project_id))
        variant_map = {
            k: v for k, v in self._build_species_variant_map().items() if v in valid_for_project
        }
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
                    if str(r.get(species_col, "")).strip() not in _BLANK_SPECIES
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
                        row_labeled_at = pd.to_datetime(row_ts, dayfirst=True)
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

    # ── Project bundle export / import ────────────────────────────────────────

    def _export_metadata_csv(self, project_id: str, camera_ids: list[str] | None = None) -> str:
        """Export video metadata (path, camera, recorded_at, lat, lon, assigned_to) as CSV."""
        params: dict[str, Any] = {"pid": project_id}
        cam_filter = ""
        if camera_ids:
            placeholders = ", ".join(f":c{i}" for i in range(len(camera_ids)))
            for i, c in enumerate(camera_ids):
                params[f"c{i}"] = c
            cam_filter = f"AND v.camera_id IN ({placeholders})"
        with self.engine.connect() as conn:
            df = pd.read_sql(
                text(f"""
                    SELECT 
                        v.video_path, 
                        v.camera_id, 
                        v.created_at, 
                        v.latitude, 
                        v.longitude,
                        va.assigned_to
                    FROM videos v
                    LEFT JOIN video_assignments va ON va.video_id = v.video_id
                    WHERE v.project_id = :pid AND v.is_missing = 0 {cam_filter}
                    ORDER BY v.camera_id, v.video_path
                """),
                conn,
                params=params,
            )
        return df.to_csv(index=False)

    def export_project_bundle(
        self,
        project_id: str,
        include: list[str],
        camera_ids: list[str] | None = None,
    ) -> bytes:
        """Build a ZIP bundle of project data.

        `include` is a subset of: "species", "tags", "model_annotations", "metadata".
        `camera_ids` optionally filters model_annotations and metadata to those cameras.
        Returns raw ZIP bytes.
        """
        import io
        import json
        import zipfile

        buf = io.BytesIO()
        contents = []

        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if "species" in include:
                csv_str = self.export_project_species_csv(project_id)
                if csv_str:
                    zf.writestr("species.csv", csv_str)
                    contents.append("species")

            if "tags" in include:
                csv_str = self.export_tags_csv()
                if csv_str:
                    zf.writestr("tags.csv", csv_str)
                    contents.append("tags")

            if "model_annotations" in include:
                ma_df = self.export_model_annotations_csv(project_id, camera_ids)
                if not ma_df.empty:
                    zf.writestr("model_annotations.csv", ma_df.to_csv(index=False))
                    contents.append("model_annotations")

            if "metadata" in include:
                csv_str = self._export_metadata_csv(project_id, camera_ids)
                if csv_str:
                    zf.writestr("metadata.csv", csv_str)
                    contents.append("metadata")

            manifest = json.dumps({"version": "1", "contents": contents})
            zf.writestr("bundle.json", manifest)

        return buf.getvalue()

    def import_project_bundle(self, project_id: str, zip_bytes: bytes) -> dict[str, Any]:
        """Unzip a project bundle and import each present component.

        Returns a dict keyed by component name with per-component import results.
        """
        import io
        import json
        import zipfile

        results: dict[str, Any] = {}

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = set(zf.namelist())

            manifest_contents: list[str] = []
            if "bundle.json" in names:
                manifest = json.loads(zf.read("bundle.json"))
                manifest_contents = manifest.get("contents", [])
            else:
                manifest_contents = [
                    n.replace(".csv", "")
                    for n in names
                    if n.endswith(".csv")
                    and n.replace(".csv", "")
                    in ("species", "tags", "model_annotations", "metadata")
                ]

            if "species" in manifest_contents and "species.csv" in names:
                content = zf.read("species.csv").decode("utf-8")
                try:
                    count = self.import_project_species_from_csv(project_id, content)
                    results["species"] = {"imported": count}
                except Exception as exc:
                    results["species"] = {"error": str(exc)}

            if "tags" in manifest_contents and "tags.csv" in names:
                content = zf.read("tags.csv").decode("utf-8")
                try:
                    count = self.import_tags_from_csv(content)
                    results["tags"] = {"imported": count}
                except Exception as exc:
                    results["tags"] = {"error": str(exc)}

            if "model_annotations" in manifest_contents and "model_annotations.csv" in names:
                import io as _io

                content = zf.read("model_annotations.csv").decode("utf-8")
                try:
                    df = pd.read_csv(_io.StringIO(content))
                    cleaned_df, errors_df, _, _ = self.validate_model_csv(
                        df, active_project_id=project_id
                    )
                    if not cleaned_df.empty:
                        stats = self.import_model_csv(cleaned_df, project_id)
                        results["model_annotations"] = stats
                    else:
                        results["model_annotations"] = {"imported": 0, "errors": len(errors_df)}
                except Exception as exc:
                    results["model_annotations"] = {"error": str(exc)}

            if "metadata" in manifest_contents and "metadata.csv" in names:
                import io as _io

                content = zf.read("metadata.csv").decode("utf-8")
                try:
                    df = pd.read_csv(_io.StringIO(content))
                    stats = self.import_video_metadata_csv(df, project_id)
                    results["metadata"] = stats
                except Exception as exc:
                    results["metadata"] = {"error": str(exc)}

        return results

    def export_all_bundles(self, project_id: str, include: list[str]) -> bytes:
        """Build one bundle ZIP per annotator and wrap them in an outer ZIP.

        Each inner ZIP is named bundle_<annotator>_<today>.zip and contains only
        that annotator's assigned cameras. Unassigned annotators get a full-project
        bundle (no camera filter). Returns raw outer ZIP bytes.
        """
        import io
        import zipfile
        from datetime import date as _date

        today = _date.today()
        annotators = self.get_all_annotators()
        camera_map = self.get_camera_assignment_map(project_id)

        outer_buf = io.BytesIO()
        with zipfile.ZipFile(outer_buf, "w", compression=zipfile.ZIP_DEFLATED) as outer:
            for annotator in annotators:
                camera_ids = [c for c, a in camera_map.items() if a == annotator] or None
                bundle_bytes = self.export_project_bundle(project_id, include, camera_ids)
                safe_name = annotator.replace(" ", "_")
                outer.writestr(f"bundle_{safe_name}_{today}.zip", bundle_bytes)
        return outer_buf.getvalue()
