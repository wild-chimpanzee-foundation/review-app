from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    case,
    func,
    select,
    text,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, object_session, relationship


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


class ProjectDir(Base):
    __tablename__ = "project_dirs"
    __table_args__ = (UniqueConstraint("project_id", "path", name="uq_project_dir"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), index=True)
    path: Mapped[str] = mapped_column(String)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Deployment(Base):
    """Project-scoped deployment identity, independent of folder and camera labels."""

    __tablename__ = "deployments"
    __table_args__ = (
        UniqueConstraint("project_id", "deployment_id", name="uq_deployment_project_key"),
        UniqueConstraint("project_id", "id", name="uq_deployment_project_id"),
        CheckConstraint("length(trim(deployment_id)) > 0", name="ck_deployment_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    deployment_id: Mapped[str] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    camera_id: Mapped[str | None] = mapped_column(String)
    installation_date: Mapped[date | None] = mapped_column(Date)
    retrieval_date: Mapped[date | None] = mapped_column(Date)
    transect_id: Mapped[str | None] = mapped_column(String)
    habitat: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)


def _deployment_label(row) -> str | None:
    if row.deployment_id is None:
        return None
    deployment = row.deployment
    session = object_session(row)
    if session is not None and (deployment is None or deployment.id != row.deployment_id):
        deployment = session.get(Deployment, row.deployment_id)
    return deployment.deployment_id if deployment else None


def _deployment_label_sql(model):
    return (
        select(Deployment.deployment_id)
        .where(Deployment.id == model.deployment_id)
        .correlate_except(Deployment)
        .scalar_subquery()
    )


class DeploymentFolderLink(Base):
    __tablename__ = "deployment_folder_links"
    __table_args__ = (
        UniqueConstraint("project_dir_id", "folder_path", name="uq_deployment_folder"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_dir_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_dirs.id", ondelete="CASCADE"), index=True
    )
    folder_path: Mapped[str] = mapped_column(String)
    deployment_id: Mapped[str | None] = mapped_column(String, ForeignKey("deployments.id"))
    source: Mapped[str] = mapped_column(String, default="manual", server_default="manual")


class DeploymentFolderReview(Base):
    __tablename__ = "deployment_folder_reviews"

    project_dir_id: Mapped[str] = mapped_column(
        String, ForeignKey("project_dirs.id", ondelete="CASCADE"), primary_key=True
    )
    folder_path: Mapped[str] = mapped_column(String, primary_key=True)
    note: Mapped[str | None] = mapped_column(String)
    available_candidates: Mapped[list[str]] = mapped_column(JSON)


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("video_path", "project_id", name="uq_video_path_project"),
        ForeignKeyConstraint(
            ("project_id", "deployment_id"), ("deployments.project_id", "deployments.id")
        ),
        CheckConstraint(
            "project_id IS NOT NULL OR deployment_id IS NULL", name="ck_video_deployment_project"
        ),
    )

    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String, ForeignKey("projects.id"), index=True)
    video_path: Mapped[str] = mapped_column(String)
    folder_camera_label: Mapped[str | None] = mapped_column(String)
    deployment_id: Mapped[str | None] = mapped_column(String, index=True)
    deployment_assignment: Mapped[str] = mapped_column(
        String, default="legacy", server_default="legacy"
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    is_valid: Mapped[bool | None] = mapped_column(Boolean)
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_web_safe: Mapped[bool | None] = mapped_column(Boolean)
    validation_error: Mapped[str | None] = mapped_column(String)
    transcoded_path: Mapped[str | None] = mapped_column(String)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    deployment: Mapped[Deployment | None] = relationship(viewonly=True, lazy="joined")

    @hybrid_property
    def camera_id(self) -> str | None:
        if self.project_id is None:
            return self.folder_camera_label
        return _deployment_label(self)

    @camera_id.inplace.expression
    @classmethod
    def _camera_id_expression(cls):
        return case(
            (cls.project_id.is_(None), cls.folder_camera_label), else_=_deployment_label_sql(cls)
        )

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
    assignments: Mapped[list["VideoAssignment"]] = relationship(
        "VideoAssignment", cascade="all, delete-orphan"
    )


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
    inaturalist_url: Mapped[str | None] = mapped_column(String)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)


class Behavior(Base):
    __tablename__ = "behaviors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String, unique=True)
    name_en: Mapped[str] = mapped_column(String)
    name_fr: Mapped[str | None] = mapped_column(String)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)


class ObservationTag(Base):
    __tablename__ = "observation_tags"
    __table_args__ = (
        ForeignKeyConstraint(
            ("video_id", "observation_id"),
            ("individual_observations.video_id", "individual_observations.id"),
            ondelete="CASCADE",
        ),
    )

    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    observation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    behavior_id: Mapped[str] = mapped_column(String, ForeignKey("behaviors.id"), primary_key=True)


class ProjectSpecies(Base):
    __tablename__ = "project_species"

    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id"), primary_key=True)
    species_id: Mapped[str] = mapped_column(String, ForeignKey("species.id"), primary_key=True)


class DistanceSpeciesSettings(Base):
    __tablename__ = "distance_species_settings"

    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    species_id: Mapped[str] = mapped_column(
        String, ForeignKey("species.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean)
    require_age: Mapped[bool] = mapped_column(Boolean)
    require_sex: Mapped[bool] = mapped_column(Boolean)


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
    __table_args__ = (UniqueConstraint("video_id", "observation_uuid"),)

    video_id: Mapped[str] = mapped_column(String, ForeignKey("videos.video_id"), primary_key=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_uuid: Mapped[str] = mapped_column(
        String, nullable=False, default=lambda: str(uuid.uuid4())
    )
    species_id: Mapped[str | None] = mapped_column(String, ForeignKey("species.id"), index=True)
    count: Mapped[int | None] = mapped_column(Integer)
    start_sec: Mapped[float] = mapped_column(Float, default=0.0)
    end_sec: Mapped[float | None] = mapped_column(Float)
    labeled_by: Mapped[str | None] = mapped_column(String)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ModelAnnotation(Base):
    __tablename__ = "model_annotations"
    __table_args__ = (
        Index(
            "uq_model_ann_identity",
            "video_id",
            "model_name",
            "annotation_type",
            func.coalesce(text("value_text"), ""),
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
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
    individual_eligible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


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


class Annotator(Base):
    __tablename__ = "annotators"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class VideoAssignment(Base):
    __tablename__ = "video_assignments"

    video_id: Mapped[str] = mapped_column(String, ForeignKey("videos.video_id"), primary_key=True)
    assigned_to: Mapped[str] = mapped_column(String, ForeignKey("annotators.name"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class HandoffAssignment(Base):
    """A local binding to one frozen, portable assignment handout."""

    __tablename__ = "handoff_assignments"
    __table_args__ = (
        UniqueConstraint("project_id", "assignment_id", name="uq_handoff_project_assignment"),
        CheckConstraint("role IN ('origin', 'working')", name="ck_handoff_role"),
        CheckConstraint("length(trim(recipient)) > 0", name="ck_handoff_recipient"),
        CheckConstraint(
            "(stage = 'species' AND target_species IS NULL) OR "
            "(stage = 'distance' AND target_species IS NOT NULL "
            "AND length(trim(target_species)) > 0)",
            name="ck_handoff_stage",
        ),
        CheckConstraint(
            "role != 'origin' OR source_project_id = project_id", name="ck_handoff_origin"
        ),
        Index(
            "uq_handoff_working_project",
            "project_id",
            unique=True,
            sqlite_where=text("role = 'working'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    assignment_id: Mapped[str] = mapped_column(String)
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String)
    # The originating project need not exist on this instance.
    source_project_id: Mapped[str] = mapped_column(String)
    recipient: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String)
    target_species: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    scope: Mapped[dict] = mapped_column(JSON)
    # Removal hides delivery, but retains validation context for future returns.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    setup_archive: Mapped[bytes] = mapped_column(LargeBinary)


class HandoffIdentityMap(Base):
    """Portable graph identities are local to a handoff, never global DB keys."""

    __tablename__ = "handoff_identity_maps"
    __table_args__ = (
        UniqueConstraint("handoff_id", "kind", "local_id", name="uq_handoff_local_identity"),
        CheckConstraint(
            "length(trim(kind)) > 0 AND length(trim(portable_id)) > 0 "
            "AND length(trim(local_id)) > 0",
            name="ck_handoff_identity",
        ),
    )

    handoff_id: Mapped[str] = mapped_column(
        String, ForeignKey("handoff_assignments.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    portable_id: Mapped[str] = mapped_column(String, primary_key=True)
    local_id: Mapped[str] = mapped_column(String)


class DistanceImport(Base):
    """Immutable source package; review drafts live in the normal Distance tables."""

    __tablename__ = "distance_imports"
    __table_args__ = (UniqueConstraint("project_id", "content_hash"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        String, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String)
    manifest: Mapped[dict] = mapped_column(JSON)
    payload: Mapped[dict] = mapped_column(JSON)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ReferenceVideo(Base):
    __tablename__ = "reference_videos"
    __table_args__ = (
        UniqueConstraint("project_id", "video_path", name="uq_reference_video_path_project"),
        CheckConstraint(
            "(deployment_id IS NULL OR unresolved_camera_label IS NULL) AND "
            "(unresolved_camera_label IS NULL OR length(trim(unresolved_camera_label)) > 0)",
            name="ck_reference_camera",
        ),
        ForeignKeyConstraint(
            ("project_id", "deployment_id"), ("deployments.project_id", "deployments.id")
        ),
        CheckConstraint("length(trim(video_path)) > 0", name="ck_reference_path"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String, ForeignKey("projects.id", ondelete="CASCADE"))
    unresolved_camera_label: Mapped[str | None] = mapped_column(String)
    deployment_id: Mapped[str | None] = mapped_column(String, index=True)
    video_path: Mapped[str] = mapped_column(String)

    deployment: Mapped[Deployment | None] = relationship(viewonly=True, lazy="joined")

    @hybrid_property
    def camera_id(self) -> str | None:
        return _deployment_label(self) if self.deployment_id else self.unresolved_camera_label

    @camera_id.inplace.expression
    @classmethod
    def _camera_id_expression(cls):
        return func.coalesce(_deployment_label_sql(cls), cls.unresolved_camera_label)


class ReferenceFrame(Base):
    __tablename__ = "reference_frames"
    __table_args__ = (
        UniqueConstraint(
            "reference_video_id", "distance_m", "offset_ms", name="uq_reference_frame_position"
        ),
        CheckConstraint("offset_ms >= 0", name="ck_reference_frame_offset"),
        CheckConstraint("playback_offset_ms >= 0", name="ck_reference_frame_playback"),
        CheckConstraint("distance_m > 0", name="ck_reference_frame_distance"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    reference_video_id: Mapped[str] = mapped_column(
        String, ForeignKey("reference_videos.id", ondelete="CASCADE")
    )
    offset_ms: Mapped[int] = mapped_column(Integer)
    # Keep the precise seek position: rounding can land in the preceding frame.
    playback_offset_ms: Mapped[float | None] = mapped_column(Float)
    distance_m: Mapped[float] = mapped_column(Float)
    mask: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    import_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("distance_imports.id", ondelete="SET NULL")
    )
    retired: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class DistanceIndividual(Base):
    __tablename__ = "distance_individuals"
    __table_args__ = (
        UniqueConstraint("task_id", "individual_number", name="uq_distance_individual_number"),
        CheckConstraint("individual_number > 0", name="ck_distance_individual_number"),
        CheckConstraint("first_seen_offset_ms >= 0", name="ck_distance_first_seen"),
        CheckConstraint(
            "age IS NULL OR age IN ('Adult', 'Adolescent', 'Juvenile', 'Infant', 'Unknown')",
            name="ck_distance_age",
        ),
        CheckConstraint(
            "sex IS NULL OR sex IN ('Male', 'Female', 'Unknown')", name="ck_distance_sex"
        ),
        CheckConstraint(
            "(anchor_x IS NULL AND anchor_y IS NULL "
            "AND anchor_width IS NULL AND anchor_height IS NULL) OR "
            "(anchor_x IS NOT NULL AND anchor_y IS NOT NULL "
            "AND anchor_x BETWEEN 0 AND 1 AND anchor_y BETWEEN 0 AND 1 AND "
            "anchor_width IS NOT NULL AND anchor_height IS NOT NULL "
            "AND anchor_width > 0 AND anchor_height > 0 "
            "AND anchor_x + anchor_width <= 1 AND anchor_y + anchor_height <= 1)",
            name="ck_distance_anchor",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    import_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("distance_imports.id", ondelete="SET NULL")
    )
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("distance_review_tasks.id", ondelete="CASCADE"), index=True
    )
    ai_original: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    individual_number: Mapped[int] = mapped_column(Integer)
    age: Mapped[str | None] = mapped_column(String)
    sex: Mapped[str | None] = mapped_column(String)
    first_seen_offset_ms: Mapped[int] = mapped_column(Integer)
    first_seen_playback_ms: Mapped[float | None] = mapped_column(Float)
    # Creation-time default only; actual carrying remains sample-specific.
    initial_carrier_individual_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("distance_individuals.id", ondelete="SET NULL")
    )
    anchor_x: Mapped[float | None] = mapped_column(Float)
    anchor_y: Mapped[float | None] = mapped_column(Float)
    anchor_width: Mapped[float | None] = mapped_column(Float)
    anchor_height: Mapped[float | None] = mapped_column(Float)
    labeled_by: Mapped[str | None] = mapped_column(String)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    review_status: Mapped[str] = mapped_column(
        String, default="not_started", server_default="not_started"
    )
    completed_by: Mapped[str | None] = mapped_column(String)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    task: Mapped[DistanceReviewTask] = relationship()


class DistanceReviewTask(Base):
    __tablename__ = "distance_review_tasks"
    __table_args__ = (UniqueConstraint("video_id", "species_id", name="uq_distance_task"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    video_id: Mapped[str] = mapped_column(
        String, ForeignKey("videos.video_id", ondelete="CASCADE"), index=True
    )
    species_id: Mapped[str] = mapped_column(String, ForeignKey("species.id"))
    source_count: Mapped[int] = mapped_column(Integer)
    completion_outcome: Mapped[str | None] = mapped_column(String)
    completion_history: Mapped[list[dict]] = mapped_column(JSON, default=list)
    rejected_ai: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Camera wall-clock time, deliberately independent of file creation metadata.
    recorded_start: Mapped[datetime | None] = mapped_column(DateTime)
    alignment_mode: Mapped[str] = mapped_column(String, default="exact", server_default="exact")
    imported_sample_times_s: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    import_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("distance_imports.id", ondelete="SET NULL")
    )
    # Sticky once human work starts, even if that work is subsequently deleted.
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    clock_anchor_offset_ms: Mapped[int | None] = mapped_column(Integer)
    clock_anchor_playback_ms: Mapped[float | None] = mapped_column(Float)
    opening_even_second: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    verified_by: Mapped[str | None] = mapped_column(String)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_by: Mapped[str | None] = mapped_column(String)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class DistanceReviewProgress(Base):
    __tablename__ = "distance_review_progress"

    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("distance_review_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    annotator_name: Mapped[str] = mapped_column(String, primary_key=True)
    active_individual_id: Mapped[str] = mapped_column(
        String, ForeignKey("distance_individuals.id", ondelete="CASCADE")
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class DistanceSample(Base):
    __tablename__ = "distance_samples"
    __table_args__ = (
        UniqueConstraint("individual_id", "offset_ms", name="uq_distance_sample_offset"),
        CheckConstraint("offset_ms >= 0", name="ck_distance_sample_offset"),
        CheckConstraint(
            "(status = 'pending' AND reference_frame_id IS NULL AND distance_m IS NULL "
            "AND skip_reason IS NULL AND labeled_at IS NULL AND labeled_by IS NULL "
            "AND carried_by_individual_id IS NULL) OR "
            "(status = 'annotated' AND skip_reason IS NULL AND distance_m IS NOT NULL AND "
            "((distance_m = 0 AND reference_frame_id IS NULL) OR "
            "(distance_m > 0 AND reference_frame_id IS NOT NULL))) OR "
            "(status = 'skipped' AND skip_reason IS NOT NULL "
            "AND skip_reason IN ('individual absent', 'midpoint not visible') "
            "AND reference_frame_id IS NULL AND distance_m IS NULL)",
            name="ck_distance_sample_state",
        ),
        CheckConstraint(
            "carried_by_individual_id IS NULL OR carried_by_individual_id != individual_id",
            name="ck_distance_carrier",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    individual_id: Mapped[str] = mapped_column(
        String, ForeignKey("distance_individuals.id", ondelete="CASCADE")
    )
    offset_ms: Mapped[int] = mapped_column(Integer)
    ai_original: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String)
    skip_reason: Mapped[str | None] = mapped_column(String)
    # Retired frames keep their identity for existing measurements.
    reference_frame_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("reference_frames.id")
    )
    distance_m: Mapped[float | None] = mapped_column(Float)
    carried_by_individual_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("distance_individuals.id")
    )
    labeled_by: Mapped[str | None] = mapped_column(String)
    labeled_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class DistanceIndividualTag(Base):
    __tablename__ = "distance_individual_tags"

    individual_id: Mapped[str] = mapped_column(
        String, ForeignKey("distance_individuals.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    tagged_by: Mapped[str | None] = mapped_column(String)
    tagged_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class DistanceSampleBehavior(Base):
    __tablename__ = "distance_sample_behaviors"

    sample_id: Mapped[str] = mapped_column(
        String, ForeignKey("distance_samples.id", ondelete="CASCADE"), primary_key=True
    )
    behavior_id: Mapped[str] = mapped_column(String, ForeignKey("behaviors.id"), primary_key=True)


Index(
    "idx_individual_video_species",
    IndividualObservation.video_id,
    IndividualObservation.species_id,
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
Index("idx_observation_tags_video", ObservationTag.video_id, ObservationTag.behavior_id)


# Raw provider SQL aliases the video as v when displaying its effective label.
VIDEO_CAMERA_LABEL_SQL = (
    "CASE WHEN v.project_id IS NULL THEN v.folder_camera_label ELSE "
    "(SELECT d.deployment_id FROM deployments d WHERE d.id = v.deployment_id) END"
)
