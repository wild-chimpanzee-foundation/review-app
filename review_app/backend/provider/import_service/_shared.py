"""Helpers shared by the CSV import/export flows in this package."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.backend.errors import DataImportError
from review_app.backend.path_matching import (
    VideoPathLookup,
    build_video_path_lookup,
    resolve_video_path,
)
from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)

BLANK_SENTINEL = "__blank__"
IGNORE_SENTINEL = "__ignore__"
FALSY_STRINGS = {"", "0", "false", "False", "nan", "none", "None", "no"}
BLANK_SPECIES = {"Vide", "Video vide", "Indetermine", "Espece indeterminee", "NA", "nan", ""}


class ImportSharedMixin(ProviderBase):
    """Lookups and validation shared by all import flows."""

    @staticmethod
    def _safety_backup() -> None:
        """Best-effort backup before imports that overwrite existing data.
        Never raises — an import must not fail because the backup did."""
        from review_app.backend.db.backup import backup_if_stale

        backup_if_stale(reason="pre_import")

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

    def _build_video_path_lookup(self, active_project_id: str | None) -> VideoPathLookup:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT video_id, video_path, camera_id FROM videos"
                    + (" WHERE project_id = :pid" if active_project_id else "")
                ),
                {"pid": active_project_id} if active_project_id else {},
            ).fetchall()
            scan_dirs: list[Path] = []
            if active_project_id:
                dir_rows = conn.execute(
                    text("SELECT path FROM project_dirs WHERE project_id = :pid"),
                    {"pid": active_project_id},
                ).fetchall()
                scan_dirs = [Path(r[0]) for r in dir_rows]

        return build_video_path_lookup(
            [(str(r[0]), str(r[1]), r[2]) for r in rows],
            scan_dirs,
        )

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

    def _resolve_annotation_video_ids(
        self, df: pd.DataFrame, active_project_id: str | None
    ) -> tuple[pd.DataFrame, bool, set[str]]:
        """Validate the annotation-CSV columns and resolve video_path → video_id.

        Exact path match first, then fuzzy suffix fallback for cross-machine sharing.
        Returns (df with a video_id column, has_path, known video_ids)."""
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

        known_ids = self._known_video_ids(active_project_id)
        if has_path and not has_id:
            path_to_id = {
                v.lower(): k for k, v in self._known_video_map(active_project_id).items()
            }
            lookup = self._build_video_path_lookup(active_project_id)
            df = df.copy()
            df["video_id"] = df["video_path"].map(
                lambda p: resolve_video_path(p, lookup, extra_suffix_map=path_to_id)[0]
            )
        return df, has_path, known_ids

    @staticmethod
    def _camera_video_sql_filter(
        params: dict[str, Any],
        camera_ids: list[str] | None,
        video_ids: list[str] | None,
    ) -> str:
        """SQL fragment restricting videos v to explicit video_ids or camera_ids.

        video_ids takes precedence over camera_ids; an empty list matches nothing;
        None means no restriction. Bind parameters are added to `params`."""
        if video_ids is not None:
            if not video_ids:
                return "AND 1=0"
            placeholders = ", ".join(f":v{i}" for i in range(len(video_ids)))
            for i, v in enumerate(video_ids):
                params[f"v{i}"] = v
            return f"AND v.video_id IN ({placeholders})"
        if camera_ids is None:
            return ""
        if not camera_ids:
            return "AND 1=0"
        placeholders = ", ".join(f":c{i}" for i in range(len(camera_ids)))
        for i, c in enumerate(camera_ids):
            params[f"c{i}"] = c
        return f"AND v.camera_id IN ({placeholders})"
