from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)


class TagMixin(ProviderBase):
    """Tag management. Requires self.engine."""

    def get_all_tags(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, key, name_en, name_fr, color, icon, is_custom FROM tags ORDER BY is_custom, key"
                )
            ).fetchall()
        return [
            {
                "id": r[0],
                "key": r[1],
                "name_en": r[2],
                "name_fr": r[3],
                "color": r[4],
                "icon": r[5],
                "is_custom": bool(r[6]),
            }
            for r in rows
        ]

    def get_video_tags(self, video_id: str) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT t.key FROM video_tags vt "
                    "JOIN tags t ON t.id = vt.tag_id "
                    "WHERE vt.video_id = :vid"
                ),
                {"vid": video_id},
            ).fetchall()
        return [r[0] for r in rows]

    def toggle_video_tag(self, video_id: str, tag_key: str, tagged_by: str | None = None) -> bool:
        """Toggle a tag on/off for a video. Returns True if the tag is now active."""
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self.engine.begin() as conn:
            tag_row = conn.execute(
                text("SELECT id FROM tags WHERE key = :k"), {"k": tag_key}
            ).fetchone()
            if tag_row is None:
                raise ValueError(f"Unknown tag key: {tag_key!r}")
            tag_id = tag_row[0]
            existing = conn.execute(
                text("SELECT 1 FROM video_tags WHERE video_id = :vid AND tag_id = :tid"),
                {"vid": video_id, "tid": tag_id},
            ).fetchone()
            if existing:
                conn.execute(
                    text("DELETE FROM video_tags WHERE video_id = :vid AND tag_id = :tid"),
                    {"vid": video_id, "tid": tag_id},
                )
                logger.debug("Removed tag %s from video %s", tag_key, video_id)
                return False
            else:
                conn.execute(
                    text(
                        "INSERT INTO video_tags (video_id, tag_id, tagged_by, tagged_at) "
                        "VALUES (:vid, :tid, :by, :at)"
                    ),
                    {"vid": video_id, "tid": tag_id, "by": tagged_by, "at": now},
                )
                logger.debug("Added tag %s to video %s", tag_key, video_id)
                return True

    def create_custom_tag(
        self, name_en: str = "", color: str | None = None, name_fr: str | None = None
    ) -> str:
        """Create a custom tag if it doesn't exist yet. Returns the tag key."""
        key_source = name_en.strip() or (name_fr.strip() if name_fr else "")
        key = re.sub(r"[^a-z0-9_]", "", key_source.lower().replace(" ", "_").replace("-", "_"))
        if not key:
            raise ValueError(f"Cannot derive a valid key from tag names: {name_en!r}, {name_fr!r}")
        name_fr_val = name_fr.strip() if name_fr and name_fr.strip() else None
        with self.engine.begin() as conn:
            existing = conn.execute(
                text("SELECT key FROM tags WHERE key = :k"), {"k": key}
            ).fetchone()
            if existing:
                return key
            conn.execute(
                text(
                    "INSERT INTO tags (id, key, name_en, name_fr, color, is_custom) "
                    "VALUES (:id, :key, :name_en, :name_fr, :color, 1)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "key": key,
                    "name_en": name_en.strip() or None,
                    "name_fr": name_fr_val,
                    "color": color,
                },
            )
        logger.info("Created custom tag: key=%s name=%s color=%s", key, name_en.strip(), color)
        return key

    def update_tag_names(self, key: str, name_en: str, name_fr: str | None = None) -> None:
        name_fr_val = name_fr.strip() if name_fr and name_fr.strip() else None
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tags SET name_en = :name_en, name_fr = :name_fr "
                    "WHERE key = :key AND is_custom = 1"
                ),
                {"name_en": name_en.strip(), "name_fr": name_fr_val, "key": key},
            )

    def update_tag_color(self, key: str, color: str | None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE tags SET color = :color WHERE key = :key AND is_custom = 1"),
                {"color": color, "key": key},
            )

    def delete_custom_tag(self, key: str) -> None:
        with self.engine.begin() as conn:
            tag_row = conn.execute(
                text("SELECT id FROM tags WHERE key = :k AND is_custom = 1"), {"k": key}
            ).fetchone()
            if tag_row is None:
                return
            conn.execute(text("DELETE FROM video_tags WHERE tag_id = :tid"), {"tid": tag_row[0]})
            conn.execute(text("DELETE FROM tags WHERE id = :tid"), {"tid": tag_row[0]})
        logger.info("Deleted custom tag: key=%s", key)

    def auto_apply_broken_metadata_tags(self, project_id: str | None) -> int:
        """Apply the broken_metadata tag to all videos with validation_error IS NOT NULL.

        Only adds the tag where it's missing; never removes it. Returns the number of rows inserted.
        """
        with self.engine.begin() as conn:
            tag_row = conn.execute(
                text("SELECT id FROM tags WHERE key = 'broken_metadata'")
            ).fetchone()
            if tag_row is None:
                return 0
            tag_id = tag_row[0]
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            pid_filter = "AND v.project_id = :pid" if project_id else ""
            result = conn.execute(
                text(f"""
                    INSERT INTO video_tags (video_id, tag_id, tagged_by, tagged_at)
                    SELECT v.video_id, :tag_id, NULL, :now
                    FROM videos v
                    WHERE v.validation_error IS NOT NULL
                      {pid_filter}
                      AND NOT EXISTS (
                          SELECT 1 FROM video_tags vt
                          WHERE vt.video_id = v.video_id AND vt.tag_id = :tag_id
                      )
                """),
                {"tag_id": tag_id, "now": now, "pid": project_id}
                if project_id
                else {"tag_id": tag_id, "now": now},
            )
        count = result.rowcount
        if count:
            logger.info("Auto-applied broken_metadata tag to %d video(s)", count)
        return count
