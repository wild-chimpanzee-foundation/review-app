from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import func, text

from review_app.backend.db.models import (
    Deployment,
    DeploymentFolderLink,
    Project,
    ProjectDir,
    ProjectSpecies,
    ReferenceVideo,
    Video,
)
from review_app.backend.deployments import (
    DeploymentInputError,
    DeploymentSetupPreview,
    preview_deployment_setup,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.base import ProviderBase

logger = logging.getLogger(__name__)


class ProjectMixin(ProviderBase):
    """Project and ProjectDir CRUD. Requires self.engine, self.Session, self._utcnow_dt."""

    def _unique_project_name(self, base: str) -> str:
        with self.Session() as s:
            existing = {r[0] for r in s.execute(text("SELECT name FROM projects")).fetchall()}
        if base not in existing:
            return base
        i = 2
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"

    def create_project(
        self,
        name: str,
        video_dir: str,
        *,
        expected_preview: DeploymentSetupPreview | None = None,
    ) -> Project:
        """Create a project and its previewed folder groups in one transaction."""
        deployments = ()
        if expected_preview is not None:
            confirmed = preview_deployment_setup(video_dir)
            if confirmed != expected_preview:
                raise DeploymentInputError("deployment_error_setup_stale")
            video_dir = confirmed.root
            deployments = confirmed.deployments

        name = self._unique_project_name(name)
        project_id = str(uuid.uuid4())
        project = Project(id=project_id, name=name)
        with self.Session() as session:
            session.add(project)
            session.flush()
            if video_dir:
                session.add(
                    ProjectDir(
                        project_id=project.id,
                        path=normalize_path_str(str(video_dir)),
                        sort_order=0,
                    )
                )
            session.add_all(
                Deployment(project_id=project.id, deployment_id=label, name=label)
                for label in deployments
            )
            session.commit()
            session.refresh(project)
        logger.info("Created project %r (id=%s, dir=%s)", name, project.id, video_dir or "none")
        return project

    def get_project_deployments(self, project_id: str) -> list[dict]:
        from review_app.backend.provider.deployments import ensure_project_initialized

        with self.Session.begin() as session:
            if session.get(Project, project_id) is None:
                return []
            ensure_project_initialized(session, project_id)
            return [
                {
                    column.name: (
                        getattr(row, column.name).isoformat()
                        if column.name in {"installation_date", "retrieval_date"}
                        and getattr(row, column.name) is not None
                        else getattr(row, column.name)
                    )
                    for column in Deployment.__table__.columns
                }
                for row in session.query(Deployment)
                .filter_by(project_id=project_id)
                .order_by(Deployment.deployment_id)
            ]

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
                path=normalize_path_str(str(path)),
                sort_order=sort_order,
            )
            s.add(d)
            s.commit()
            s.refresh(d)
        logger.info("Added directory %s to project %s", path, project_id)
        return d

    def update_project_dir(self, dir_id: str, new_path: str) -> ProjectDir | None:
        """Relocate videos and references together, retaining IDs and saved evidence.

        Path collisions fail the transaction rather than merging reference identities.
        A re-sync afterwards picks up added/removed files.
        """
        with self.Session() as s:
            d = s.query(ProjectDir).filter_by(id=dir_id).first()
            if not d:
                return None
            old_prefix = normalize_path_str(d.path).rstrip("/") + "/"
            new_prefix = normalize_path_str(str(new_path)).rstrip("/") + "/"
            if old_prefix != new_prefix:
                old_root, new_root = Path(old_prefix), Path(new_prefix)
                paired_root = any(
                    (root / "videos").is_dir() and (root / "references").is_dir()
                    for root in (old_root, new_root)
                )
                for model in (Video, ReferenceVideo):
                    s.execute(
                        text(f"""
                            UPDATE {model.__tablename__}
                            SET video_path = :new_prefix || substr(REPLACE(video_path, '\\', '/'), :remainder_at)
                            WHERE project_id IS :pid
                              AND substr(REPLACE(video_path, '\\', '/'), 1, :prefix_len) = :old_prefix
                        """),
                        {
                            "new_prefix": new_prefix,
                            "remainder_at": len(old_prefix) + 1,
                            "prefix_len": len(old_prefix),
                            "old_prefix": old_prefix,
                            "pid": d.project_id,
                        },
                    )
                    # Paired folders imported as the root use its name as camera ID.
                    # Keep references in step with the next sync; leave explicit IDs
                    # and nested deployments alone.
                    if paired_root and old_root.name != new_root.name:
                        for row in s.query(model).filter_by(
                            project_id=d.project_id, camera_id=old_root.name
                        ):
                            parent = Path(normalize_path_str(row.video_path)).parent
                            if (
                                parent in (new_root / "videos", new_root / "references")
                                and row.deployment_id is None
                            ):
                                row.camera_id = new_root.name
            d.path = normalize_path_str(str(new_path))
            s.commit()
            s.refresh(d)
        logger.info("Updated directory %s to %s (project %s)", dir_id, new_path, d.project_id)
        return d

    def remove_project_dir(self, dir_id: str) -> None:
        with self.Session() as s:
            d = s.query(ProjectDir).filter_by(id=dir_id).first()
            if not d:
                return
            prefix = normalize_path_str(d.path).rstrip("/") + "/"
            self._cascade_delete_videos(
                s,
                (Video.project_id == d.project_id)
                & func.replace(Video.video_path, "\\", "/").startswith(prefix),
            )
            s.delete(d)
            s.commit()
        logger.info("Removed directory %s from project %s", d.path, d.project_id)

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

            # 1. Efficiently collect transcoded paths for cleanup without loading full objects
            transcoded_paths = [
                Path(p)
                for (p,) in s.query(Video.transcoded_path)
                .filter(Video.project_id == project_id, Video.transcoded_path.isnot(None))
                .all()
                if p
            ]

            # 2. Bulk delete related data. This is much faster than SQLAlchemy's
            # relationship cascade, which loads every object.
            video_count = s.query(Video).filter_by(project_id=project_id).count()
            self._cascade_delete_videos(s, Video.project_id == project_id)
            # Project-only tables (not linked via video_id)
            s.query(ProjectSpecies).filter_by(project_id=project_id).delete()
            s.query(DeploymentFolderLink).filter(
                DeploymentFolderLink.project_dir_id.in_(
                    s.query(ProjectDir.id).filter_by(project_id=project_id)
                )
            ).delete(synchronize_session=False)
            # External prepared references retain their own lifetime, but must not
            # keep a dangling inventory FK when the project is removed.
            s.query(ReferenceVideo).filter_by(project_id=project_id).update(
                {"deployment_id": None}
            )
            s.query(ProjectDir).filter_by(project_id=project_id).delete()
            s.query(Deployment).filter_by(project_id=project_id).delete()
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
