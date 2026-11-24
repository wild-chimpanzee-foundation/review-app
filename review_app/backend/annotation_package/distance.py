"""Complete Distance assignment snapshots, independent of manual archive v1.

Work is one video/target-species task. Calibration and projected AI sources are
separate dependencies; their local handles are never annotation identity.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer

from review_app.backend.annotation_package import species
from review_app.backend.annotation_package.archive import (
    FOLDER_REVIEW_VERSION,
    RETIRED_REFERENCE_VERSION,
)
from review_app.backend.annotation_package.scope import id_selection
from review_app.backend.db.models import (
    DistanceImport,
    DistanceIndividual,
    DistanceIndividualTag,
    DistanceReviewProgress,
    DistanceReviewTask,
    DistanceSample,
    DistanceSampleBehavior,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Video,
)
from review_app.backend.provider.deployments import (
    assign_reference_deployment,
    resolve_reference_deployment,
)

TASK = "id source_count completion_outcome completion_history rejected_ai recorded_start alignment_mode imported_sample_times_s import_id started_at clock_anchor_offset_ms clock_anchor_playback_ms opening_even_second verified_by verified_at completed_by completed_at"
INDIVIDUAL = "id import_id task_id ai_original individual_number age sex first_seen_offset_ms first_seen_playback_ms initial_carrier_individual_id anchor_x anchor_y anchor_width anchor_height labeled_by labeled_at updated_at review_status completed_by completed_at"
SAMPLE = "id individual_id offset_ms ai_original status skip_reason reference_frame_id distance_m carried_by_individual_id labeled_by labeled_at updated_at"
FRAME = "id reference_video_id offset_ms playback_offset_ms distance_m mask import_id"
PROGRESS = "task_id annotator_name active_individual_id updated_at"


def frame_record(frame):
    record = species.fields(frame, FRAME)
    if frame.retired:
        record["retired"] = True
    return record


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def rewrite(value, mapping):
    """Remap embedded runtime foreign IDs, including AI suggestions and rejected drafts."""
    if isinstance(value, dict):
        foreign_keys = {
            "id",
            "video_id",
            "root_id",
            "task_id",
            "import_id",
            "individual_id",
            "distance_individual_id",
            "reference_frame_id",
            "reference_video_id",
            "initial_carrier_individual_id",
            "carried_by_individual_id",
            "active_individual_id",
        }
        return {
            key: mapping.get(val, val)
            if key in foreign_keys and isinstance(val, str)
            else rewrite(val, mapping)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [rewrite(val, mapping) for val in value]
    return value


def collect(session, project, videos, roots, target):
    target_row = session.query(Species).filter_by(scientific_name=target).one()
    tasks = {
        row.video_id: row
        for row in session.query(DistanceReviewTask).filter(
            DistanceReviewTask.video_id.in_(id_selection(v.video_id for v in videos)),
            DistanceReviewTask.species_id == target_row.id,
        )
    }
    work = []
    for video in videos:
        task = tasks.get(video.video_id)
        unit = {
            "video_id": video.video_id,
            "task": species.fields(task, TASK) if task else None,
            "individuals": [],
            "samples": [],
            "progress": [],
        }
        if task:
            for row in (
                session.query(DistanceIndividual)
                .filter_by(task_id=task.id)
                .order_by(DistanceIndividual.id)
            ):
                tags = [
                    {"key": tag.key, **species.fields(link, "tagged_by tagged_at")}
                    for link, tag in session.query(DistanceIndividualTag, species.Tag)
                    .join(species.Tag)
                    .filter(DistanceIndividualTag.individual_id == row.id)
                ]
                unit["individuals"].append({**species.fields(row, INDIVIDUAL), "tags": tags})
            ids = id_selection(row["id"] for row in unit["individuals"])
            for row in (
                session.query(DistanceSample)
                .filter(DistanceSample.individual_id.in_(ids))
                .order_by(DistanceSample.id)
            ):
                behaviors = [
                    key
                    for (key,) in session.query(species.Behavior.key)
                    .join(DistanceSampleBehavior)
                    .filter(DistanceSampleBehavior.sample_id == row.id)
                ]
                unit["samples"].append(
                    {**species.fields(row, SAMPLE), "behaviors": sorted(behaviors)}
                )
            unit["progress"] = [
                species.fields(row, PROGRESS)
                for row in session.query(DistanceReviewProgress).filter_by(task_id=task.id)
            ]
        work.append(unit)
    deployments = {v.deployment_id for v in videos if v.deployment_id}
    # Include prepared and merely discoverable reference footage, even on untouched tasks.
    references = [
        row
        for row in session.query(ReferenceVideo).filter(
            ReferenceVideo.project_id == project.id, ReferenceVideo.deployment_id.in_(deployments)
        )
    ]
    needed_frames = {
        row["reference_frame_id"]
        for unit in work
        for row in unit["samples"]
        if row["reference_frame_id"]
    }
    needed_frames.update(
        row.get("reference_frame_id")
        for unit in work
        for row in unit["samples"]
        if row["ai_original"]
        for row in [row["ai_original"]]
        if row.get("reference_frame_id")
    )
    frames = list(
        session.query(ReferenceFrame)
        .filter(
            (
                ReferenceFrame.reference_video_id.in_(id_selection(r.id for r in references))
                & ReferenceFrame.retired.is_(False)
            )
            | ReferenceFrame.id.in_(id_selection(needed_frames))
        )
        .order_by(ReferenceFrame.id)
    )
    for frame in frames:
        ref = session.get(ReferenceVideo, frame.reference_video_id)
        if ref.project_id != project.id:
            raise ValueError("Reference dependency belongs to another project")
        if ref.id not in {r.id for r in references}:
            references.append(ref)
    from review_app.backend.provider.distance_references import reference_tagged

    tagged = [
        v
        for v in session.query(Video).filter(
            Video.project_id == project.id,
            Video.deployment_id.in_(deployments),
            reference_tagged(session),
        )
    ]
    media_videos = {v.video_id: v for v in videos + tagged}
    for ref in references:
        source = (
            session.query(Video)
            .filter_by(project_id=project.id, video_path=ref.video_path)
            .first()
        )
        if source is None:
            # Registered calibration footage need not have a normal Video row.
            source = Video(
                video_id="reference-media:" + ref.id,
                project_id=project.id,
                video_path=ref.video_path,
                deployment=ref.deployment,
                deployment_id=ref.deployment_id,
                is_missing=not Path(ref.video_path).is_file(),
            )
        media_videos[source.video_id] = source
    context, species_work = species.collect(
        session, project, sorted(media_videos.values(), key=lambda v: v.video_id), roots
    )
    media_by_path = {v.video_path: v.video_id for v in media_videos.values()}
    # Registered-only calibration clips must remain discoverable in the fresh project.
    reference_media = {media_by_path[r.video_path] for r in references}
    if reference_media and not any(r["key"] == "reference_video" for r in context["tags"]):
        context["tags"].append(
            {
                "key": "reference_video",
                "name_en": "Reference video",
                "name_fr": None,
                "color": None,
                "icon": None,
                "is_custom": False,
                "individual_eligible": False,
            }
        )
    for unit in species_work:
        if unit["video_id"] in reference_media and not any(
            t["key"] == "reference_video" for t in unit["tags"]
        ):
            unit["tags"].append(
                {"key": "reference_video", "tagged_by": None, "tagged_at": "1970-01-01T00:00:00"}
            )
    import_ids = {
        row.get("import_id")
        for unit in work
        for row in ([unit["task"]] if unit["task"] else []) + unit["individuals"]
    }
    import_ids.update(f.import_id for f in frames)
    imports = []
    individual_ids = {r["id"] for u in work for r in u["individuals"]}
    rejected_ids = {
        r["original"]["individual_id"]
        for u in work
        if u["task"]
        for r in u["task"]["rejected_ai"]
        if r.get("original") and r["original"].get("individual_id")
    }
    for source in session.query(DistanceImport).filter(
        DistanceImport.id.in_(id_selection(import_ids - {None}))
    ):
        # Preserve original identity/hash separately: this is explicitly a projection.
        identities = [
            r
            for r in source.payload.get("individuals", [])
            if r.get("distance_individual_id") in individual_ids
            or r.get("individual_id") in rejected_ids
        ]
        source_ids = {r["individual_id"] for r in identities}
        payload = {
            "individuals": identities,
            "references": [
                r
                for r in source.payload.get("references", [])
                if r.get("reference_frame_id") in {f.id for f in frames}
            ],
            "predictions": [
                r
                for r in source.payload.get("predictions", [])
                if r.get("individual_id") in source_ids
            ],
            "sampling": [
                {
                    **{k: v for k, v in row.items() if k != "video_path"},
                    "video_id": media_by_path.get(row.get("video_path"), row.get("video_id")),
                }
                for row in source.payload.get("sampling") or []
                if row.get("species") == target
                and media_by_path.get(row.get("video_path"), row.get("video_id"))
                in {v.video_id for v in videos}
            ],
        }
        prior = source.manifest.get("assignment_projection")
        imports.append(
            {
                "id": source.id,
                "source_id": prior["source_id"] if prior else source.id,
                "source_hash": prior["source_hash"] if prior else source.content_hash,
                "manifest": {
                    k: v for k, v in source.manifest.items() if k != "assignment_projection"
                },
                "payload": payload,
                "imported_at": source.imported_at.isoformat(),
            }
        )
    context["distance"] = {
        "references": [{"id": r.id, "video_id": media_by_path[r.video_path]} for r in references],
        "frames": [frame_record(f) for f in frames],
        "imports": imports,
        "species_work": species_work,
    }
    return context, work


def used_vocabulary(work, target):
    return {
        "species": {target},
        "behaviors": {key for u in work for r in u["samples"] for key in r["behaviors"]},
        "tags": {r["key"] for u in work for i in u["individuals"] for r in i["tags"]},
    }


def canonical(unit, frames, references, imports=()):
    value = copy.deepcopy(unit)
    mapping = {
        row["id"]: "individual:" + str(row["individual_number"]) for row in value["individuals"]
    }
    if value["task"]:
        mapping[value["task"]["id"]] = "task"
    for row in value["samples"]:
        mapping[row["id"]] = mapping[row["individual_id"]] + ":" + str(row["offset_ms"])
    mapping.update({r["id"]: json.dumps([r["source_id"], r["source_hash"]]) for r in imports})
    ref_videos = {r["id"]: r["video_id"] for r in references}
    mapping.update(
        {
            f["id"]: json.dumps(
                [ref_videos[f["reference_video_id"]], f["distance_m"], f["offset_ms"]]
            )
            for f in frames
        }
    )

    def clean(obj):
        if isinstance(obj, dict):
            return {
                k: (
                    species.timestamp(v).isoformat(timespec="microseconds")
                    if k.endswith("_at") and v
                    else clean(v)
                )
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return sorted((clean(v) for v in obj), key=lambda v: json.dumps(v, sort_keys=True))
        return obj

    return clean(rewrite(value, mapping))


def counts(unit):
    task = unit["task"]
    state = (
        bool(
            task
            and any(
                task.get(k) is not None
                for k in ("clock_anchor_offset_ms", "clock_anchor_playback_ms")
            )
        )
        or bool(
            task
            and any(
                task.get(k)
                for k in (
                    "completion_outcome",
                    "completion_history",
                    "rejected_ai",
                    "recorded_start",
                    "import_id",
                    "started_at",
                    "clock_anchor_offset_ms",
                    "clock_anchor_playback_ms",
                    "opening_even_second",
                    "verified_by",
                    "verified_at",
                    "completed_by",
                    "completed_at",
                )
            )
        )
        or bool(
            task
            and (task["alignment_mode"] != "exact" or task["imported_sample_times_s"] is not None)
        )
    )
    return {
        "task_state": int(state),
        "individuals": len(unit["individuals"]),
        "samples": len(unit["samples"]),
        "behaviors": sum(len(r["behaviors"]) for r in unit["samples"]),
        "tags": sum(len(r["tags"]) for r in unit["individuals"]),
        "progress": len(unit["progress"]),
    }


def validate_rows(rows, model, names, extra=()):
    if not isinstance(rows, list):
        raise ValueError("Expected Distance record list")
    for row in rows:
        if not isinstance(row, dict) or row.keys() != set(names.split()) | set(extra):
            raise ValueError("Unexpected Distance snapshot fields")
        for name in names.split():
            column = model.__table__.columns[name]
            value = row[name]
            if value is None:
                if not column.nullable:
                    raise ValueError(f"Missing Distance field: {name}")
                continue
            kind = column.type
            valid = (
                isinstance(value, (dict, list))
                if isinstance(kind, JSON)
                else type(value) is bool
                if isinstance(kind, Boolean)
                else type(value) is int
                if isinstance(kind, Integer)
                else type(value) in (int, float)
                if isinstance(kind, Float)
                else isinstance(value, str)
            )
            if not valid or ((name == "id" or name.endswith("_id")) and not value.strip()):
                raise ValueError(f"Invalid Distance field: {name}")
            if isinstance(kind, DateTime):
                species.timestamp(value)


def validate(manifest, context, work):
    # Validate nested JSON too (e.g. 1e999 parses as infinity without parse_constant).
    try:
        json.dumps([context, work], allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid nested Distance JSON") from exc
    from review_app.backend.annotation_package.archive import TEXT, _records

    deps = context["distance"]
    if not isinstance(deps, dict) or deps.keys() != {
        "references",
        "frames",
        "imports",
        "species_work",
    }:
        raise ValueError("Invalid Distance dependencies")
    media = {r["video_id"] for r in context["media"]}
    refs = _records(deps["references"], {"id": TEXT, "video_id": TEXT}, "id")
    if any(r["video_id"] not in media for r in refs.values()) or len(
        {r["video_id"] for r in refs.values()}
    ) != len(refs):
        raise ValueError("Invalid reference media mapping")
    if not isinstance(deps["frames"], list):
        raise ValueError("Expected Distance record list")
    for frame in deps["frames"]:
        fields = FRAME
        if isinstance(frame, dict) and "retired" in frame:
            if (
                manifest["version"] not in {RETIRED_REFERENCE_VERSION, FOLDER_REVIEW_VERSION}
                or frame["retired"] is not True
            ):
                raise ValueError("Invalid retired reference frame")
            fields += " retired"
        validate_rows([frame], ReferenceFrame, fields)
    frames = {r["id"]: r for r in deps["frames"]}
    imports = _records(
        deps["imports"],
        {
            "id": TEXT,
            "source_id": TEXT,
            "source_hash": TEXT,
            "manifest": (dict,),
            "payload": (dict,),
            "imported_at": TEXT,
        },
        "id",
    )
    positions = set()
    for r in frames.values():
        position = r["reference_video_id"], r["distance_m"], r["offset_ms"]
        if (
            r["reference_video_id"] not in refs
            or position in positions
            or r["offset_ms"] < 0
            or r["distance_m"] <= 0
            or (r["playback_offset_ms"] is not None and r["playback_offset_ms"] < 0)
        ):
            raise ValueError("Invalid or duplicate reference video/distance/timestamp")
        positions.add(position)
    if len(frames) != len(deps["frames"]):
        raise ValueError("Duplicate reference ID")
    for frame in frames.values():
        if frame["import_id"] and frame["import_id"] not in imports:
            raise ValueError("Dangling reference provenance")
    if not set(manifest["work_keys"]) <= media:
        raise ValueError("Distance work media is missing")
    keys = set()
    global_ids = set(refs) | set(frames) | set(imports)
    if len(global_ids) != len(refs) + len(frames) + len(imports):
        raise ValueError("Ambiguous Distance dependency identity")
    tags = {r["key"] for r in context["tags"]}
    behaviors = {r["key"] for r in context["behaviors"]}
    for unit in work:
        if not isinstance(unit, dict) or unit.keys() != {
            "video_id",
            "task",
            "individuals",
            "samples",
            "progress",
        }:
            raise ValueError("Invalid Distance work fields")
        if unit["video_id"] in keys or unit["video_id"] not in manifest["work_keys"]:
            raise ValueError("Distance work outside saved scope or duplicated")
        keys.add(unit["video_id"])
        task = unit["task"]
        if task is not None and (not isinstance(task, dict) or not task):
            raise ValueError("Invalid Distance task")
        validate_rows([task] if task else [], DistanceReviewTask, TASK)
        if task:
            if task["alignment_mode"] not in {"exact", "simplified", "imported"} or task[
                "completion_outcome"
            ] not in {None, "present", "absent"}:
                raise ValueError("Invalid Distance task state")
            if (
                not isinstance(task["completion_history"], list)
                or not isinstance(task["rejected_ai"], list)
                or any(
                    not isinstance(r, dict)
                    for r in task["completion_history"] + task["rejected_ai"]
                )
            ):
                raise ValueError("Invalid Distance history")
            schedule = task["imported_sample_times_s"]
            if schedule is not None and (
                not isinstance(schedule, list)
                or any(type(t) not in (int, float) or t < 0 for t in schedule)
                or schedule != sorted(set(schedule))
            ):
                raise ValueError("Invalid imported schedule")
            if any(
                task[k] is not None and task[k] < 0
                for k in ("clock_anchor_offset_ms", "clock_anchor_playback_ms")
            ):
                raise ValueError("Invalid clock anchor")
        validate_rows(unit["individuals"], DistanceIndividual, INDIVIDUAL, ("tags",))
        validate_rows(unit["samples"], DistanceSample, SAMPLE, ("behaviors",))
        validate_rows(unit["progress"], DistanceReviewProgress, PROGRESS)
        individuals = {r["id"]: r for r in unit["individuals"]}
        numbers, offsets, authors = set(), set(), set()
        if not task and any(unit[k] for k in ("individuals", "samples", "progress")):
            raise ValueError("Distance evidence needs a task")
        for row in ([task] if task else []) + unit["individuals"] + unit["samples"]:
            if row["id"] in global_ids:
                raise ValueError("Duplicate Distance identity")
            global_ids.add(row["id"])
            if row.get("import_id") and row["import_id"] not in imports:
                raise ValueError("Dangling AI source")
        for row in unit["individuals"]:
            if (
                row["task_id"] != task["id"]
                or row["individual_number"] <= 0
                or row["individual_number"] in numbers
                or row["first_seen_offset_ms"] < 0
            ):
                raise ValueError("Invalid Distance individual")
            numbers.add(row["individual_number"])
            carrier = row["initial_carrier_individual_id"]
            if carrier and (carrier not in individuals or carrier == row["id"]):
                raise ValueError("Carrier must belong to the same task")
            if row["ai_original"] is not None and (
                not isinstance(row["ai_original"], dict)
                or row["ai_original"].get("species", manifest["target_species"])
                != manifest["target_species"]
            ):
                raise ValueError("Invalid individual AI evidence or species")
            if row["review_status"] not in {"not_started", "in_progress", "completed"}:
                raise ValueError("Invalid individual review status")
            if row["age"] not in {
                None,
                "Adult",
                "Adolescent",
                "Juvenile",
                "Infant",
                "Unknown",
            } or row["sex"] not in {None, "Male", "Female", "Unknown"}:
                raise ValueError("Invalid individual attributes")
            anchors = [row["anchor_" + key] for key in ("x", "y", "width", "height")]
            if any(v is not None for v in anchors):
                if any(v is None for v in anchors) or not (
                    0 <= anchors[0]
                    and 0 <= anchors[1]
                    and anchors[2] > 0
                    and anchors[3] > 0
                    and anchors[0] + anchors[2] <= 1
                    and anchors[1] + anchors[3] <= 1
                ):
                    raise ValueError("Invalid individual anchor")
            from review_app.backend.annotation_package.archive import OPTIONAL_TEXT

            links = _records(
                row["tags"], {"key": TEXT, "tagged_by": OPTIONAL_TEXT, "tagged_at": TEXT}, "key"
            )
            if not links.keys() <= tags:
                raise ValueError("Unknown individual tag")
        for row in unit["samples"]:
            if row["ai_original"] is not None and not isinstance(row["ai_original"], dict):
                raise ValueError("Invalid sample AI evidence")
            key = row["individual_id"], row["offset_ms"]
            if row["individual_id"] not in individuals or row["offset_ms"] < 0 or key in offsets:
                raise ValueError("Invalid sample ownership or duplicate offset")
            offsets.add(key)
            carrier = row["carried_by_individual_id"]
            if carrier and (carrier not in individuals or carrier == row["individual_id"]):
                raise ValueError("Sample carrier must belong to the same task")
            if not isinstance(row["behaviors"], list) or not set(row["behaviors"]) <= behaviors:
                raise ValueError("Unknown sample behavior")
            if row["reference_frame_id"] and row["reference_frame_id"] not in frames:
                raise ValueError("Dangling sample reference")
            if (
                row["ai_original"]
                and row["ai_original"].get("reference_frame_id")
                and row["ai_original"]["reference_frame_id"] not in frames
            ):
                raise ValueError("Dangling AI suggestion")
            if row["status"] == "annotated":
                valid = (
                    row["skip_reason"] is None
                    and row["distance_m"] is not None
                    and (
                        row["distance_m"] == 0
                        if row["reference_frame_id"] is None
                        else row["distance_m"] > 0
                    )
                )
            elif row["status"] == "pending":
                valid = all(
                    row[k] is None
                    for k in (
                        "reference_frame_id",
                        "distance_m",
                        "skip_reason",
                        "labeled_at",
                        "labeled_by",
                        "carried_by_individual_id",
                    )
                )
            elif row["status"] == "skipped":
                valid = (
                    row["skip_reason"] in {"individual absent", "midpoint not visible"}
                    and row["reference_frame_id"] is None
                    and row["distance_m"] is None
                )
            else:
                valid = False
            if not valid:
                raise ValueError("Invalid Distance sample state")
        for row in unit["progress"]:
            if (
                row["task_id"] != task["id"]
                or row["active_individual_id"] not in individuals
                or row["annotator_name"] in authors
            ):
                raise ValueError("Invalid resume progress")
            authors.add(row["annotator_name"])
    live_ids = {r["id"] for u in work for r in u["individuals"]}
    rejected_ids = {
        r["original"]["individual_id"]
        for u in work
        if u["task"]
        for r in u["task"]["rejected_ai"]
        if isinstance(r, dict)
        and isinstance(r.get("original"), dict)
        and r["original"].get("individual_id")
    }
    for source in imports.values():
        payload = source["payload"]
        if payload.keys() != {"individuals", "references", "predictions", "sampling"} or any(
            not isinstance(v, list) for v in payload.values()
        ):
            raise ValueError("Invalid projected AI source")
        for row in payload["individuals"]:
            if not isinstance(row, dict) or (
                row.get("distance_individual_id") not in live_ids
                and row.get("individual_id") not in rejected_ids
            ):
                raise ValueError("AI source contains out-of-scope individual")
        if any(
            r.get("species", manifest["target_species"]) != manifest["target_species"]
            for r in payload["individuals"]
        ):
            raise ValueError("AI source contains wrong-species work")
        source_ids = {r.get("individual_id") for r in payload["individuals"]}
        if any(
            not isinstance(r, dict) or r.get("individual_id") not in source_ids
            for r in payload["predictions"]
        ):
            raise ValueError("AI source contains out-of-scope prediction")
        if any(
            not isinstance(r, dict) or r.get("reference_frame_id") not in frames
            for r in payload["references"]
        ):
            raise ValueError("AI source contains dangling reference")
        if any(
            not isinstance(r, dict)
            or r.get("video_id") not in manifest["work_keys"]
            or r.get("species") != manifest["target_species"]
            for r in payload["sampling"]
        ):
            raise ValueError("AI source contains out-of-scope schedule")
    if manifest["direction"] == "setup" and keys != set(manifest["work_keys"]):
        raise ValueError("Distance setup must contain every work unit")
    if manifest["target_species"] not in {r["scientific_name"] for r in context["species"]}:
        raise ValueError("Unknown target species")


def validate_reference_membership(context, work, setup):
    """Use the setup's deployment links, or its historical root/label pairs."""
    deps = context["distance"]
    reference_media = {row["id"]: row["video_id"] for row in deps["references"]}
    frame_media = {row["id"]: reference_media[row["reference_video_id"]] for row in deps["frames"]}
    deployment_snapshot = setup.get("deployments")
    if deployment_snapshot is None:
        media = {row["video_id"]: (row["root_id"], row["camera_id"]) for row in setup["media"]}
    else:
        media = {row["video_id"]: row["deployment_id"] for row in deployment_snapshot["media"]}
    for unit in work:
        scope = media.get(unit["video_id"])
        for sample in unit["samples"]:
            suggested = (sample["ai_original"] or {}).get("reference_frame_id")
            for frame_id in (sample["reference_frame_id"], suggested):
                if frame_id and (scope is None or media.get(frame_media[frame_id]) != scope):
                    raise ValueError("Reference evidence belongs to another deployment")


def restore_work(session, project_id, video_id, unit, vocab, identities, mapping, target):
    from review_app.backend.db.models import Annotator

    authors = {
        r.get(key)
        for r in ([unit["task"]] if unit["task"] else []) + unit["individuals"] + unit["samples"]
        for key in ("labeled_by", "verified_by", "completed_by")
    }
    authors.update(t["tagged_by"] for r in unit["individuals"] for t in r["tags"])
    authors.update(r["annotator_name"] for r in unit["progress"])
    for name in authors - {None, ""}:
        if session.get(Annotator, name) is None:
            session.add(Annotator(name=name))
    session.flush()
    species_id = vocab["species"][target]
    old = (
        session.query(DistanceReviewTask)
        .filter_by(video_id=video_id, species_id=species_id)
        .one_or_none()
    )
    if old:
        # Explicitly break self/cross-individual FKs before deleting the graph.
        ids = id_selection(
            r.id for r in session.query(DistanceIndividual).filter_by(task_id=old.id)
        )
        session.query(DistanceSample).filter(DistanceSample.individual_id.in_(ids)).update(
            {"carried_by_individual_id": None}, synchronize_session=False
        )
        session.query(DistanceIndividual).filter_by(task_id=old.id).update(
            {"initial_carrier_individual_id": None}, synchronize_session=False
        )
        session.query(DistanceReviewTask).filter_by(id=old.id).delete(synchronize_session=False)
        session.expunge(old)
        session.flush()
    if unit["task"] is None:
        return
    for kind, rows in (
        ("task", [unit["task"]]),
        ("individual", unit["individuals"]),
        ("sample", unit["samples"]),
    ):
        for row in rows:
            mapping[row["id"]] = identities.local("distance_" + kind, row["id"])

    def converted(row, model, exclude=()):
        value = rewrite({k: v for k, v in row.items() if k not in exclude}, mapping)
        for column in model.__table__.columns:
            if column.name in value and isinstance(column.type, DateTime):
                value[column.name] = species.timestamp(value[column.name])
        return value

    session.add(
        DistanceReviewTask(
            video_id=video_id, species_id=species_id, **converted(unit["task"], DistanceReviewTask)
        )
    )
    session.flush()
    for row in unit["individuals"]:
        data = converted(row, DistanceIndividual, ("tags",))
        data["initial_carrier_individual_id"] = None
        session.add(DistanceIndividual(**data))
    session.flush()
    for row in unit["individuals"]:
        # Bulk update avoids ORM onupdate changing the saved timestamp.
        session.query(DistanceIndividual).filter_by(id=mapping[row["id"]]).update(
            {
                "initial_carrier_individual_id": mapping.get(row["initial_carrier_individual_id"]),
                "updated_at": species.timestamp(row["updated_at"]),
            },
            synchronize_session=False,
        )
        session.add_all(
            DistanceIndividualTag(
                individual_id=mapping[row["id"]],
                tag_id=vocab["tags"][t["key"]],
                tagged_by=t["tagged_by"],
                tagged_at=species.timestamp(t["tagged_at"]),
            )
            for t in row["tags"]
        )
    for row in unit["samples"]:
        session.add(DistanceSample(**converted(row, DistanceSample, ("behaviors",))))
    session.flush()
    for row in unit["samples"]:
        session.add_all(
            DistanceSampleBehavior(
                sample_id=mapping[row["id"]], behavior_id=vocab["behaviors"][key]
            )
            for key in row["behaviors"]
        )
    session.add_all(
        DistanceReviewProgress(**converted(row, DistanceReviewProgress))
        for row in unit["progress"]
    )
    session.flush()


def match_reference_frames(incoming, current, mapped_ids):
    """Match exact calibration positions and preserve established frame identities."""
    by_id = {frame["id"]: frame for frame in current}
    by_position = {
        (frame["reference_video_id"], frame["distance_m"], frame["offset_ms"]): frame
        for frame in current
    }
    matches = {}
    for frame in incoming:
        found = by_position.get(
            (frame["reference_video_id"], frame["distance_m"], frame["offset_ms"])
        )
        if found:
            matches[frame["id"]] = found
    claimed = {frame["id"] for frame in matches.values()}
    for frame in incoming:
        if frame["id"] in matches:
            continue
        found = by_id.get(mapped_ids.get(frame["id"]))
        if (
            found
            and found["id"] not in claimed
            and found["reference_video_id"] == frame["reference_video_id"]
            and found["distance_m"] == frame["distance_m"]
        ):
            matches[frame["id"]] = found
            claimed.add(found["id"])
    return matches


def restore_dependencies(
    session,
    project_id,
    context,
    identities,
    videos,
    media_paths,
    work,
    *,
    approved_substitutions=(),
    frozen_reference_deployments=None,
):
    deps = context["distance"]
    mapping = dict(videos)
    for kind, rows in (
        ("task", [u["task"] for u in work if u["task"]]),
        ("individual", [r for u in work for r in u["individuals"]]),
        ("sample", [r for u in work for r in u["samples"]]),
    ):
        mapping.update({r["id"]: identities.local("distance_" + kind, r["id"]) for r in rows})
    media_by_id = {row["video_id"]: row for row in context["media"]}
    for ref in deps["references"]:
        video_id = ref["video_id"]
        path = media_paths[video_id]
        local = (
            session.query(ReferenceVideo).filter_by(project_id=project_id, video_path=path).first()
        )
        label = media_by_id[video_id]["camera_id"]
        if frozen_reference_deployments is not None:
            deployment_id = frozen_reference_deployments[video_id]
        elif "deployments" in context:
            # Setup restores the portable links after all reference records exist.
            deployment_id = None
        else:
            deployment_id = resolve_reference_deployment(session, project_id, path, label)
        if local is None:
            local = ReferenceVideo(
                id=identities.local("distance_reference", ref["id"]),
                project_id=project_id,
                video_path=path,
                unresolved_camera_label=label if deployment_id is None else None,
                deployment_id=deployment_id,
            )
            session.add(local)
            session.flush()
        elif deployment_id:
            if local.deployment_id not in (None, deployment_id):
                raise ValueError("Reference calibration belongs to another deployment")
            assign_reference_deployment(local, deployment_id)
        mapping[ref["id"]] = local.id
    incoming = [
        {**frame, "reference_video_id": mapping[frame["reference_video_id"]]}
        for frame in deps["frames"]
    ]
    current = [
        frame_record(frame)
        for frame in session.query(ReferenceFrame).filter(
            ReferenceFrame.reference_video_id.in_(
                id_selection(frame["reference_video_id"] for frame in incoming)
            )
        )
    ]
    mapped_ids = {
        row["returned_frame_id"]: videos.get(row["existing_frame_id"], row["existing_frame_id"])
        for row in approved_substitutions
    }
    matches = match_reference_frames(incoming, current, mapped_ids)
    for frame in deps["frames"]:
        local = matches.get(frame["id"])
        local_id = local["id"] if local else identities.local("distance_frame", frame["id"])
        if local is None and session.get(ReferenceFrame, local_id) is not None:
            # The previously bound calibration still occupies a different position.
            import uuid

            local_id = str(uuid.uuid4())
        mapping[frame["id"]] = local_id
    for source in deps["imports"]:
        projected = rewrite(source["payload"], mapping)
        content_hash = digest({"source": source["source_hash"], "payload": projected})
        local = (
            session.query(DistanceImport)
            .filter_by(project_id=project_id, content_hash=content_hash)
            .first()
        )
        if local is None:
            local = DistanceImport(
                id=identities.local("distance_import", source["id"]),
                project_id=project_id,
                content_hash=content_hash,
                manifest={
                    **source["manifest"],
                    "assignment_projection": {
                        "source_id": source["source_id"],
                        "source_hash": source["source_hash"],
                        "payload_hash": digest(projected),
                    },
                },
                payload=projected,
                imported_at=species.timestamp(source["imported_at"]),
            )
            # A later scoped return may project a changed source under a fresh ID.
            if session.get(DistanceImport, local.id) is not None:
                import uuid

                local.id = str(uuid.uuid4())
            session.add(local)
            session.flush()
        mapping[source["id"]] = local.id
    for frame in deps["frames"]:
        if session.get(ReferenceFrame, mapping[frame["id"]]) is None:
            session.add(ReferenceFrame(**rewrite(frame, mapping)))
    session.flush()
    return mapping
