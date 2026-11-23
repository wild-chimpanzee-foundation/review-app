"""Folder-based deployment setup and inventory validation."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from review_app.backend.camera_layout import iter_footage
from review_app.backend.path_matching import normalize_path_str

DEPLOYMENT_FIELDS = (
    "deployment_id",
    "name",
    "camera_id",
    "installation_date",
    "retrieval_date",
    "transect_id",
    "habitat",
    "notes",
)


class DeploymentInputError(ValueError):
    def __init__(self, key: str, **params):
        self.key = key
        self.params = params
        super().__init__(f"{key}: {params}")


@dataclass(frozen=True)
class DeploymentFolderMatch:
    folder: str
    deployment: str
    ordinary: int
    references: int
    unmatched_reference: bool


@dataclass(frozen=True)
class DeploymentSetupPreview:
    root: str
    deployments: tuple[str, ...]
    folders: tuple[DeploymentFolderMatch, ...]
    footage_signature: str


def relative_deployment_folder(value: str, root: Path) -> str:
    try:
        path = Path(normalize_path_str(value)).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        relative = path.relative_to(root)
        if path.exists() and not path.is_dir():
            raise ValueError()
    except (ValueError, OSError, RuntimeError):
        raise DeploymentInputError("deployment_error_path", value=value) from None
    return relative.as_posix()


def validate_deployment_fields(values: dict) -> dict:
    fields = {key: str(values.get(key) or "").strip() or None for key in DEPLOYMENT_FIELDS}
    if not fields["deployment_id"]:
        raise DeploymentInputError("deployment_error_id")
    for key in ("installation_date", "retrieval_date"):
        value = fields[key]
        if value is None:
            continue
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError()
            fields[key] = parsed
        except ValueError:
            raise DeploymentInputError("deployment_error_date", column=key) from None
    if (
        fields["installation_date"]
        and fields["retrieval_date"]
        and fields["retrieval_date"] < fields["installation_date"]
    ):
        raise DeploymentInputError("deployment_error_date_order")
    return fields


def preview_deployment_setup(video_dir: str) -> DeploymentSetupPreview:
    root = Path(normalize_path_str(video_dir)).expanduser().resolve()
    if not video_dir.strip() or not root.is_dir():
        raise DeploymentInputError("deployment_error_root")

    counts = defaultdict(Counter)
    ordinary_deployments = set()
    signature = hashlib.sha256()
    for path, deployment, reference in iter_footage(root):
        relative = path.relative_to(root)
        counts[relative.parent.as_posix(), deployment][reference] += 1
        if not reference:
            ordinary_deployments.add(deployment)
        signature.update(repr((relative.as_posix(), deployment, reference)).encode())
    if not counts:
        raise DeploymentInputError("deployment_error_empty")

    return DeploymentSetupPreview(
        root=root.as_posix(),
        deployments=tuple(sorted({deployment for _, deployment in counts})),
        folders=tuple(
            DeploymentFolderMatch(
                folder,
                deployment,
                count[False],
                count[True],
                bool(count[True] and deployment not in ordinary_deployments),
            )
            for (folder, deployment), count in sorted(counts.items())
        ),
        footage_signature=signature.hexdigest(),
    )
