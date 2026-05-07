from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


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

    # ── Cross-mixin stubs ─────────────────────────────────────────────────────
    # Declared here so mixins that call methods from other mixins satisfy pyright.
    # Each stub is overridden by the mixin that actually implements it.

    def get_valid_species(self, project_id: str | None) -> list[str]:
        raise NotImplementedError

    def _validate_species_fuzzy(
        self, value_text: str, variant_map: dict[str, str] | None
    ) -> tuple[bool, str | None]:
        raise NotImplementedError

    def _build_species_variant_map(self) -> dict[str, str]:
        raise NotImplementedError

    def update_manual_review(
        self,
        video_id: str,
        selections: list[dict] | None,
        is_blank: bool | None = None,
        labeled_by: str | None = None,
        active_project_id: str | None = None,
        append: bool = False,
    ) -> None:
        raise NotImplementedError
