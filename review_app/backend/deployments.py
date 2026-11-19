"""Deployment inventory input and directory matching, independent of the database."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from review_app.backend.camera_layout import iter_footage
from review_app.backend.path_matching import normalize_path_str

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_DEPLOYMENTS = 10_000
CSV_COLUMNS = (
    "deployment_id",
    "folder_path",
    "name",
    "camera_id",
    "installation_date",
    "retrieval_date",
    "transect_id",
    "habitat",
    "notes",
)
CSV_TEMPLATE = ",".join(CSV_COLUMNS) + "\nP5-0042,,,,,,,,\n"


class DeploymentInputError(ValueError):
    def __init__(self, key: str, **params):
        self.key = key
        self.params = params
        super().__init__(f"{key}: {params}")


@dataclass(frozen=True)
class DeploymentPreview:
    rows: tuple[dict, ...]
    ignored_columns: tuple[str, ...] = ()
    missing_folders: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeploymentFolderMatch:
    folder: str
    candidates: tuple[str, ...]
    ordinary: int
    references: int


@dataclass(frozen=True)
class DeploymentSetupPreview:
    root: str
    inventory: DeploymentPreview
    folders: tuple[DeploymentFolderMatch, ...]
    empty_deployments: tuple[str, ...]
    footage_signature: str


def _matching_key(value: str) -> str:
    return re.sub(r"[\s_-]+", "_", value.strip().casefold())


def deployment_id_index(identifiers):
    index = defaultdict(list)
    for identifier in identifiers:
        index[_matching_key(identifier)].append(identifier)
    return index


def csv_folder_candidates(folder: str, baseline: str, index) -> tuple[str, ...]:
    """Match whole directory components and separator-bounded ID prefixes."""
    parts = set(PurePosixPath(folder).parts)
    if baseline != "default":
        parts.update(PurePosixPath(baseline).parts)
    keys = {_matching_key(part) for part in parts}
    keys.update(key[:i] for key in tuple(keys) for i, char in enumerate(key) if char == "_")
    return tuple(sorted(identifier for key in keys for identifier in index.get(key, ())))


def _folder_path(value: str, root: Path, row: int) -> str:
    try:
        path = Path(normalize_path_str(value)).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        relative = path.relative_to(root)
        if path.exists() and not path.is_dir():
            raise ValueError()
    except (ValueError, OSError, RuntimeError):
        raise DeploymentInputError("deployment_error_path", row=row, value=value) from None
    return relative.as_posix()


def parse_deployment_csv(raw: bytes, video_dir: str) -> DeploymentPreview:
    """Validate the whole inventory before writes; extra columns are reported and ignored."""
    if len(raw) > MAX_CSV_BYTES:
        raise DeploymentInputError("deployment_error_size")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DeploymentInputError("deployment_error_encoding") from None
    try:
        # Support spreadsheet semicolon exports as well as the downloadable comma template.
        header_line = content.splitlines()[0] if content.splitlines() else ""
        delimiter = ";" if ";" in header_line and "," not in header_line else ","
        reader = csv.DictReader(io.StringIO(content, newline=""), delimiter=delimiter, strict=True)
        headers = [header.strip().lower() for header in (reader.fieldnames or [])]
        if "deployment_id" not in headers or len(set(headers)) != len(headers):
            raise DeploymentInputError("deployment_error_headers")
        reader.fieldnames = headers
        ignored = tuple(header for header in headers if header not in CSV_COLUMNS)
        rows = []
        identifiers = set()
        folders = set()
        missing = []
        root = Path(normalize_path_str(video_dir)).expanduser().resolve()
        for record in reader:
            number = reader.line_num
            if None in record:
                raise DeploymentInputError("deployment_error_row", row=number)
            if not any(value and value.strip() for value in record.values()):
                continue
            if len(rows) >= MAX_DEPLOYMENTS:
                raise DeploymentInputError("deployment_error_count", count=MAX_DEPLOYMENTS)
            values = {key: (record.get(key) or "").strip() for key in CSV_COLUMNS}
            identifier = values["deployment_id"]
            if not identifier:
                raise DeploymentInputError("deployment_error_id", row=number)
            if identifier in identifiers:
                raise DeploymentInputError(
                    "deployment_error_duplicate", row=number, value=identifier
                )
            identifiers.add(identifier)
            row = {key: value or None for key, value in values.items()}
            for key in ("installation_date", "retrieval_date"):
                try:
                    value = values[key]
                    parsed = date.fromisoformat(value) if value else None
                    if parsed and parsed.isoformat() != value:
                        raise ValueError()
                    row[key] = parsed
                except ValueError:
                    raise DeploymentInputError(
                        "deployment_error_date", row=number, column=key
                    ) from None
            if (
                row["installation_date"]
                and row["retrieval_date"]
                and row["retrieval_date"] < row["installation_date"]
            ):
                raise DeploymentInputError("deployment_error_date_order", row=number)
            row["source"] = "csv"
            if values["folder_path"]:
                if not video_dir.strip():
                    raise DeploymentInputError("deployment_error_root")
                folder = _folder_path(values["folder_path"], root, number)
                if folder in folders:
                    raise DeploymentInputError(
                        "deployment_error_folder_duplicate", row=number, value=folder
                    )
                folders.add(folder)
                row["folder_path"] = folder
                if not (root / folder).is_dir():
                    missing.append(folder)
            rows.append(row)
    except csv.Error:
        raise DeploymentInputError("deployment_error_csv") from None
    if not rows:
        raise DeploymentInputError("deployment_error_empty")
    return DeploymentPreview(tuple(rows), ignored, tuple(missing))


def infer_deployment_inventory(video_dir: str) -> DeploymentPreview:
    """Create inventory candidates with the existing conservative folder detector."""
    root = Path(normalize_path_str(video_dir)).expanduser().resolve()
    if not root.is_dir():
        raise DeploymentInputError("deployment_error_root")
    return _inferred_inventory(root, iter_footage(root))


def _inferred_inventory(root, footage):
    groups: dict[str, set[str]] = {}
    for path, label, _ in footage:
        groups.setdefault(label, set()).add(path.parent.relative_to(root).as_posix())
    if not groups:
        raise DeploymentInputError("deployment_error_empty")
    return DeploymentPreview(
        tuple(
            {
                "deployment_id": label,
                "source": "inferred",
                "folder_path": next(iter(folders)) if len(folders) == 1 else None,
            }
            for label, folders in sorted(groups.items())
        )
    )


def preview_deployment_setup(
    video_dir: str, *, csv_data: bytes | None = None, infer_from_folders: bool = False
) -> DeploymentSetupPreview:
    root = Path(normalize_path_str(video_dir)).expanduser().resolve()
    if not video_dir.strip() or not root.is_dir():
        raise DeploymentInputError("deployment_error_root")
    if infer_from_folders and csv_data is not None:
        raise DeploymentInputError("deployment_error_mode")
    if not infer_from_folders and csv_data is None:
        raise DeploymentInputError("deployment_error_required")
    inventory = None if infer_from_folders else parse_deployment_csv(csv_data, root.as_posix())
    footage = list(iter_footage(root))
    if inventory is None:
        inventory = _inferred_inventory(root, footage)
    identifiers = {row["deployment_id"] for row in inventory.rows}
    links = {
        row["folder_path"]: row["deployment_id"]
        for row in inventory.rows
        if row.get("folder_path")
    }
    index = deployment_id_index(identifiers)
    folders = Counter(
        (path.parent.relative_to(root).as_posix(), baseline, reference)
        for path, baseline, reference in footage
    )
    counts = defaultdict(Counter)
    for (folder, baseline, reference), count in folders.items():
        if infer_from_folders:
            candidates = (baseline,)
        elif folder in links:
            candidates = (links[folder],)
        else:
            candidates = csv_folder_candidates(folder, baseline, index)
        counts[folder, candidates][reference] += count
    matched = {candidates[0] for _, candidates in counts if len(candidates) == 1}
    signature = hashlib.sha256()
    for path, baseline, reference in footage:
        signature.update(repr((path.relative_to(root).as_posix(), baseline, reference)).encode())
    return DeploymentSetupPreview(
        root.as_posix(),
        inventory,
        tuple(
            DeploymentFolderMatch(folder, candidates, count[False], count[True])
            for (folder, candidates), count in sorted(counts.items())
        ),
        tuple(sorted(identifiers - matched)),
        signature.hexdigest(),
    )
