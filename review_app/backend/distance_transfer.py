"""Version 1 manual Distance archives: canonical UTF-8 TSV, never locale guessed.

This is an editable snapshot, not an AI input package or a baseline-aware return.
No media is extracted and no absolute source-machine paths are stored.
"""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer

from review_app.backend.db.models import (
    Behavior,
    DistanceIndividual,
    DistanceReviewProgress,
    DistanceReviewTask,
    DistanceSample,
    ReferenceFrame,
    Species,
    Tag,
)

FORMAT = "review-app.distance-manual"
VERSION = 1
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


def _columns(model, names: str):
    return {name: model.__table__.columns[name].type for name in names.split()}


# Freeze version 1 headers independently of future ORM column additions.
# AI provenance is deliberately outside this manual-only contract.
SCHEMAS = {
    "tasks": {
        **_columns(
            DistanceReviewTask,
            "id source_count completion_outcome completion_history rejected_ai recorded_start "
            "alignment_mode imported_sample_times_s started_at clock_anchor_offset_ms "
            "clock_anchor_playback_ms opening_even_second verified_by verified_at completed_by completed_at",
        ),
        "root_id": None,
        "video_path": None,
        "camera_id": None,
        "duration_sec": Float(),
        "species": None,
        "scheduled_offsets_ms": JSON(),
    },
    "individuals": {
        **_columns(
            DistanceIndividual,
            "id task_id individual_number age sex first_seen_offset_ms first_seen_playback_ms "
            "initial_carrier_individual_id anchor_x anchor_y anchor_width anchor_height "
            "labeled_by labeled_at updated_at review_status completed_by completed_at",
        ),
        "tags": JSON(),
    },
    "samples": {
        **_columns(
            DistanceSample,
            "id individual_id offset_ms status skip_reason reference_frame_id distance_m "
            "carried_by_individual_id labeled_by labeled_at updated_at",
        ),
        "behaviors": JSON(),
    },
    "references": {
        **_columns(
            ReferenceFrame, "id reference_video_id offset_ms playback_offset_ms distance_m mask"
        ),
        "root_id": None,
        "video_path": None,
        "camera_id": None,
    },
    "progress": _columns(
        DistanceReviewProgress, "task_id annotator_name active_individual_id updated_at"
    ),
    "species": _columns(
        Species,
        "id scientific_name name_en name_fr group_en group_fr iucn inaturalist_url is_custom",
    ),
    "behaviors": _columns(Behavior, "id key name_en name_fr is_custom"),
    "tags": _columns(Tag, "id key name_en name_fr color icon is_custom individual_eligible"),
}


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def write_tsv(rows: list[dict], fields: list[str]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        encoded = {}
        for field in fields:
            value = row.get(field)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, bool):
                value = int(value)
            elif isinstance(value, (list, dict)):
                value = json_text(value)
            encoded[field] = value
        writer.writerow(encoded)
    return output.getvalue().encode("utf-8")


def encode_archive(tables: dict[str, list[dict]], manifest: dict) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json", json_text({**manifest, "format": FORMAT, "version": VERSION})
        )
        total_size = sum(info.file_size for info in archive.infolist())
        for name, schema in SCHEMAS.items():
            content = write_tsv(tables[name], list(schema))
            total_size += len(content)
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Distance export exceeds 100 MiB; select fewer tasks")
            archive.writestr(f"{name}.tsv", content)
    data = output.getvalue()
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Distance export exceeds 50 MiB; select fewer tasks")
    return data


def _json(value):
    def invalid_constant(_):
        raise ValueError("Non-finite JSON numbers are not supported")

    return json.loads(value, parse_constant=invalid_constant)


def _decode_value(value: str, kind):
    if value == "":
        return None
    if isinstance(kind, Boolean):
        if value not in {"0", "1"}:
            raise ValueError("Booleans must be 0 or 1")
        return value == "1"
    if isinstance(kind, Integer):
        return int(value)
    if isinstance(kind, Float):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Numbers must be finite")
        return number
    if isinstance(kind, DateTime):
        return datetime.fromisoformat(value)
    if isinstance(kind, JSON):
        return _json(value)
    return value


def decode_archive(data: bytes) -> tuple[dict, dict[str, list[dict]]]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Distance archive exceeds the 50 MiB upload limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            expected = {"manifest.json", *(f"{name}.tsv" for name in SCHEMAS)}
            if len(names) != len(expected) or set(names) != expected:
                raise ValueError("Distance archive must contain exactly the version 1 tables")
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Distance archive exceeds the 100 MiB uncompressed limit")
            manifest = _json(archive.read("manifest.json").decode("utf-8-sig"))
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != FORMAT
                or type(manifest.get("version")) is not int
                or manifest["version"] != VERSION
            ):
                raise ValueError("Unsupported manual Distance archive format/version")
            # JSON masks/history can exceed csv's small default cell limit. Only
            # raise its process-wide ceiling; ZIP size limits above still apply.
            if csv.field_size_limit() < MAX_UNCOMPRESSED_BYTES:
                csv.field_size_limit(MAX_UNCOMPRESSED_BYTES)
            tables = {}
            for name, schema in SCHEMAS.items():
                reader = csv.DictReader(
                    io.StringIO(archive.read(f"{name}.tsv").decode("utf-8-sig"), newline=""),
                    delimiter="\t",
                    strict=True,
                )
                if reader.fieldnames != list(schema):
                    raise ValueError(f"Unexpected columns in {name}.tsv")
                tables[name] = []
                for line, row in enumerate(reader, 2):
                    if None in row or None in row.values():
                        raise ValueError(f"Wrong field count in {name}.tsv at row {line}")
                    try:
                        tables[name].append(
                            {key: _decode_value(value, schema[key]) for key, value in row.items()}
                        )
                    except (ValueError, TypeError) as exc:
                        raise ValueError(f"Invalid {name}.tsv row {line}: {exc}") from exc
            return manifest, tables
    except (zipfile.BadZipFile, UnicodeError, csv.Error, RuntimeError, KeyError) as exc:
        raise ValueError(f"Invalid manual Distance archive: {exc}") from exc
