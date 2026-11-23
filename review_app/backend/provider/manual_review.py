"""Selection normalization and update rules for Species reviews."""

from typing import Any

import pandas as pd

from review_app.backend.errors import SpeciesError
from review_app.backend.provider.observation_identity import normalize_observation_uuid


def normalize_manual_selections(
    selections: list[dict],
    valid_species: set[str],
    species_id_map: dict[str, str],
    behavior_id_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Validate and normalize raw selection dicts into DB-shaped rows.

    Raises SpeciesError for a species outside valid_species. Pure — no DB access,
    so bulk imports can reuse it with maps loaded once instead of per video.
    """

    def _parse_tag_ids(selection: dict) -> list[str]:
        raw = selection.get("tags")
        if raw is None:
            # Legacy import: fall back to single "behavior" key
            raw = selection.get("behavior", "")
        if isinstance(raw, list):
            keys = raw
        else:
            keys = [t.strip() for t in str(raw).split(",") if t.strip()]
        # Filter out legacy sentinel values
        keys = [k for k in keys if k not in ("unlabeled", "does_not_react", "")]
        return [behavior_id_map[k] for k in keys if k in behavior_id_map]

    normalized: list[dict[str, Any]] = []
    for selection in selections:
        species = str(selection.get("species") or "").strip() or "unknown"
        if species not in valid_species:
            raise SpeciesError(
                user_message_key="species_error_unknown",
                detail=f"Unknown species: {species!r}",
                name=species,
            )

        # Support both 'start_sec' and 'timestamp' keys
        start_sec_raw = selection.get("start_sec")
        if start_sec_raw is None:
            start_sec_raw = selection.get("timestamp")
        start_sec = pd.to_numeric(start_sec_raw, errors="coerce")
        if pd.isna(start_sec):
            start_sec = 0.0

        end_sec_raw = selection.get("end_sec")
        end_sec = pd.to_numeric(end_sec_raw, errors="coerce")
        end_sec_val: float | None = None if pd.isna(end_sec) else float(end_sec)

        obs_id = selection.get("id")
        if obs_id is not None:
            try:
                obs_id = int(obs_id)
            except (ValueError, TypeError):
                obs_id = None

        observation_uuid = normalize_observation_uuid(selection.get("observation_uuid"))

        count_raw = selection.get("count")
        count_val: int | None = int(count_raw) if count_raw is not None else None

        normalized.append(
            {
                "id": obs_id,
                "observation_uuid": observation_uuid,
                "species_id": species_id_map.get(species),
                "tag_ids": _parse_tag_ids(selection),
                "count": count_val,
                "start_sec": float(start_sec),
                "end_sec": end_sec_val,
                "labeled_by": selection.get("labeled_by"),
                "labeled_at": selection.get("labeled_at"),
            }
        )
    return normalized


def observation_fields_changed(
    existing: tuple[str | None, int | None, float | None, float | None],
    incoming: tuple[str | None, int | None, float, float | None],
) -> bool:
    old_species, old_count, old_start, old_end = existing
    species, count, start, end = incoming
    end_changed = (
        abs(old_end - end) > 0.001 if old_end is not None and end is not None else old_end != end
    )
    return (
        old_species != species
        or old_count != count
        or abs((old_start or 0.0) - start) > 0.001
        or end_changed
    )


def review_blank_status(requested: bool | None, observation_count: int) -> bool | None:
    """A clip with observations is always nonblank."""
    return False if observation_count else requested
