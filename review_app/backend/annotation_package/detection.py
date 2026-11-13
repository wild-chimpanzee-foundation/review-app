"""Identify setup formats without interpreting legacy bundles as handoffs."""

import io
import json
import zipfile

from review_app.backend.annotation_package.archive import MAX_ARCHIVE_BYTES, decode_archive


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
            if len(names) != len(set(names)):
                raise ValueError("Duplicate package members")
            info = archive.getinfo("bundle.json")
            if info.file_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Bundle manifest exceeds the upload limit")
            manifest = json.loads(archive.read(info))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("contents"), list):
                raise ValueError("Invalid legacy bundle manifest")
            return "legacy"
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Choose an assignment setup package or a legacy project bundle") from exc
