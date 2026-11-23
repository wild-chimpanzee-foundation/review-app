"""Identify setup formats without interpreting legacy bundles as handoffs."""

import csv
import io
import json
import zipfile

from review_app.backend.annotation_package.archive import (
    MAX_ARCHIVE_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    decode_archive,
)

LEGACY_COMPONENTS = ("species", "tags", "model_annotations", "metadata")


def legacy_contents(archive: zipfile.ZipFile) -> set[str]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("Duplicate package members")
    if sum(info.file_size for info in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Package exceeds the uncompressed size limit")
    available = {name for name in LEGACY_COMPONENTS if f"{name}.csv" in names}
    if "bundle.json" not in names:
        if not available:
            raise ValueError("Choose an assignment setup package or a legacy project bundle")
        return available
    info = archive.getinfo("bundle.json")
    if info.file_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Bundle manifest exceeds the upload limit")
    manifest = json.loads(archive.read(info))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("contents"), list):
        raise ValueError("Invalid legacy bundle manifest")
    return {name for name in available if name in manifest["contents"]}


def inspect_legacy_package(raw: bytes) -> dict[str, int]:
    """Summarize legacy bundle contents without exposing format-specific file names."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        included = legacy_contents(archive)
        counts = {}
        for name in LEGACY_COMPONENTS:
            filename = f"{name}.csv"
            if name not in included:
                counts[name] = 0
                continue
            text = archive.read(filename).decode("utf-8-sig")
            counts[name] = max(0, sum(1 for _ in csv.reader(io.StringIO(text))) - 1)
    return {
        "species_count": counts["species"],
        "tag_count": counts["tags"],
        "prediction_count": counts["model_annotations"],
        "metadata_count": counts["metadata"],
    }


def detect_setup_package(data: bytes) -> str:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Package exceeds the upload limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "manifest.json" in names:
                manifest, _, _ = decode_archive(data)
                if manifest["direction"] != "setup":
                    raise ValueError("Open a setup package, not an assignment return")
                return "assignment"
            legacy_contents(archive)
            return "legacy"
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Choose an assignment setup package or a legacy project bundle") from exc
