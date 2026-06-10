from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import pandas as pd
from sqlalchemy import text

from review_app.backend.errors import DataImportError, SpeciesError
from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)


@dataclass
class SpeciesCatalog:
    """All species lookup maps, keyed by scientific name."""

    display: dict[str, str]  # project-scoped: scientific → display name
    groups: dict[str, str | None]  # project-scoped: scientific → group
    global_display: dict[str, str]  # global: scientific → display name
    inat: dict[str, str]  # global: scientific → iNaturalist URL


class SpeciesMixin(ProviderBase):
    """Species and behavior loading and queries. Requires self.engine."""

    _SPECIES_BASE_COLS = frozenset(
        {
            "scientific_name",
            "english_name",
            "french_name",
            "group_fr",
            "group_en",
            "IUCN",
            "inaturalist",
        }
    )

    @classmethod
    def _parse_species_csv(cls, path: Path | IO[str]) -> list[dict[str, Any]]:
        df = pd.read_csv(path, sep=";")
        if "scientific_name" not in df.columns:
            raise SpeciesError(
                user_message_key="species_error_csv_format",
                detail=f"Species CSV at `{path}` must have a `scientific_name` column.",
            )
        # Any column not in the base set is treated as a collection membership flag (y = present).
        collection_cols = [c for c in df.columns if c not in cls._SPECIES_BASE_COLS]
        rows = []
        for _, row in df.iterrows():
            sci = str(row.get("scientific_name", "") or "").strip()
            if not sci or sci.lower() in ("na", "nan", "none", ""):
                continue
            collections = {
                col: str(row.get(col, "") or "").strip().lower() == "y" for col in collection_cols
            }
            # Blank cells come back as NaN (which is truthy), so check explicitly.
            inat_raw = row.get("inaturalist") if "inaturalist" in df.columns else None
            inaturalist_url = None if pd.isna(inat_raw) else (str(inat_raw).strip() or None)
            rows.append(
                {
                    "scientific_name": sci,
                    "name_en": (str(row.get("english_name", "") or "").strip() or None)
                    if "english_name" in df.columns
                    else None,
                    "name_fr": (str(row.get("french_name", "") or "").strip() or None)
                    if "french_name" in df.columns
                    else None,
                    "group_fr": (str(row.get("group_fr", "") or "").strip() or None)
                    if "group_fr" in df.columns
                    else None,
                    "group_en": (str(row.get("group_en", "") or "").strip() or None)
                    if "group_en" in df.columns
                    else None,
                    "iucn": (str(row.get("IUCN", "") or "").strip() or None)
                    if "IUCN" in df.columns
                    else None,
                    "inaturalist_url": inaturalist_url,
                    "collections": collections,
                }
            )
        return rows

    def _load_species_data(self) -> None:
        from review_app.app.config import get_bundled_species_csv

        bundled_path = get_bundled_species_csv()
        if not bundled_path:
            raise SpeciesError(
                user_message_key="species_error_csv_not_found",
                detail="Bundled species CSV not found.",
            )
        rows = self._parse_species_csv(Path(bundled_path))
        if not rows:
            raise SpeciesError(
                user_message_key="species_error_csv_empty",
                detail="Bundled species CSV is empty or missing a scientific_name column.",
            )
        logger.debug("Loaded %d species from bundled CSV", len(rows))

        with self.engine.begin() as conn:
            # Preserve existing IDs so FK references in individual_observations remain valid.
            existing = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
            }
            for row in rows:
                row["id"] = existing.get(row["scientific_name"]) or str(uuid.uuid4())

            conn.execute(
                text(
                    """
                    INSERT INTO species (id, scientific_name, name_en, name_fr, group_en, group_fr, iucn, inaturalist_url, is_custom)
                    VALUES (:id, :scientific_name, :name_en, :name_fr, :group_en, :group_fr, :iucn, :inaturalist_url, 0)
                    ON CONFLICT(scientific_name) DO UPDATE SET
                        name_en  = excluded.name_en,
                        name_fr  = excluded.name_fr,
                        group_en = excluded.group_en,
                        group_fr = excluded.group_fr,
                        iucn     = excluded.iucn,
                        inaturalist_url = excluded.inaturalist_url
                    """
                ),
                rows,
            )
            # Sync collections first so their FK references are cleared before
            # we delete species that are no longer in the CSV.
            self._sync_bundled_collections(conn, rows)
            # Remove species no longer in the CSV that have no remaining FK references.
            placeholders = ", ".join(f":sn{i}" for i in range(len(rows)))
            params = {f"sn{i}": r["scientific_name"] for i, r in enumerate(rows)}
            conn.execute(
                text(
                    f"""
                    DELETE FROM species
                    WHERE scientific_name NOT IN ({placeholders})
                    AND is_custom = 0
                    AND id NOT IN (SELECT DISTINCT species_id FROM individual_observations WHERE species_id IS NOT NULL)
                    AND id NOT IN (SELECT DISTINCT species_id FROM project_species WHERE species_id IS NOT NULL)
                    AND id NOT IN (SELECT DISTINCT species_id FROM species_collection_members WHERE species_id IS NOT NULL)
                    """
                ),
                params,
            )
        self._resync_projects_for_bundled_collections()

    @staticmethod
    def _sync_bundled_collections(conn, rows: list[dict[str, Any]]) -> None:
        """Create/update bundled collections from parsed CSV rows. Idempotent."""
        all_col_names: set[str] = set()
        for row in rows:
            all_col_names.update(row.get("collections", {}).keys())
        if not all_col_names:
            logger.debug("No collection columns found in bundled CSV — skipping collection sync")
            return

        # Ensure a species_collections row exists for each collection name.
        existing_colls = {
            r[0]: r[1]
            for r in conn.execute(text("SELECT name, id FROM species_collections")).fetchall()
        }
        for name in all_col_names:
            if name not in existing_colls:
                coll_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO species_collections (id, name, is_custom) VALUES (:id, :name, 0)"
                    ),
                    {"id": coll_id, "name": name},
                )
                existing_colls[name] = coll_id

        # Rebuild membership for all bundled collections.
        bundled_coll_ids = [existing_colls[n] for n in all_col_names if n in existing_colls]
        if bundled_coll_ids:
            ids_ph = ", ".join(f":cid{i}" for i in range(len(bundled_coll_ids)))
            id_params = {f"cid{i}": cid for i, cid in enumerate(bundled_coll_ids)}
            conn.execute(
                text(f"DELETE FROM species_collection_members WHERE collection_id IN ({ids_ph})"),
                id_params,
            )

        species_map = {
            r[0]: r[1]
            for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
        }
        to_insert = []
        for row in rows:
            sp_id = species_map.get(row["scientific_name"])
            if not sp_id:
                continue
            for col_name, present in row.get("collections", {}).items():
                if present:
                    coll_id = existing_colls.get(col_name)
                    if coll_id:
                        to_insert.append({"collection_id": coll_id, "species_id": sp_id})
        if to_insert:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO species_collection_members (collection_id, species_id)"
                    " VALUES (:collection_id, :species_id)"
                ),
                to_insert,
            )
        logger.debug(
            "Synced %d bundled collection(s): %s",
            len(all_col_names),
            ", ".join(sorted(all_col_names)),
        )

    def _resync_projects_for_bundled_collections(self) -> None:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT p.id, p.collection_id
                    FROM projects p
                    JOIN species_collections sc ON sc.id = p.collection_id
                    WHERE p.collection_id IS NOT NULL AND sc.is_custom = 0
                    """
                )
            ).fetchall()
        for project_id, collection_id in rows:
            self.set_project_collection(project_id, collection_id)
            logger.debug(
                "Re-synced project_species for project %s from bundled collection %s",
                project_id,
                collection_id,
            )

    def list_collections(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, name, is_custom FROM species_collections ORDER BY name")
            ).fetchall()
        return [{"id": r[0], "name": r[1], "is_custom": bool(r[2])} for r in rows]

    def get_project_collection(self, project_id: str) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT collection_id FROM projects WHERE id = :pid"),
                {"pid": project_id},
            ).fetchone()
        return row[0] if row else None

    def set_project_collection(self, project_id: str, collection_id: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE projects SET collection_id = :cid WHERE id = :pid"),
                {"cid": collection_id, "pid": project_id},
            )
        if collection_id is not None:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT s.scientific_name FROM species s
                        JOIN species_collection_members m ON m.species_id = s.id
                        WHERE m.collection_id = :cid
                        ORDER BY s.scientific_name
                        """
                    ),
                    {"cid": collection_id},
                ).fetchall()
            names = [r[0] for r in rows]
            self.set_project_species(project_id, names)
            logger.info(
                "Project %s: applied collection %s → %d species",
                project_id,
                collection_id,
                len(names),
            )
        else:
            logger.info("Project %s: collection cleared", project_id)

    def import_collection_from_csv(self, name: str, content: str) -> int:
        """Parse a species CSV, upsert species, and create/replace a custom collection."""
        import io

        rows = self._parse_species_csv(io.StringIO(content))
        if not rows:
            raise DataImportError(
                user_message_key="csv_error_no_valid_rows",
                detail="No valid rows found. Ensure the CSV uses ';' as separator and has a 'scientific_name' column.",
            )
        for row in rows:
            self._upsert_species(row)

        with self.engine.begin() as conn:
            existing = conn.execute(
                text("SELECT id FROM species_collections WHERE name = :name"), {"name": name}
            ).fetchone()
            if existing:
                coll_id = existing[0]
                conn.execute(
                    text("DELETE FROM species_collection_members WHERE collection_id = :cid"),
                    {"cid": coll_id},
                )
                conn.execute(
                    text("UPDATE species_collections SET is_custom = 1 WHERE id = :cid"),
                    {"cid": coll_id},
                )
            else:
                coll_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO species_collections (id, name, is_custom) VALUES (:id, :name, 1)"
                    ),
                    {"id": coll_id, "name": name},
                )
            species_map = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
            }
            members = [
                {"collection_id": coll_id, "species_id": species_map[r["scientific_name"]]}
                for r in rows
                if r["scientific_name"] in species_map
            ]
            if members:
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO species_collection_members (collection_id, species_id)"
                        " VALUES (:collection_id, :species_id)"
                    ),
                    members,
                )
        logger.info("Imported custom collection %r with %d species", name, len(members))
        return len(members)

    @staticmethod
    def _parse_behaviors_csv(path: Path | IO[str]) -> list[dict[str, Any]]:
        try:
            df = pd.read_csv(path, sep=";")
            if "scientific_name" not in df.columns or "key" not in df.columns:
                return []
            rows = []
            for _, row in df.iterrows():
                sci = str(row["scientific_name"]).strip()
                key = str(row["key"]).strip()
                name_en = str(row.get("name_en", "") or "").strip() or key
                name_fr = str(row.get("name_fr", "") or "").strip() or None
                if sci and key:
                    rows.append(
                        {
                            "scientific_name": sci,
                            "key": key,
                            "name_en": name_en,
                            "name_fr": name_fr,
                        }
                    )
            return rows
        except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
            logger.error("Failed to parse behaviors CSV %s: %s", path, exc)
            return []

    def _load_species_behaviors(self) -> None:
        from review_app.app.config import get_bundled_behaviors_csv

        bundled_path = get_bundled_behaviors_csv()
        csv_rows = self._parse_behaviors_csv(Path(bundled_path)) if bundled_path else []

        behavior_meta: dict[str, dict[str, str | None]] = {
            row["key"]: {"name_en": row["name_en"], "name_fr": row["name_fr"]} for row in csv_rows
        }

        with self.engine.begin() as conn:
            existing_beh = {
                r[0]: r[1] for r in conn.execute(text("SELECT key, id FROM behaviors")).fetchall()
            }
            behavior_rows = [
                {
                    "id": existing_beh.get(key) or str(uuid.uuid4()),
                    "key": key,
                    "name_en": meta["name_en"],
                    "name_fr": meta["name_fr"],
                    "is_custom": 0,
                }
                for key, meta in behavior_meta.items()
            ]
            if behavior_rows:
                conn.execute(
                    text(
                        """
                        INSERT INTO behaviors (id, key, name_en, name_fr, is_custom)
                        VALUES (:id, :key, :name_en, :name_fr, :is_custom)
                        ON CONFLICT(key) DO UPDATE SET
                            name_en = excluded.name_en,
                            name_fr = excluded.name_fr
                        """
                    ),
                    behavior_rows,
                )

    def _build_species_variant_map(self) -> dict[str, str]:
        """Return {lowercase_variant -> scientific_name} for all species names/aliases.
        Underscores are normalised to spaces so e.g. 'common_warthog' key becomes 'common warthog'."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT scientific_name, name_en, name_fr FROM species")
            ).fetchall()
        variant_to_sci: dict[str, str] = {}
        for sci, name_en, name_fr in rows:
            variant_to_sci[sci.lower().replace("_", " ")] = sci
            if name_en:
                variant_to_sci[name_en.lower()] = sci
            if name_fr:
                variant_to_sci[name_fr.lower()] = sci
        return variant_to_sci

    def _validate_species_fuzzy(
        self, value_text: str, variant_map: dict[str, str] | None = None
    ) -> tuple[bool, str | None]:
        from thefuzz import fuzz, process

        if not value_text:
            return False, None

        value_lower = str(value_text).strip().lower().replace("_", " ")
        variant_to_sci = (
            variant_map if variant_map is not None else self._build_species_variant_map()
        )

        if value_lower in variant_to_sci:
            return True, variant_to_sci[value_lower]

        candidates = list(variant_to_sci.keys())
        if not candidates:
            return False, None
        # token_sort_ratio compares whole-word tokens, preventing short inputs like 'car'
        # from matching longer candidates like 'caracal aurata' via substring.
        match, score = process.extractOne(value_lower, candidates, scorer=fuzz.token_sort_ratio)
        if score >= 85:
            return True, variant_to_sci[match]

        return False, None

    def get_valid_species(self, project_id: str | None = None) -> list[str]:
        with self.engine.connect() as conn:
            if project_id:
                # Check if project has specific species configured
                has_proj_sp = conn.execute(
                    text("SELECT 1 FROM project_species WHERE project_id = :pid LIMIT 1"),
                    {"pid": project_id},
                ).fetchone()
                if has_proj_sp:
                    rows = conn.execute(
                        text(
                            """
                            SELECT s.scientific_name FROM species s
                            JOIN project_species ps ON ps.species_id = s.id
                            WHERE ps.project_id = :pid
                            ORDER BY s.scientific_name
                            """
                        ),
                        {"pid": project_id},
                    ).fetchall()
                    return [r[0] for r in rows]

            rows = conn.execute(
                text("SELECT scientific_name FROM species ORDER BY scientific_name")
            ).fetchall()
        return [r[0] for r in rows]

    def get_species_display_map(
        self, lang: str = "en", project_id: str | None = None
    ) -> dict[str, str]:
        col = {"en": "name_en", "fr": "name_fr"}.get(lang, "name_en")
        with self.engine.connect() as conn:
            if project_id:
                has_proj_sp = conn.execute(
                    text("SELECT 1 FROM project_species WHERE project_id = :pid LIMIT 1"),
                    {"pid": project_id},
                ).fetchone()
                if has_proj_sp:
                    rows = conn.execute(
                        text(
                            f"""
                            SELECT s.scientific_name, s.{col} FROM species s
                            JOIN project_species ps ON ps.species_id = s.id
                            WHERE ps.project_id = :pid
                            ORDER BY s.scientific_name
                            """
                        ),
                        {"pid": project_id},
                    ).fetchall()
                    return {sci: f"{name} ({sci})" if name else sci for sci, name in rows}

            rows = conn.execute(
                text(f"SELECT scientific_name, {col} FROM species ORDER BY scientific_name")
            ).fetchall()
        return {sci: f"{name} ({sci})" if name else sci for sci, name in rows}

    def get_species_catalog(
        self, lang: str = "en", project_id: str | None = None
    ) -> SpeciesCatalog:
        """Load all species lookup maps in a single query.

        The project-scoped maps fall back to all species when the project has
        no species of its own configured (matching get_species_display_map).
        """
        name_col = {"en": "name_en", "fr": "name_fr"}.get(lang, "name_en")
        group_col = "group_en" if lang != "fr" else "group_fr"
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT s.scientific_name, s.{name_col}, s.{group_col},
                           s.inaturalist_url, ps.species_id IS NOT NULL
                    FROM species s
                    LEFT JOIN project_species ps
                        ON ps.species_id = s.id AND ps.project_id = :pid
                    ORDER BY s.scientific_name
                    """
                ),
                {"pid": project_id},
            ).fetchall()

        has_project_scope = any(in_project for *_, in_project in rows)
        catalog = SpeciesCatalog(display={}, groups={}, global_display={}, inat={})
        for sci, name, group, inat_url, in_project in rows:
            label = f"{name} ({sci})" if name else sci
            catalog.global_display[sci] = label
            if inat_url:
                catalog.inat[sci] = inat_url
            if in_project or not has_project_scope:
                catalog.display[sci] = label
                catalog.groups[sci] = group
        return catalog

    def get_all_behaviors(self) -> list[dict[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT key, name_en, name_fr FROM behaviors ORDER BY key")
            ).fetchall()
        return [{"key": r[0], "name_en": r[1], "name_fr": r[2]} for r in rows]

    def add_custom_behavior(self, key: str, name_en: str, name_fr: str | None = None) -> bool:
        if self.behavior_exists(key):
            return False
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO behaviors (id, key, name_en, name_fr, is_custom)
                    VALUES (:id, :key, :name_en, :name_fr, 1)
                    """
                ),
                {"id": str(uuid.uuid4()), "key": key, "name_en": name_en, "name_fr": name_fr},
            )
        return True

    def get_behavior_display_map(self, lang: str = "en", **_kwargs) -> dict[str, str]:
        col = {"en": "name_en", "fr": "name_fr"}.get(lang, "name_en")
        with self.engine.connect() as conn:
            rows = conn.execute(text(f"SELECT key, {col} FROM behaviors ORDER BY key")).fetchall()
        return {key: name or key for key, name in rows}

    def get_project_species(self, project_id: str) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT s.scientific_name FROM species s
                    JOIN project_species ps ON ps.species_id = s.id
                    WHERE ps.project_id = :pid
                    """
                ),
                {"pid": project_id},
            ).fetchall()
        return [r[0] for r in rows]

    def set_project_species(self, project_id: str, species_names: list[str]) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM project_species WHERE project_id = :pid"),
                {"pid": project_id},
            )
            if species_names:
                placeholders = ", ".join(f":n{i}" for i in range(len(species_names)))
                params = {f"n{i}": name for i, name in enumerate(species_names)}
                params["pid"] = project_id
                conn.execute(
                    text(
                        f"""
                        INSERT INTO project_species (project_id, species_id)
                        SELECT :pid, id FROM species WHERE scientific_name IN ({placeholders})
                        """
                    ),
                    params,
                )

    def species_exists(self, scientific_name: str) -> bool:
        with self.engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM species WHERE scientific_name = :s"), {"s": scientific_name}
            ).fetchone()
        return exists is not None

    def behavior_exists(self, key: str) -> bool:
        with self.engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM behaviors WHERE key = :k"), {"k": key}
            ).fetchone()
        return exists is not None

    def get_existing_groups(self) -> dict[str, list[str]]:
        with self.engine.connect() as conn:
            en_rows = conn.execute(
                text("SELECT DISTINCT group_en FROM species WHERE group_en IS NOT NULL")
            ).fetchall()
            fr_rows = conn.execute(
                text("SELECT DISTINCT group_fr FROM species WHERE group_fr IS NOT NULL")
            ).fetchall()
        return {
            "en": [r[0] for r in en_rows],
            "fr": [r[0] for r in fr_rows],
        }

    def get_existing_iucn(self) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT iucn FROM species WHERE iucn IS NOT NULL")
            ).fetchall()
        return [r[0] for r in rows]

    def add_custom_species(
        self,
        scientific_name: str,
        name_en: str,
        name_fr: str,
        group_en: str,
        group_fr: str,
        iucn: str | None = None,
    ) -> bool:
        if self.species_exists(scientific_name):
            return False

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO species (id, scientific_name, name_en, name_fr, group_en, group_fr, iucn, is_custom)
                    VALUES (:id, :scientific_name, :name_en, :name_fr, :group_en, :group_fr, :iucn, 1)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "scientific_name": scientific_name,
                    "name_en": name_en,
                    "name_fr": name_fr,
                    "group_en": group_en,
                    "group_fr": group_fr,
                    "iucn": iucn,
                },
            )
        logger.info("Added custom species %r", scientific_name)
        return True

    def _upsert_species(self, row: dict[str, Any]) -> None:
        """Insert or update a species from a user-supplied CSV. Marks as custom so the
        bundled-data reload on startup cannot overwrite it."""
        sci = row["scientific_name"]
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO species (id, scientific_name, name_en, name_fr, group_en, group_fr, iucn, is_custom)
                    VALUES (:id, :sci, :name_en, :name_fr, :group_en, :group_fr, :iucn, 1)
                    ON CONFLICT(scientific_name) DO UPDATE SET
                        name_en   = COALESCE(excluded.name_en,  species.name_en),
                        name_fr   = COALESCE(excluded.name_fr,  species.name_fr),
                        group_en  = COALESCE(excluded.group_en, species.group_en),
                        group_fr  = COALESCE(excluded.group_fr, species.group_fr),
                        iucn      = COALESCE(excluded.iucn,     species.iucn),
                        is_custom = 1
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "sci": sci,
                    "name_en": row["name_en"] or sci,
                    "name_fr": row["name_fr"] or sci,
                    "group_en": row["group_en"] or "",
                    "group_fr": row["group_fr"] or "",
                    "iucn": row["iucn"],
                },
            )

    def import_project_species_from_csv(self, project_id: str, content: str) -> int:
        """Parse species CSV content, upsert non-custom species, and overwrite
        the project's species list. Returns the number of species set."""
        import io

        rows = self._parse_species_csv(io.StringIO(content))
        if not rows:
            raise DataImportError(
                user_message_key="csv_error_no_valid_rows",
                detail="No valid rows found. Ensure the CSV uses ';' as separator and has a 'scientific_name' column.",
            )

        for row in rows:
            self._upsert_species(row)

        names = [r["scientific_name"] for r in rows]
        self.set_project_species(project_id, names)
        logger.info("Set %d project species for project %s from CSV", len(names), project_id)
        return len(names)

    def import_project_behaviors_from_csv(self, project_id: str, content: str) -> int:
        """Parse behaviors CSV content and add any unknown behaviors as custom global behaviors.
        Returns the number of new behaviors added."""
        import io

        rows = self._parse_behaviors_csv(io.StringIO(content))
        if not rows:
            raise DataImportError(
                user_message_key="csv_error_no_valid_rows",
                detail="No valid rows found. Ensure the CSV uses ';' as separator and has 'scientific_name' and 'key' columns.",
            )

        added = 0
        for row in rows:
            if not self.behavior_exists(row["key"]):
                self.add_custom_behavior(row["key"], row["name_en"], row["name_fr"])
                added += 1
        logger.info("Added %d custom behaviors from CSV for project %s", added, project_id)
        return added

    def export_project_species_csv(self, project_id: str) -> str:
        """Export the project's species list as a semicolon-separated CSV string."""
        import csv
        import io

        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT s.scientific_name, s.name_en, s.name_fr,
                           s.group_en, s.group_fr, s.iucn
                    FROM species s
                    JOIN project_species ps ON ps.species_id = s.id
                    WHERE ps.project_id = :pid
                    ORDER BY s.scientific_name
                """),
                {"pid": project_id},
            ).fetchall()

        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(
            ["scientific_name", "english_name", "french_name", "group_en", "group_fr", "iucn"]
        )
        for r in rows:
            writer.writerow(
                [r[0] or "", r[1] or "", r[2] or "", r[3] or "", r[4] or "", r[5] or ""]
            )
        return buf.getvalue()
