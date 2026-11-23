"""Bounded, versioned Species/Distance snapshots with explicit graph validation."""

from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import datetime

from review_app.backend.annotation_package.scope import safe_relative

FORMAT = "review-app.assignment"
VERSION = 2
RETIRED_REFERENCE_VERSION = 3
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MEMBERS = ("manifest.json", "context.json", "work.json")

_NULL = type(None)
TEXT = (str,)
OPTIONAL_TEXT = (str, _NULL)
NUMBER = (int, float)
OPTIONAL_NUMBER = (int, float, _NULL)
OPTIONAL_BOOL = (bool, _NULL)


def _record(value, schema):
    if not isinstance(value, dict) or value.keys() != schema.keys():
        raise ValueError("Unexpected assignment record fields")
    for key, kinds in schema.items():
        if type(value[key]) not in kinds:
            raise ValueError(f"Invalid assignment field: {key}")
        if isinstance(value[key], float) and not math.isfinite(value[key]):
            raise ValueError("Assignment numbers must be finite")
        if key.endswith("_at") and value[key] is not None:
            try:
                datetime.fromisoformat(value[key])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid assignment timestamp: {key}") from exc
    return value


def _strings(value):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError("Assignment keys must be nonempty strings")
    if len(value) != len(set(value)):
        raise ValueError("Duplicate assignment keys")
    return set(value)


def _records(value, schema, key):
    if not isinstance(value, list):
        raise ValueError("Expected an assignment record list")
    for row in value:
        _record(row, schema)
    _strings([row[key] for row in value])
    return {row[key]: row for row in value}


def validate_snapshot(manifest, context, work):
    if isinstance(context, dict) and "deployments" in context:
        base = {key: value for key, value in context.items() if key != "deployments"}
        _validate_snapshot(manifest, base, work)
        if (
            manifest["version"] not in {VERSION, RETIRED_REFERENCE_VERSION}
            or manifest["direction"] != "setup"
        ):
            raise ValueError("Unexpected assignment deployment context")
        from review_app.backend.annotation_package import deployments

        deployments.validate(context["deployments"], base)
        return
    _validate_snapshot(manifest, context, work)
    if manifest["version"] == VERSION and manifest["direction"] == "setup":
        raise ValueError("Assignment deployment context is missing")


def _validate_snapshot(manifest, context, work):
    _record(
        manifest,
        {
            "format": TEXT,
            "version": (int,),
            "direction": TEXT,
            "assignment_id": TEXT,
            "source_project_id": TEXT,
            "recipient": TEXT,
            "stage": TEXT,
            "target_species": OPTIONAL_TEXT,
            "created_at": TEXT,
            "work_keys": (list,),
            "roots": (list,),
        },
    )
    if manifest["format"] != FORMAT or manifest["version"] not in {
        1,
        VERSION,
        RETIRED_REFERENCE_VERSION,
    }:
        raise ValueError("Unsupported assignment format/version")
    if manifest["stage"] not in {"species", "distance"} or manifest["direction"] not in {
        "setup",
        "return",
    }:
        raise ValueError("Unknown assignment stage or direction")
    is_distance = manifest["stage"] == "distance"
    if is_distance and (
        not isinstance(manifest["target_species"], str) or not manifest["target_species"].strip()
    ):
        raise ValueError("Distance assignments require a target species")
    if not is_distance and manifest["target_species"] is not None:
        raise ValueError("Species assignments cannot have a target species")
    if any(
        not manifest[key].strip() for key in ("assignment_id", "source_project_id", "recipient")
    ):
        raise ValueError("Assignment identity and recipient are required")
    work_keys, roots = _strings(manifest["work_keys"]), _strings(manifest["roots"])
    if not work_keys:
        raise ValueError("Assignment scope must not be empty")
    if is_distance:
        from review_app.backend.annotation_package import distance

        if "distance" not in context:
            raise ValueError("Missing Distance dependencies")
        distance.validate(manifest, context, work)
        # Reuse the strict Species context/graph validator for carried setup context.
        base = {key: value for key, value in context.items() if key != "distance"}
        synthetic = {
            **manifest,
            "stage": "species",
            "target_species": None,
            "work_keys": [row["video_id"] for row in context["media"]],
        }
        _validate_snapshot(synthetic, base, context["distance"]["species_work"])
        return
    _record(
        context,
        {
            "project_name": TEXT,
            "project_species": (list,),
            "species": (list,),
            "behaviors": (list,),
            "tags": (list,),
            "media": (list,),
            "predictions": (list,),
        },
    )
    species = _records(
        context["species"],
        {
            "scientific_name": TEXT,
            "name_en": OPTIONAL_TEXT,
            "name_fr": OPTIONAL_TEXT,
            "group_en": OPTIONAL_TEXT,
            "group_fr": OPTIONAL_TEXT,
            "iucn": OPTIONAL_TEXT,
            "inaturalist_url": OPTIONAL_TEXT,
            "is_custom": (bool,),
        },
        "scientific_name",
    )
    behaviors = _records(
        context["behaviors"],
        {
            "key": TEXT,
            "name_en": TEXT,
            "name_fr": OPTIONAL_TEXT,
            "is_custom": (bool,),
        },
        "key",
    )
    tags = _records(
        context["tags"],
        {
            "key": TEXT,
            "name_en": TEXT,
            "name_fr": OPTIONAL_TEXT,
            "color": OPTIONAL_TEXT,
            "icon": OPTIONAL_TEXT,
            "is_custom": (bool,),
            "individual_eligible": (bool,),
        },
        "key",
    )
    if not _strings(context["project_species"]) <= species.keys():
        raise ValueError("Unknown project species")
    media = _records(
        context["media"],
        {
            "video_id": TEXT,
            "root_id": TEXT,
            "video_path": TEXT,
            "camera_id": OPTIONAL_TEXT,
            "created_at": OPTIONAL_TEXT,
            "duration_sec": OPTIONAL_NUMBER,
            "latitude": OPTIONAL_NUMBER,
            "longitude": OPTIONAL_NUMBER,
            "is_missing": (bool,),
        },
        "video_id",
    )
    if media.keys() != work_keys or {row["root_id"] for row in media.values()} != roots:
        raise ValueError("Setup media must exactly match the saved scope and roots")
    paths = set()
    for row in media.values():
        key = row["root_id"], safe_relative(row["video_path"])
        if key in paths or (row["duration_sec"] is not None and row["duration_sec"] < 0):
            raise ValueError("Duplicate media path or invalid video duration")
        paths.add(key)
    predictions = _records(
        context["predictions"],
        {
            "id": TEXT,
            "video_id": TEXT,
            "annotation_type": TEXT,
            "model_name": TEXT,
            "value_text": OPTIONAL_TEXT,
            "value_num": OPTIONAL_NUMBER,
            "probability": OPTIONAL_NUMBER,
            "t_start_sec": OPTIONAL_NUMBER,
            "t_end_sec": OPTIONAL_NUMBER,
            "updated_at": TEXT,
        },
        "id",
    )
    prediction_keys = set()
    for row in predictions.values():
        key = row["video_id"], row["model_name"], row["annotation_type"], row["value_text"] or ""
        if row["video_id"] not in work_keys or key in prediction_keys:
            raise ValueError("Prediction is outside scope or has duplicate identity")
        prediction_keys.add(key)
    units = _records(
        work,
        {
            "video_id": TEXT,
            "label": (dict, _NULL),
            "observations": (list,),
            "tags": (list,),
        },
        "video_id",
    )
    if not units.keys() <= work_keys or (
        manifest["direction"] == "setup" and units.keys() != work_keys
    ):
        raise ValueError("Work must match the saved scope; setup must contain every unit")
    for unit in units.values():
        if unit["label"] is not None:
            _record(
                unit["label"],
                {
                    "is_blank": OPTIONAL_BOOL,
                    "review_later": OPTIONAL_BOOL,
                    "labeled_by": OPTIONAL_TEXT,
                    "labeled_at": OPTIONAL_TEXT,
                },
            )
        observations = _records(
            unit["observations"],
            {
                "observation_uuid": TEXT,
                "species": OPTIONAL_TEXT,
                "count": (int, _NULL),
                "start_sec": NUMBER,
                "end_sec": OPTIONAL_NUMBER,
                "labeled_by": OPTIONAL_TEXT,
                "labeled_at": OPTIONAL_TEXT,
                "updated_at": TEXT,
                "behaviors": (list,),
            },
            "observation_uuid",
        )
        for row in observations.values():
            if row["species"] is not None and row["species"] not in species:
                raise ValueError("Unknown observation species")
            if not _strings(row["behaviors"]) <= behaviors.keys():
                raise ValueError("Unknown observation behavior")
            if (
                row["start_sec"] < 0
                or (row["end_sec"] is not None and row["end_sec"] < row["start_sec"])
                or (row["count"] is not None and row["count"] < 0)
            ):
                raise ValueError("Invalid observation count or interval")
        links = _records(
            unit["tags"],
            {
                "key": TEXT,
                "tagged_by": OPTIONAL_TEXT,
                "tagged_at": TEXT,
            },
            "key",
        )
        if not links.keys() <= tags.keys():
            raise ValueError("Unknown video tag")


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def encode_archive(manifest: dict, context: dict, work: list[dict]) -> bytes:
    if (
        manifest["stage"] == "distance"
        and manifest["version"] in {1, VERSION}
        and any(frame.get("retired") for frame in context["distance"]["frames"])
    ):
        manifest = {**manifest, "version": RETIRED_REFERENCE_VERSION}
    validate_snapshot(manifest, context, work)
    output = io.BytesIO()
    total = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in zip(MEMBERS, (manifest, context, work), strict=True):
            content = _json(value).encode("utf-8")
            total += len(content)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Assignment exceeds the uncompressed size limit")
            archive.writestr(name, content)
    data = output.getvalue()
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Assignment exceeds the archive size limit")
    return data


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key in assignment")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Non-finite JSON number in assignment: {value}")


def decode_archive(data: bytes) -> tuple[dict, dict, list[dict]]:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Assignment exceeds the archive size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.namelist()) != len(MEMBERS) or set(archive.namelist()) != set(MEMBERS):
                raise ValueError("Assignment archive must contain exactly the expected members")
            if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError("Assignment exceeds the uncompressed size limit")
            values = []
            for name in MEMBERS:
                with archive.open(name) as member:
                    content = member.read(MAX_UNCOMPRESSED_BYTES + 1)
                if len(content) > MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("Assignment exceeds the uncompressed size limit")
                values.append(
                    json.loads(
                        content.decode("utf-8"),
                        object_pairs_hook=_object,
                        parse_constant=_constant,
                    )
                )
        manifest, context, work = values
        validate_snapshot(manifest, context, work)
        return manifest, context, work
    except (
        zipfile.BadZipFile,
        UnicodeError,
        RuntimeError,
        NotImplementedError,
        RecursionError,
    ) as exc:
        raise ValueError(f"Invalid assignment archive: {exc}") from exc
