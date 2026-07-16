from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from review_app.backend.db.models import (
    IndividualObservation,
    ModelAnnotation,
    ObservationTag,
    Video,
    VideoAssignment,
    VideoLabel,
    VideoTag,
)

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
    def _cascade_delete_videos(session: Session, video_filter) -> None:
        """Delete every Video matching *video_filter* and all rows referencing them.

        Children are removed before the videos themselves so the FK-enforced delete
        cannot fail. Use this for any video deletion — ORM cascade misses
        observation_tags, and bulk ``.delete()`` triggers no cascade at all.
        """
        v_sub = session.query(Video.video_id).filter(video_filter).scalar_subquery()
        for model in _VIDEO_CHILD_MODELS:
            session.query(model).filter(model.video_id.in_(v_sub)).delete(
                synchronize_session=False
            )
        session.query(Video).filter(video_filter).delete(synchronize_session=False)

    # ── Cross-mixin stubs ─────────────────────────────────────────────────────
    # Declared here so mixins that call methods from other mixins satisfy pyright.
    # Each stub is overridden by the mixin that actually implements it.

    def get_valid_species(self, project_id: str | None) -> list[str]: ...

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

    def add_annotators_bulk(self, conn, names) -> None: ...

    def set_assignments_bulk(self, conn, assignments: dict[str, str]) -> None: ...

    def set_video_tags_bulk(
        self, conn, tags_by_video: dict[str, list[str]], append: bool = False
    ) -> None: ...
