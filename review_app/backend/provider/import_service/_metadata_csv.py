"""Video metadata CSV: recorded-at timestamps, coordinates, and assignments."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from pyproj import Transformer
from sqlalchemy import text

from review_app.backend.errors import DataImportError
from review_app.backend.path_matching import normalize_path_str, resolve_video_path
from review_app.backend.provider.import_service._shared import ImportSharedMixin

logger = logging.getLogger(__name__)


class MetadataCsvMixin(ImportSharedMixin):
    """Validation and import of per-video metadata CSVs."""

    def validate_metadata_csv(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        folder_col: str = "",
        file_col: str = "",
    ) -> dict[str, Any]:
        """Dry-run path matching; returns {total, matched, unmatched} without writing to the DB."""
        lookup = self._build_video_path_lookup(active_project_id)
        matched = 0
        unmatched_paths: list[str] = []
        for _, row in df.iterrows():
            raw_path = self._meta_resolve_path(row, folder_col, file_col)
            video_id, _ = resolve_video_path(raw_path, lookup)
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
        annotator_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Update video rows from a CSV with columns for path, created_at, latitude, longitude.
        When folder_col and file_col are provided the path is constructed as folder/file;
        otherwise the 'path' column is used.
        When source_epsg is given, coordinates are reprojected to WGS84 before storing.
        When annotator_map is provided, it maps original assigned_to names to existing or new names;
        names not in the map are skipped. If annotator_map is None, unknown annotators are
        auto-created (legacy behavior).
        Returns {"updated": int, "skipped": list[str]}.
        """
        self._safety_backup()
        use_mapped_path = bool(folder_col and file_col)
        if not use_mapped_path and "path" not in df.columns and "video_path" not in df.columns:
            raise DataImportError(
                user_message_key="csv_error_missing_column_video_path",
                detail="Metadata CSV must contain a 'path' or 'video_path' column",
            )

        transformer: Transformer | None = None
        if source_epsg:
            transformer = Transformer.from_crs(source_epsg, 4326, always_xy=True)

        lookup = self._build_video_path_lookup(active_project_id)
        has_created_at = datetime_col and datetime_col in df.columns
        has_latitude = lat_col and lat_col in df.columns
        has_longitude = lon_col and lon_col in df.columns
        has_assignment = "assigned_to" in df.columns

        if has_assignment:
            if annotator_map is not None:
                target_names = {v for v in annotator_map.values() if v is not None}
                for a in target_names:
                    self.add_annotator(a)
            else:
                all_annotators = {
                    str(a).strip() for a in df["assigned_to"].dropna().unique() if str(a).strip()
                }
                for a in all_annotators:
                    self.add_annotator(a)

        path_to_id = {
            normalize_path_str(v).lower(): k
            for k, v in self._known_video_map(active_project_id).items()
        }

        updated = 0
        skipped: list[str] = []
        now_str = self._utcnow_dt().isoformat()

        with self.engine.begin() as conn:
            for _, row in df.iterrows():
                raw_path = self._meta_resolve_path(row, folder_col, file_col)
                video_id, _ = resolve_video_path(raw_path, lookup, extra_suffix_map=path_to_id)
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
                    raw = str(row["assigned_to"]).strip() if pd.notna(row["assigned_to"]) else None
                    if raw:
                        annotator = annotator_map.get(raw) if annotator_map is not None else raw
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
