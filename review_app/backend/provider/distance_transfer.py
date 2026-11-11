"""Manual Distance snapshots and explicit, task-level replace/skip restoration."""

from __future__ import annotations

import math
from datetime import datetime, time
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

from review_app.backend.db.models import (
    Behavior,
    DistanceIndividual,
    DistanceIndividualTag,
    DistanceReviewProgress,
    DistanceReviewTask,
    DistanceSample,
    DistanceSampleBehavior,
    IndividualObservation,
    Project,
    ProjectDir,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Tag,
    Video,
)
from review_app.backend.distance_timing import sample_camera_time
from review_app.backend.distance_transfer import decode_archive, encode_archive, write_tsv
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.base import ProviderBase
from review_app.backend.provider.distance_identities import (
    _anchor_fields,
    _attributes,
    _first_seen,
)
from review_app.backend.provider.distance_references import reference_tagged
from review_app.backend.provider.distance_review import review_offsets
from review_app.backend.provider.distance_tasks import ensure_tasks


def _record(row, exclude=()):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in exclude
    }


def _roots(session, project_id):
    if session.get(Project, project_id) is None:
        raise ValueError("Project not found")
    return list(session.query(ProjectDir).filter_by(project_id=project_id))


def _portable_path(path, roots):
    path = Path(normalize_path_str(path))
    for root in sorted(roots, key=lambda row: len(Path(row.path).parts), reverse=True):
        base = Path(normalize_path_str(root.path))
        if path.is_relative_to(base):
            relative = path.relative_to(base).as_posix()
            _safe_relative(relative)
            return {"root_id": root.id, "video_path": relative}
    raise ValueError(f"Media must be inside a registered project folder: {path}")


def _safe_relative(value):
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or any(part in {"..", ".", ""} for part in value.split("/"))
    ):
        raise ValueError("Media paths must be safe relative paths")
    return value


def _time_window(start, end):
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise ValueError("Provide both start and end times, or neither")
    try:
        start, end = time.fromisoformat(start), time.fromisoformat(end)
    except (ValueError, TypeError) as exc:
        raise ValueError("Use 24-hour start/end times, such as 18:00 and 20:00") from exc
    if start.tzinfo or end.tzinfo or start == end:
        raise ValueError("Time-of-day boundaries must be different local times")
    return start, end


def _manual_presence(session, video_id, species_id):
    return (
        session.query(IndividualObservation.video_id)
        .filter(
            IndividualObservation.video_id == video_id,
            IndividualObservation.species_id == species_id,
            (IndividualObservation.count.is_(None)) | (IndividualObservation.count > 0),
        )
        .correlate(Video)
        .exists()
    )


def _manual_only(session, task):
    if task.import_id or task.rejected_ai or task.alignment_mode == "imported":
        raise ValueError("AI-linked tasks are not supported by the manual Distance format")
    individuals = session.query(DistanceIndividual).filter_by(task_id=task.id).all()
    if any(row.import_id or row.ai_original is not None for row in individuals):
        raise ValueError("AI-linked individuals are not supported by the manual Distance format")
    if any(
        row.ai_original is not None
        for row in session.query(DistanceSample).filter(
            DistanceSample.individual_id.in_([row.id for row in individuals])
        )
    ):
        raise ValueError("AI-linked samples are not supported by the manual Distance format")


def _tables(session, project_id, task_ids):
    roots = _roots(session, project_id)
    if not task_ids or len(task_ids) != len(set(task_ids)):
        raise ValueError("Select one or more distinct Distance tasks")
    tasks = (
        session.query(DistanceReviewTask)
        .join(Video, Video.video_id == DistanceReviewTask.video_id)
        .filter(Video.project_id == project_id, DistanceReviewTask.id.in_(task_ids))
        .order_by(DistanceReviewTask.id)
        .all()
    )
    if len(tasks) != len(task_ids):
        raise ValueError("Every selected task must belong to the active project")
    tables = {
        name: []
        for name in (
            "tasks",
            "individuals",
            "samples",
            "references",
            "progress",
            "species",
            "behaviors",
            "tags",
        )
    }
    for task in tasks:
        _manual_only(session, task)
        video = session.get(Video, task.video_id)
        if (
            not session.query(Video)
            .filter(
                Video.video_id == task.video_id,
                _manual_presence(session, Video.video_id, task.species_id),
            )
            .first()
        ):
            raise ValueError("Selected tasks require manually confirmed Species Review labels")
        tables["tasks"].append(
            {
                **_record(task, ("video_id", "species_id", "import_id")),
                **_portable_path(video.video_path, roots),
                "camera_id": video.camera_id,
                "duration_sec": video.duration_sec,
                "species": session.get(Species, task.species_id).scientific_name,
                "scheduled_offsets_ms": review_offsets(task, video.duration_sec),
            }
        )
    individuals = (
        session.query(DistanceIndividual)
        .filter(DistanceIndividual.task_id.in_(task_ids))
        .order_by(DistanceIndividual.id)
        .all()
    )
    for individual in individuals:
        tags = [
            {"key": tag.key, "tagged_by": link.tagged_by, "tagged_at": link.tagged_at.isoformat()}
            for link, tag in session.query(DistanceIndividualTag, Tag)
            .join(Tag, Tag.id == DistanceIndividualTag.tag_id)
            .filter(DistanceIndividualTag.individual_id == individual.id)
            .order_by(Tag.key)
        ]
        tables["individuals"].append(
            {
                **_record(individual, ("video_id", "species_id", "import_id", "ai_original")),
                "tags": tags,
            }
        )
    samples = (
        session.query(DistanceSample)
        .filter(DistanceSample.individual_id.in_([row.id for row in individuals]))
        .order_by(DistanceSample.id)
        .all()
    )
    for sample in samples:
        behaviors = [
            key
            for (key,) in session.query(Behavior.key)
            .join(DistanceSampleBehavior, DistanceSampleBehavior.behavior_id == Behavior.id)
            .filter(DistanceSampleBehavior.sample_id == sample.id)
            .order_by(Behavior.key)
        ]
        tables["samples"].append({**_record(sample, ("ai_original",)), "behaviors": behaviors})
    for frame in (
        session.query(ReferenceFrame)
        .filter(
            ReferenceFrame.id.in_(
                {row.reference_frame_id for row in samples if row.reference_frame_id}
            )
        )
        .order_by(ReferenceFrame.id)
    ):
        reference = session.get(ReferenceVideo, frame.reference_video_id)
        if reference.project_id != project_id:
            raise ValueError("Reference frame belongs to a different project")
        if frame.import_id:
            raise ValueError(
                "AI-linked references are not supported by the manual Distance format"
            )
        tables["references"].append(
            {
                **_record(frame, ("import_id",)),
                **_portable_path(reference.video_path, roots),
                "camera_id": reference.camera_id,
            }
        )
    tables["progress"] = [
        _record(row)
        for row in session.query(DistanceReviewProgress)
        .filter(DistanceReviewProgress.task_id.in_(task_ids))
        .order_by(DistanceReviewProgress.task_id, DistanceReviewProgress.annotator_name)
    ]
    tables["species"] = [
        _record(row)
        for row in session.query(Species)
        .filter(Species.id.in_({task.species_id for task in tasks}))
        .order_by(Species.id)
    ]
    behavior_keys = {key for row in tables["samples"] for key in row["behaviors"]}
    tag_keys = {tag["key"] for row in tables["individuals"] for tag in row["tags"]}
    tables["behaviors"] = [
        _record(row)
        for row in session.query(Behavior)
        .filter(Behavior.key.in_(behavior_keys))
        .order_by(Behavior.key)
    ]
    tables["tags"] = [
        _record(row) for row in session.query(Tag).filter(Tag.key.in_(tag_keys)).order_by(Tag.key)
    ]
    return tables, {
        "source_project_id": project_id,
        "roots": sorted(
            {row["root_id"] for name in ("tasks", "references") for row in tables[name]}
        ),
    }


def _unique(rows, key):
    result = {}
    for row in rows:
        identity = row[key]
        if not identity or identity in result:
            raise ValueError(f"Missing or duplicate {key} in Distance archive")
        result[identity] = row
    return result


def _required(rows, model, excluded=()):
    for row in rows:
        for col in model.__table__.columns:
            if col.name in excluded:
                continue
            if not col.nullable and row.get(col.name) is None:
                raise ValueError(f"Missing required {model.__tablename__}.{col.name}")


def _validate_tables(tables):
    """Validate the complete graph before any database writes (also used on export)."""
    for name, model, excluded in (
        ("tasks", DistanceReviewTask, ("video_id", "species_id", "import_id")),
        (
            "individuals",
            DistanceIndividual,
            ("video_id", "species_id", "import_id", "ai_original"),
        ),
        ("samples", DistanceSample, ("ai_original",)),
        ("references", ReferenceFrame, ("import_id",)),
        ("progress", DistanceReviewProgress, ()),
        ("species", Species, ()),
        ("behaviors", Behavior, ()),
        ("tags", Tag, ()),
    ):
        _required(tables[name], model, excluded)
    tasks = _unique(tables["tasks"], "id")
    individuals = _unique(tables["individuals"], "id")
    _unique(tables["samples"], "id")
    references = _unique(tables["references"], "id")
    species = _unique(tables["species"], "scientific_name")
    behaviors = _unique(tables["behaviors"], "key")
    tags = _unique(tables["tags"], "key")
    if not tasks:
        raise ValueError("Distance archive contains no tasks")
    task_keys, individual_keys, sample_keys, progress_keys = set(), set(), set(), set()
    schedules = {}
    for task in tasks.values():
        _safe_relative(task["video_path"])
        if not task["root_id"] or not task["camera_id"] or task["species"] not in species:
            raise ValueError("Task requires a media root, camera and species definition")
        key = task["root_id"], task["video_path"], task["species"]
        if key in task_keys:
            raise ValueError("Duplicate video/species task")
        task_keys.add(key)
        if task["source_count"] <= 0:
            raise ValueError("Task source count must be positive")
        if task["alignment_mode"] not in {"exact", "simplified"} or task["rejected_ai"]:
            raise ValueError("AI state is not supported by the manual Distance format")
        if task["imported_sample_times_s"] is not None:
            raise ValueError("Imported schedules are not supported by the manual Distance format")
        if not isinstance(task["completion_history"], list) or not isinstance(
            task["rejected_ai"], list
        ):
            raise ValueError("Task history must be a JSON list")
        if task["recorded_start"] and task["recorded_start"].tzinfo is not None:
            raise ValueError("Camera recording time must be local time without a timezone")
        schedules[task["id"]] = review_offsets(SimpleNamespace(**task), task["duration_sec"])
        if task["scheduled_offsets_ms"] != schedules[task["id"]]:
            raise ValueError("Task alignment does not reproduce the exported sampling schedule")
        if task["verified_at"] is not None and not schedules[task["id"]]:
            raise ValueError("Verified task must have a valid sampling schedule")
        if task["completion_outcome"] not in {None, "present", "absent"}:
            raise ValueError("Invalid task completion outcome")
        if (task["completed_at"] is not None) != (task["completion_outcome"] is not None):
            raise ValueError("Task completion requires an outcome and annotation time")
    reference_keys, reference_videos = set(), {}
    for frame in references.values():
        _safe_relative(frame["video_path"])
        key = frame["root_id"], frame["video_path"], frame["offset_ms"]
        if key in reference_keys or not frame["root_id"] or not frame["camera_id"]:
            raise ValueError("Duplicate reference frame or missing media context")
        reference_keys.add(key)
        context = frame["root_id"], frame["video_path"], frame["camera_id"]
        if reference_videos.setdefault(frame["reference_video_id"], context) != context:
            raise ValueError("Contradictory reference video identity")
        playback = frame["playback_offset_ms"]
        if (
            frame["offset_ms"] < 0
            or frame["distance_m"] <= 0
            or (
                playback is not None
                and (playback < 0 or math.floor(playback + 0.5) != frame["offset_ms"])
            )
        ):
            raise ValueError("Invalid reference calibration/frame position")
    for individual in individuals.values():
        task = tasks.get(individual["task_id"])
        if task is None:
            raise ValueError("Individual must belong to an exported task")
        key = individual["task_id"], individual["individual_number"]
        if key in individual_keys or individual["individual_number"] <= 0:
            raise ValueError("Duplicate or invalid individual number")
        individual_keys.add(key)
        _attributes(individual["age"], individual["sex"])
        _first_seen(
            SimpleNamespace(duration_sec=task["duration_sec"]), individual["first_seen_offset_ms"]
        )
        box = {key: individual[f"anchor_{key}"] for key in ("x", "y", "width", "height")}
        _anchor_fields(box if any(value is not None for value in box.values()) else None)
        precise = individual["first_seen_playback_ms"]
        if precise is not None and not 0 <= precise < task["duration_sec"] * 1000:
            raise ValueError("First-seen playback position must be within the video")
        if individual["review_status"] not in {"not_started", "in_progress", "completed"}:
            raise ValueError("Invalid individual review status")
        carrier_id = individual["initial_carrier_individual_id"]
        if carrier_id:
            _carrier(individual, individuals.get(carrier_id))
        if not isinstance(individual["tags"], list):
            raise ValueError("Individual tags must be a JSON list")
        seen = set()
        for tag in individual["tags"]:
            if not isinstance(tag, dict) or set(tag) != {"key", "tagged_by", "tagged_at"}:
                raise ValueError("Invalid individual tag attribution")
            if (
                tag["key"] not in tags
                or not tags[tag["key"]]["individual_eligible"]
                or tag["key"] in seen
            ):
                raise ValueError("Invalid or duplicate individual tag")
            seen.add(tag["key"])
            datetime.fromisoformat(tag["tagged_at"])
    evidence = {}
    for sample in tables["samples"]:
        individual = individuals.get(sample["individual_id"])
        if individual is None:
            raise ValueError("Sample must belong to an exported individual")
        task = tasks[individual["task_id"]]
        key = sample["individual_id"], sample["offset_ms"]
        if key in sample_keys or sample["offset_ms"] not in schedules[task["id"]]:
            raise ValueError("Duplicate or unscheduled sample")
        sample_keys.add(key)
        evidence[key] = sample
        status = sample["status"]
        if status not in {"annotated", "skipped"}:
            raise ValueError("Manual archives support only saved annotated/skipped samples")
        if not isinstance(sample["behaviors"], list) or any(
            not isinstance(key, str) for key in sample["behaviors"]
        ):
            raise ValueError("Sample behaviors must be a JSON list of keys")
        if (
            len(set(sample["behaviors"])) != len(sample["behaviors"])
            or not set(sample["behaviors"]) <= behaviors.keys()
        ):
            raise ValueError("Unknown or duplicate behavior key")
        if status == "skipped":
            if (
                sample["skip_reason"] not in {"individual absent", "midpoint not visible"}
                or any(sample[key] is not None for key in ("reference_frame_id", "distance_m"))
                or sample["behaviors"]
            ):
                raise ValueError("Invalid visibility skip")
        else:
            frame = references.get(sample["reference_frame_id"])
            if (
                frame is None
                or sample["skip_reason"] is not None
                or sample["distance_m"] != frame["distance_m"]
            ):
                raise ValueError("Measured sample must retain its calibrated reference distance")
            if (frame["root_id"], frame["camera_id"]) != (task["root_id"], task["camera_id"]):
                raise ValueError("Reference must belong to the task's deployment")
        if sample["carried_by_individual_id"]:
            _carrier(individual, individuals.get(sample["carried_by_individual_id"]))
    for sample in tables["samples"]:
        if sample["carried_by_individual_id"]:
            parent = evidence.get((sample["carried_by_individual_id"], sample["offset_ms"]))
            if (
                parent is None
                or parent["carried_by_individual_id"]
                or any(
                    sample[key] != parent[key]
                    for key in ("status", "skip_reason", "reference_frame_id", "distance_m")
                )
            ):
                raise ValueError(
                    "Carried infant evidence must match the carrier at the same sample"
                )
    for individual in individuals.values():
        expected = set(schedules[individual["task_id"]])
        saved = {offset for owner, offset in evidence if owner == individual["id"]}
        if individual["review_status"] == "completed" and (not expected or not expected <= saved):
            raise ValueError(
                "Individual completion requires every scheduled sample on a verified schedule"
            )
    for task in tasks.values():
        members = [row for row in individuals.values() if row["task_id"] == task["id"]]
        if task["completion_outcome"] == "absent" and members:
            raise ValueError("Explicit absence cannot contain individuals")
        if task["completion_outcome"] == "present" and (
            not members
            or any(
                row["review_status"] != "completed" or row["age"] is None or row["sex"] is None
                for row in members
            )
        ):
            raise ValueError("Task completion requires complete individuals with age and sex")
    for progress in tables["progress"]:
        key = progress["task_id"], progress["annotator_name"]
        individual = individuals.get(progress["active_individual_id"])
        if (
            key in progress_keys
            or individual is None
            or individual["task_id"] != progress["task_id"]
        ):
            raise ValueError("Resume state must identify an individual in its own task")
        progress_keys.add(key)


def _carrier(individual, carrier):
    if (
        carrier is None
        or carrier["id"] == individual["id"]
        or carrier["task_id"] != individual["task_id"]
        or individual["age"] != "Infant"
        or carrier["age"] == "Infant"
    ):
        raise ValueError(
            "Carried infant relationships must stay within a task and use a non-infant carrier"
        )


class DistanceTransferMixin(ProviderBase):
    def preview_distance_export(self, project_id, species_id, *, start_time=None, end_time=None):
        """Select whole tasks by confirmed labels and verified camera-local start time.

        Windows are start-inclusive/end-exclusive; an end before the start crosses
        midnight. Unfiltered exports also allow tasks with unknown camera time.
        """
        window = _time_window(start_time, end_time)
        with self.Session.begin() as session:
            _roots(session, project_id)
            if session.get(Species, species_id) is None:
                raise ValueError("Species not found")
            ensure_tasks(session, project_id)
            rows = (
                session.query(DistanceReviewTask, Video)
                .join(Video, Video.video_id == DistanceReviewTask.video_id)
                .filter(
                    Video.project_id == project_id,
                    DistanceReviewTask.species_id == species_id,
                    _manual_presence(session, Video.video_id, species_id),
                    ~reference_tagged(session),
                )
                .order_by(Video.video_path)
                .all()
            )
            selected, unknown = [], []
            for task, video in rows:
                row = {
                    "task_id": task.id,
                    "video_path": video.video_path,
                    "recorded_start": task.recorded_start.isoformat()
                    if task.recorded_start
                    else None,
                    "duration_sec": video.duration_sec,
                    "is_missing": video.is_missing,
                    "completion_outcome": task.completion_outcome,
                }
                try:
                    _manual_only(session, task)
                    row["error"] = None
                except ValueError as exc:
                    row["error"] = str(exc)
                if window:
                    if task.recorded_start is None:
                        unknown.append(row)
                        continue
                    value = task.recorded_start.time()
                    start, end = window
                    if not (
                        start <= value < end if start < end else value >= start or value < end
                    ):
                        continue
                selected.append(row)
            return {"tasks": selected, "unknown_time": unknown}

    def export_distance_annotations(self, project_id: str, task_ids: list[str]) -> bytes:
        with self.Session() as session:
            # sqlite3 legacy mode does not begin a read transaction for SELECT.
            # All tables must describe the same snapshot while another editor saves.
            session.connection().exec_driver_sql("BEGIN")
            tables, manifest = _tables(session, project_id, task_ids)
            _validate_tables(tables)
            return encode_archive(tables, manifest)

    def export_distance_measurements(self, project_id: str, task_ids: list[str]) -> bytes:
        """Analysis-only table; explicit absence/empty tasks are in the editable archive."""
        with self.Session() as session:
            session.connection().exec_driver_sql("BEGIN")
            tables, _ = _tables(session, project_id, task_ids)
            _validate_tables(tables)
        tasks = {row["id"]: row for row in tables["tasks"]}
        individuals = {row["id"]: row for row in tables["individuals"]}
        rows = []
        for sample in tables["samples"]:
            individual = individuals[sample["individual_id"]]
            task = tasks[individual["task_id"]]
            rows.append(
                {
                    "task_id": task["id"],
                    "root_id": task["root_id"],
                    "video_path": task["video_path"],
                    "camera_id": task["camera_id"],
                    "camera_time": sample_camera_time(task, sample["offset_ms"]),
                    "reference_frame_id": sample["reference_frame_id"],
                    "tags": [tag["key"] for tag in individual["tags"]],
                    "species": task["species"],
                    "individual_id": individual["id"],
                    "individual_number": individual["individual_number"],
                    "offset_ms": sample["offset_ms"],
                    "distance_m": sample["distance_m"],
                    "skip_reason": sample["skip_reason"],
                    "behaviors": sample["behaviors"],
                    "age": individual["age"],
                    "sex": individual["sex"],
                    "carried_by_individual_id": sample["carried_by_individual_id"],
                    "annotator": sample["labeled_by"],
                    "labeled_at": sample["labeled_at"],
                }
            )
        fields = [
            "task_id",
            "root_id",
            "video_path",
            "camera_id",
            "camera_time",
            "reference_frame_id",
            "tags",
            "species",
            "individual_id",
            "individual_number",
            "offset_ms",
            "distance_m",
            "skip_reason",
            "behaviors",
            "age",
            "sex",
            "carried_by_individual_id",
            "annotator",
            "labeled_at",
        ]
        return write_tsv(rows, fields)

    def preview_distance_annotations_import(self, project_id, data: bytes, *, root_map=None):
        from review_app.backend.provider.distance_transfer_import import plan_import

        manifest, tables = decode_archive(data)
        _validate_tables(tables)
        with self.Session() as session:
            plans = plan_import(session, project_id, manifest, tables, root_map)
            return [
                {key: plan[key] for key in ("task_id", "video_path", "exists", "error")}
                for plan in plans
            ]

    def import_distance_annotations(
        self, project_id, data: bytes, decisions: dict[str, str], *, root_map=None
    ):
        """Import only explicitly accepted tasks; omitted decisions mean skip.

        Revalidate against current source on every call. All accepted tasks and
        their dependencies share a transaction; skipped payloads are not stored.
        """
        from review_app.backend.provider.distance_transfer_import import apply_task, plan_import

        manifest, tables = decode_archive(data)
        _validate_tables(tables)
        task_ids = {row["id"] for row in tables["tasks"]}
        if not decisions.keys() <= task_ids or any(
            value not in {"replace", "skip"} for value in decisions.values()
        ):
            raise ValueError("Choose replace or skip for each returned task")
        with self.Session.begin() as session:
            # Hold the writer reservation while revalidating dependencies and
            # replacing evidence, so a concurrent save cannot invalidate the plan.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            plans = plan_import(session, project_id, manifest, tables, root_map)
            for plan in plans:
                if decisions.get(plan["task_id"]) == "replace" and plan["error"]:
                    raise ValueError(plan["error"])
            imported, skipped = [], []
            for plan in plans:
                if decisions.get(plan["task_id"]) != "replace":
                    skipped.append(plan["task_id"])
                    continue
                apply_task(session, project_id, plan, tables)
                imported.append(plan["task_id"])
            return {"imported": imported, "skipped": skipped}
