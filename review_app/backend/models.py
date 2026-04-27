from __future__ import annotations

import sys
import uuid
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    last_opened = Column(DateTime, nullable=True)
    dirs = relationship("ProjectDir", backref="project", cascade="all, delete-orphan")


class ProjectDir(Base):
    __tablename__ = "project_dirs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, index=True)
    path = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("video_path", "project_id", name="uq_video_path_project"),)

    video_id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)
    video_path = Column(String, nullable=False)
    camera_id = Column(String, index=True)
    created_at = Column(DateTime, nullable=True)
    duration_sec = Column(Float, nullable=True)
    last_seen_at = Column(DateTime, nullable=False, default=func.now())
    # Populated by ffprobe on first ingest; never overwritten for existing rows.
    is_valid = Column(Boolean, nullable=True)
    is_web_safe = Column(Boolean, nullable=True)
    validation_error = Column(String, nullable=True)
    transcoded_path = Column(String, nullable=True)


class VideoLabel(Base):
    __tablename__ = "video_labels"

    video_id = Column(String, ForeignKey("videos.video_id"), primary_key=True)
    is_blank = Column(Boolean, nullable=True)
    labeled_by = Column(String, nullable=True)
    labeled_at = Column(DateTime, nullable=True)


class IndividualObservation(Base):
    __tablename__ = "individual_observations"

    video_id = Column(String, ForeignKey("videos.video_id"), primary_key=True)
    id = Column(Integer, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)
    species = Column(String, nullable=False)
    behavior = Column(String, nullable=False)
    start_sec = Column(Float, nullable=False, default=0.0)
    end_sec = Column(Float, nullable=True)
    labeled_by = Column(String, nullable=True)
    labeled_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class ModelAnnotation(Base):
    __tablename__ = "model_annotations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)
    video_id = Column(String, ForeignKey("videos.video_id"), nullable=False, index=True)
    annotation_type = Column(String, nullable=False, index=True)
    model_name = Column(String, nullable=False, index=True)
    value_text = Column(String, nullable=True)
    value_num = Column(Float, nullable=True)
    probability = Column(Float, nullable=True)
    t_start_sec = Column(Float, nullable=True)
    t_end_sec = Column(Float, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "video_id", "model_name", "annotation_type", name="uq_model_ann_identity"
        ),
    )


class Species(Base):
    __tablename__ = "species"

    scientific_name = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)
    name_en = Column(String, nullable=True)
    name_fr = Column(String, nullable=True)
    group_en = Column(String, nullable=True)
    group_fr = Column(String, nullable=True)
    iucn = Column(String, nullable=True)
    behaviors = relationship("SpeciesBehavior", backref="species", cascade="all, delete-orphan")


class SpeciesBehavior(Base):
    __tablename__ = "species_behavior"

    scientific_name = Column(String, ForeignKey("species.scientific_name"), primary_key=True)
    behavior = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)


Index(
    "idx_individual_video_species", IndividualObservation.video_id, IndividualObservation.species
)
Index(
    "idx_individual_video_behavior", IndividualObservation.video_id, IndividualObservation.behavior
)
Index("idx_individual_video_time", IndividualObservation.video_id, IndividualObservation.start_sec)
Index("idx_videos_is_valid", Video.is_valid)
Index("idx_model_ann_type_value", ModelAnnotation.annotation_type, ModelAnnotation.value_text)
# Covers: WHERE annotation_type='species' AND value_text=:ps  (possible_species filter)
Index(
    "idx_model_ann_type_text_video",
    ModelAnnotation.annotation_type,
    ModelAnnotation.value_text,
    ModelAnnotation.video_id,
)
# Covers: WHERE annotation_type='blank_non_blank' inside effective_blank CTE
Index(
    "idx_model_ann_blank_probe",
    ModelAnnotation.annotation_type,
    ModelAnnotation.video_id,
    ModelAnnotation.probability,
)
# Covers: WHERE video_id=? AND behavior=?  (behavior filter EXISTS)
Index(
    "idx_individual_behavior_video",
    IndividualObservation.behavior,
    IndividualObservation.video_id,
)
