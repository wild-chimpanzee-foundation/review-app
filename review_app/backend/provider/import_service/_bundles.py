"""Project bundle ZIPs for distributing work to annotators and collecting results."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.backend.provider.import_service._shared import ImportSharedMixin

logger = logging.getLogger(__name__)


class BundleMixin(ImportSharedMixin):
    """Export and import of project bundle ZIPs (species, tags, model annotations, metadata)."""

    def get_bundle_annotators(self, zip_bytes: bytes) -> list[str]:
        """Inspect a bundle ZIP and return all annotator names from metadata.csv."""
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            if "metadata.csv" not in zf.namelist():
                return []
            df = pd.read_csv(io.BytesIO(zf.read("metadata.csv")))
        if "assigned_to" not in df.columns:
            return []
        names = {str(a).strip() for a in df["assigned_to"].dropna().unique() if str(a).strip()}
        return sorted(names)

    def _export_metadata_csv(
        self,
        project_id: str,
        camera_ids: list[str] | None = None,
        video_ids: list[str] | None = None,
    ) -> str:
        """Export video metadata (path, camera, recorded_at, lat, lon, assigned_to) as CSV."""
        params: dict[str, Any] = {"pid": project_id}
        cam_filter = self._camera_video_sql_filter(params, camera_ids, video_ids)
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
        video_ids: list[str] | None = None,
    ) -> bytes:
        """Build a ZIP bundle of project data.

        `include` is a subset of: "species", "tags", "model_annotations", "metadata".
        `camera_ids` or `video_ids` optionally filters model_annotations and metadata.
        Returns raw ZIP bytes.
        """
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
                ma_df = self.export_model_annotations_csv(project_id, camera_ids, video_ids)
                if not ma_df.empty:
                    zf.writestr("model_annotations.csv", ma_df.to_csv(index=False))
                    contents.append("model_annotations")

            if "metadata" in include:
                csv_str = self._export_metadata_csv(project_id, camera_ids, video_ids)
                if csv_str:
                    zf.writestr("metadata.csv", csv_str)
                    contents.append("metadata")

            manifest = json.dumps({"version": "1", "contents": contents})
            zf.writestr("bundle.json", manifest)

        return buf.getvalue()

    def import_project_bundle(
        self,
        project_id: str,
        zip_bytes: bytes,
        annotator_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Unzip a project bundle and import each present component.

        When annotator_map is provided (for metadata), it maps original assigned_to
        names to existing or new annotator names.

        Returns a dict keyed by component name with per-component import results.
        """
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
                content = zf.read("model_annotations.csv").decode("utf-8")
                try:
                    df = pd.read_csv(io.StringIO(content))
                    cleaned_df, errors_df, _, _ = self.validate_model_csv(
                        df, active_project_id=project_id
                    )
                    error_count = len(errors_df)
                    if not cleaned_df.empty:
                        stats = self.import_model_csv(cleaned_df, project_id)
                        results["model_annotations"] = {**stats, "errors": error_count}
                    else:
                        unmatched = (
                            errors_df.loc[errors_df["error"] == "error_unknown_path", "video_path"]
                            .dropna()
                            .tolist()[:5]
                            if not errors_df.empty and "video_path" in errors_df.columns
                            else []
                        )
                        if unmatched:
                            logger.warning(
                                "Bundle model_annotations: 0 rows imported, %d errors. "
                                "Sample unmatched paths: %s",
                                error_count,
                                unmatched,
                            )
                        results["model_annotations"] = {
                            "imported": 0,
                            "errors": error_count,
                        }
                except Exception as exc:
                    results["model_annotations"] = {"error": str(exc)}

            if "metadata" in manifest_contents and "metadata.csv" in names:
                content = zf.read("metadata.csv").decode("utf-8")
                try:
                    df = pd.read_csv(io.StringIO(content))
                    stats = self.import_video_metadata_csv(
                        df, project_id, annotator_map=annotator_map
                    )
                    results["metadata"] = stats
                except Exception as exc:
                    results["metadata"] = {"error": str(exc)}

        return results

    def export_all_bundles(self, project_id: str, include: list[str]) -> bytes:
        """Build one bundle ZIP per annotator and wrap them in an outer ZIP.

        Each inner ZIP is named bundle_<annotator>_<today>.zip and contains only
        the videos assigned to that annotator (works correctly even when a camera
        is split across multiple annotators). Returns raw outer ZIP bytes.
        """
        today = date.today()
        annotators = self.get_all_annotators()

        # Query per-annotator video_ids directly from video_assignments
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT va.assigned_to, va.video_id
                    FROM video_assignments va
                    JOIN videos v ON v.video_id = va.video_id
                    WHERE v.project_id = :pid
                      AND NOT EXISTS (
                          SELECT 1 FROM video_labels WHERE video_id = v.video_id
                      )
                """),
                {"pid": project_id},
            ).fetchall()
        video_ids_by_annotator: dict[str, list[str]] = {}
        for assigned_to, video_id in rows:
            video_ids_by_annotator.setdefault(assigned_to, []).append(video_id)

        outer_buf = io.BytesIO()
        written = 0
        with zipfile.ZipFile(outer_buf, "w", compression=zipfile.ZIP_DEFLATED) as outer:
            for annotator in annotators:
                video_ids = video_ids_by_annotator.get(annotator, [])
                if not video_ids:
                    continue
                bundle_bytes = self.export_project_bundle(project_id, include, video_ids=video_ids)
                safe_name = annotator.replace(" ", "_")
                outer.writestr(f"bundle_{safe_name}_{today}.zip", bundle_bytes)
                written += 1
        if not written:
            return b""
        return outer_buf.getvalue()
