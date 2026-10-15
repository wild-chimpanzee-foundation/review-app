"""Explicit maintenance for legacy category-folder camera assignments."""

from pathlib import Path

from review_app.backend.camera_layout import camera_id_for_path
from review_app.backend.db.models import ProjectDir, ReferenceVideo, Video
from review_app.backend.path_matching import normalize_path_str


def repair_distance_camera_layout(session, project_id: str) -> None:
    """Repair inferred IDs for one project's videos and prepared references."""
    roots = sorted(
        (
            Path(normalize_path_str(row.path))
            for row in session.query(ProjectDir).filter_by(project_id=project_id)
        ),
        key=lambda root: len(root.parts),
        reverse=True,
    )
    for model in (Video, ReferenceVideo):
        for row in session.query(model).filter_by(project_id=project_id):
            path = Path(normalize_path_str(row.video_path))
            for root in roots:
                if not path.is_relative_to(root):
                    continue
                relative = path.parent.relative_to(root)
                old_camera = (
                    normalize_path_str(str(relative)) if relative != Path(".") else "default"
                )
                new_camera = camera_id_for_path(path, root)
                if row.camera_id == old_camera and new_camera != old_camera:
                    row.camera_id = new_camera
                break
    session.flush()
