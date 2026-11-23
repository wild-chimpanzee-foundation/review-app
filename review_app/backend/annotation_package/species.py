"""Versioned Species snapshot fields and session-aware collection, not CSV update semantics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from review_app.backend.annotation_package.scope import id_selection, portable_path
from review_app.backend.db.models import (
    Annotator,
    Behavior,
    IndividualObservation,
    ModelAnnotation,
    ObservationTag,
    ProjectSpecies,
    Species,
    Tag,
    VideoLabel,
    VideoTag,
)

# Explicit v1 fields: schema evolution must not silently change the transfer contract.
SPECIES_FIELDS = "scientific_name name_en name_fr group_en group_fr iucn inaturalist_url is_custom"
BEHAVIOR_FIELDS = "key name_en name_fr is_custom"
TAG_FIELDS = "key name_en name_fr color icon is_custom individual_eligible"
MEDIA_FIELDS = "camera_id created_at duration_sec latitude longitude is_missing"
LABEL_FIELDS = "is_blank review_later labeled_by labeled_at"
OBSERVATION_FIELDS = "observation_uuid count start_sec end_sec labeled_by labeled_at updated_at"
PREDICTION_FIELDS = (
    "id video_id annotation_type model_name value_text value_num probability "
    "t_start_sec t_end_sec updated_at"
)


def fields(row, names: str) -> dict:
    result = {name: getattr(row, name) for name in names.split()}
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in result.items()
    }


def timestamp(value):
    """SQLite stores naive timestamps; retain the instant when input includes an offset."""
    from datetime import timezone

    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def used_vocabulary(work):
    return {
        "species": {
            row["species"] for unit in work for row in unit["observations"] if row["species"]
        },
        "behaviors": {
            key for unit in work for row in unit["observations"] for key in row["behaviors"]
        },
        "tags": {row["key"] for unit in work for row in unit["tags"]},
    }


def vocabulary(session, context, work=None):
    """Reuse natural keys, never overwrite shared definitions; import only used return keys."""
    used = None if work is None else used_vocabulary(work)
    result = {}
    for name, model, key in (
        ("species", Species, "scientific_name"),
        ("behaviors", Behavior, "key"),
        ("tags", Tag, "key"),
    ):
        local = {getattr(row, key): row.id for row in session.query(model)}
        for definition in context[name]:
            value = definition[key]
            if used is not None and value not in used[name]:
                continue
            if value not in local:
                row = model(**definition)
                session.add(row)
                session.flush()
                local[value] = row.id
        result[name] = local
    return result


def restore_work(session, project_id, video_id, unit, vocab):
    """Replace exactly one Species video, preserving saved evidence and unrelated Distance."""
    existing = {
        row.observation_uuid: row.id
        for row in session.query(IndividualObservation).filter_by(video_id=video_id)
    }
    next_id = max(existing.values(), default=0) + 1
    for model in (ObservationTag, IndividualObservation, VideoTag, VideoLabel):
        session.query(model).filter_by(video_id=video_id).delete(synchronize_session=False)
    session.flush()
    authors = {row.get("labeled_by") for row in unit["observations"]}
    authors.update(row["tagged_by"] for row in unit["tags"])
    if unit["label"]:
        authors.add(unit["label"]["labeled_by"])
        label = {**unit["label"], "labeled_at": timestamp(unit["label"]["labeled_at"])}
        session.add(VideoLabel(video_id=video_id, **label))
    for name in authors - {None, ""}:
        if session.get(Annotator, name) is None:
            session.add(Annotator(name=name))
    for source in unit["observations"]:
        number = existing.get(source["observation_uuid"])
        if number is None:
            number, next_id = next_id, next_id + 1
        row = {key: value for key, value in source.items() if key not in {"species", "behaviors"}}
        for key in ("labeled_at", "updated_at"):
            row[key] = timestamp(row[key])
        session.add(
            IndividualObservation(
                video_id=video_id,
                id=number,
                project_id=project_id,
                species_id=vocab["species"].get(source["species"]),
                **row,
            )
        )
        session.flush()
        session.add_all(
            ObservationTag(
                video_id=video_id, observation_id=number, behavior_id=vocab["behaviors"][key]
            )
            for key in source["behaviors"]
        )
    session.add_all(
        VideoTag(
            video_id=video_id,
            tag_id=vocab["tags"][row["key"]],
            tagged_by=row["tagged_by"],
            tagged_at=timestamp(row["tagged_at"]),
        )
        for row in unit["tags"]
    )
    session.flush()


def meaningful_label(label):
    return (
        label
        if label
        and any(
            (
                label["is_blank"] is not None,
                label["review_later"],
                label["labeled_by"],
                label["labeled_at"],
            )
        )
        else None
    )


def merge_append(local, returned):
    """Merge returned Species work by stable UUID while retaining local-only evidence."""
    observations = {row["observation_uuid"]: row for row in local["observations"]}
    observations.update({row["observation_uuid"]: row for row in returned["observations"]})
    tags = {row["key"]: row for row in local["tags"]}
    tags.update({row["key"]: row for row in returned["tags"] if row["key"] not in tags})
    label = meaningful_label(local["label"]) or meaningful_label(returned["label"])
    blank_corrected = bool(observations and label and label["is_blank"] is True)
    if blank_corrected:
        label = {**label, "is_blank": False}
    return {
        "video_id": returned["video_id"],
        "label": label,
        "observations": list(observations.values()),
        "tags": list(tags.values()),
    }, blank_corrected


def canonical(unit):
    """Compare saved meaning, ignoring local handles, list ordering, and timestamp spelling."""
    import copy

    value = copy.deepcopy(unit)
    label = value["label"]
    if label and not any(
        (
            label["is_blank"] is not None,
            label["review_later"],
            label["labeled_by"],
            label["labeled_at"],
        )
    ):
        value["label"] = None
    elif label:
        label["review_later"] = bool(label["review_later"])
    rows = value["observations"] + value["tags"] + ([value["label"]] if value["label"] else [])
    for row in rows:
        for key, val in row.items():
            if key.endswith("_at") and val is not None:
                row[key] = timestamp(val).isoformat(timespec="microseconds")
        if "behaviors" in row:
            row["behaviors"] = sorted(row["behaviors"])
    value["observations"].sort(key=lambda row: row["observation_uuid"])
    value["tags"].sort(key=lambda row: row["key"])
    return value


def counts(unit):
    unit = canonical(unit)
    return {
        "labels": int(unit["label"] is not None),
        "observations": len(unit["observations"]),
        "behaviors": sum(len(row["behaviors"]) for row in unit["observations"]),
        "tags": len(unit["tags"]),
    }


def removals(before, after):
    def keys(unit):
        unit = canonical(unit)
        return {
            "labels": {"label"} if unit["label"] else set(),
            "observations": {row["observation_uuid"] for row in unit["observations"]},
            "behaviors": {
                (row["observation_uuid"], key)
                for row in unit["observations"]
                for key in row["behaviors"]
            },
            "tags": {row["key"] for row in unit["tags"]},
        }

    old, new = keys(before), keys(after)
    return {name: len(values - new[name]) for name, values in old.items()}


def collect(session: Session, project, videos, roots) -> tuple[dict, list[dict]]:
    selected = id_selection([video.video_id for video in videos])
    species_rows = session.query(Species).order_by(Species.scientific_name).all()
    species_names = {row.id: row.scientific_name for row in species_rows}
    behaviors = session.query(Behavior).order_by(Behavior.key).all()
    behavior_keys = {row.id: row.key for row in behaviors}
    tags = session.query(Tag).order_by(Tag.key).all()
    tag_keys = {row.id: row.key for row in tags}
    project_species = [
        species_names[row.species_id]
        for row in session.query(ProjectSpecies).filter_by(project_id=project.id)
    ]
    # No explicit project vocabulary means all global species, as in ordinary Review.
    if not project_species:
        project_species = list(species_names.values())
    labels = {
        row.video_id: fields(row, LABEL_FIELDS)
        for row in session.query(VideoLabel).filter(VideoLabel.video_id.in_(selected))
    }
    observation_behaviors = {}
    for link in session.query(ObservationTag).filter(ObservationTag.video_id.in_(selected)):
        observation_behaviors.setdefault((link.video_id, link.observation_id), []).append(
            behavior_keys[link.behavior_id]
        )
    observations = {}
    for row in (
        session.query(IndividualObservation)
        .filter(IndividualObservation.video_id.in_(selected))
        .order_by(IndividualObservation.video_id, IndividualObservation.id)
    ):
        observations.setdefault(row.video_id, []).append(
            {
                **fields(row, OBSERVATION_FIELDS),
                "species": species_names.get(row.species_id),
                "behaviors": sorted(observation_behaviors.get((row.video_id, row.id), [])),
            }
        )
    video_tags = {}
    for row in session.query(VideoTag).filter(VideoTag.video_id.in_(selected)):
        video_tags.setdefault(row.video_id, []).append(
            {"key": tag_keys[row.tag_id], **fields(row, "tagged_by tagged_at")}
        )
    media = []
    deployment_roots = {}
    for video in videos:
        root_id, path = portable_path(video.video_path, roots)
        if video.deployment_id:
            deployment_roots.setdefault(video.deployment_id, set()).add(root_id)
            if len(deployment_roots[video.deployment_id]) > 1:
                raise ValueError(
                    "The current package format cannot carry one deployment across multiple import roots"
                )
        media.append(
            {
                "video_id": video.video_id,
                "root_id": root_id,
                "video_path": path,
                **fields(video, MEDIA_FIELDS),
            }
        )
    context = {
        "project_name": project.name,
        "project_species": sorted(project_species),
        "species": [fields(row, SPECIES_FIELDS) for row in species_rows],
        "behaviors": [fields(row, BEHAVIOR_FIELDS) for row in behaviors],
        "tags": [fields(row, TAG_FIELDS) for row in tags],
        "media": media,
        "predictions": [
            fields(row, PREDICTION_FIELDS)
            for row in session.query(ModelAnnotation)
            .filter(ModelAnnotation.video_id.in_(selected))
            .order_by(ModelAnnotation.id)
        ],
    }
    work = [
        {
            "video_id": video.video_id,
            "label": labels.get(video.video_id),
            "observations": observations.get(video.video_id, []),
            "tags": sorted(video_tags.get(video.video_id, []), key=lambda row: row["key"]),
        }
        for video in videos
    ]
    return context, work
