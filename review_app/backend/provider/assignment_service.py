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

    def apply_distribution(
        self,
        project_id: str,
        assignment: dict[str, list[str]],
        clear_cameras: list[str] | None = None,
    ) -> int:
        """Write VideoAssignment rows for the given camera→annotator mapping.

        Replaces any existing assignments for cameras mentioned in the mapping.
        `clear_cameras` lists cameras explicitly unassigned (assigned to nobody);
        their existing rows are deleted and no new row is inserted.
        Returns the total number of video rows assigned.
        """
        now = datetime.now(timezone.utc).isoformat()
        total = 0
        all_cameras = list(
            {cam for cams in assignment.values() for cam in cams} | set(clear_cameras or [])
        )
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

    def export_annotator_videos(
        self,
        project_id: str,
        progress_callback=None,
        max_workers: int = 4,
        output_dir: str | None = None,
        annotators: list[str] | None = None,
    ) -> list[dict]:
        """Copy each annotator's assigned videos into a folder under output_dir.

        Defaults to <first_project_dir>/annotator_exports/ when output_dir is None.
        Folders are created at <output_dir>/<annotator_name>/ preserving the camera
        subfolder structure. Files are copied in parallel using a thread pool.

        progress_callback(done: int, total: int) is called after each file is copied.

        Returns a list of {annotator, path, video_count} dicts for each folder written.
        """
        import shutil
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from pathlib import Path

        dirs = self.get_project_dirs(project_id)
        if not dirs:
            raise RuntimeError("Project has no video directory configured.")
        project_root = Path(dirs[0].path)
        if output_dir:
            exports_root = Path(output_dir)
        else:
            exports_root = project_root / "annotator_exports"
        exports_root.mkdir(parents=True, exist_ok=True)

        with self.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT va.assigned_to, v.video_path
                    FROM video_assignments va
                    JOIN videos v ON v.video_id = va.video_id
                    WHERE v.project_id = :pid AND v.is_missing = 0
                    ORDER BY va.assigned_to, v.video_path
                """),
                {"pid": project_id},
            ).fetchall()

        annotator_filter = set(annotators) if annotators else None
        by_annotator: dict[str, list[str]] = {}
        for annotator, video_path in rows:
            if annotator_filter is not None and annotator not in annotator_filter:
                continue
            by_annotator.setdefault(annotator, []).append(video_path)

        def _safe_dirname(name: str) -> str:
            import re

            return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("_") or "unknown"

        # Build dest dirs and (src, dest) copy list up front
        results = []
        copy_tasks: list[tuple[Path, Path]] = []
        for annotator, paths in by_annotator.items():
            safe_name = _safe_dirname(annotator)
            dest_dir = exports_root / safe_name
            dest_dir.mkdir(exist_ok=True)
            for video_path in paths:
                src = Path(video_path)
                if not src.exists():
                    continue
                try:
                    rel = src.relative_to(project_root)
                except ValueError:
                    rel = Path(src.name)
                dest = dest_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                copy_tasks.append((src, dest))
            results.append(
                {"annotator": annotator, "path": str(dest_dir), "video_count": len(paths)}
            )

        total = len(copy_tasks)
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(shutil.copy2, src, dest): (src, dest) for src, dest in copy_tasks
            }
            for future in as_completed(futures):
                future.result()
                done += 1
                if progress_callback:
                    progress_callback(done, total)

        logger.info(
            "Exported %d files across %d annotators → %s", total, len(results), exports_root
        )
        return results
