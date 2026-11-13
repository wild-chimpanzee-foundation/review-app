"""Versioned Species snapshot fields and session-aware collection, not CSV update semantics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from review_app.backend.annotation_package.scope import id_selection, portable_path
from review_app.backend.db.models import (
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
    for video in videos:
        root_id, path = portable_path(video.video_path, roots)
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
