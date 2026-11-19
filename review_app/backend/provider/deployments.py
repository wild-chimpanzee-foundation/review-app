"""Deployment assignment: one session-aware write seam for scans and administration.

Video.camera_id is the compatibility projection consumed by existing queues and
exports. inferred_camera_id is only the folder baseline; deployment_id is the local
identity. Legacy/package labels are retained when reconstructing historical scope.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from collections import defaultdict
from pathlib import Path

from sqlalchemy import func, or_

from review_app.backend.camera_layout import deployment_key
from review_app.backend.db.models import (
    Deployment,
    DeploymentFolderLink,
    DistanceIndividual,
    DistanceReviewTask,
    HandoffAssignment,
    HandoffIdentityMap,
    Project,
    ProjectDir,
    ReferenceFrame,
    ReferenceVideo,
    Tag,
    Video,
    VideoTag,
)
from review_app.backend.deployment_review import group_deployment_folders
from review_app.backend.deployments import (
    CSV_COLUMNS,
    DeploymentInputError,
    _folder_path,
    csv_folder_candidates,
    deployment_id_index,
    parse_deployment_csv,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.base import ProviderBase


def _path(value):
    return Path(normalize_path_str(value)).expanduser().resolve()


def _roots(session, project_id):
    return session.query(ProjectDir).filter_by(project_id=project_id).order_by(ProjectDir.id).all()


class _StoredLocations:
    """Index registered paths without touching media (also works with offline roots).

    Filesystem canonicalization remains in explicit folder validation. Page reads
    and historical assignment initialization use the paths already stored in the DB.
    """

    def __init__(self, roots):
        self.roots = sorted(
            (
                (root, Path(os.path.abspath(os.path.expanduser(normalize_path_str(root.path)))))
                for root in roots
            ),
            key=lambda item: len(item[1].parts),
            reverse=True,
        )
        self.folders = {}

    def locate(self, video_path):
        parent = os.path.dirname(
            os.path.abspath(os.path.expanduser(normalize_path_str(video_path)))
        )
        if parent not in self.folders:
            path = Path(parent)
            self.folders[parent] = next(
                (
                    (root, path.relative_to(root_path).as_posix())
                    for root, root_path in self.roots
                    if path.is_relative_to(root_path)
                ),
                (None, None),
            )
        return self.folders[parent]


class _AssignmentDependencies:
    """One transaction-local snapshot for bulk initialization, never a cross-call cache."""

    def __init__(self, session, project_id, videos=None):
        video_ids = None if videos is None else [video.video_id for video in videos]
        paths = (
            None if videos is None else [normalize_path_str(video.video_path) for video in videos]
        )
        tasks = (
            session.query(DistanceReviewTask.video_id)
            .join(Video, Video.video_id == DistanceReviewTask.video_id)
            .filter(
                Video.project_id == project_id,
                or_(
                    DistanceReviewTask.verified_at.is_not(None),
                    DistanceReviewTask.started_at.is_not(None),
                    DistanceReviewTask.completed_at.is_not(None),
                    DistanceReviewTask.import_id.is_not(None),
                ),
            )
        )
        individuals = (
            session.query(DistanceIndividual.video_id)
            .join(Video, Video.video_id == DistanceIndividual.video_id)
            .filter(Video.project_id == project_id)
        )
        if video_ids is not None:
            tasks = tasks.filter(DistanceReviewTask.video_id.in_(video_ids))
            individuals = individuals.filter(DistanceIndividual.video_id.in_(video_ids))
        self.distance = {vid for (vid,) in tasks.union(individuals)}
        calibrated = (
            session.query(ReferenceFrame.id)
            .filter(ReferenceFrame.reference_video_id == ReferenceVideo.id)
            .exists()
        )
        self.references = defaultdict(list)
        self.calibrated = set()
        for reference, has_frames in session.query(ReferenceVideo, calibrated).filter(
            ReferenceVideo.project_id == project_id,
            func.replace(ReferenceVideo.video_path, "\\", "/").in_(paths)
            if paths is not None
            else True,
        ):
            path = normalize_path_str(reference.video_path)
            self.references[path].append(reference)
            if has_frames:
                self.calibrated.add(path)
        self.frozen = {
            vid
            for (vid,) in session.query(HandoffIdentityMap.local_id)
            .join(HandoffAssignment, HandoffAssignment.id == HandoffIdentityMap.handoff_id)
            .filter(
                HandoffAssignment.project_id == project_id,
                HandoffIdentityMap.kind == "video",
                HandoffIdentityMap.local_id.in_(video_ids) if video_ids is not None else True,
            )
        }

    def block_reason(self, video):
        if video.video_id in self.distance:
            return "deployment_block_distance"
        if normalize_path_str(video.video_path) in self.calibrated:
            return "deployment_block_reference"
        if video.video_id in self.frozen:
            return "deployment_block_handoff"
        return None

    def references_for(self, video):
        return self.references.get(normalize_path_str(video.video_path), ())


def ensure_project_initialized(session, project_id):
    """Read without a write lock unless legacy rows or missing initial links exist."""
    project = session.get(Project, project_id)
    if project is None:
        raise DeploymentInputError("deployment_error_project")
    pending = (
        session.query(Video.video_id)
        .filter_by(project_id=project_id, deployment_assignment="legacy")
        .first()
    )
    roots = _roots(session, project_id)
    missing_link = False
    if len(roots) == 1:
        linked = (
            session.query(DeploymentFolderLink.id)
            .filter(
                DeploymentFolderLink.project_dir_id == roots[0].id,
                DeploymentFolderLink.folder_path == Deployment.folder_path,
            )
            .exists()
        )
        missing_link = (
            session.query(Deployment.id)
            .filter(
                Deployment.project_id == project_id,
                Deployment.source == "csv",
                Deployment.folder_path.is_not(None),
                ~linked,
            )
            .first()
            is not None
        )
    if pending or missing_link:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return initialize_project(session, project_id)
    return project


def resolved_scope(session, row, roots=None):
    """Durable project identity, with conservative fallback for old/external records."""
    scope = _recorded_scope(session, row, roots)
    if scope is not None and isinstance(row, ReferenceVideo):
        source = (
            session.query(Video)
            .filter(
                Video.project_id == row.project_id,
                func.replace(Video.video_path, "\\", "/") == normalize_path_str(row.video_path),
            )
            .first()
        )
        if source is not None and scope != _recorded_scope(session, source, roots):
            return None
    return scope


def _recorded_scope(session, row, roots=None):
    if row.deployment_id:
        return row.project_id, "deployment", row.deployment_id
    if isinstance(row, Video) and row.deployment_assignment != "legacy":
        return None
    roots = (
        roots if roots is not None else [(r.id, r.path) for r in _roots(session, row.project_id)]
    )
    legacy = deployment_key(row.project_id, row.camera_id, row.video_path, roots)
    if legacy is not None:
        # Explicit external legacy references can follow a resolved ordinary deployment
        # only within their established root scope, never by a cross-root label guess.
        candidates = {
            video.deployment_id
            for video in session.query(Video).filter_by(
                project_id=row.project_id, camera_id=row.camera_id
            )
            if video.deployment_id
            and deployment_key(video.project_id, video.camera_id, video.video_path, roots)
            == legacy
        }
        if len(candidates) == 1:
            return row.project_id, "deployment", candidates.pop()
    return legacy


def _legacy_deployment(session, project_id, label, root, cache):
    root_key = root.id if root else "external"
    key = root_key, label
    if key in cache:
        return cache[key]
    # Existing links retain root separation even when old labels were identical.
    linked = (
        session.query(Deployment)
        .join(DeploymentFolderLink, DeploymentFolderLink.deployment_id == Deployment.id)
        .filter(
            Deployment.project_id == project_id,
            DeploymentFolderLink.project_dir_id == root_key,
        )
        .all()
    )
    row = next((row for row in linked if row.deployment_id == label), None)
    if row is None:
        row = next(
            (
                row
                for row in linked
                if row.source == "inferred"
                and row.deployment_id.startswith(f"{label} [")
                and row.name == label
            ),
            None,
        )
    used = {
        row.deployment_id: row
        for row in session.query(Deployment).filter_by(project_id=project_id)
    }
    if row is None:
        candidate = used.get(label)
        other_root = (
            candidate
            and session.query(DeploymentFolderLink.id)
            .filter(
                DeploymentFolderLink.deployment_id == candidate.id,
                DeploymentFolderLink.project_dir_id != root_key,
            )
            .first()
        )
        if candidate and not other_root:
            row = candidate
        else:
            identifier = label
            suffix = 2
            while identifier in used:
                identifier = f"{label} [{suffix}]"
                suffix += 1
            row = Deployment(
                project_id=project_id, deployment_id=identifier, name=label, source="inferred"
            )
            session.add(row)
            session.flush()
    cache[key] = row
    return row


def _confirmed_link(link, deployments):
    """Only explicit admin rules and declared CSV paths control future scans."""
    return link.source == "manual" or (
        link.source == "csv"
        and link.deployment_id in deployments
        and deployments[link.deployment_id].source == "csv"
    )


def _set_assignment(video, target, source, *, references, preserve_label=False):
    video.deployment_id = target.id if target else None
    if not preserve_label:
        video.camera_id = target.deployment_id if target else None
    video.deployment_assignment = source
    for reference in references:
        reference.deployment_id = video.deployment_id
        if video.camera_id:
            reference.camera_id = video.camera_id


def initialize_project(session, project_id):
    """Initialize pre-feature rows without discarding their historical scope/evidence."""
    project = session.get(Project, project_id)
    if project is None:
        raise DeploymentInputError("deployment_error_project")
    roots = _roots(session, project_id)
    links = {
        (link.project_dir_id, link.folder_path): link
        for link in session.query(DeploymentFolderLink).filter(
            DeploymentFolderLink.project_dir_id.in_([root.id for root in roots])
        )
    }
    if len(roots) == 1:
        declarations = session.query(Deployment).filter(
            Deployment.project_id == project_id,
            Deployment.source == "csv",
            Deployment.folder_path.is_not(None),
        )
        for deployment in declarations:
            key = roots[0].id, deployment.folder_path
            if key not in links:
                link = DeploymentFolderLink(
                    project_dir_id=roots[0].id,
                    folder_path=deployment.folder_path,
                    deployment_id=deployment.id,
                    source=deployment.source,
                )
                session.add(link)
                links[key] = link
    session.flush()
    legacy = (
        session.query(Video).filter_by(project_id=project_id, deployment_assignment="legacy").all()
    )
    if not legacy:
        return project
    dependencies = _AssignmentDependencies(session, project_id)
    locations = _StoredLocations(roots)
    cache = {}
    automatic = (
        _automatic_targets(session, project, roots, legacy)
        if project.deployment_mode == "csv"
        else {}
    )
    # Queries for a new group must not flush every preceding individual video.
    # Assignment updates can be sent in batches while target/link creation stays atomic.
    with session.no_autoflush:
        for video in legacy:
            video.inferred_camera_id = video.inferred_camera_id or video.camera_id
            if not video.camera_id:
                video.deployment_assignment = "unresolved"
                continue
            root, folder = locations.locate(video.video_path)
            if root is None and len(roots) == 1:
                root = roots[0]
            reason = dependencies.block_reason(video)
            references = dependencies.references_for(video)
            if project.deployment_mode == "csv":
                desired = automatic.get(video.video_id)
                if video.camera_id == (desired.deployment_id if desired else None) or not reason:
                    _set_assignment(
                        video,
                        desired,
                        "folder" if desired else "unresolved",
                        references=references,
                    )
                    continue
                # A v28 inventory may coexist with already saved legacy work.
                # Preserve that established scope instead of reinterpreting it.
            target = _legacy_deployment(session, project_id, video.camera_id, root, cache)
            if root and folder is not None and project.deployment_mode != "csv":
                key = root.id, folder
                if key not in links:
                    link = DeploymentFolderLink(
                        project_dir_id=root.id,
                        folder_path=folder,
                        deployment_id=target.id,
                        source="legacy",
                    )
                    session.add(link)
                    links[key] = link
                    # The next root/group must see this link before deciding whether
                    # an equal historical label belongs to a different deployment.
                    session.flush()
            _set_assignment(
                video,
                target,
                "manual" if reason else "folder",
                preserve_label=True,
                references=references,
            )
    session.flush()
    return project


def _automatic_targets(session, project, roots, videos, *, scanned_paths=(), reset_ids=()):
    """Resolve each direct directory once using the registered project inventory."""
    locations = _StoredLocations(roots)
    deployments = {
        row.id: row for row in session.query(Deployment).filter_by(project_id=project.id)
    }
    by_identifier = {row.deployment_id: row for row in deployments.values()}
    links = {
        (row.project_dir_id, row.folder_path): row
        for row in session.query(DeploymentFolderLink).filter(
            DeploymentFolderLink.project_dir_id.in_([root.id for root in roots])
        )
    }
    groups = defaultdict(list)
    explicit_roots = defaultdict(set)
    known_roots = defaultdict(set)
    for (root_id, _), link in links.items():
        if link.deployment_id:
            known_roots[link.deployment_id].add(root_id)
            if _confirmed_link(link, deployments):
                explicit_roots[link.deployment_id].add(root_id)
    for video in videos:
        root, folder = locations.locate(video.video_path)
        if root is None:
            continue
        groups[root.id, folder].append(video)
        if video.deployment_id and video.video_id not in reset_ids:
            known_roots[video.deployment_id].add(root.id)
            if video.deployment_assignment == "manual":
                explicit_roots[video.deployment_id].add(root.id)

    targets = {}
    if project.deployment_mode == "csv":
        index = deployment_id_index(by_identifier)
        candidates_by_folder = {}
        candidate_roots = defaultdict(set)
        for key, members in groups.items():
            root_id, folder = key
            link = links.get(key)
            if link and _confirmed_link(link, deployments):
                targets[key] = deployments.get(link.deployment_id)
                continue
            candidates = {
                identifier
                for baseline in {v.inferred_camera_id or v.camera_id or "" for v in members}
                for identifier in csv_folder_candidates(folder, baseline, index)
            }
            if len(candidates) == 1:
                target = by_identifier[candidates.pop()]
                candidates_by_folder[key] = target
                candidate_roots[target.id].add(root_id)
        for key, target in candidates_by_folder.items():
            allowed = explicit_roots[target.id]
            if key[0] in allowed or (not allowed and len(candidate_roots[target.id]) == 1):
                targets[key] = target
    else:
        inferred = {}
        scanned_labels = {
            (root_id, video.inferred_camera_id or video.camera_id)
            for (root_id, _), members in groups.items()
            for video in members
            if video.video_path in scanned_paths and video.deployment_assignment != "manual"
        }
        for key, members in sorted(groups.items()):
            root_id, _ = key
            link = links.get(key)
            if link and _confirmed_link(link, deployments):
                targets[key] = deployments.get(link.deployment_id)
                continue
            labels = {v.inferred_camera_id or v.camera_id for v in members}
            if len(labels) != 1 or None in labels:
                continue
            label = labels.pop()
            if (root_id, label) not in inferred:
                identifier = label
                suffix = 2
                while identifier in by_identifier:
                    existing = by_identifier[identifier]
                    if not known_roots[existing.id] or root_id in known_roots[existing.id]:
                        break
                    identifier = f"{label} [{suffix}]"
                    suffix += 1
                target = by_identifier.get(identifier)
                if target is None and (root_id, label) in scanned_labels:
                    target = Deployment(
                        project_id=project.id,
                        deployment_id=identifier,
                        name=label,
                        source="inferred",
                    )
                    session.add(target)
                    session.flush([target])
                    by_identifier[identifier] = target
                if target is not None:
                    known_roots[target.id].add(root_id)
                inferred[root_id, label] = target
            targets[key] = inferred[root_id, label]
    return {video.video_id: target for key, target in targets.items() for video in groups[key]}


def _assignment_changes(video, target):
    return video.deployment_id != (target.id if target else None) or video.camera_id != (
        target.deployment_id if target else None
    )


def reconcile_scanned(session, project_id, paths, new_paths):
    project = session.get(Project, project_id)
    roots = _roots(session, project_id)
    videos = session.query(Video).filter_by(project_id=project_id).all()
    if not any(v.video_path in paths and v.deployment_assignment != "manual" for v in videos):
        return
    targets = _automatic_targets(session, project, roots, videos, scanned_paths=paths)
    selected = [
        (video, targets.get(video.video_id))
        for video in videos
        if video.video_path in paths
        and video.deployment_assignment != "manual"
        and (
            video.video_path in new_paths
            or _assignment_changes(video, targets.get(video.video_id))
        )
    ]
    if not selected:
        return
    dependencies = _AssignmentDependencies(session, project_id, [video for video, _ in selected])
    root_pairs = [(root.id, root.path) for root in roots]
    for video, target in selected:
        references = dependencies.references_for(video)
        reason = dependencies.block_reason(video)
        if video.video_path in new_paths and reason == "deployment_block_reference":
            # A prepared external clip already has a scope when it first appears in a scan.
            scope = _recorded_scope(session, references[0], root_pairs)
            if scope and scope[1] == "deployment":
                prior_target = session.get(Deployment, scope[2])
                if prior_target and prior_target.project_id == project_id:
                    _set_assignment(video, prior_target, "manual", references=references)
                    continue
        if not _assignment_changes(video, target):
            continue
        if reason:
            # Pin established work whose inferred scope changed.
            video.deployment_assignment = "manual"
        else:
            _set_assignment(
                video,
                target,
                "folder" if target else "unresolved",
                references=references,
            )


def reconstruct_package_assignments(session, project_id):
    """v1 transports effective camera_id only; reconstruct pinned local assignments.

    No unversioned archive fields are added and recipient rescans cannot undo them.
    """
    initialize_project(session, project_id)
    for video in session.query(Video).filter_by(project_id=project_id):
        video.deployment_assignment = "manual"
    session.flush()


def restore_effective_assignments(session, project_id, assignments):
    """Restore the existing metadata CSV's effective labels, in its write transaction."""
    project = initialize_project(session, project_id)
    locations = _StoredLocations(_roots(session, project_id))
    dependencies = _AssignmentDependencies(session, project_id)
    by_identifier = {
        row.deployment_id: row
        for row in session.query(Deployment).filter_by(project_id=project_id)
    }
    legacy = {}
    for video_id, label in assignments.items():
        video = session.get(Video, video_id)
        if video is None or video.project_id != project_id:
            raise DeploymentInputError("deployment_error_selection")
        if video.camera_id == label:
            continue  # Preserve the durable assignment and its provenance on a round trip.
        target = by_identifier.get(label) if label else None
        if label and target is None:
            if project.deployment_mode == "csv":
                raise DeploymentInputError("deployment_error_metadata_id", value=label)
            root, _ = locations.locate(video.video_path)
            key = (root.id if root else None, label)
            if key not in legacy:
                legacy[key] = _legacy_deployment(session, project_id, label, root, {})
            target = legacy[key]
        if dependencies.block_reason(video):
            raise DeploymentInputError("deployment_error_blocked", count=1)
        _set_assignment(video, target, "manual", references=dependencies.references_for(video))


def _reference_tagged(session):
    return (
        session.query(VideoTag.video_id)
        .join(Tag, Tag.id == VideoTag.tag_id)
        .filter(VideoTag.video_id == Video.video_id, Tag.key == "reference_video")
        .exists()
    )


def _folder_videos(session, project_id, root, relative, locations):
    directory = Path(normalize_path_str(root.path)) / relative if root else Path(relative)
    prefix = directory.as_posix().rstrip("/") + "/"
    path = func.replace(Video.video_path, "\\", "/")
    videos = (
        session.query(Video, _reference_tagged(session))
        .filter(
            Video.project_id == project_id,
            path.startswith(prefix, autoescape=True),
            func.instr(func.substr(path, len(prefix) + 1), "/") == 0,
        )
        .order_by(Video.video_path)
    )
    return [
        (video, reference)
        for video, reference in videos
        if locations.locate(video.video_path)[0] is root
        and (root is None or locations.locate(video.video_path)[1] == relative)
    ]


def _management_video(video, reference, locations, deployments):
    root, folder = locations.locate(video.video_path)
    deployment = deployments.get(video.deployment_id)
    return {
        "video_id": video.video_id,
        "video_path": video.video_path,
        "filename": Path(normalize_path_str(video.video_path)).name,
        "root_id": root.id if root else None,
        "folder": folder,
        "deployment_id": video.deployment_id,
        "deployment": deployment.deployment_id if deployment else None,
        "inferred": video.inferred_camera_id,
        "assignment": video.deployment_assignment,
        "reference": reference,
        "missing": bool(video.is_missing),
    }


def _management_link(link, deployments):
    target = deployments.get(link.deployment_id)
    return {
        "id": link.id,
        "root_id": link.project_dir_id,
        "folder": link.folder_path,
        "deployment_id": link.deployment_id,
        "deployment": target.deployment_id if target else None,
        "source": link.source,
    }


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class DeploymentMixin(ProviderBase):
    def get_deployment_summary(self, project_id):
        with self.Session.begin() as session:
            ensure_project_initialized(session, project_id)
            reference = _reference_tagged(session)
            groups = (
                session.query(Video.deployment_id, reference, func.count())
                .filter(Video.project_id == project_id)
                .group_by(Video.deployment_id, reference)
                .all()
            )
            ordinary = {did for did, tagged, count in groups if did and not tagged}
            unresolved = sum(count for did, tagged, count in groups if did is None)
            unmatched = sum(
                count for did, tagged, count in groups if did and tagged and did not in ordinary
            )
            return {
                "deployments": session.query(Deployment).filter_by(project_id=project_id).count(),
                "references": sum(count for did, tagged, count in groups if tagged),
                "unresolved": unresolved,
                "unmatched_references": unmatched,
            }

    def get_deployment_management(self, project_id):
        with self.Session.begin() as session:
            ensure_project_initialized(session, project_id)
            roots = _roots(session, project_id)
            locations = _StoredLocations(roots)
            deployments = {
                r.id: r for r in session.query(Deployment).filter_by(project_id=project_id)
            }
            videos = [
                _management_video(video, reference, locations, deployments)
                for video, reference in (
                    session.query(Video, _reference_tagged(session))
                    .filter(Video.project_id == project_id)
                    .order_by(Video.video_path)
                )
            ]
            links = [
                _management_link(link, deployments)
                for link in session.query(DeploymentFolderLink).filter(
                    DeploymentFolderLink.project_dir_id.in_([root.id for root in roots])
                )
                if _confirmed_link(link, deployments)
            ]
            return {
                "videos": videos,
                "links": links,
                "roots": [{"id": r.id, "path": r.path} for r in roots],
                "folders": group_deployment_folders(videos, links),
            }

    def get_deployment_folder(self, project_id, root_id, folder):
        """Refresh one folder for review, including clips resolved elsewhere in the project."""
        with self.Session.begin() as session:
            roots = _roots(session, project_id)
            root = next((row for row in roots if row.id == root_id), None)
            if root_id is not None and root is None:
                raise DeploymentInputError("deployment_error_root")
            locations = _StoredLocations(roots)
            deployments = {
                row.id: row for row in session.query(Deployment).filter_by(project_id=project_id)
            }
            videos = [
                _management_video(video, reference, locations, deployments)
                for video, reference in _folder_videos(
                    session, project_id, root, folder, locations
                )
            ]
            reference_targets = {
                row["deployment_id"] for row in videos if row["reference"] and row["deployment_id"]
            }
            ordinary = {
                identifier
                for (identifier,) in session.query(Video.deployment_id)
                .filter(
                    Video.project_id == project_id,
                    Video.deployment_id.in_(reference_targets),
                    ~_reference_tagged(session),
                )
                .distinct()
            }
            links = [
                _management_link(row, deployments)
                for row in session.query(DeploymentFolderLink).filter_by(
                    project_dir_id=root_id, folder_path=folder
                )
                if _confirmed_link(row, deployments)
            ]
            summaries = group_deployment_folders(videos, links, ordinary=ordinary)
            return (summaries[0] if summaries else None), videos

    def save_deployment(self, project_id, values, *, deployment_id=None):
        """Create or edit inventory. The stable public key is immutable after creation."""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerow({key: values.get(key) or "" for key in CSV_COLUMNS})
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            initialize_project(session, project_id)
            roots = _roots(session, project_id)
            preview = parse_deployment_csv(
                buffer.getvalue().encode(), roots[0].path if len(roots) == 1 else ""
            )
            row_values = dict(preview.rows[0])
            folder = row_values.pop("folder_path")
            if deployment_id:
                row = session.get(Deployment, deployment_id)
                if row is None or row.project_id != project_id:
                    raise DeploymentInputError("deployment_error_target")
                if row.deployment_id != row_values["deployment_id"]:
                    raise DeploymentInputError("deployment_error_immutable")
                for key, value in row_values.items():
                    if key not in {"deployment_id", "source"}:
                        setattr(row, key, value)
                if folder != row.folder_path:
                    raise DeploymentInputError("deployment_error_edit_folder")
            else:
                if (
                    session.query(Deployment.id)
                    .filter_by(project_id=project_id, deployment_id=row_values["deployment_id"])
                    .first()
                ):
                    raise DeploymentInputError(
                        "deployment_error_duplicate", row=2, value=row_values["deployment_id"]
                    )
                if folder is not None:
                    # Applying a folder is a separately previewed operation, never a
                    # hidden side effect of creating/editing inventory metadata.
                    raise DeploymentInputError("deployment_error_edit_folder")
                row = Deployment(project_id=project_id, **row_values)
                session.add(row)
            session.flush()
            return row.id

    def _deployment_plan(self, session, project_id, video_ids, target_id, action, folder):
        project = initialize_project(session, project_id)
        roots = _roots(session, project_id)
        locations = _StoredLocations(roots)
        target = session.get(Deployment, target_id) if target_id else None
        if target_id and (target is None or target.project_id != project_id):
            raise DeploymentInputError("deployment_error_target")
        if action not in {"assign", "reset", "folder"}:
            raise DeploymentInputError("deployment_error_action")
        folder_info = None
        link = None
        if action == "folder":
            if not isinstance(folder, dict):
                raise DeploymentInputError("deployment_error_path", row=0, value="")
            root = next((r for r in roots if r.id == folder.get("root_id")), None)
            if root is None:
                raise DeploymentInputError("deployment_error_root")
            relative = _folder_path(folder.get("path", ""), _path(root.path), 0)
            folder_info = {"root_id": root.id, "path": relative}
            videos = [
                video
                for video, _ in _folder_videos(session, project_id, root, relative, locations)
            ]
            link = (
                session.query(DeploymentFolderLink)
                .filter_by(project_dir_id=root.id, folder_path=relative)
                .first()
            )
        else:
            if not video_ids or len(set(video_ids)) != len(video_ids):
                raise DeploymentInputError("deployment_error_selection")
            videos = (
                session.query(Video)
                .filter(Video.video_id.in_(video_ids), Video.project_id == project_id)
                .all()
            )
            if {v.video_id for v in videos} != set(video_ids):
                raise DeploymentInputError("deployment_error_selection")
        if action == "reset":
            scope = (
                session.query(Video).filter_by(project_id=project_id).all()
                if len(roots) > 1
                else videos
            )
            targets = _automatic_targets(session, project, roots, scope, reset_ids=set(video_ids))
        else:
            targets = {video.video_id: target for video in videos}
        rows = []
        dependencies = _AssignmentDependencies(session, project_id, videos)
        for video in sorted(videos, key=lambda v: v.video_id):
            root, relative = locations.locate(video.video_path)
            preserve = action == "folder" and video.deployment_assignment == "manual"
            desired = targets.get(video.video_id)
            effective_change = not preserve and _assignment_changes(video, desired)
            blocker = (
                (
                    "deployment_error_membership"
                    if root is None
                    else dependencies.block_reason(video)
                )
                if effective_change
                else None
            )
            rows.append(
                {
                    "video_id": video.video_id,
                    "path": video.video_path,
                    "folder": relative,
                    "root_id": root.id if root else None,
                    "baseline": video.inferred_camera_id,
                    "before": video.deployment_id,
                    "before_label": video.camera_id,
                    "after": desired.id if desired else None,
                    "after_label": desired.deployment_id if desired else None,
                    "source": video.deployment_assignment,
                    "preserved": preserve,
                    "changed": effective_change,
                    "blocked": blocker,
                }
            )
        plan = {
            "project_id": project_id,
            "action": action,
            "target_id": target_id,
            "folder": folder_info,
            "rows": rows,
            "roots": [(r.id, r.path) for r in roots],
            "link": (link.deployment_id, link.source) if link is not None else None,
        }
        preview = plan | {
            "fingerprint": _fingerprint(plan),
            "changed": sum(r["changed"] for r in rows),
            "blocked": sum(bool(r["blocked"]) for r in rows),
            "unchanged": sum(not r["changed"] for r in rows),
        }
        return preview, {video.video_id: video for video in videos}, targets, dependencies, link

    def preview_deployment_assignment(
        self, project_id, video_ids=(), target_id=None, *, action="assign", folder=None
    ):
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            plan, *_ = self._deployment_plan(
                session, project_id, video_ids, target_id, action, folder
            )
            return plan

    def apply_deployment_assignment(self, preview):
        """Rebuild the entire preview under the write lock; stale/blocked means no writes."""
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            fresh, videos, targets, dependencies, link = self._deployment_plan(
                session,
                preview["project_id"],
                [r["video_id"] for r in preview["rows"]],
                preview["target_id"],
                preview["action"],
                preview["folder"],
            )
            if fresh["fingerprint"] != preview["fingerprint"]:
                raise DeploymentInputError("deployment_error_stale")
            if fresh["blocked"]:
                raise DeploymentInputError("deployment_error_blocked", count=fresh["blocked"])
            if fresh["action"] == "folder":
                folder = fresh["folder"]
                if link is None:
                    link = DeploymentFolderLink(
                        project_dir_id=folder["root_id"], folder_path=folder["path"]
                    )
                    session.add(link)
                link.deployment_id, link.source = fresh["target_id"], "manual"
            for item in fresh["rows"]:
                if item["preserved"]:
                    continue
                video = videos[item["video_id"]]
                target = targets.get(video.video_id)
                source = "folder" if target else "unresolved"
                if fresh["action"] == "assign":
                    source = "manual"
                _set_assignment(
                    video, target, source, references=dependencies.references_for(video)
                )
            return {"changed": fresh["changed"], "unchanged": fresh["unchanged"]}
