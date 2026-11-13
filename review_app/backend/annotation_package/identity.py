"""Session-owned, bidirectional portable identity mapping."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from review_app.backend.db.models import HandoffAssignment, HandoffIdentityMap


class IdentityMap:
    def __init__(self, session: Session, handoff_id: str):
        if session.get(HandoffAssignment, handoff_id) is None:
            raise ValueError("Assignment not found")
        self.session = session
        self.handoff_id = handoff_id

    def bind(self, kind: str, portable_id: str, local_id: str) -> str:
        """Bind once; an identical binding is a no-op, never silently remap."""
        for value in (kind, portable_id, local_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Identity kind and IDs must be nonempty strings")
        existing = self.session.get(HandoffIdentityMap, (self.handoff_id, kind, portable_id))
        reverse = (
            self.session.query(HandoffIdentityMap)
            .filter_by(handoff_id=self.handoff_id, kind=kind, local_id=local_id)
            .one_or_none()
        )
        if (existing is not None and existing.local_id != local_id) or (
            reverse is not None and reverse.portable_id != portable_id
        ):
            raise ValueError("Assignment identity is already mapped to another entity")
        if existing is None:
            self.session.add(
                HandoffIdentityMap(
                    handoff_id=self.handoff_id,
                    kind=kind,
                    portable_id=portable_id,
                    local_id=local_id,
                )
            )
            self.session.flush()
        return local_id

    def local(self, kind: str, portable_id: str) -> str:
        """Resolve a portable ID, allocating a fresh local UUID if not mapped yet."""
        existing = self.session.get(HandoffIdentityMap, (self.handoff_id, kind, portable_id))
        return existing.local_id if existing else self.bind(kind, portable_id, str(uuid.uuid4()))

    def portable(self, kind: str, local_id: str) -> str:
        """Give new local work a stable portable UUID for subsequent returns."""
        existing = (
            self.session.query(HandoffIdentityMap)
            .filter_by(handoff_id=self.handoff_id, kind=kind, local_id=local_id)
            .one_or_none()
        )
        if existing is not None:
            return existing.portable_id
        portable_id = str(uuid.uuid4())
        self.bind(kind, portable_id, local_id)
        return portable_id
