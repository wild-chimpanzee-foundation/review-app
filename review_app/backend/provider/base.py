from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceReviewTask,
    DistanceSample,
    IndividualObservation,
    ModelAnnotation,
    ObservationTag,
    Video,
    VideoAssignment,
    VideoLabel,
    VideoTag,
)
from review_app.backend.errors import VideoError
from review_app.backend.utils import bind_id_list

# Tables that reference videos.video_id. The schema has no ON DELETE CASCADE, so these
# must be cleared before the Video rows or SQLite (foreign_keys=ON) raises IntegrityError.
# observation_tags in particular is not covered by any ORM relationship cascade.
_VIDEO_CHILD_MODELS = (
    ObservationTag,
    VideoLabel,
    VideoTag,
    VideoAssignment,
    IndividualObservation,
    ModelAnnotation,
)


class ProviderBase:
    """Shared attribute declarations for the mixin composition that forms LocalDataProvider.

    Each mixin inherits from this so pyright knows engine, Session, and _utcnow_dt are
    available at runtime (provided by LocalDataProvider.__init__). Cross-mixin method stubs
    are declared here and implemented by the respective mixin.
    """

    engine: Engine
    Session: sessionmaker[Session]
    _consensus_min_probability: float

    @staticmethod
    def _utcnow_dt() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _validate_video_scope(
        conn: Connection, video_ids: list[str], active_project_id: str | None
    ) -> None:
        if not video_ids:
            return
        params: dict[str, Any] = {}
        id_list = bind_id_list(params, "vids", video_ids)
        projects = {
            video_id: project_id
            for video_id, project_id in conn.execute(
                text(f"SELECT video_id, project_id FROM videos WHERE video_id IN {id_list}"),
                params,
            )
        }
        for video_id in video_ids:
            if video_id not in projects:
                raise VideoError(f"Video {video_id!r} does not exist")
            if active_project_id is not None and projects[video_id] != active_project_id:
                raise VideoError(
                    f"Video {video_id!r} does not belong to project {active_project_id!r}"
                )

    @staticmethod
    def _cascade_delete_videos(session: Session, video_filter) -> None:
        """Delete every Video matching *video_filter* and all rows referencing them.

        Children are removed before the videos themselves so the FK-enforced delete
        cannot fail. Use this for any video deletion — ORM cascade misses
        observation_tags, and bulk ``.delete()`` triggers no cascade at all.
        """
        v_sub = session.query(Video.video_id).filter(video_filter).scalar_subquery()
        tasks = select(DistanceReviewTask.id).where(DistanceReviewTask.video_id.in_(v_sub))
        individuals = (
            session.query(DistanceIndividual.id)
            .filter(DistanceIndividual.task_id.in_(tasks))
            .scalar_subquery()
        )
        # Delete all samples first: an infant's sample can reference another individual.
        session.query(DistanceSample).filter(DistanceSample.individual_id.in_(individuals)).delete(
            synchronize_session=False
        )
        session.query(DistanceIndividual).filter(DistanceIndividual.task_id.in_(tasks)).delete(
            synchronize_session=False
        )
        for model in _VIDEO_CHILD_MODELS:
            session.query(model).filter(model.video_id.in_(v_sub)).delete(
                synchronize_session=False
            )
        session.query(Video).filter(video_filter).delete(synchronize_session=False)

    def _reconcile_distance_video_completion(self, session: Session, video_id: str) -> None: ...

    # ── Cross-mixin stubs ─────────────────────────────────────────────────────
    # Declared here so mixins that call methods from other mixins satisfy pyright.
    # Each stub is overridden by the mixin that actually implements it.

    def get_valid_species(self, project_id: str | None) -> list[str]: ...

    @staticmethod
    def _get_valid_species(conn: Connection, project_id: str | None) -> list[str]: ...

    @staticmethod
    def _add_custom_species(
        conn: Connection,
        scientific_name: str,
        name_en: str,
        name_fr: str,
        group_en: str,
        group_fr: str,
        iucn: str | None = None,
    ) -> bool: ...

    @staticmethod
    def _insert_custom_tag(
        conn: Connection,
        key: str,
        name_en: str,
        color: str | None = None,
        name_fr: str | None = None,
    ) -> None: ...

    def get_species_catalog(self, lang: str = "en", project_id: str | None = None): ...

    def _validate_species_fuzzy(
        self, value_text: str, variant_map: dict[str, str] | None
    ) -> tuple[bool, str | None]: ...

    def _build_species_variant_map(self) -> dict[str, str]: ...

    def update_manual_review(
        self,
        video_id: str,
        selections: list[dict] | None,
        is_blank: bool | None = None,
        labeled_by: str | None = None,
        active_project_id: str | None = None,
        append: bool = False,
    ) -> None: ...

    def apply_manual_reviews(
        self,
        reviews: list[dict],
        active_project_id: str | None = None,
        append: bool = False,
        review_later: dict[str, bool] | None = None,
        assignments: dict[str, str] | None = None,
        video_tags: dict[str, list[str]] | None = None,
    ) -> None: ...

    def _apply_manual_reviews(
        self,
        conn: Connection,
        reviews: list[dict],
        active_project_id: str | None,
        append: bool,
        review_later: dict[str, bool],
        assignments: dict[str, str],
        video_tags: dict[str, list[str]],
    ) -> None: ...

    def add_annotators_bulk(self, conn, names) -> None: ...

    def set_assignments_bulk(self, conn, assignments: dict[str, str]) -> None: ...

    def set_video_tags_bulk(
        self, conn, tags_by_video: dict[str, list[str]], append: bool = False
    ) -> None: ...
