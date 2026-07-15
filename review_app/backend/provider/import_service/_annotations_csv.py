"""Manual-annotation CSV round trip: export, validation (dry-run diff), and import."""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any

import pandas as pd
from sqlalchemy import text

from review_app.backend.provider.import_service._shared import IGNORE_SENTINEL, ImportSharedMixin

logger = logging.getLogger(__name__)


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


class AnnotationsCsvMixin(ImportSharedMixin):
    """Export and (re-)import of the full manual annotations CSV."""

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
                        (
                            SELECT GROUP_CONCAT(b2.key)
                            FROM observation_tags ot
                            JOIN behaviors b2 ON b2.id = ot.behavior_id
                            WHERE ot.video_id = io.video_id AND ot.observation_id = io.id
                        )                         AS attributes,
                        io.count,
                        io.start_sec,
                        io.end_sec
                    FROM videos v
                    LEFT JOIN projects p ON p.id = v.project_id
                    LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    LEFT JOIN video_assignments va ON va.video_id = v.video_id
                    LEFT JOIN individual_observations io ON io.video_id = v.video_id
                    LEFT JOIN species s ON s.id = io.species_id
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

        # Format float columns as fixed-decimal strings so that to_csv() writes them
        # quoted. Without this, LibreOffice with European locale settings misreads "60.085"
        # as the integer 60085 (treating "." as a thousands separator).
        for col in ("duration_sec", "start_sec", "end_sec"):
            if col in base_df.columns:
                base_df[col] = base_df[col].apply(lambda v: f"{v:.3f}" if pd.notna(v) else "")

        return base_df

    def import_annotations_csv(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        mode: str = "override",
        species_mappings: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        _t0 = time.monotonic()
        self._safety_backup()
        logger.info(
            "Importing annotations CSV: %d rows (project=%s, mode=%s), backup took %.1fs",
            len(df),
            active_project_id,
            mode,
            time.monotonic() - _t0,
        )
        df, has_path, known_ids = self._resolve_annotation_video_ids(df, active_project_id)
        map_species = self._build_annotation_species_mapper(
            df, active_project_id, species_mappings
        )

        imported = 0
        skipped: list[str] = []
        skipped_observations = 0
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
                    sp = map_species(sp)
                    if sp is None:
                        skipped_observations += 1
                        continue
                    beh_raw = row.get("attributes") or row.get("behavior") or ""
                    tags_list = [
                        t.strip()
                        for t in str(beh_raw).split(",")
                        if t.strip() and t.strip() not in ("unlabeled", "does_not_react")
                    ]
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
                            "tags": tags_list,
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

            # Restore review_later — reset to False too so override is complete
            rl_raw = first.get("review_later") if "review_later" in group.columns else None
            if pd.notna(rl_raw):
                self.set_review_later(str(video_id), bool(int(rl_raw)))

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
            "Annotations CSV import complete in %.1fs: imported=%d skipped=%d skipped_obs=%d",
            time.monotonic() - _t0,
            imported,
            len(skipped),
            skipped_observations,
        )
        return {
            "imported": imported,
            "skipped": skipped,
            "skipped_observations": skipped_observations,
            "by_annotator": dict(observations_by_annotator),
            "custom_tags": len(all_custom_keys),
        }

    def _build_annotation_species_mapper(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        species_mappings: dict[str, str] | None,
    ):
        """Resolve incoming species against the project, attaching explicitly-mapped
        "create as new" targets to the project so they import as-is.

        Returns a callable ``map_species(name) -> resolved_name | None`` where ``None``
        means the observation should be skipped (mapped to ignore, or awaiting a mapping
        decision). A species that is neither already configured nor given an explicit
        mapping is skipped — matching the dry-run in ``validate_annotations_csv``.
        """
        mappings = species_mappings or {}

        # Without a project scope every global species is valid — import as-is,
        # honouring explicit mappings/ignore but never skipping as "unconfigured".
        if not active_project_id:

            def _target_global(sp: str) -> str | None:
                mapped = mappings.get(sp, sp) or sp
                return None if mapped == IGNORE_SENTINEL else mapped

            return _target_global

        valid = set(self.get_valid_species(active_project_id))

        def _target(sp: str) -> str | None:
            """Mapped target if importable, else None (ignored or awaiting a decision).

            Mirrors validate_annotations_csv.resolve_species so the dry-run preview and
            the real import agree on which species get written. An unmapped species that
            isn't already configured is skipped — it is NOT silently auto-created.
            """
            if sp in valid:
                return sp
            raw = mappings.get(sp)
            if not raw or raw == IGNORE_SENTINEL:
                return None
            return raw

        targets = {
            t
            for sp in (str(s).strip() for s in df.get("species", pd.Series(dtype=str)).dropna())
            if sp and (t := _target(sp)) is not None
        }
        existing_project_species = self.get_project_species(active_project_id)
        to_add = sorted(t for t in targets if t not in valid)
        if to_add:
            logger.info(
                "Annotations CSV: adding %d species to project %s: %s",
                len(to_add),
                active_project_id,
                to_add,
            )
            # Create the ones that aren't in the global catalog yet (the "create as a
            # new species" path), then attach all of them to the active project.
            for name in to_add:
                if not self.species_exists(name):
                    self.add_custom_species(
                        name, name_en=name, name_fr="", group_en="", group_fr=""
                    )
            # Only when the project has an explicit species list — an empty list means
            # the project implicitly allows every species, so don't narrow it to these.
            if existing_project_species:
                self.set_project_species(active_project_id, existing_project_species + to_add)
            valid |= set(to_add)

        def map_species(sp: str) -> str | None:
            target = _target(sp)
            return target if target in valid else None

        return map_species

    def validate_annotations_csv(
        self,
        df: pd.DataFrame,
        active_project_id: str | None,
        mode: str = "override",
        species_mappings: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Dry-run of import_annotations_csv: resolves paths and diffs observations without writing."""
        _t0 = time.monotonic()
        df, has_path, known_ids = self._resolve_annotation_video_ids(df, active_project_id)
        mappings = species_mappings or {}

        matched = 0
        skipped: list[str] = []
        obs_to_insert = 0
        obs_to_change = 0
        obs_unchanged = 0
        obs_to_delete = 0
        blanks_to_set = 0
        blanks_already_set = 0
        append = mode == "append"

        with self.engine.connect() as conn:
            existing_obs_rows = conn.execute(
                text(
                    "SELECT video_id, id, species_id, count, start_sec, end_sec"
                    " FROM individual_observations WHERE project_id = :pid"
                    if active_project_id
                    else "SELECT video_id, id, species_id, count, start_sec, end_sec"
                    " FROM individual_observations"
                ),
                {"pid": active_project_id} if active_project_id else {},
            ).fetchall()
            species_id_map: dict[str, str] = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
            }
            already_blank: set[str] = {
                r[0]
                for r in conn.execute(
                    text("SELECT video_id FROM video_labels WHERE is_blank = 1")
                ).fetchall()
            }

        # (video_id, obs_id) -> (species_id, count, start_sec, end_sec)
        existing_obs_data: dict[tuple[str, int], tuple] = {}
        existing_obs_by_video: dict[str, set[int]] = {}
        for vid, oid, sp_id, cnt, start, end in existing_obs_rows:
            existing_obs_by_video.setdefault(vid, set()).add(oid)
            existing_obs_data[(vid, oid)] = (sp_id, cnt, start, end)

        # Classify each incoming species against the project: configured species
        # import directly; non-configured ones either map to a configured species,
        # get added to the project ("create as new"), are ignored, or stay pending.
        valid_species = set(self.get_valid_species(active_project_id))
        unknown_species: set[str] = set()
        species_to_add: set[str] = set()

        def resolve_species(sp: str) -> str | None:
            """Mapped target if importable, else None (ignored / pending)."""
            if sp in valid_species:
                return sp
            target = mappings.get(sp, "")
            if target == IGNORE_SENTINEL:
                return None
            if not target:
                unknown_species.add(sp)  # awaiting a mapping decision
                return None
            if target in valid_species:
                return target
            # Non-configured target: added to the project on import, creating it in the
            # global catalog first if it isn't there yet ("create as a new species").
            species_to_add.add(target)
            return target

        for video_id, group in df.groupby("video_id", sort=False, dropna=False):
            if pd.isna(video_id) or video_id not in known_ids:
                label = group["video_path"].iloc[0] if has_path else str(video_id)
                skipped.append(str(label))
                continue

            matched += 1
            existing_ids = existing_obs_by_video.get(str(video_id), set())
            first = group.iloc[0]
            is_blank_raw = first["is_blank"]
            is_blank = bool(int(is_blank_raw)) if pd.notna(is_blank_raw) else None

            if is_blank:
                if str(video_id) in already_blank:
                    blanks_already_set += 1
                else:
                    blanks_to_set += 1
                if not append and existing_ids:
                    obs_to_delete += len(existing_ids)
                continue

            incoming_ids: set[int] = set()
            for _, row in group.iterrows():
                sp_raw = row.get("species")
                sp = str(sp_raw).strip() if pd.notna(sp_raw) else ""
                if not sp:
                    continue
                resolved = resolve_species(sp)
                if resolved is None:
                    continue  # ignored or pending — not counted in the diff
                sp = resolved
                obs_id_raw = row.get("observation_id")
                obs_id = int(obs_id_raw) if pd.notna(obs_id_raw) and not append else None
                if obs_id and obs_id in existing_ids:
                    incoming_ids.add(obs_id)
                    existing = existing_obs_data.get((str(video_id), obs_id))
                    if existing is not None:
                        ex_sp, ex_cnt, ex_start, ex_end = existing
                        incoming_sp_id = species_id_map.get(sp)
                        count_raw = row.get("count")
                        incoming_cnt = int(count_raw) if pd.notna(count_raw) else None
                        start_raw = pd.to_numeric(row.get("start_sec"), errors="coerce")
                        incoming_start = 0.0 if pd.isna(start_raw) else float(start_raw)
                        end_raw = pd.to_numeric(row.get("end_sec"), errors="coerce")
                        incoming_end = None if pd.isna(end_raw) else float(end_raw)
                        changed = (
                            incoming_sp_id != ex_sp
                            or incoming_cnt != ex_cnt
                            or abs(incoming_start - (ex_start or 0.0)) > 0.001
                            or incoming_end != ex_end
                        )
                        if changed:
                            obs_to_change += 1
                        else:
                            obs_unchanged += 1
                    else:
                        obs_to_change += 1
                else:
                    obs_to_insert += 1

            if not append:
                obs_to_delete += len(existing_ids - incoming_ids)

        logger.info(
            "Validated annotations CSV in %.1fs: %d matched, %d skipped, %d unknown species",
            time.monotonic() - _t0,
            matched,
            len(skipped),
            len(unknown_species),
        )
        return {
            "matched": matched,
            "skipped": skipped,
            "blanks_to_set": blanks_to_set,
            "blanks_already_set": blanks_already_set,
            "obs_to_insert": obs_to_insert,
            "obs_to_change": obs_to_change,
            "obs_unchanged": obs_unchanged,
            "obs_to_delete": obs_to_delete,
            "unknown_species": sorted(unknown_species),
            "species_to_add": sorted(species_to_add),
        }
