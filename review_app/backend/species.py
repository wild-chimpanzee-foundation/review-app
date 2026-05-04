from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import text


class SpeciesMixin:
    """Species and behavior loading and queries. Requires self.engine, self._resolve_path, self._behavior_defaults, self._fuzzy_match_threshold."""

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
            conn.execute(text("DELETE FROM species"))
            conn.execute(
                text(
                    "INSERT INTO species (scientific_name, name_en, name_fr, group_en, group_fr, iucn) "
                    "VALUES (:scientific_name, :name_en, :name_fr, :group_en, :group_fr, :iucn)"
                ),
                rows,
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
                if species and behavior:
                    rows.append({"scientific_name": species, "behavior": behavior})
            return rows
        except Exception:
            return []

    def _load_species_behaviors(self) -> None:
        from review_app.app.config import get_bundled_behaviors_csv

        bundled_path = get_bundled_behaviors_csv()
        rows = self._parse_behaviors_csv(Path(bundled_path)) if bundled_path else []

        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM species_behavior"))
            all_species = [
                r[0] for r in conn.execute(text("SELECT scientific_name FROM species")).fetchall()
            ]
            default_rows = [
                {"scientific_name": sci, "behavior": b}
                for sci in all_species
                for b in self._behavior_defaults
            ]
            to_insert = {(r["scientific_name"], r["behavior"]): r for r in default_rows}
            to_insert.update({(r["scientific_name"], r["behavior"]): r for r in rows})
            if to_insert:
                conn.execute(
                    text(
                        "INSERT INTO species_behavior (scientific_name, behavior) "
                        "VALUES (:scientific_name, :behavior)"
                    ),
                    list(to_insert.values()),
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

    def get_valid_species(self) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT scientific_name FROM species ORDER BY scientific_name")
            ).fetchall()
        return [r[0] for r in rows]

    def get_species_display_map(self, lang: str = "en") -> dict[str, str]:
        col = "name_en" if lang == "en" else "name_fr"
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT scientific_name, {col} FROM species ORDER BY {col}, scientific_name")
            ).fetchall()
        return {sci: f"{name} ({sci})" if name else sci for sci, name in rows}

    def get_behaviors_for_species(self, species_name: str) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT behavior FROM species_behavior WHERE scientific_name = :s"),
                {"s": species_name},
            ).fetchall()
        result = [r[0] for r in rows]
        return result or ["does_not_react"]
