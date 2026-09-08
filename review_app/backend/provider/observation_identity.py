"""Observation identity rules shared by CSV previews and annotation writes."""

import uuid
from collections.abc import Collection, Mapping

import pandas as pd

from review_app.backend.errors import DataImportError


class ObservationIdentityError(DataImportError):
    def __init__(self, key: str, rows: str, detail: str):
        super().__init__(detail, user_message_key=key)
        self.rows = rows


def normalize_observation_uuid(raw, row_number: int | None = None) -> str | None:
    if raw is None or pd.isna(raw) or not str(raw).strip():
        return None
    try:
        return str(uuid.UUID(str(raw).strip()))
    except ValueError as exc:
        rows = str(row_number) if row_number is not None else "?"
        raise ObservationIdentityError(
            "csv_error_invalid_observation_uuid",
            rows,
            f"Invalid observation_uuid at row {rows}: {raw!r}",
        ) from exc


def match_observation_id(
    observation_uuid: str | None,
    observation_id: int | None,
    existing_ids: Collection[int],
    uuid_ids: Mapping[str, int],
) -> int | None:
    if observation_uuid is not None:
        return uuid_ids.get(observation_uuid)
    return observation_id if observation_id in existing_ids else None


def prepare_observation_csv(df: pd.DataFrame, known_ids: set[str]) -> pd.DataFrame:
    """Canonicalize UUIDs and check duplicates before any import writes.

    Row numbers include the CSV header. Local numeric IDs and source path/project
    metadata do not distinguish copies of the same portable observation.
    """
    if "observation_uuid" not in df.columns:
        return df
    df = df.copy()
    ignored = {"observation_id", "video_path", "project_name"}
    compare_columns = [column for column in df.columns if column not in ignored]
    seen: dict[tuple[str, str], tuple[int, pd.Series]] = {}
    keep = []
    uuid_column = df.columns.get_loc("observation_uuid")
    for position in range(len(df)):
        row = df.iloc[position].copy()
        vid = row.get("video_id")
        if pd.isna(vid) or vid not in known_ids:
            keep.append(position)
            continue
        obs_uuid = normalize_observation_uuid(row.get("observation_uuid"), position + 2)
        df.iat[position, uuid_column] = obs_uuid
        row["observation_uuid"] = obs_uuid
        if obs_uuid is not None:
            key = (str(vid), obs_uuid)
            values = row[compare_columns].fillna("")
            if key in seen:
                first_position, first_values = seen[key]
                if not values.equals(first_values):
                    rows = f"{first_position + 2}, {position + 2}"
                    raise ObservationIdentityError(
                        "csv_error_conflicting_observation_uuid",
                        rows,
                        f"Conflicting observation_uuid {obs_uuid} at rows {rows}",
                    )
                continue
            seen[key] = (position, values)
        keep.append(position)
    return df.iloc[keep]
