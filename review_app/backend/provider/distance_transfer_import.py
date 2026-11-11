"""Session-aware planning and atomic restoration for manual Distance archives."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import update

from review_app.backend.camera_layout import deployment_key
from review_app.backend.db.models import (
    Behavior,
    DistanceIndividual,
    DistanceIndividualTag,
    DistanceReviewProgress,
    DistanceReviewTask,
    DistanceSample,
    DistanceSampleBehavior,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Tag,
    Video,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.distance_transfer import _manual_only, _roots, _safe_relative


def _media_path(row, roots, manifest, root_map):
    root_id = row["root_id"]
    mapping = root_map or {}
    local_id = mapping.get(root_id, root_id)
    root = next((root for root in roots if root.id == local_id), None)
    if root is None and root_id not in mapping and len(roots) == len(manifest["roots"]) == 1:
        root = roots[0]
    if root is None:
        raise ValueError(f"Map source media root {root_id} to a registered project folder")
    return normalize_path_str(str(Path(root.path) / _safe_relative(row["video_path"])))


def _reference_plan(session, project_id, row, path):
    reference = (
        session.query(ReferenceVideo)
        .filter_by(project_id=project_id, video_path=path)
        .one_or_none()
    )
    by_id = session.get(ReferenceVideo, row["reference_video_id"])
    if by_id is not None and by_id is not reference:
        raise ValueError("Reference video identity conflicts with existing media")
    if reference is not None and reference.camera_id != row["camera_id"]:
        raise ValueError("Reference camera conflicts with existing calibration context")
    frame = session.get(ReferenceFrame, row["id"])
    if frame is not None and (reference is None or frame.reference_video_id != reference.id):
        raise ValueError("Reference frame identity belongs to different media")
    if frame is None and reference is not None:
        frame = (
            session.query(ReferenceFrame)
            .filter_by(reference_video_id=reference.id, offset_ms=row["offset_ms"])
            .one_or_none()
        )
    if frame is not None and any(
        getattr(frame, key) != row[key]
        for key in ("offset_ms", "playback_offset_ms", "distance_m", "mask")
    ):
        raise ValueError(
            "Reference calibration conflicts; shared references will not be overwritten"
        )
    return {"source": row, "path": path, "reference": reference, "frame": frame}


def plan_import(session, project_id, manifest, tables, root_map):
    roots = _roots(session, project_id)
    used_roots = {row["root_id"] for name in ("tasks", "references") for row in tables[name]}
    if (
        not isinstance(manifest.get("source_project_id"), str)
        or not isinstance(manifest.get("roots"), list)
        or any(not isinstance(value, str) for value in manifest["roots"])
        or len(manifest["roots"]) != len(used_roots)
        or set(manifest["roots"]) != used_roots
    ):
        raise ValueError("Invalid source project/media roots in Distance manifest")
    if root_map is not None and (
        not isinstance(root_map, dict) or not root_map.keys() <= used_roots
    ):
        raise ValueError("Media-root mapping contains an unknown source root")
    reference_rows = {row["id"]: row for row in tables["references"]}
    plans = []
    for task in tables["tasks"]:
        plan = {
            "task_id": task["id"],
            "video_path": task["video_path"],
            "exists": False,
            "error": None,
        }
        plans.append(plan)
        try:
            path = _media_path(task, roots, manifest, root_map)
            video = (
                session.query(Video)
                .filter_by(project_id=project_id, video_path=path)
                .one_or_none()
            )
            if video is None:
                raise ValueError(
                    f"Import/sync the target video before restoring annotations: {task['video_path']}"
                )
            if video.camera_id != task["camera_id"]:
                raise ValueError("Task camera does not match the target video's deployment")
            if video.duration_sec != task["duration_sec"]:
                raise ValueError(
                    "Target video duration differs from the exported sampling context"
                )
            species = (
                session.query(Species).filter_by(scientific_name=task["species"]).one_or_none()
            )
            existing = (
                None
                if species is None
                else session.query(DistanceReviewTask)
                .filter_by(video_id=video.video_id, species_id=species.id)
                .one_or_none()
            )
            by_id = session.get(DistanceReviewTask, task["id"])
            if by_id is not None and by_id is not existing:
                raise ValueError("Task identity belongs to a different video/species/project")
            plan["exists"] = existing is not None
            if existing is not None:
                _manual_only(session, existing)
            individuals = [row for row in tables["individuals"] if row["task_id"] == task["id"]]
            individual_ids = {row["id"] for row in individuals}
            samples = [row for row in tables["samples"] if row["individual_id"] in individual_ids]
            for model, rows in ((DistanceIndividual, individuals), (DistanceSample, samples)):
                for row in rows:
                    entity = session.get(model, row["id"])
                    if entity is None:
                        continue
                    owner = (
                        entity
                        if model is DistanceIndividual
                        else session.get(DistanceIndividual, entity.individual_id)
                    )
                    if existing is None or owner.task_id != existing.id:
                        raise ValueError("Returned entity identity belongs to another task")
            refs = {}
            for frame_id in {
                row["reference_frame_id"] for row in samples if row["reference_frame_id"]
            }:
                row = reference_rows[frame_id]
                ref_path = _media_path(row, roots, manifest, root_map)
                scope_roots = [(root.id, root.path) for root in roots]
                scope = deployment_key(project_id, video.camera_id, video.video_path, scope_roots)
                if scope is None or scope != deployment_key(
                    project_id, row["camera_id"], ref_path, scope_roots
                ):
                    raise ValueError("Reference frame must match the target video's deployment")
                refs[frame_id] = _reference_plan(session, project_id, row, ref_path)
            for tag in {tag["key"] for row in individuals for tag in row["tags"]}:
                local = session.query(Tag).filter_by(key=tag).one_or_none()
                if local is not None and not local.individual_eligible:
                    raise ValueError(f"Local tag is not eligible for individuals: {tag}")
            plan.update(
                task=task,
                video=video,
                species=species,
                existing=existing,
                individuals=individuals,
                samples=samples,
                references=refs,
            )
        except ValueError as exc:
            plan["error"] = str(exc)
    return plans


def _vocabulary(session, model, key, definitions, used):
    result = {}
    for definition in definitions:
        value = definition[key]
        if value not in used:
            continue
        row = session.query(model).filter(getattr(model, key) == value).one_or_none()
        if row is None:
            # Vocabulary identities are local; scientific names/keys are portable.
            row = model(**{name: value for name, value in definition.items() if name != "id"})
            session.add(row)
            session.flush()
        result[value] = row.id
    return result


def apply_task(session, project_id, plan, tables):
    source, video = plan["task"], plan["video"]
    species_id = _vocabulary(
        session, Species, "scientific_name", tables["species"], {source["species"]}
    )[source["species"]]
    task = plan["existing"]
    if task is None:
        task = DistanceReviewTask(id=source["id"], video_id=video.video_id, species_id=species_id)
        session.add(task)
    else:
        ids = session.query(DistanceIndividual.id).filter_by(task_id=task.id)
        # Delete dependent evidence before identities, including carried-infant links.
        session.query(DistanceSample).filter(DistanceSample.individual_id.in_(ids)).delete(
            synchronize_session=False
        )
        session.query(DistanceReviewProgress).filter_by(task_id=task.id).delete(
            synchronize_session=False
        )
        session.query(DistanceIndividual).filter_by(task_id=task.id).delete(
            synchronize_session=False
        )
        session.flush()
        session.expire_all()
    for column in DistanceReviewTask.__table__.columns:
        if column.name not in {"id", "video_id", "species_id", "import_id"}:
            setattr(task, column.name, source[column.name])
    session.flush()
    frame_ids = {}
    for source_id, ref_plan in plan["references"].items():
        row = ref_plan["source"]
        # Earlier accepted tasks may already have installed this shared dependency.
        current = _reference_plan(session, project_id, row, ref_plan["path"])
        reference, frame = current["reference"], current["frame"]
        if reference is None:
            reference = ReferenceVideo(
                id=row["reference_video_id"],
                project_id=project_id,
                camera_id=row["camera_id"],
                video_path=current["path"],
            )
            session.add(reference)
            session.flush()
        if frame is None:
            frame = ReferenceFrame(
                **{
                    column.name: row[column.name]
                    for column in ReferenceFrame.__table__.columns
                    if column.name not in {"reference_video_id", "import_id"}
                },
                reference_video_id=reference.id,
            )
            session.add(frame)
            session.flush()
        frame_ids[source_id] = frame.id
    individuals = plan["individuals"]
    behavior_ids = _vocabulary(
        session,
        Behavior,
        "key",
        tables["behaviors"],
        {key for row in plan["samples"] for key in row["behaviors"]},
    )
    tag_ids = _vocabulary(
        session,
        Tag,
        "key",
        tables["tags"],
        {tag["key"] for row in individuals for tag in row["tags"]},
    )
    for source_individual in individuals:
        row = DistanceIndividual(
            **{
                column.name: source_individual[column.name]
                for column in DistanceIndividual.__table__.columns
                if column.name
                not in {
                    "video_id",
                    "species_id",
                    "task_id",
                    "import_id",
                    "ai_original",
                    "initial_carrier_individual_id",
                }
            },
            task_id=task.id,
            video_id=video.video_id,
            species_id=species_id,
        )
        session.add(row)
    session.flush()
    for source_individual in individuals:
        row = session.get(DistanceIndividual, source_individual["id"])
        # Setting the relationship in a second pass must not invoke onupdate and
        # replace the original evidence timestamp with the import time.
        session.execute(
            update(DistanceIndividual)
            .where(DistanceIndividual.id == row.id)
            .values(
                initial_carrier_individual_id=source_individual["initial_carrier_individual_id"],
                updated_at=source_individual["updated_at"],
            )
        )
        session.add_all(
            DistanceIndividualTag(
                individual_id=row.id,
                tag_id=tag_ids[tag["key"]],
                tagged_by=tag["tagged_by"],
                tagged_at=datetime.fromisoformat(tag["tagged_at"]),
            )
            for tag in source_individual["tags"]
        )
    session.flush()
    for source_sample in plan["samples"]:
        row = DistanceSample(
            **{
                column.name: source_sample[column.name]
                for column in DistanceSample.__table__.columns
                if column.name not in {"ai_original", "reference_frame_id"}
            },
            reference_frame_id=frame_ids.get(source_sample["reference_frame_id"]),
        )
        session.add(row)
        session.flush()
        session.add_all(
            DistanceSampleBehavior(sample_id=row.id, behavior_id=behavior_ids[key])
            for key in source_sample["behaviors"]
        )
    for progress in tables["progress"]:
        if progress["task_id"] == source["id"]:
            session.add(DistanceReviewProgress(**{**progress, "task_id": task.id}))
    session.flush()
