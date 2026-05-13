from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_opened: Mapped[datetime | None] = mapped_column(DateTime)
    collection_id: Mapped[str | None] = mapped_column(String, ForeignKey("species_collections.id"))
    dirs: Mapped[list[ProjectDir]] = relationship(
        "ProjectDir", backref="project", cascade="all, delete-orphan"
    )
    videos: Mapped[list[Video]] = relationship("Video", cascade="all, delete-orphan")
    project_species: Mapped[list[ProjectSpecies]] = relationship(
        "ProjectSpecies", cascade="all, delete-orphan"
    )
    project_species_behaviors: Mapped[list[ProjectSpeciesBehavior]] = relationship(
        "ProjectSpeciesBehavior", cascade="all, delete-orphan"
    )


class ProjectDir(Base):
    __tablename__ = "project_dirs"
    __table_args__ = (UniqueConstraint("project_id", "path", name="uq_project_dir"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), index=True)
    path: Mapped[str] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("video_path", "project_id", name="uq_video_path_project"),)

    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String, ForeignKey("projects.id"), index=True)
    video_path: Mapped[str] = mapped_column(String)
    camera_id: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    is_valid: Mapped[bool | None] = mapped_column(Boolean)
    is_web_safe: Mapped[bool | None] = mapped_column(Boolean)
    validation_error: Mapped[str | None] = mapped_column(String)
    transcoded_path: Mapped[str | None] = mapped_column(String)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    label: Mapped[VideoLabel | None] = relationship(
        "VideoLabel", cascade="all, delete-orphan", uselist=False
    )
    observations: Mapped[list[IndividualObservation]] = relationship(
        "IndividualObservation", cascade="all, delete-orphan"
    )
    annotations: Mapped[list[ModelAnnotation]] = relationship(
        "ModelAnnotation", cascade="all, delete-orphan"
    )
    tags: Mapped[list[VideoTag]] = relationship("VideoTag", cascade="all, delete-orphan")


class VideoLabel(Base):
    __tablename__ = "video_labels"

    video_id: Mapped[str] = mapped_column(String, ForeignKey("videos.video_id"), primary_key=True)
    is_blank: Mapped[bool | None] = mapped_column(Boolean)
    labeled_by: Mapped[str | None] = mapped_column(String)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_later: Mapped[bool | None] = mapped_column(Boolean, default=False)


class Species(Base):
    __tablename__ = "species"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    scientific_name: Mapped[str] = mapped_column(String, unique=True)
    name_en: Mapped[str | None] = mapped_column(String)
    name_fr: Mapped[str | None] = mapped_column(String)
    group_en: Mapped[str | None] = mapped_column(String)
    group_fr: Mapped[str | None] = mapped_column(String)
    iucn: Mapped[str | None] = mapped_column(String)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    behaviors: Mapped[list[SpeciesBehavior]] = relationship(
        "SpeciesBehavior", cascade="all, delete-orphan"
    )


class Behavior(Base):
    __tablename__ = "behaviors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String, unique=True)
    name_en: Mapped[str] = mapped_column(String)
    name_fr: Mapped[str | None] = mapped_column(String)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)


class SpeciesBehavior(Base):
    __tablename__ = "species_behaviors"

    species_id: Mapped[str] = mapped_column(String, ForeignKey("species.id"), primary_key=True)
    behavior_id: Mapped[str] = mapped_column(String, ForeignKey("behaviors.id"), primary_key=True)


class ProjectSpecies(Base):
    __tablename__ = "project_species"

    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), primary_key=True)
    species_id: Mapped[str] = mapped_column(String, ForeignKey("species.id"), primary_key=True)


class ProjectSpeciesBehavior(Base):
    __tablename__ = "project_species_behaviors"

    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), primary_key=True)
    species_id: Mapped[str] = mapped_column(String, ForeignKey("species.id"), primary_key=True)
    behavior_id: Mapped[str] = mapped_column(String, ForeignKey("behaviors.id"), primary_key=True)


class SpeciesCollection(Base):
    __tablename__ = "species_collections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    members: Mapped[list[SpeciesCollectionMember]] = relationship(
        "SpeciesCollectionMember", cascade="all, delete-orphan"
    )


class SpeciesCollectionMember(Base):
    __tablename__ = "species_collection_members"

    collection_id: Mapped[str] = mapped_column(
        String, ForeignKey("species_collections.id"), primary_key=True
    )
    species_id: Mapped[str] = mapped_column(String, ForeignKey("species.id"), primary_key=True)


class IndividualObservation(Base):
    __tablename__ = "individual_observations"

    video_id: Mapped[str] = mapped_column(String, ForeignKey("videos.video_id"), primary_key=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String, ForeignKey("projects.id"), index=True)
    species_id: Mapped[str | None] = mapped_column(String, ForeignKey("species.id"), index=True)
    behavior_id: Mapped[str | None] = mapped_column(String, ForeignKey("behaviors.id"), index=True)
    count: Mapped[int | None] = mapped_column(Integer)
    start_sec: Mapped[float] = mapped_column(Float, default=0.0)
    end_sec: Mapped[float | None] = mapped_column(Float)
    labeled_by: Mapped[str | None] = mapped_column(String)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ModelAnnotation(Base):
    __tablename__ = "model_annotations"
    __table_args__ = (
        UniqueConstraint(
            "video_id", "model_name", "annotation_type", name="uq_model_ann_identity"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str | None] = mapped_column(String, ForeignKey("projects.id"), index=True)
    video_id: Mapped[str] = mapped_column(String, ForeignKey("videos.video_id"), index=True)
    annotation_type: Mapped[str] = mapped_column(String, index=True)
    model_name: Mapped[str] = mapped_column(String, index=True)
    value_text: Mapped[str | None] = mapped_column(String)
    value_num: Mapped[float | None] = mapped_column(Float)
    probability: Mapped[float | None] = mapped_column(Float)
    t_start_sec: Mapped[float | None] = mapped_column(Float)
    t_end_sec: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String, unique=True)
    name_en: Mapped[str] = mapped_column(String)
    name_fr: Mapped[str | None] = mapped_column(String)
    color: Mapped[str | None] = mapped_column(String)
    icon: Mapped[str | None] = mapped_column(String)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)


class VideoTag(Base):
    __tablename__ = "video_tags"

    video_id: Mapped[str] = mapped_column(String, ForeignKey("videos.video_id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String, ForeignKey("tags.id"), primary_key=True)
    tagged_by: Mapped[str | None] = mapped_column(String)
    tagged_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(String)


Index(
    "idx_individual_video_species",
    IndividualObservation.video_id,
    IndividualObservation.species_id,
)
Index(
    "idx_individual_video_behavior",
    IndividualObservation.video_id,
    IndividualObservation.behavior_id,
)
Index("idx_individual_video_time", IndividualObservation.video_id, IndividualObservation.start_sec)
Index("idx_videos_is_valid", Video.is_valid)
Index("idx_videos_is_web_safe", Video.is_web_safe)
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
# Covers: WHERE video_id=? AND behavior_id=?  (behavior filter EXISTS)
Index(
    "idx_individual_behavior_video",
    IndividualObservation.behavior_id,
    IndividualObservation.video_id,
)
