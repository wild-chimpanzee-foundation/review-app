"""Shared vocabulary fallback for visible Distance samples."""

from __future__ import annotations

import uuid

from sqlalchemy import text


def ensure_unidentified_behavior(conn) -> str:
    """Reuse an existing vocabulary ID, or create the built-in fallback atomically."""
    conn.execute(
        text("""
            INSERT INTO behaviors (id, key, name_en, name_fr, is_custom)
            VALUES (:id, 'unidentified', 'Unidentified', 'Non identifié', 0)
            ON CONFLICT(key) DO NOTHING
        """),
        {"id": str(uuid.uuid4())},
    )
    return conn.execute(text("SELECT id FROM behaviors WHERE key = 'unidentified'")).scalar_one()
