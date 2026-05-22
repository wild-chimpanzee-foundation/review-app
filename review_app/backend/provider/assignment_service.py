from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)


class AssignmentMixin(ProviderBase):
    """Work distribution: annotator registry and camera-based video assignment."""

    # ── Annotator registry ────────────────────────────────────────────────────

    def get_all_annotators(self) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT name FROM annotators ORDER BY name")).fetchall()
        return [r[0] for r in rows]

    def add_annotator(self, name: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO annotators (name, created_at) VALUES (:name, :now) "
                    "ON CONFLICT(name) DO NOTHING"
                ),
                {"name": name, "now": now},
            )

    def remove_annotator(self, name: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM video_assignments WHERE assigned_to = :name"), {"name": name}
            )
            conn.execute(text("DELETE FROM annotators WHERE name = :name"), {"name": name})

    # ── Camera stats ──────────────────────────────────────────────────────────

    def get_camera_stats(self, project_id: str) -> list[dict]:
        """Return per-camera video count and total hours for the project."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT
                        camera_id,
                        COUNT(*) AS video_count,
                        COALESCE(SUM(duration_sec), 0) / 3600.0 AS hours
                    FROM videos
                    WHERE project_id = :pid AND camera_id IS NOT NULL
                    GROUP BY camera_id
                    ORDER BY video_count DESC
                """),
                {"pid": project_id},
            ).fetchall()
        return [{"camera_id": r[0], "video_count": r[1], "hours": round(r[2], 2)} for r in rows]

    # ── Distribution algorithm ────────────────────────────────────────────────

    def auto_distribute(self, project_id: str, annotator_names: list[str]) -> dict[str, list[str]]:
        """Greedily assign cameras to annotators to balance total hours.

        Sorts cameras by video_count DESC, then assigns each to the annotator
        with the fewest hours so far (standard list-scheduling heuristic).
        Returns {annotator_name: [camera_id, ...]}.
        """
        if not annotator_names:
            return {}
        cameras = self.get_camera_stats(project_id)
        loads: dict[str, float] = {n: 0.0 for n in annotator_names}
        assignment: dict[str, list[str]] = {n: [] for n in annotator_names}
        for cam in cameras:
            least_loaded = min(loads, key=lambda n: loads[n])
            assignment[least_loaded].append(cam["camera_id"])
            loads[least_loaded] += cam["hours"]
        return assignment

    def apply_distribution(self, project_id: str, assignment: dict[str, list[str]]) -> int:
        """Write VideoAssignment rows for the given camera→annotator mapping.

        Replaces any existing assignments for cameras mentioned in the mapping.
        Returns the total number of video rows assigned.
        """
        now = datetime.now(timezone.utc).isoformat()
        total = 0
        all_cameras = [cam for cams in assignment.values() for cam in cams]
        with self.engine.begin() as conn:
            if all_cameras:
                placeholders = ",".join(f":c{i}" for i in range(len(all_cameras)))
                params = {f"c{i}": c for i, c in enumerate(all_cameras)}
                params["pid"] = project_id
                conn.execute(
                    text(f"""
                        DELETE FROM video_assignments
                        WHERE video_id IN (
                            SELECT video_id FROM videos
                            WHERE project_id = :pid AND camera_id IN ({placeholders})
                        )
                    """),
                    params,
                )
            for annotator_name, camera_ids in assignment.items():
                if not camera_ids:
                    continue
                placeholders = ",".join(f":c{i}" for i in range(len(camera_ids)))
                params = {f"c{i}": c for i, c in enumerate(camera_ids)}
                params["pid"] = project_id
                params["annotator"] = annotator_name
                params["now"] = now
                rows = conn.execute(
                    text(f"""
                        INSERT OR REPLACE INTO video_assignments (video_id, assigned_to, assigned_at)
                        SELECT video_id, :annotator, :now
                        FROM videos
                        WHERE project_id = :pid AND camera_id IN ({placeholders})
                    """),
                    params,
                )
                total += rows.rowcount
        return total

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_assignment_summary(self, project_id: str) -> list[dict]:
        """Return per-annotator summary: cameras, video_count, hours, labeling stats."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT
                        va.assigned_to,
                        COUNT(DISTINCT v.camera_id) AS cameras,
                        COUNT(*) AS video_count,
                        ROUND(COALESCE(SUM(v.duration_sec), 0) / 3600.0, 2) AS hours,
                        COUNT(DISTINCT CASE WHEN vl.video_id IS NOT NULL THEN v.video_id END) AS labeled,
                        COUNT(DISTINCT CASE WHEN vl.is_blank = 1 THEN v.video_id END) AS blank,
                        COUNT(DISTINCT CASE WHEN vl.is_blank = 0 THEN v.video_id END) AS non_blank
                    FROM video_assignments va
                    JOIN videos v ON v.video_id = va.video_id
                    LEFT JOIN video_labels vl ON vl.video_id = v.video_id
                    WHERE v.project_id = :pid
                    GROUP BY va.assigned_to
                    ORDER BY va.assigned_to
                """),
                {"pid": project_id},
            ).fetchall()
        return [
            {
                "annotator": r[0],
                "cameras": r[1],
                "video_count": r[2],
                "hours": r[3],
                "labeled": r[4],
                "blank": r[5],
                "non_blank": r[6],
            }
            for r in rows
        ]

    def get_assigned_video_ids(self, project_id: str, annotator_name: str) -> list[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT va.video_id FROM video_assignments va
                    JOIN videos v ON v.video_id = va.video_id
                    WHERE v.project_id = :pid AND va.assigned_to = :name
                """),
                {"pid": project_id, "name": annotator_name},
            ).fetchall()
        return [r[0] for r in rows]

    def get_camera_assignment_map(self, project_id: str) -> dict[str, str | None]:
        """Return {camera_id: assigned_to | None} for all cameras in the project."""
        cameras = {c["camera_id"]: None for c in self.get_camera_stats(project_id)}
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT DISTINCT v.camera_id, va.assigned_to
                    FROM video_assignments va
                    JOIN videos v ON v.video_id = va.video_id
                    WHERE v.project_id = :pid AND v.camera_id IS NOT NULL
                """),
                {"pid": project_id},
            ).fetchall()
        for camera_id, assigned_to in rows:
            cameras[camera_id] = assigned_to
        return cameras
