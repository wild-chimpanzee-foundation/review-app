from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import text

from review_app.backend.db.models import Project, ProjectDir, Video
from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)


class ProjectMixin(ProviderBase):
    """Project and ProjectDir CRUD. Requires self.engine, self.Session, self._utcnow_dt."""

    def create_project(self, name: str, video_dir: str) -> Project:
        project = Project(id=str(uuid.uuid4()), name=name)
        with self.Session() as s:
            s.add(project)
            s.flush()
            if video_dir:
                s.add(
                    ProjectDir(
                        id=str(uuid.uuid4()),
                        project_id=project.id,
                        path=str(video_dir),
                        sort_order=0,
                    )
                )
            s.commit()
            s.refresh(project)
        logger.info("Created project %r (id=%s, dir=%s)", name, project.id, video_dir or "none")
        return project

    def list_projects(self) -> list[Project]:
        with self.Session() as s:
            return (
                s.query(Project)
                .order_by(Project.last_opened.desc().nullslast(), Project.created_at)
                .all()
            )

    def get_most_recent_project(self) -> Project | None:
        with self.Session() as s:
            return (
                s.query(Project)
                .order_by(Project.last_opened.desc().nullslast(), Project.created_at)
                .first()
            )

    def get_project(self, project_id: str) -> Project | None:
        with self.Session() as s:
            return s.query(Project).filter_by(id=project_id).first()

    def update_project_name(self, project_id: str, name: str) -> None:
        with self.Session() as s:
            project = s.query(Project).filter_by(id=project_id).first()
            if project:
                project.name = name
                s.commit()

    def touch_project(self, project_id: str) -> None:
        with self.Session() as s:
            project = s.query(Project).filter_by(id=project_id).first()
            if project:
                project.last_opened = self._utcnow_dt()
                s.commit()

    def get_project_dirs(self, project_id: str | None) -> list[ProjectDir]:
        with self.Session() as s:
            return (
                s.query(ProjectDir)
                .filter_by(project_id=project_id)
                .order_by(ProjectDir.sort_order)
                .all()
            )

    def add_project_dir(self, project_id: str, path: str) -> ProjectDir:
        with self.Session() as s:
            existing = s.query(ProjectDir).filter_by(project_id=project_id).all()
            sort_order = max((d.sort_order for d in existing), default=-1) + 1
            d = ProjectDir(
                id=str(uuid.uuid4()),
                project_id=project_id,
                path=str(path),
                sort_order=sort_order,
            )
            s.add(d)
            s.commit()
            s.refresh(d)
        logger.info("Added directory %s to project %s", path, project_id)
        return d

    def remove_project_dir(self, dir_id: str) -> None:
        with self.Session() as s:
            d = s.query(ProjectDir).filter_by(id=dir_id).first()
            if not d:
                return
            prefix = d.path.rstrip("/") + "/"
            deleted = (
                s.query(Video)
                .filter(
                    Video.project_id == d.project_id,
                    Video.video_path.startswith(prefix),
                )
                .delete(synchronize_session=False)
            )
            s.delete(d)
            s.commit()
        logger.info(
            "Removed directory %s from project %s (%d videos deleted)",
            d.path,
            d.project_id,
            deleted,
        )

    def get_project_video_count(self, project_id: str) -> int:
        with self.engine.connect() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM videos WHERE project_id = :pid"),
                {"pid": project_id},
            ).fetchone()
            return result[0] if result else 0

    def delete_project(self, project_id: str) -> dict[str, bool | int]:
        with self.Session() as s:
            project = s.get(Project, project_id)
            if project is None:
                logger.warning("delete_project: project %s not found", project_id)
                return {"deleted": False}
            project_name = project.name
            video_count = len(project.videos)
            transcoded_paths = [
                Path(v.transcoded_path) for v in project.videos if v.transcoded_path is not None
            ]
            s.delete(project)
            s.commit()
        for p in transcoded_paths:
            p.unlink(missing_ok=True)
        logger.info(
            "Deleted project %r (id=%s): %d videos removed, %d transcoded files cleaned up",
            project_name,
            project_id,
            video_count,
            len(transcoded_paths),
        )
        return {"deleted": True, "videos_removed": video_count}
