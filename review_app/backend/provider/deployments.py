"""Deployment assignment for scans and administration."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path, PurePosixPath

from sqlalchemy import func, or_

from review_app.backend.camera_layout import deployment_key
from review_app.backend.db.models import (
    Deployment,
    DeploymentFolderLink,
    DeploymentFolderReview,
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
from review_app.backend.deployment_review import (
    available_folder_candidates,
    group_deployment_folders,
)
from review_app.backend.deployments import (
    DeploymentInputError,
    relative_deployment_folder,
    validate_deployment_fields,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.base import ProviderBase
from review_app.backend.utils import sqlite_values


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
        video_ids = None if videos is None else sqlite_values(video.video_id for video in videos)
        paths = (
            None
            if videos is None
            else sqlite_values(normalize_path_str(video.video_path) for video in videos)
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
            session.query(DistanceReviewTask.video_id)
            .select_from(DistanceIndividual)
            .join(DistanceIndividual.task)
            .join(Video, Video.video_id == DistanceReviewTask.video_id)
            .filter(Video.project_id == project_id)
        )
        if video_ids is not None:
            tasks = tasks.filter(DistanceReviewTask.video_id.in_(video_ids))
            individuals = individuals.filter(DistanceReviewTask.video_id.in_(video_ids))
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
    """Read without a write lock unless legacy videos need initialization."""
    project = session.get(Project, project_id)
    if project is None:
        raise DeploymentInputError("deployment_error_project")
    pending = (
        session.query(Video.video_id)
        .filter_by(project_id=project_id, deployment_assignment="legacy")
        .first()
    )
    if pending:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return initialize_project(session, project_id)
    return project


def video_deployment_for_label(video, label):
    """Match a known clip by its effective deployment or its historical folder label."""
    return (
        video.deployment_id
        if label and label in (video.camera_id, video.folder_camera_label)
        else None
    )


def _deployments_by_scope(project_id, label, roots, videos):
    """Prefer an effective deployment identifier over a reused folder label."""
    matches = defaultdict(lambda: (set(), set()))
    for video in videos:
        if not video.deployment_id:
            continue
        scope = deployment_key(project_id, label, video.video_path, roots)
        if scope is not None:
            exact, inferred = matches[scope]
            (exact if video.camera_id == label else inferred).add(video.deployment_id)
    return {
        scope: next(iter(candidates))
        for scope, (exact, inferred) in matches.items()
        if len(candidates := exact or inferred) == 1
    }


def resolve_reference_deployment(session, project_id, video_path, label):
    """Resolve a legacy label once, within its registered import root."""
    if not label:
        return None
    path = normalize_path_str(video_path)
    videos = session.query(Video).filter_by(project_id=project_id)
    source = videos.filter_by(video_path=path).one_or_none()
    if source is None:
        source = videos.filter(func.replace(Video.video_path, "\\", "/") == path).first()
    if source is not None:
        return video_deployment_for_label(source, label)
    roots = [(root.id, root.path) for root in _roots(session, project_id)]
    scope = deployment_key(project_id, label, video_path, roots)
    if scope is None:
        return None
    candidates = videos.filter(
        Video.deployment_id.is_not(None),
        or_(Video.camera_id == label, Video.folder_camera_label == label),
    )
    return _deployments_by_scope(project_id, label, roots, candidates).get(scope)


def assign_reference_deployment(reference, deployment_id):
    reference.deployment_id = deployment_id
    if deployment_id is not None:
        reference.unresolved_camera_label = None


def reconcile_pending_references(session, project_id, roots, videos=None, *, within_roots=False):
    """Resolve registrations that predate assignments, in the same transaction."""
    pending = (
        session.query(ReferenceVideo)
        .filter(
            ReferenceVideo.project_id == project_id,
            ReferenceVideo.deployment_id.is_(None),
            ReferenceVideo.unresolved_camera_label.is_not(None),
        )
        .all()
    )
    if within_roots:
        paths = [PurePosixPath(normalize_path_str(root.path)) for root in roots]
        pending = [
            row
            for row in pending
            if any(
                PurePosixPath(normalize_path_str(row.video_path)).is_relative_to(path)
                for path in paths
            )
        ]
    if not pending:
        return
    if videos is None:
        videos = session.query(Video).filter_by(project_id=project_id)
    labels = {row.unresolved_camera_label for row in pending}
    by_path = {}
    by_label = defaultdict(list)
    for video in videos:
        by_path[normalize_path_str(video.video_path)] = video
        for label in {video.camera_id, video.folder_camera_label} & labels:
            by_label[label].append(video)
    root_paths = [(root.id, root.path) for root in roots]
    matches = {}
    for reference in pending:
        label = reference.unresolved_camera_label
        source = by_path.get(normalize_path_str(reference.video_path))
        if source is not None:
            candidate = video_deployment_for_label(source, label)
        else:
            scope = deployment_key(project_id, label, reference.video_path, root_paths)
            if scope is None:
                continue
            if label not in matches:
                matches[label] = _deployments_by_scope(
                    project_id, label, root_paths, by_label[label]
                )
            candidate = matches[label].get(scope)
        if candidate is not None:
            assign_reference_deployment(reference, candidate)


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
                if row.deployment_id.startswith(f"{label} [") and row.name == label
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
            row = Deployment(project_id=project_id, deployment_id=identifier, name=label)
            session.add(row)
            session.flush()
    cache[key] = row
    return row


def _set_assignment(video, target, source, *, references):
    video.deployment_id = target.id if target else None
    video.deployment = target
    video.deployment_assignment = source
    for reference in references:
        assign_reference_deployment(reference, video.deployment_id)


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
    legacy = (
        session.query(Video).filter_by(project_id=project_id, deployment_assignment="legacy").all()
    )
    if not legacy:
        return project
    dependencies = _AssignmentDependencies(session, project_id)
    locations = _StoredLocations(roots)
    cache = {}
    # Queries for a new group must not flush every preceding individual video.
    # Assignment updates can be sent in batches while target/link creation stays atomic.
    with session.no_autoflush:
        for video in legacy:
            if not video.folder_camera_label:
                video.deployment_assignment = "unresolved"
                continue
            root, folder = locations.locate(video.video_path)
            if root is None and len(roots) == 1:
                root = roots[0]
            reason = dependencies.block_reason(video)
            references = dependencies.references_for(video)
            target = _legacy_deployment(
                session, project_id, video.folder_camera_label, root, cache
            )
            if root and folder is not None:
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
                references=[
                    ref
                    for ref in references
                    if ref.deployment_id is None
                    and ref.unresolved_camera_label
                    in (video.folder_camera_label, target.deployment_id)
                ],
            )
    reconcile_pending_references(session, project_id, roots)
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
    known_roots = defaultdict(set)
    for (root_id, _), link in links.items():
        if link.deployment_id:
            known_roots[link.deployment_id].add(root_id)
    for video in videos:
        root, folder = locations.locate(video.video_path)
        if root is None:
            continue
        groups[root.id, folder].append(video)
        if video.deployment_id and video.video_id not in reset_ids:
            known_roots[video.deployment_id].add(root.id)

    targets = {}
    inferred = {}
    scanned_labels = {
        (root_id, video.folder_camera_label)
        for (root_id, _), members in groups.items()
        for video in members
        if video.video_path in scanned_paths and video.deployment_assignment != "manual"
    }
    for key, members in sorted(groups.items()):
        root_id, _ = key
        link = links.get(key)
        if link and link.source == "manual":
            targets[key] = deployments.get(link.deployment_id)
            continue
        labels = {v.folder_camera_label for v in members}
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
    return video.deployment_id != (target.id if target else None)


def reconcile_scanned(session, project_id, paths, new_paths):
    project = session.get(Project, project_id)
    roots = _roots(session, project_id)
    videos = session.query(Video).filter_by(project_id=project_id).all()
    _reconcile_scanned_assignments(session, project, roots, videos, paths, new_paths)
    reconcile_pending_references(session, project_id, roots, videos)


def _reconcile_scanned_assignments(session, project, roots, videos, paths, new_paths):
    if not any(v.video_path in paths and v.deployment_assignment != "manual" for v in videos):
        return
    project_id = project.id
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
    reviewed = {
        (row.project_dir_id, row.folder_path) for row in _folder_reviews(session, project_id)
    }
    if reviewed:
        locations = _StoredLocations(roots)
        reference_ids = {
            identifier
            for (identifier,) in session.query(Video.video_id).filter(
                Video.project_id == project_id, _reference_tagged(session)
            )
        }
        retained = []
        for video, target in selected:
            if video.video_id in reference_ids and video.video_path not in new_paths:
                root, folder = locations.locate(video.video_path)
                if root and (root.id, folder) in reviewed:
                    continue
            retained.append((video, target))
        selected = retained
    if not selected:
        return
    dependencies = _AssignmentDependencies(session, project_id, [video for video, _ in selected])
    for video, target in selected:
        references = dependencies.references_for(video)
        reason = dependencies.block_reason(video)
        if video.video_path in new_paths and reason == "deployment_block_reference":
            # A calibrated external clip can appear in a later scan.
            reference = references[0]
            prior_target = (
                session.get(Deployment, reference.deployment_id)
                if reference.deployment_id
                else None
            )
            if prior_target is not None:
                _set_assignment(video, prior_target, "manual", references=references)
                continue
            if target and reference.unresolved_camera_label in (
                video.folder_camera_label,
                target.deployment_id,
            ):
                _set_assignment(video, target, "manual", references=references)
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
    initialize_project(session, project_id)
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
        "inferred": video.folder_camera_label,
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


def _playback_fields(video):
    return {
        "video_id": video.video_id,
        "video_path": video.video_path,
        "filename": Path(normalize_path_str(video.video_path)).name,
        "is_web_safe": video.is_web_safe,
        "transcoded_path": video.transcoded_path,
    }


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _folder_reviews(session, project_id):
    return (
        session.query(DeploymentFolderReview)
        .join(ProjectDir)
        .filter(ProjectDir.project_id == project_id)
    )


def _show_folder_review(folder, review, inventory, ordinary):
    if review is None or not folder["references"]:
        return
    new_candidates = available_folder_candidates(folder, inventory, ordinary).difference(
        review.available_candidates
    )
    folder["review_note"] = review.note
    folder["ordinary_unavailable"] = not new_candidates
    folder["new_comparison_candidates"] = sorted(new_candidates)
    folder["needs_reference_review"] = bool(new_candidates)
    folder["needs_attention"] = bool(
        new_candidates or folder["unresolved"] > folder["unresolved_references"]
    )


def _apply_folder_reviews(folders, reviews, inventory, available):
    by_folder = {(row.project_dir_id, row.folder_path): row for row in reviews}
    for folder in folders:
        _show_folder_review(
            folder, by_folder.get((folder["root_id"], folder["folder"])), inventory, available
        )


class DeploymentMixin(ProviderBase):
    def initialize_project_deployments(self, project_id: str) -> bool:
        """Prepare saved deployment assignments when entering a project."""
        with self.Session.begin() as session:
            project = session.get(Project, project_id)
            if project is None:
                return False
            ensure_project_initialized(session, project_id)
            return True

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
            unresolved_ordinary = any(did is None and not tagged for did, tagged, _ in groups)
            unmatched = sum(
                count for did, tagged, count in groups if did and tagged and did not in ordinary
            )
            pending = bool(unmatched or any(did is None and tagged for did, tagged, _ in groups))
            reviews = _folder_reviews(session, project_id).all()
            if reviews:
                locations = _StoredLocations(_roots(session, project_id))
                deployments = {
                    row.id: row
                    for row in session.query(Deployment).filter_by(project_id=project_id)
                }
                references = [
                    _management_video(video, True, locations, deployments)
                    for video in session.query(Video).filter(
                        Video.project_id == project_id, reference
                    )
                ]
                _, available = self._ordinary_deployments(session, project_id)
                folders = group_deployment_folders(references, (), ordinary=ordinary)
                _apply_folder_reviews(
                    folders, reviews, self._comparison_inventory(deployments), available
                )
                pending = any(folder["needs_reference_review"] for folder in folders)
            return {
                "deployments": session.query(Deployment).filter_by(project_id=project_id).count(),
                "references": sum(count for did, tagged, count in groups if tagged),
                "unresolved": unresolved,
                "unmatched_references": unmatched,
                "has_pending_reference_reviews": pending,
                "has_pending_deployment_issues": pending or unresolved_ordinary,
            }

    def get_deployment_management(self, project_id):
        with self.Session.begin() as session:
            ensure_project_initialized(session, project_id)
            return self._deployment_management(
                session, project_id, _folder_reviews(session, project_id).all()
            )

    def _deployment_management(self, session, project_id, reviews):
        roots = _roots(session, project_id)
        locations = _StoredLocations(roots)
        deployments = {r.id: r for r in session.query(Deployment).filter_by(project_id=project_id)}
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
            if link.source == "manual"
        ]
        folders = group_deployment_folders(videos, links)
        available = {
            row["deployment_id"]
            for row in videos
            if not row["reference"] and not row["missing"] and row["deployment_id"]
        }
        _apply_folder_reviews(folders, reviews, self._comparison_inventory(deployments), available)
        return {
            "videos": videos,
            "links": links,
            "roots": [{"id": r.id, "path": r.path} for r in roots],
            "folders": folders,
        }

    @staticmethod
    def _comparison_inventory(deployments):
        return [
            {
                "id": row.id,
                "deployment_id": row.deployment_id,
                "camera_id": row.camera_id,
                "transect_id": row.transect_id,
            }
            for row in deployments.values()
        ]

    def _ordinary_deployments(self, session, project_id):
        rows = (
            session.query(Video.deployment_id, Video.is_missing)
            .filter(
                Video.project_id == project_id,
                Video.deployment_id.is_not(None),
                ~_reference_tagged(session),
            )
            .distinct()
            .all()
        )
        return {identifier for identifier, _ in rows}, {
            identifier for identifier, missing in rows if not missing
        }

    def get_deployment_folder(self, project_id, root_id, folder):
        """Refresh a folder and find the project's deployments with ordinary footage."""
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
                | _playback_fields(video)
                for video, reference in _folder_videos(
                    session, project_id, root, folder, locations
                )
            ]
            ordinary, available = self._ordinary_deployments(session, project_id)
            links = [
                _management_link(row, deployments)
                for row in session.query(DeploymentFolderLink).filter_by(
                    project_dir_id=root_id, folder_path=folder
                )
                if row.source == "manual"
            ]
            summaries = group_deployment_folders(videos, links, ordinary=ordinary)
            if summaries and root_id is not None:
                review = session.get(DeploymentFolderReview, (root_id, folder))
                _show_folder_review(
                    summaries[0], review, self._comparison_inventory(deployments), available
                )
            return (summaries[0] if summaries else None), videos, ordinary

    def set_folder_ordinary_unavailable(self, project_id, root_id, folder, note):
        """A string records the outcome. None reopens the folder manually."""
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            root = session.get(ProjectDir, root_id) if root_id else None
            if root is None or root.project_id != project_id:
                raise DeploymentInputError("deployment_error_root")
            folder = relative_deployment_folder(folder, _path(root.path))
            review = session.get(DeploymentFolderReview, (root_id, folder))
            if note is None:
                if review is not None:
                    session.delete(review)
                return
            inventory = {
                row.id: row for row in session.query(Deployment).filter_by(project_id=project_id)
            }
            locations = _StoredLocations(_roots(session, project_id))
            videos = [
                _management_video(video, reference, locations, inventory)
                for video, reference in _folder_videos(
                    session, project_id, root, folder, locations
                )
            ]
            ordinary, available = self._ordinary_deployments(session, project_id)
            summaries = group_deployment_folders(videos, (), ordinary=ordinary)
            if not summaries or not summaries[0]["references"]:
                raise DeploymentInputError("deployment_error_selection")
            candidates = available_folder_candidates(
                summaries[0],
                self._comparison_inventory(inventory),
                available,
            )
            if review is None:
                review = DeploymentFolderReview(project_dir_id=root_id, folder_path=folder)
                session.add(review)
            review.note = note.strip() or None
            review.available_candidates = sorted(candidates)

    def _comparison_clips(self, session, project_id, deployment_id):
        return session.query(Video).filter(
            Video.project_id == project_id,
            Video.deployment_id == deployment_id,
            ~_reference_tagged(session),
        )

    def get_deployment_comparison_clips(self, project_id, deployment_id):
        """List ordinary clips by path, with available footage first."""
        with self.Session() as session:
            rows = (
                self._comparison_clips(session, project_id, deployment_id)
                .with_entities(Video.video_id, Video.video_path, Video.is_missing)
                .order_by(Video.is_missing, Video.video_path)
            )
            return [
                {
                    "video_id": identifier,
                    "video_path": path,
                    "filename": Path(normalize_path_str(path)).name,
                    "missing": bool(missing),
                }
                for identifier, path, missing in rows
            ]

    def get_deployment_comparison_clip(self, project_id, deployment_id, video_id=None):
        """Load playback details for the selected clip, or the first available clip."""
        with self.Session() as session:
            clips = self._comparison_clips(session, project_id, deployment_id)
            if video_id is not None:
                clips = clips.filter(Video.video_id == video_id)
            video = clips.order_by(Video.is_missing, Video.video_path).first()
            if video is None:
                return None
            locations = _StoredLocations(_roots(session, project_id))
            root, folder = locations.locate(video.video_path)
            return _playback_fields(video) | {
                "root_path": root.path if root else None,
                "folder": folder
                if root
                else Path(normalize_path_str(video.video_path)).parent.as_posix(),
            }

    def save_deployment(self, project_id, values, *, deployment_id=None):
        """Create or edit inventory. The stable public key is immutable after creation."""
        row_values = validate_deployment_fields(values)
        if values.get("folder_path"):
            raise DeploymentInputError("deployment_error_edit_folder")
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            initialize_project(session, project_id)
            if deployment_id:
                row = session.get(Deployment, deployment_id)
                if row is None or row.project_id != project_id:
                    raise DeploymentInputError("deployment_error_target")
                if row.deployment_id != row_values["deployment_id"]:
                    raise DeploymentInputError("deployment_error_immutable")
                for key, value in row_values.items():
                    if key != "deployment_id":
                        setattr(row, key, value)
            else:
                if (
                    session.query(Deployment.id)
                    .filter_by(project_id=project_id, deployment_id=row_values["deployment_id"])
                    .first()
                ):
                    raise DeploymentInputError(
                        "deployment_error_duplicate", value=row_values["deployment_id"]
                    )
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
            relative = relative_deployment_folder(folder.get("path", ""), _path(root.path))
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
                .filter(
                    Video.video_id.in_(sqlite_values(video_ids)), Video.project_id == project_id
                )
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
                    "baseline": video.folder_camera_label,
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
            reviewed_folders = (
                {(fresh["folder"]["root_id"], fresh["folder"]["path"])}
                if fresh["action"] == "folder"
                else {(row["root_id"], row["folder"]) for row in fresh["rows"] if row["changed"]}
            )
            if reviewed_folders:
                for review in _folder_reviews(session, fresh["project_id"]):
                    if (review.project_dir_id, review.folder_path) in reviewed_folders:
                        session.delete(review)
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
