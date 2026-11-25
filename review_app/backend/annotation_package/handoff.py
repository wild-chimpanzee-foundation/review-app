"""Prepare once and download the frozen handout, using the caller's transaction."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from review_app.backend.annotation_package import deployments, distance, species
from review_app.backend.annotation_package.archive import (
    DISTANCE_SETTINGS_VERSION,
    FOLDER_REVIEW_VERSION,
    FORMAT,
    VERSION,
    decode_archive,
    encode_archive,
)
from review_app.backend.annotation_package.identity import IdentityMap
from review_app.backend.annotation_package.scope import (
    AcceptanceResult,
    AssignmentRequest,
    OpenedAssignment,
    PreparedAssignment,
    ReturnPreview,
    bound_videos,
    id_selection,
    select_videos,
)
from review_app.backend.annotation_package.species import (
    collect,
    restore_work,
    timestamp,
    vocabulary,
)
from review_app.backend.db.models import (
    Annotator,
    HandoffAssignment,
    IndividualObservation,
    ModelAnnotation,
    Project,
    ProjectDir,
    ProjectSpecies,
    Species,
    Video,
    VideoAssignment,
)
from review_app.backend.provider.distance_species import (
    distance_species_settings,
    save_distance_species_settings,
)


def prepare(session: Session, project_id: str, request: AssignmentRequest) -> PreparedAssignment:
    project, videos, roots = select_videos(session, project_id, request)
    if request.stage == "distance":
        from review_app.backend.provider.deployments import reconcile_pending_references

        reconcile_pending_references(session, project_id, roots, within_roots=True)
        context, work = distance.collect(session, project, videos, roots, request.target_species)
    else:
        context, work = collect(session, project, videos, roots)
    version = 1
    deployment_context = deployments.collect(session, project_id, context, work, roots)
    if deployment_context is not None:
        context["deployments"] = deployment_context
        version = FOLDER_REVIEW_VERSION if "folder_reviews" in deployment_context else VERSION
    if request.stage == "distance":
        target = session.query(Species).filter_by(scientific_name=request.target_species).one()
        rules = distance_species_settings(session, project_id, target.id)
        context["distance"]["requirements"] = {
            "require_age": bool(rules.require_age),
            "require_sex": bool(rules.require_sex),
        }
        version = DISTANCE_SETTINGS_VERSION
    assignment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    manifest = {
        "format": FORMAT,
        "version": version,
        "direction": "setup",
        "assignment_id": assignment_id,
        "source_project_id": project_id,
        "recipient": request.recipient.strip(),
        "stage": request.stage,
        "target_species": request.target_species,
        "created_at": now.isoformat(),
        "work_keys": [video.video_id for video in videos],
        "roots": sorted({row["root_id"] for row in context["media"]}),
    }
    data = encode_archive(manifest, context, work)
    handoff = HandoffAssignment(
        assignment_id=assignment_id,
        project_id=project_id,
        role="origin",
        source_project_id=project_id,
        recipient=manifest["recipient"],
        stage=request.stage,
        target_species=request.target_species,
        created_at=now,
        scope={
            "work_keys": manifest["work_keys"],
            "roots": manifest["roots"],
            "media": context["media"],
        },
        setup_archive=data,
    )
    session.add(handoff)
    session.flush()
    identities = IdentityMap(session, handoff.id)
    for kind, ids in (
        ("video", [r["video_id"] for r in context["media"]]),
        ("root", manifest["roots"]),
        ("prediction", [row["id"] for row in context["predictions"]]),
    ):
        for value in ids:
            identities.bind(kind, value, value)
    if request.stage == "distance":
        from review_app.backend.annotation_package.distance_handoff import bind_origin

        bind_origin(identities, context, work)
    for row in session.query(IndividualObservation).filter(
        IndividualObservation.video_id.in_(id_selection(manifest["work_keys"]))
    ):
        identities.bind(
            "observation",
            json.dumps([row.video_id, row.observation_uuid]),
            json.dumps([row.video_id, row.id]),
        )
    return PreparedAssignment(
        id=handoff.id,
        assignment_id=assignment_id,
        stage=handoff.stage,
        recipient=handoff.recipient,
        video_count=len(videos),
        missing_video_ids=tuple(row["video_id"] for row in context["media"] if row["is_missing"]),
    )


def locate_media(manifest, context, media_roots):
    """Resolve only listed footage, before writing anything. Missing files retain their scope."""
    if not isinstance(media_roots, dict) or media_roots.keys() != set(manifest["roots"]):
        raise ValueError("Locate every assignment media root, with no unknown roots")
    roots = {}
    for key, value in media_roots.items():
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("Media roots must be nonempty folder paths")
        roots[key] = Path(value).expanduser().resolve()
    if len(set(roots.values())) != len(roots):
        raise ValueError("Each assignment media root needs a distinct local folder")
    paths, seen = {}, set()
    for row in context["media"]:
        path = (roots[row["root_id"]] / row["video_path"]).resolve()
        if not path.is_relative_to(roots[row["root_id"]]) or path in seen:
            raise ValueError("Media mapping escapes its root or maps two videos to the same file")
        seen.add(path)
        paths[row["video_id"]] = (path.as_posix(), not path.is_file())
    return roots, paths


def open_setup(
    session, data, manifest, context, work, project_name, roots, paths, *, local_recipient=None
):
    if not isinstance(project_name, str) or not project_name.strip():
        raise ValueError("Choose a project name")
    recipient = manifest["recipient"]
    if local_recipient is not None:
        if not isinstance(local_recipient, str) or not local_recipient.strip():
            raise ValueError("Choose a registered local annotator")
        local_recipient = local_recipient.strip()
        if session.get(Annotator, local_recipient) is None:
            raise ValueError("Choose a registered local annotator")
    base, name, suffix = project_name.strip(), project_name.strip(), 2
    while session.query(Project).filter_by(name=name).first():
        name, suffix = f"{base} ({suffix})", suffix + 1
    project = Project(name=name)
    session.add(project)
    session.flush()
    binding = HandoffAssignment(
        assignment_id=manifest["assignment_id"],
        project_id=project.id,
        role="working",
        source_project_id=manifest["source_project_id"],
        recipient=manifest["recipient"],
        stage=manifest["stage"],
        target_species=manifest["target_species"],
        created_at=timestamp(manifest["created_at"]),
        scope={
            "work_keys": manifest["work_keys"],
            "roots": manifest["roots"],
            "media": context["media"],
        },
        setup_archive=data,
    )
    session.add(binding)
    session.flush()
    identities = IdentityMap(session, binding.id)
    for order, (key, path) in enumerate(roots.items()):
        session.add(
            ProjectDir(
                id=identities.local("root", key),
                project_id=project.id,
                path=path.as_posix(),
                sort_order=order,
            )
        )
    vocab = vocabulary(session, context)
    session.add_all(
        ProjectSpecies(project_id=project.id, species_id=vocab["species"][name])
        for name in context["project_species"]
    )
    if session.get(Annotator, recipient) is None:
        session.add(Annotator(name=recipient))
        session.flush()
    videos = {}
    for source in context["media"]:
        local_id = identities.local("video", source["video_id"])
        row = {
            key: value
            for key, value in source.items()
            if key not in {"video_id", "video_path", "root_id", "is_missing", "camera_id"}
        }
        row["created_at"] = timestamp(row["created_at"])
        session.add(
            Video(
                video_id=local_id,
                project_id=project.id,
                video_path=paths[source["video_id"]][0],
                is_missing=paths[source["video_id"]][1],
                folder_camera_label=source["camera_id"],
                **row,
            )
        )
        session.flush()
        if source["video_id"] in manifest["work_keys"]:
            session.add(
                VideoAssignment(video_id=local_id, assigned_to=local_recipient or recipient)
            )
        videos[source["video_id"]] = local_id
    for source in context["predictions"]:
        row = {key: value for key, value in source.items() if key not in {"id", "video_id"}}
        row["updated_at"] = timestamp(row["updated_at"])
        session.add(
            ModelAnnotation(
                id=identities.local("prediction", source["id"]),
                video_id=videos[source["video_id"]],
                **row,
            )
        )
    if manifest["stage"] == "distance":
        target_id = vocab["species"][manifest["target_species"]]
        defaults = distance_species_settings(session, project.id, target_id)
        requirements = context["distance"].get(
            "requirements",
            {"require_age": defaults.require_age, "require_sex": defaults.require_sex},
        )
        save_distance_species_settings(
            session, project.id, target_id, enabled=True, **requirements
        )
        media_paths = {video_id: path for video_id, (path, _) in paths.items()}
        mapping = distance.restore_dependencies(
            session, project.id, context, identities, videos, media_paths, work
        )
        for unit in context["distance"]["species_work"]:
            restore_work(session, project.id, videos[unit["video_id"]], unit, vocab)
        for unit in work:
            distance.restore_work(
                session,
                project.id,
                videos[unit["video_id"]],
                unit,
                vocab,
                identities,
                mapping,
                manifest["target_species"],
            )
    else:
        for unit in work:
            restore_work(session, project.id, videos[unit["video_id"]], unit, vocab)
    if "deployments" in context:
        deployments.restore(session, project.id, context["deployments"], identities, videos, paths)
    else:
        from review_app.backend.provider.deployments import reconstruct_package_assignments

        reconstruct_package_assignments(session, project.id)
    session.flush()
    return OpenedAssignment(
        binding.id,
        binding.assignment_id,
        project.id,
        len(manifest["work_keys"]),
        tuple(key for key, (_, missing) in paths.items() if missing),
    )


def export_return(session, working_project_id):
    binding = (
        session.query(HandoffAssignment)
        .filter_by(project_id=working_project_id, role="working")
        .one_or_none()
    )
    if binding is None:
        raise ValueError("Choose a working assignment project")
    if binding.stage == "distance":
        from review_app.backend.annotation_package.distance_handoff import (
            export_return as export_distance,
        )

        return export_distance(session, binding)
    videos = bound_videos(session, binding)
    roots = session.query(ProjectDir).filter_by(project_id=working_project_id).all()
    current, work = collect(
        session, session.get(Project, working_project_id), list(videos.values()), roots
    )
    portable = {row.video_id: key for key, row in videos.items()}
    for unit in work:
        unit["video_id"] = portable[unit["video_id"]]
    manifest, original, _ = decode_archive(binding.setup_archive)
    manifest["direction"] = "return"
    used = species.used_vocabulary(work)
    context = {
        "project_name": original["project_name"],
        "project_species": [],
        "media": binding.scope["media"],
        "predictions": [],
    }
    for name, key in (("species", "scientific_name"), ("behaviors", "key"), ("tags", "key")):
        context[name] = [row for row in current[name] if row[key] in used[name]]
    return encode_archive(manifest, context, sorted(work, key=lambda row: row["video_id"]))


def plan_return(session, project_id, data, policy):
    if not isinstance(policy, str) or policy not in {"keep_existing", "use_imported", "append"}:
        raise ValueError(
            "Choose Keep existing, Use imported, or Append once for the entire return"
        )
    manifest, context, work = decode_archive(data)
    if manifest["direction"] != "return":
        raise ValueError("Accept a return, not an assignment setup package")
    binding = (
        session.query(HandoffAssignment)
        .filter_by(project_id=project_id, assignment_id=manifest["assignment_id"], role="origin")
        .one_or_none()
    )
    if binding is None or manifest["source_project_id"] != project_id:
        raise ValueError("Return does not belong to an originating assignment in this project")
    expected, original, _ = decode_archive(binding.setup_archive)
    for key in (
        "assignment_id",
        "source_project_id",
        "stage",
        "target_species",
        "recipient",
        "work_keys",
        "roots",
    ):
        # Order of scope keys has no authority, membership does.
        a, b = manifest[key], expected[key]
        differs = set(a) != set(b) if key in {"work_keys", "roots"} else a != b
        if differs:
            raise ValueError(
                "Returned assignment scope or ownership differs from the saved handout"
            )
    if context["media"] != original["media"]:
        raise ValueError("Returned media mapping differs from the saved handout")
    if binding.stage == "distance":
        if policy == "append":
            raise ValueError("Append is not supported for Distance assignment returns")
        from review_app.backend.annotation_package.distance_handoff import plan

        return plan(session, binding, manifest, context, work, data, policy, original)
    videos = bound_videos(session, binding)
    current_context, current_work = collect(
        session,
        session.get(Project, project_id),
        list(videos.values()),
        session.query(ProjectDir).filter_by(project_id=project_id).all(),
    )
    current = {unit["video_id"]: unit for unit in current_work}
    descriptions = {row["video_id"]: row for row in current_context["media"]}
    units, selected = [], []
    totals = dict.fromkeys(
        ("added", "appended", "unchanged", "replaced", "unapplied", "annotations_removed"), 0
    )
    if policy == "append":
        totals["blank_states_corrected"] = 0
    for returned in sorted(work, key=lambda unit: unit["video_id"]):
        video = videos[returned["video_id"]]
        local = {**current[video.video_id], "video_id": returned["video_id"]}
        merged = returned
        if policy == "append":
            merged, blank_corrected = species.merge_append(local, returned)
        if species.canonical(local) == species.canonical(merged):
            classification, action = "identical", "unchanged"
        elif not any(species.counts(local).values()):
            classification, action = "new", "added"
        else:
            classification = "different"
            action = (
                "appended"
                if policy == "append"
                else "replaced"
                if policy == "use_imported"
                else "unapplied"
            )
        removed = (
            species.removals(local, merged)
            if action in {"replaced", "appended"}
            else dict.fromkeys(species.counts(local), 0)
        )
        totals[action] += 1
        totals["annotations_removed"] += sum(removed.values())
        units.append(
            {
                "video_id": returned["video_id"],
                "video_path": video.video_path,
                "classification": classification,
                "action": action,
                "existing": species.counts(local),
                "returned": species.counts(merged if policy == "append" else returned),
                "removed": removed,
            }
        )
        if action in {"added", "replaced", "appended"}:
            selected.append((video.video_id, merged))
            if policy == "append" and blank_corrected:
                totals["blank_states_corrected"] += 1
    vocabulary_work = [unit for _, unit in selected]
    used = species.used_vocabulary(vocabulary_work)
    relevant_vocab = {}
    for name, key in (("species", "scientific_name"), ("behaviors", "key"), ("tags", "key")):
        definitions = {row[key]: row for row in current_context[name]}
        definitions.update({row[key]: row for row in context[name] if row[key] not in definitions})
        relevant_vocab[name] = [row for value, row in definitions.items() if value in used[name]]
    context.update(relevant_vocab)
    state = {
        "payload": hashlib.sha256(data).hexdigest(),
        "policy": policy,
        "scope": binding.scope,
        "binding": [binding.id, binding.stage, binding.recipient],
        "units": [
            species.canonical(
                {**current[videos[unit["video_id"]].video_id], "video_id": unit["video_id"]}
            )
            for unit in work
        ],
        "media": [descriptions[videos[unit["video_id"]].video_id] for unit in work],
        "vocabulary": relevant_vocab,
        "paths": [unit["video_path"] for unit in units],
    }
    token = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    warnings = []
    if totals["unapplied"]:
        warnings.append(
            "Keep existing leaves returned updates unapplied, including later annotator progress."
        )
    if totals["replaced"]:
        warnings.append("Use imported replaces whole video states, including admin corrections.")
    if totals["appended"]:
        warnings.append(
            "Append adds returned observations, updates matching UUIDs, merges video tags, "
            "keeps local-only observations, and preserves existing video labels where consistent."
        )
    if count := totals.get("blank_states_corrected"):
        warnings.append(f"Append will clear the blank label on {count} videos with observations.")
    if totals["annotations_removed"]:
        warnings.append(
            "Append updates matching observations' attribute links without deleting observations."
            if policy == "append"
            else "Replacement removes existing annotations, even for unfinished or empty returned work."
        )
    preview = ReturnPreview(token, policy, tuple(units), totals, tuple(warnings))
    return binding, context, selected, preview, None


def accept_return(session, project_id, data, policy, preview_token):
    binding, context, selected, preview, deployment_snapshot = plan_return(
        session, project_id, data, policy
    )
    if not isinstance(preview_token, str) or preview_token != preview.token:
        raise ValueError("Return preview is stale; preview again before accepting")
    if selected and binding.stage == "distance":
        from review_app.backend.annotation_package.distance_handoff import accept

        accept(
            session,
            binding,
            context,
            selected,
            preview.reference_substitutions,
            deployment_snapshot,
        )
        return AcceptanceResult(binding.assignment_id, preview.totals)
    if selected:
        vocab = vocabulary(session, context, [unit for _, unit in selected])
        for video_id, unit in selected:
            restore_work(session, project_id, video_id, unit, vocab)
    return AcceptanceResult(binding.assignment_id, preview.totals)


def export(session: Session, assignment_id: str) -> bytes:
    handoff = session.get(HandoffAssignment, assignment_id)
    if handoff is None:
        raise ValueError("Assignment not found")
    if handoff.role != "origin":
        raise ValueError("Download setup from the originating assignment, not a working project")
    if handoff.removed_at is not None:
        raise ValueError("This assignment has been removed from delivery")
    return handoff.setup_archive
