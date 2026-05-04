from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import text


class SpeciesMixin:
    """Species and behavior loading and queries. Requires self.engine, self._behavior_defaults."""

    @staticmethod
    def _parse_species_csv(path: Path) -> list[dict]:
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
                for r in conn.execute(
                    text("SELECT scientific_name, id FROM species")
                ).fetchall()
            }
            for row in rows:
                row["id"] = existing.get(row["scientific_name"]) or str(uuid.uuid4())

            conn.execute(
                text(
                    """
                    INSERT INTO species (id, scientific_name, name_en, name_fr, group_en, group_fr, iucn)
                    VALUES (:id, :scientific_name, :name_en, :name_fr, :group_en, :group_fr, :iucn)
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
            valid = ", ".join(f"'{r['scientific_name']}'" for r in rows)
            conn.execute(
                text(
                    f"""
                    DELETE FROM species
                    WHERE scientific_name NOT IN ({valid})
                    AND id NOT IN (SELECT DISTINCT species_id FROM individual_observations WHERE species_id IS NOT NULL)
                    """
                )
            )

    @staticmethod
    def _parse_behaviors_csv(path: Path) -> list[dict]:
        try:
            df = pd.read_csv(path, sep=";")
            if "Species" not in df.columns or "Behavior" not in df.columns:
                return []
            rows = []
            for _, row in df.iterrows():
                species = str(row["Species"]).strip()
                behavior = str(row["Behavior"]).strip()
                name_fr = str(row.get("behavior_fr", "") or "").strip() or None
                if species and behavior:
                    rows.append(
                        {"scientific_name": species, "behavior": behavior, "behavior_fr": name_fr}
                    )
            return rows
        except Exception:
            return []

    def _load_species_behaviors(self) -> None:
        from review_app.app.config import DEFAULT_BEHAVIORS, get_bundled_behaviors_csv

        bundled_path = get_bundled_behaviors_csv()
        csv_rows = self._parse_behaviors_csv(Path(bundled_path)) if bundled_path else []

        # Collect all behavior keys with optional French names.
        behavior_meta: dict[str, dict[str, str | None]] = {
            b["key"]: {"name_en": b["name_en"], "name_fr": b["name_fr"]} for b in DEFAULT_BEHAVIORS
        }
        for row in csv_rows:
            behavior_meta[row["behavior"]] = {"name_en": row["behavior"], "name_fr": row.get("behavior_fr")}

        with self.engine.begin() as conn:
            # Upsert behaviors — preserve existing IDs.
            existing_beh = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT key, id FROM behaviors")).fetchall()
            }
            behavior_rows = [
                {
                    "id": existing_beh.get(key) or str(uuid.uuid4()),
                    "key": key,
                    "name_en": meta["name_en"],
                    "name_fr": meta["name_fr"],
                }
                for key, meta in behavior_meta.items()
            ]
            conn.execute(
                text(
                    """
                    INSERT INTO behaviors (id, key, name_en, name_fr)
                    VALUES (:id, :key, :name_en, :name_fr)
                    ON CONFLICT(key) DO UPDATE SET
                        name_en = excluded.name_en,
                        name_fr = excluded.name_fr
                    """
                ),
                behavior_rows,
            )

            # Rebuild global species_behaviors from defaults + CSV.
            conn.execute(text("DELETE FROM species_behaviors"))

            species_map = {
                r[0]: r[1]
                for r in conn.execute(
                    text("SELECT scientific_name, id FROM species")
                ).fetchall()
            }
            behavior_id_map = {
                r[0]: r[1]
                for r in conn.execute(text("SELECT key, id FROM behaviors")).fetchall()
            }

            to_insert: dict[tuple, dict] = {}
            for sci, sp_id in species_map.items():
                for b_key in behavior_meta.keys():
                    b_id = behavior_id_map.get(b_key)
                    if b_id:
                        to_insert[(sp_id, b_id)] = {"species_id": sp_id, "behavior_id": b_id}
            for row in csv_rows:
                sp_id = species_map.get(row["scientific_name"])
                b_id = behavior_id_map.get(row["behavior"])
                if sp_id and b_id:
                    to_insert[(sp_id, b_id)] = {"species_id": sp_id, "behavior_id": b_id}

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
        variant_to_sci = variant_map if variant_map is not None else self._build_species_variant_map()

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

    def get_species_display_map(self, lang: str = "en", project_id: str | None = None) -> dict[str, str]:
        col = "name_en" if lang == "en" else "name_fr"
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
                            ORDER BY s.{col}, s.scientific_name
                            """
                        ),
                        {"pid": project_id},
                    ).fetchall()
                    return {sci: f"{name} ({sci})" if name else sci for sci, name in rows}

            rows = conn.execute(
                text(f"SELECT scientific_name, {col} FROM species ORDER BY {col}, scientific_name")
            ).fetchall()
        return {sci: f"{name} ({sci})" if name else sci for sci, name in rows}

    def get_behaviors_for_species(self, species_name: str, project_id: str | None = None) -> list[str]:
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
        return result or ["does_not_react"]

    def get_all_behaviors(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT key, name_en, name_fr FROM behaviors ORDER BY key")
            ).fetchall()
        return [{"key": r[0], "name_en": r[1], "name_fr": r[2]} for r in rows]

    def add_custom_behavior(self, key: str, name_en: str, name_fr: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO behaviors (id, key, name_en, name_fr)
                    VALUES (:id, :key, :name_en, :name_fr)
                    ON CONFLICT(key) DO UPDATE SET
                        name_en = excluded.name_en,
                        name_fr = excluded.name_fr
                    """
                ),
                {"id": str(uuid.uuid4()), "key": key, "name_en": name_en, "name_fr": name_fr},
            )

    def get_behavior_display_map(
        self, lang: str = "en", species_name: str | None = None, project_id: str | None = None
    ) -> dict[str, str]:
        col = "name_en" if lang == "en" else "name_fr"
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

    def get_existing_groups(self) -> dict[str, list[str]]:
        with self.engine.connect() as conn:
            en_rows = conn.execute(text("SELECT DISTINCT group_en FROM species WHERE group_en IS NOT NULL")).fetchall()
            fr_rows = conn.execute(text("SELECT DISTINCT group_fr FROM species WHERE group_fr IS NOT NULL")).fetchall()
        return {
            "en": [r[0] for r in en_rows],
            "fr": [r[0] for r in fr_rows],
        }

    def get_existing_iucn(self) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT DISTINCT iucn FROM species WHERE iucn IS NOT NULL")).fetchall()
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
