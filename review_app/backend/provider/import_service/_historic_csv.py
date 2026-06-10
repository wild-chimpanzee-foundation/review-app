"""Historic (legacy spreadsheet) CSV import: species, behaviors, counts, tags."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from review_app.backend.provider.import_service._shared import (
    BLANK_SENTINEL,
    BLANK_SPECIES,
    FALSY_STRINGS,
    ImportSharedMixin,
)

logger = logging.getLogger(__name__)


def _apply_historic_tags(
    provider, video_id: str, row: dict, tag_cols: list[str], append: bool
) -> None:
    """Create missing custom tags for truthy tag_cols values, then apply them to video_id."""
    if not tag_cols:
        return
    active_cols = [col for col in tag_cols if str(row.get(col, "")).strip() not in FALSY_STRINGS]
    if not active_cols:
        return
    # create_custom_tag is idempotent and returns the normalised key
    tag_keys = [provider.create_custom_tag(name_en=col) for col in active_cols]
    provider.set_video_tags(video_id, tag_keys, append=append)


class HistoricCsvMixin(ImportSharedMixin):
    """Validation and import of historic annotation spreadsheets."""

    def _filter_and_group_historic(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        folder_col: str,
        video_col: str,
        data_type_col: str,
        data_type_val: str = "",
        path_col: str = "",
    ) -> tuple[pd.DataFrame, int, dict[str, list[dict]], list[str]]:
        """Filter rows by data_type_col == data_type_val (when both are set), build path lookup, group matched rows by video_id.

        When path_col is set, use it as the full video path directly instead of constructing it
        from folder_col + video_col.
        """
        from review_app.backend.path_matching import resolve_video_path

        if data_type_col in df.columns and data_type_val:
            video_df = df[df[data_type_col].astype(str).str.strip() == data_type_val].copy()
            skipped_installation = len(df) - len(video_df)
        else:
            video_df = df.copy()
            skipped_installation = 0

        lookup = self._build_video_path_lookup(active_project_id)
        groups: dict[str, list[dict]] = {}
        skipped: list[str] = []

        use_single_path = bool(path_col) and path_col in video_df.columns

        for _, row in video_df.iterrows():
            if use_single_path:
                synthetic = str(row.get(path_col, "")).strip()
            else:
                folder = (
                    str(row.get(folder_col, "")).strip() if folder_col in video_df.columns else ""
                )
                video = (
                    str(row.get(video_col, "")).strip() if video_col in video_df.columns else ""
                )
                synthetic = f"{folder}/{video}" if folder else video
            video_id, _ = resolve_video_path(synthetic, lookup)
            if video_id is None:
                skipped.append(synthetic)
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
        path_col: str = "",
    ) -> dict[str, Any]:
        species_mappings = species_mappings or {}
        video_df, skipped_installation, groups, skipped = self._filter_and_group_historic(
            df,
            active_project_id,
            folder_col,
            video_col,
            data_type_col,
            data_type_val,
            path_col=path_col,
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
                    if not sp or sp in BLANK_SPECIES:
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
        path_col: str = "",
    ) -> dict[str, Any]:
        self._safety_backup()
        species_mappings = species_mappings or {}
        tag_cols = tag_cols or []
        _, _, groups, skipped = self._filter_and_group_historic(
            df,
            active_project_id,
            folder_col,
            video_col,
            data_type_col,
            data_type_val,
            path_col=path_col,
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
                and str(first.get(is_blank_col, "")).strip() not in FALSY_STRINGS
            )

            non_blank = (
                []
                if force_blank
                else [
                    r
                    for r in rows
                    if str(r.get(species_col, "")).strip() not in BLANK_SPECIES
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
