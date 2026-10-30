from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from review_app.backend.provider.base import ProviderBase
from review_app.backend.utils import bind_id_list

logger = logging.getLogger(__name__)


class TagMixin(ProviderBase):
    """Tag management. Requires self.engine."""

    def _ensure_reference_video_tag(self) -> None:
        """Ensure built-ins and individual-tag eligibility on fresh and upgraded databases."""
        with self.engine.begin() as conn:
            for key, name_en, name_fr, color, icon in (
                ("reference_video", "Reference Video", "Vidéo de référence", "indigo", "bookmark"),
                ("injured", "Injured", "Animal blessé", "red", "healing"),
                ("disease", "Disease", "Animal malade", "deep-orange", "sick"),
            ):
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO tags "
                        "(id, key, name_en, name_fr, color, icon, is_custom) "
                        "VALUES (:id, :key, :name_en, :name_fr, :color, :icon, 0)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "key": key,
                        "name_en": name_en,
                        "name_fr": name_fr,
                        "color": color,
                        "icon": icon,
                    },
                )
            conn.execute(
                text(
                    "UPDATE tags SET individual_eligible = 1 "
                    "WHERE is_custom = 0 AND key IN ('injured', 'disease')"
                )
            )
            # These legacy custom tags describe video/import metadata, not animals.
            # Older databases used to make every existing custom tag eligible.
            conn.execute(
                text(
                    "UPDATE tags SET individual_eligible = 0 "
                    "WHERE lower(trim(key)) IN ('comments', 'import_tags')"
                )
            )

    def get_all_tags(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, key, name_en, name_fr, color, icon, is_custom, "
                    "individual_eligible FROM tags ORDER BY is_custom, key"
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
                "individual_eligible": bool(r[7]),
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
                    "INSERT INTO tags (id, key, name_en, name_fr, color, is_custom, individual_eligible) "
                    "VALUES (:id, :key, :name_en, :name_fr, :color, 1, 0)"
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

    def set_tag_individual_eligibility(self, key: str, eligible: bool) -> None:
        """Manage whether a custom tag is available for new individual assignments."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE tags SET individual_eligible = :eligible "
                    "WHERE key = :key AND is_custom = 1"
                ),
                {"eligible": bool(eligible), "key": key},
            )
            if not result.rowcount:
                raise ValueError("Custom tag not found")

    def set_video_tags(self, video_id: str, tag_keys: list[str], append: bool = False) -> None:
        """Apply tag_keys to video_id.

        override mode (append=False): clears all existing tags for the video first.
        append mode: adds only tags not already present; never removes existing tags.
        Unknown keys (not in DB) are skipped with a warning.
        """
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self.engine.begin() as conn:
            known = {r[0]: r[1] for r in conn.execute(text("SELECT key, id FROM tags")).fetchall()}
            valid_ids = []
            for key in tag_keys:
                if key in known:
                    valid_ids.append(known[key])
                else:
                    logger.warning("set_video_tags: unknown tag key %r — skipped", key)

            if not append:
                conn.execute(
                    text("DELETE FROM video_tags WHERE video_id = :vid"), {"vid": video_id}
                )
                for tag_id in valid_ids:
                    conn.execute(
                        text(
                            "INSERT INTO video_tags (video_id, tag_id, tagged_by, tagged_at) "
                            "VALUES (:vid, :tid, NULL, :at)"
                        ),
                        {"vid": video_id, "tid": tag_id, "at": now},
                    )
            else:
                existing = {
                    r[0]
                    for r in conn.execute(
                        text("SELECT tag_id FROM video_tags WHERE video_id = :vid"),
                        {"vid": video_id},
                    ).fetchall()
                }
                for tag_id in valid_ids:
                    if tag_id not in existing:
                        conn.execute(
                            text(
                                "INSERT INTO video_tags (video_id, tag_id, tagged_by, tagged_at) "
                                "VALUES (:vid, :tid, NULL, :at)"
                            ),
                            {"vid": video_id, "tid": tag_id, "at": now},
                        )

    def set_video_tags_bulk(
        self, conn, tags_by_video: dict[str, list[str]], append: bool = False
    ) -> None:
        """Batch set_video_tags inside the caller's transaction — same per-video semantics.

        override mode (append=False): clears each listed video's tags first.
        append mode: adds only tags not already present; never removes existing tags.
        Unknown keys (not in DB) are skipped with a warning. Videos not listed in
        tags_by_video are never touched.
        """
        if not tags_by_video:
            return
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        known = {r[0]: r[1] for r in conn.execute(text("SELECT key, id FROM tags")).fetchall()}
        valid_by_video: dict[str, list[str]] = {}
        for video_id, keys in tags_by_video.items():
            valid_ids = []
            for key in keys:
                if key in known:
                    valid_ids.append(known[key])
                else:
                    logger.warning("set_video_tags: unknown tag key %r — skipped", key)
            valid_by_video[video_id] = valid_ids

        params: dict = {}
        id_list = bind_id_list(params, "vids", list(valid_by_video))
        if not append:
            conn.execute(text(f"DELETE FROM video_tags WHERE video_id IN {id_list}"), params)
            rows = [
                {"vid": vid, "tid": tid, "at": now}
                for vid, tag_ids in valid_by_video.items()
                for tid in tag_ids
            ]
        else:
            existing = {
                (vid, tid)
                for vid, tid in conn.execute(
                    text(f"SELECT video_id, tag_id FROM video_tags WHERE video_id IN {id_list}"),
                    params,
                )
            }
            rows = [
                {"vid": vid, "tid": tid, "at": now}
                for vid, tag_ids in valid_by_video.items()
                for tid in tag_ids
                if (vid, tid) not in existing
            ]
        if rows:
            conn.execute(
                text(
                    "INSERT INTO video_tags (video_id, tag_id, tagged_by, tagged_at) "
                    "VALUES (:vid, :tid, NULL, :at)"
                ),
                rows,
            )

    def export_tags_csv(self) -> str:
        """Export custom tags, including individual-vocabulary eligibility."""
        import csv
        import io

        tags = [t for t in self.get_all_tags() if t["is_custom"]]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["key", "name_en", "name_fr", "color", "icon", "individual_eligible"])
        for tag in tags:
            writer.writerow(
                [
                    tag.get("key") or "",
                    tag.get("name_en") or "",
                    tag.get("name_fr") or "",
                    tag.get("color") or "",
                    tag.get("icon") or "",
                    "true" if tag["individual_eligible"] else "false",
                ]
            )
        return buf.getvalue()

    def import_tags_from_csv(self, content: str) -> int:
        """Import custom tags from a CSV string. Idempotent upsert by key.
        Returns the number of tags imported."""
        import csv
        import io

        reader = csv.DictReader(io.StringIO(content))
        has_eligibility_column = "individual_eligible" in (reader.fieldnames or [])
        count = 0
        for row in reader:
            key = (row.get("key") or "").strip()
            name_en = (row.get("name_en") or "").strip()
            name_fr = (row.get("name_fr") or "").strip() or None
            color = (row.get("color") or "").strip() or None
            icon = (row.get("icon") or "").strip() or None
            eligibility_value = (row.get("individual_eligible") or "").strip().lower()
            individual_eligible = None
            if has_eligibility_column and eligibility_value:
                if eligibility_value not in {"true", "false", "1", "0"}:
                    raise ValueError(
                        f"Invalid individual_eligible value on CSV row {reader.line_num}: "
                        f"{eligibility_value!r} (expected true/false or 1/0)"
                    )
                individual_eligible = eligibility_value in {"true", "1"}
            if not key or not name_en:
                continue
            with self.engine.begin() as conn:
                existing = conn.execute(
                    text("SELECT id, is_custom FROM tags WHERE key = :k"), {"k": key}
                ).fetchone()
                if existing is None:
                    conn.execute(
                        text(
                            "INSERT INTO tags (id, key, name_en, name_fr, color, icon, is_custom, "
                            "individual_eligible) VALUES (:id, :key, :name_en, :name_fr, :color, "
                            ":icon, 1, :individual_eligible)"
                        ),
                        {
                            "id": str(uuid.uuid4()),
                            "key": key,
                            "name_en": name_en,
                            "name_fr": name_fr,
                            "color": color,
                            "icon": icon,
                            "individual_eligible": bool(individual_eligible),
                        },
                    )
                else:
                    eligibility_sql = (
                        ", individual_eligible = :individual_eligible"
                        if individual_eligible is not None
                        else ""
                    )
                    conn.execute(
                        text(
                            "UPDATE tags SET name_en = :name_en, name_fr = :name_fr, "
                            "color = :color, icon = :icon"
                            f"{eligibility_sql} WHERE key = :key AND is_custom = 1"
                        ),
                        {
                            "key": key,
                            "name_en": name_en,
                            "name_fr": name_fr,
                            "color": color,
                            "icon": icon,
                            "individual_eligible": individual_eligible,
                        },
                    )
            count += 1
        return count

    def delete_custom_tag(self, key: str) -> None:
        with self.engine.begin() as conn:
            tag_row = conn.execute(
                text("SELECT id FROM tags WHERE key = :k AND is_custom = 1"), {"k": key}
            ).fetchone()
            if tag_row is None:
                return
            # Removing an individual attribute is a Distance review edit. Reopen
            # explicit video completion in the same transaction as the deletion.
            conn.execute(
                text(
                    "UPDATE distance_review_tasks SET completed_by = NULL, completed_at = NULL, "
                    "completion_outcome = NULL WHERE id IN ("
                    "SELECT di.task_id FROM distance_individual_tags dit "
                    "JOIN distance_individuals di ON di.id = dit.individual_id "
                    "WHERE dit.tag_id = :tid)"
                ),
                {"tid": tag_row[0]},
            )
            conn.execute(text("DELETE FROM video_tags WHERE tag_id = :tid"), {"tid": tag_row[0]})
            conn.execute(text("DELETE FROM tags WHERE id = :tid"), {"tid": tag_row[0]})
        logger.info("Deleted custom tag: key=%s", key)
