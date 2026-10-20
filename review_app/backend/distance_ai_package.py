"""Read the versioned AI Distance ZIP format without extracting or writing files."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 10_000

COLUMNS = {
    "reference_frames.csv": [
        "reference_id",
        "video_path",
        "time_s",
        "known_distance_m",
        "mask_path",
    ],
    "individuals.csv": ["individual_id", "video_path", "species"],
    "predictions.csv": [
        "prediction_id",
        "individual_id",
        "time_s",
        "predicted_distance_m",
        "suggested_reference_id",
        "mask_path",
    ],
}
OPTIONAL = {"mask_path", "suggested_reference_id"}


@dataclass
class DistanceAIPackage:
    manifest: dict
    references: list[dict]
    individuals: list[dict]
    predictions: list[dict]
    package_hash: str


def relative_path(value: str) -> str:
    """Accept portable relative paths only, including paths originating on Windows."""
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or ":" in value or "\0" in value:
        raise ValueError(f"Unsafe or non-relative path: {value!r}")
    if str(path) == ".":
        raise ValueError("A file path is required")
    return str(path)


def _json(data: bytes, name: str):
    try:
        return json.loads(data.decode("utf-8-sig"), parse_constant=lambda x: _invalid_json(x))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{name}: invalid JSON ({exc})") from exc


def _invalid_json(value):
    raise ValueError(f"non-finite JSON value {value}")


def _text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{name}: expected a non-empty string of at most 512 characters")
    return value.strip()


def _number(value: str, name: str, *, positive=False) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name}: expected a number") from exc
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        raise ValueError(
            f"{name}: expected a finite {'positive' if positive else 'non-negative'} number"
        )
    return number


def mask_counts(mask: dict, name: str = "animal mask") -> list[int]:
    """Decode and validate COCO column-major RLE for import and mask overlays."""
    if not isinstance(mask, dict):
        raise ValueError(f"{name}: expected one COCO RLE object")
    size, counts = mask.get("size"), mask.get("counts")
    if (
        not isinstance(size, list)
        or len(size) != 2
        or any(type(n) is not int or n <= 0 for n in size)
        or size[0] * size[1] > 100_000_000
    ):
        raise ValueError(f"{name}: invalid RLE dimensions [height, width]")
    if isinstance(counts, str):
        decoded = []
        position = 0
        while position < len(counts):
            value = shift = 0
            while True:
                if position >= len(counts) or shift > 12:
                    raise ValueError(f"{name}: invalid compressed RLE")
                code = ord(counts[position]) - 48
                position += 1
                if not 0 <= code <= 63:
                    raise ValueError(f"{name}: invalid compressed RLE")
                value |= (code & 31) << (5 * shift)
                shift += 1
                if not code & 32:
                    if code & 16:
                        value |= -1 << (5 * shift)
                    break
            if len(decoded) > 2:
                value += decoded[-2]
            decoded.append(value)
        counts = decoded
    if (
        not isinstance(counts, list)
        or not counts
        or any(type(n) is not int or n < 0 for n in counts)
        or sum(counts) != size[0] * size[1]
    ):
        raise ValueError(f"{name}: RLE run totals do not match its dimensions")
    return counts


def _validate_mask(mask, name: str) -> dict:
    mask_counts(mask, name)
    return mask


def mask_bounding_box(mask: dict) -> dict[str, float] | None:
    """Compute an exact normalized box from COCO column-major runs without a bitmap."""
    counts = mask_counts(mask)
    height, width = mask["size"]
    left, top, right, bottom = width, height, -1, -1
    position = 0
    for index, length in enumerate(counts):
        if index % 2 and length:
            first_x, first_y = divmod(position, height)
            last_x, last_y = divmod(position + length - 1, height)
            left, right = min(left, first_x), max(right, last_x)
            top = min(top, first_y if first_x == last_x else 0)
            bottom = max(bottom, last_y if first_x == last_x else height - 1)
        position += length
    if right < 0:
        return None
    x, y = left / width, top / height
    return {
        "x": x,
        "y": y,
        "width": (right + 1) / width - x,
        "height": (bottom + 1) / height - y,
    }


def read_distance_ai_package(data: bytes) -> DistanceAIPackage:
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("AI Distance ZIP must be at most 32 MiB")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _read_archive(archive)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise ValueError(f"Invalid or unsupported AI Distance ZIP: {exc}") from exc


def _read_archive(archive: zipfile.ZipFile) -> DistanceAIPackage:
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES:
        raise ValueError("AI Distance ZIP contains too much data")
    names = {}
    for entry in entries:
        path = relative_path(entry.filename)
        if entry.file_size > MAX_MEMBER_BYTES or entry.flag_bits & 1:
            raise ValueError(f"{path}: oversized or encrypted ZIP entry")
        if path in names:
            raise ValueError(f"Duplicate ZIP path: {path}")
        names[path] = entry

    def read(name):
        if name not in names or names[name].is_dir():
            raise ValueError(f"Missing package file: {name}")
        return archive.read(names[name])

    manifest = _json(read("manifest.json"), "manifest.json")
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "distance-ai"
        or type(manifest.get("version")) is not int
        or manifest["version"] != 1
    ):
        raise ValueError("Expected distance-ai format version 1")
    for field in ("source_id", "run_id"):
        manifest[field] = _text(manifest.get(field), f"manifest.json: {field}")
    model = manifest.get("model")
    if not isinstance(model, dict):
        raise ValueError("manifest.json: model is required")
    model["name"] = _text(model.get("name"), "manifest.json: model.name")
    if "version" not in model:
        raise ValueError("manifest.json: model.version is required (null if unknown)")
    if model["version"] is not None:
        model["version"] = _text(model["version"], "manifest.json: model.version")
    if manifest.get("timing_basis") != "video_playback_seconds":
        raise ValueError("Only video_playback_seconds timing is supported")

    masks = {}

    def table(name, id_column):
        try:
            reader = csv.DictReader(io.StringIO(read(name).decode("utf-8-sig"), newline=""))
            headers = reader.fieldnames or []
            required = set(COLUMNS[name]) - OPTIONAL
            if len(headers) != len(set(headers)) or not required.issubset(headers):
                raise ValueError(f"{name}: expected columns {', '.join(COLUMNS[name])}")
            rows, ids = [], set()
            for line, raw in enumerate(reader, 2):
                context = f"{name} row {line}"
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError(f"{context}: wrong number of CSV fields")
                row = {key: raw.get(key, "").strip() for key in COLUMNS[name]}
                for field in set(COLUMNS[name]) - OPTIONAL:
                    row[field] = _text(row[field], f"{context}: {field}")
                if row[id_column] in ids:
                    raise ValueError(f"{context}: duplicate {id_column} {row[id_column]!r}")
                ids.add(row[id_column])
                if "video_path" in row:
                    row["video_path"] = relative_path(row["video_path"])
                if "time_s" in row:
                    row["time_s"] = _number(row["time_s"], f"{context}: time_s")
                    field = (
                        "known_distance_m"
                        if name == "reference_frames.csv"
                        else "predicted_distance_m"
                    )
                    row[field] = _number(row[field], f"{context}: {field}", positive=True)
                    path = row["mask_path"]
                    row["mask"] = None
                    if path:
                        path = relative_path(path)
                        row["mask_path"] = path
                        if path not in masks:
                            masks[path] = _validate_mask(_json(read(path), path), path)
                        row["mask"] = masks[path]
                rows.append(row)
                if len(rows) > 100_000:
                    raise ValueError(f"{name}: too many rows")
            return rows
        except (UnicodeError, csv.Error) as exc:
            raise ValueError(f"{name}: invalid CSV ({exc})") from exc

    references = table("reference_frames.csv", "reference_id")
    individuals = table("individuals.csv", "individual_id")
    predictions = table("predictions.csv", "prediction_id")
    refs = {row["reference_id"] for row in references}
    tracks = {row["individual_id"] for row in individuals}
    samples = set()
    for line, row in enumerate(predictions, 2):
        context = f"predictions.csv row {line}"
        if row["individual_id"] not in tracks:
            raise ValueError(f"{context}: unknown individual_id {row['individual_id']!r}")
        if row["suggested_reference_id"] and row["suggested_reference_id"] not in refs:
            raise ValueError(f"{context}: unknown suggested_reference_id")
        key = row["individual_id"], row["time_s"]
        if key in samples:
            raise ValueError(f"{context}: duplicate individual/time")
        samples.add(key)
    # Hash semantic content, not ZIP timestamps, README text, or CSV row order.
    canonical = [manifest] + [
        sorted(rows, key=lambda row: row[key])
        for rows, key in (
            (references, "reference_id"),
            (individuals, "individual_id"),
            (predictions, "prediction_id"),
        )
    ]
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
    return DistanceAIPackage(manifest, references, individuals, predictions, digest)
