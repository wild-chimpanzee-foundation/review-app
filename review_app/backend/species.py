from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import text


class SpeciesMixin:
    """Species and behavior loading and queries. Requires self.engine."""

    @staticmethod
    def _parse_species_csv(path) -> list[dict]:
        df = pd.read_csv(path, sep=";")
        if "scientific_name" not in df.columns:
            raise ValueError(f"Species CSV at `{path}` must have a `scientific_name` column.")
        rows = []
        for _, row in df.iterrows():
            sci = str(row.get("scientific_name", "") or "").strip()
            if not sci or sci.lower() in ("na", "nan", "none", ""):
                continue
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
                }
            )
        return rows

    def _load_species_data(self) -> None:
        from review_app.app.config import get_bundled_species_csv

        bundled_path = get_bundled_species_csv()
        if not bundled_path:
            raise ValueError("Bundled species CSV not found.")
        rows = self._parse_species_csv(Path(bundled_path))
        if not rows:
            raise ValueError("Bundled species CSV is empty or missing a scientific_name column.")

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
                    INSERT INTO species (id, scientific_name, name_en, name_fr, group_en, group_fr, iucn, is_custom)
                    VALUES (:id, :scientific_name, :name_en, :name_fr, :group_en, :group_fr, :iucn, 0)
                    ON CONFLICT(scientific_name) DO UPDATE SET
                        name_en  = excluded.name_en,
                        name_fr  = excluded.name_fr,
                        group_en = excluded.group_en,
                        group_fr = excluded.group_fr,
                        iucn     = excluded.iucn
                    """
                ),
                rows,
            )
            # Remove species no longer in the CSV that have no observations.
            placeholders = ", ".join(f":sn{i}" for i in range(len(rows)))
            params = {f"sn{i}": r["scientific_name"] for i, r in enumerate(rows)}
            conn.execute(
                text(
                    f"""
                    DELETE FROM species
                    WHERE scientific_name NOT IN ({placeholders})
                    AND is_custom = 0
                    AND id NOT IN (SELECT DISTINCT species_id FROM individual_observations WHERE species_id IS NOT NULL)
                    """
                ),
                params,
            )

    @staticmethod
    def _parse_behaviors_csv(path) -> list[dict]:
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
                        {"scientific_name": sci, "key": key, "name_en": name_en, "name_fr": name_fr}
                    )
            return rows
        except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
            print(f"Failed to parse behaviors CSV {path}: {exc}")
            return []

    def _load_species_behaviors(self) -> None:
        from review_app.app.config import get_bundled_behaviors_csv

        bundled_path = get_bundled_behaviors_csv()
        csv_rows = self._parse_behaviors_csv(Path(bundled_path)) if bundled_path else []

        global_rows = [r for r in csv_rows if r["scientific_name"] == "*"]
        specific_rows = [r for r in csv_rows if r["scientific_name"] != "*"]

        all_species_behaviors = global_rows

        behavior_meta: dict[str, dict[str, str | None]] = {
            row["key"]: {"name_en": row["name_en"], "name_fr": row["name_fr"]}
            for row in csv_rows
        }

        with self.engine.begin() as conn:
            # Upsert behaviors — preserve existing IDs.
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

            # Insert or update all bundled species-behavior mappings.
            # Then remove stale bundled mappings (for non-custom species)
            # that are no longer in the bundled data.
            species_map = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT scientific_name, id FROM species")).fetchall()
            }
            behavior_id_map = {
                r[0]: r[1] for r in conn.execute(text("SELECT key, id FROM behaviors")).fetchall()
            }

            desired: set[tuple[str, str]] = set()
            to_insert: dict[tuple, dict] = {}
            # Global behaviors (from * rows or code defaults) apply to every species.
            for sp_id in species_map.values():
                for b in all_species_behaviors:
                    b_id = behavior_id_map.get(b["key"])
                    if sp_id and b_id:
                        to_insert[(sp_id, b_id)] = {"species_id": sp_id, "behavior_id": b_id}
                        desired.add((sp_id, b_id))
            # Species-specific CSV rows assign behaviors to their named species only.
            for row in specific_rows:
                sp_id = species_map.get(row["scientific_name"])
                b_id = behavior_id_map.get(row["key"])
                if sp_id and b_id:
                    to_insert[(sp_id, b_id)] = {"species_id": sp_id, "behavior_id": b_id}
                    desired.add((sp_id, b_id))
            if to_insert:
                placeholders = ", ".join(f"(:sid{i}, :bid{i})" for i in range(len(to_insert)))
                params = {}
                for i, ((sid, bid), _) in enumerate(to_insert.items()):
                    params[f"sid{i}"] = sid
                    params[f"bid{i}"] = bid
                conn.execute(
                    text(
                        f"INSERT OR IGNORE INTO species_behaviors (species_id, behavior_id) VALUES {placeholders}"
                    ),
                    params,
                )

            # Remove bundled species-behavior pairs that are no longer in
            # the bundled data.  Mappings involving custom species or
            # custom behaviors are preserved since users may have
            # added them manually.
            bundled_species_ids = {
                r[0]
                for r in conn.execute(
                    text("SELECT id FROM species WHERE is_custom = 0")
                ).fetchall()
            }
            custom_behavior_ids = {
                r[0]
                for r in conn.execute(
                    text("SELECT id FROM behaviors WHERE is_custom = 1")
                ).fetchall()
            }

            # Build a temp table of desired bundled mappings so we can
            # delete stale rows via a subquery instead of an unbounded
            # parameterised IN clause.
            conn.execute(
                text(
                    "CREATE TEMP TABLE IF NOT EXISTS _desired_sb"
                    " (species_id TEXT NOT NULL, behavior_id TEXT NOT NULL)"
                )
            )
            conn.execute(text("DELETE FROM _desired_sb"))
            if desired:
                desired_rows = [{"species_id": sid, "behavior_id": bid} for sid, bid in desired]
                conn.execute(
                    text(
                        "INSERT INTO _desired_sb (species_id, behavior_id)"
                        " VALUES (:species_id, :behavior_id)"
                    ),
                    desired_rows,
                )

            # Delete stale bundled mappings — rows where the species is
            # bundled (not custom), the behavior is bundled (not custom),
            # and the pair is not in the desired set.
            if bundled_species_ids:
                sp_ids_ph = ", ".join(f":sp{i}" for i in range(len(bundled_species_ids)))
                sp_params: dict[str, str] = {}
                for i, sp_id in enumerate(bundled_species_ids):
                    sp_params[f"sp{i}"] = sp_id

                where_behavior = ""
                params = {**sp_params}
                if custom_behavior_ids:
                    cb_ids_ph = ", ".join(f":cb{i}" for i in range(len(custom_behavior_ids)))
                    for i, cb_id in enumerate(custom_behavior_ids):
                        params[f"cb{i}"] = cb_id
                    where_behavior = f"AND behavior_id NOT IN ({cb_ids_ph})"

                conn.execute(
                    text(
                        f"""
                        DELETE FROM species_behaviors
                        WHERE species_id IN ({sp_ids_ph})
                        {where_behavior}
                        AND (species_id, behavior_id) NOT IN (
                            SELECT species_id, behavior_id FROM _desired_sb
                        )
                        """
                    ),
                    params,
                )

            conn.execute(text("DROP TABLE IF EXISTS _desired_sb"))

    def _build_species_variant_map(self) -> dict[str, str]:
        """Return {lowercase_variant -> scientific_name} for all species names/aliases."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT scientific_name, name_en, name_fr FROM species")
            ).fetchall()
        variant_to_sci: dict[str, str] = {}
        for sci, name_en, name_fr in rows:
            variant_to_sci[sci.lower()] = sci
            if name_en:
                variant_to_sci[name_en.lower()] = sci
            if name_fr:
                variant_to_sci[name_fr.lower()] = sci
        return variant_to_sci

    def _validate_species_fuzzy(
        self, value_text: str, variant_map: dict[str, str] | None = None
    ) -> tuple[bool, str | None]:
        from thefuzz import process

        if not value_text:
            return False, None

        value_lower = str(value_text).strip().lower()
        variant_to_sci = (
            variant_map if variant_map is not None else self._build_species_variant_map()
        )

        if value_lower in variant_to_sci:
            return True, variant_to_sci[value_lower]

        candidates = list(variant_to_sci.keys())
        if not candidates:
            return False, None
        match, score = process.extractOne(value_lower, candidates)
        if score >= 80:
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

    def get_behaviors_for_species(
        self, species_name: str, project_id: str | None = None
    ) -> list[str]:
        with self.engine.connect() as conn:
            if project_id:
                # Try project-specific species behaviors first
                rows = conn.execute(
                    text(
                        """
                        SELECT b.key FROM behaviors b
                        JOIN project_species_behaviors psb ON psb.behavior_id = b.id
                        JOIN species s ON s.id = psb.species_id
                        WHERE s.scientific_name = :s AND psb.project_id = :pid
                        """
                    ),
                    {"s": species_name, "pid": project_id},
                ).fetchall()
                if rows:
                    return [r[0] for r in rows]

            # Fallback to global species behaviors
            rows = conn.execute(
                text(
                    """
                    SELECT b.key FROM behaviors b
                    JOIN species_behaviors sb ON sb.behavior_id = b.id
                    JOIN species s ON s.id = sb.species_id
                    WHERE s.scientific_name = :s
                    """
                ),
                {"s": species_name},
            ).fetchall()
        result = [r[0] for r in rows]
        return result

    def get_all_behaviors(self) -> list[dict]:
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

    def get_behavior_display_map(
        self, lang: str = "en", species_name: str | None = None, project_id: str | None = None
    ) -> dict[str, str]:
        col = {"en": "name_en", "fr": "name_fr"}.get(lang, "name_en")
        keys = []
        if species_name:
            keys = self.get_behaviors_for_species(species_name, project_id=project_id)

        with self.engine.connect() as conn:
            if keys:
                placeholders = ", ".join(f":k{i}" for i in range(len(keys)))
                params = {f"k{i}": key for i, key in enumerate(keys)}
                rows = conn.execute(
                    text(f"SELECT key, {col} FROM behaviors WHERE key IN ({placeholders})"),
                    params,
                ).fetchall()
                lookup = {key: name or key for key, name in rows}
                return {k: lookup[k] for k in keys if k in lookup}
            else:
                rows = conn.execute(text(f"SELECT key, {col} FROM behaviors")).fetchall()

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

    def set_project_species_behaviors(
        self, project_id: str, species_name: str, behavior_keys: list[str]
    ) -> None:
        with self.engine.begin() as conn:
            # Get species ID
            sp_id = conn.execute(
                text("SELECT id FROM species WHERE scientific_name = :s"), {"s": species_name}
            ).scalar()
            if not sp_id:
                return

            conn.execute(
                text(
                    "DELETE FROM project_species_behaviors WHERE project_id = :pid AND species_id = :sid"
                ),
                {"pid": project_id, "sid": sp_id},
            )
            if behavior_keys:
                placeholders = ", ".join(f":k{i}" for i in range(len(behavior_keys)))
                params = {f"k{i}": key for i, key in enumerate(behavior_keys)}
                params.update({"pid": project_id, "sid": sp_id})
                conn.execute(
                    text(
                        f"""
                        INSERT INTO project_species_behaviors (project_id, species_id, behavior_id)
                        SELECT :pid, :sid, id FROM behaviors WHERE key IN ({placeholders})
                        """
                    ),
                    params,
                )

    def get_project_species_behaviors(self, project_id: str, species_name: str) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT b.key FROM behaviors b
                    JOIN project_species_behaviors psb ON psb.behavior_id = b.id
                    JOIN species s ON s.id = psb.species_id
                    WHERE psb.project_id = :pid AND s.scientific_name = :s
                    """
                ),
                {"pid": project_id, "s": species_name},
            ).fetchall()
        return [r[0] for r in rows]

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
        return True

    def _upsert_species(self, row: dict) -> None:
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
            raise ValueError("No valid rows found. Ensure the CSV uses ';' as separator and has a 'scientific_name' column.")

        for row in rows:
            self._upsert_species(row)

        names = [r["scientific_name"] for r in rows]
        self.set_project_species(project_id, names)
        return len(names)

    def import_project_behaviors_from_csv(self, project_id: str, content: str) -> int:
        """Parse behaviors CSV content, add any unknown behaviors as custom, and overwrite
        per-species behavior lists for each species in the project.

        Rows with scientific_name='*' apply to every project species.
        Rows with a specific scientific_name apply to that species only.
        Returns the number of species whose behavior lists were updated."""
        import io

        rows = self._parse_behaviors_csv(io.StringIO(content))
        if not rows:
            raise ValueError("No valid rows found. Ensure the CSV uses ';' as separator and has 'scientific_name' and 'key' columns.")

        for row in rows:
            if not self.behavior_exists(row["key"]):
                self.add_custom_behavior(row["key"], row["name_en"], row["name_fr"])

        global_keys = [r["key"] for r in rows if r["scientific_name"] == "*"]
        by_species: dict[str, list[str]] = {}
        for row in rows:
            if row["scientific_name"] != "*":
                by_species.setdefault(row["scientific_name"], []).append(row["key"])

        project_species = self.get_project_species(project_id)
        updated = 0
        # Apply to all project species: global keys + any species-specific keys.
        for sci in project_species:
            keys = global_keys + by_species.get(sci, [])
            self.set_project_species_behaviors(project_id, sci, keys)
            updated += 1
        # Also apply to species named in the CSV that aren't in the project species list.
        for sci, keys in by_species.items():
            if sci not in project_species and self.species_exists(sci):
                self.set_project_species_behaviors(project_id, sci, global_keys + keys)
                updated += 1
        return updated
