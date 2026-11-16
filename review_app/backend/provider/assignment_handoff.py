"""Assignment handoff interface. Graph and format mechanics stay inside the module."""

from datetime import datetime, timezone
from pathlib import Path

from review_app.backend.annotation_package import delivery, handoff
from review_app.backend.annotation_package.archive import decode_archive
from review_app.backend.annotation_package.scope import (
    AssignmentRequest,
    PreparedAssignment,
    distance_selection,
)
from review_app.backend.db.models import HandoffAssignment, ProjectDir, Species, Video
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.base import ProviderBase


class AssignmentHandoffMixin(ProviderBase):
    def prepare_assignment(
        self, project_id: str, request: AssignmentRequest
    ) -> PreparedAssignment:
        """Freeze selected stage work and dependencies; never reconstruct saved scope."""
        with self.Session.begin() as session:
            # Freeze collection and persistence together despite concurrent editor saves.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.prepare(session, project_id, request)

    def get_assignment_video_ids(self, project_id: str) -> list[str]:
        """All project media for explicit preparation, including labeled/missing videos.

        Review queues intentionally omit missing footage, so they are not a source
        for the UI's 'all videos' selection.
        """
        with self.Session() as session:
            return [
                row.video_id
                for row in session.query(Video)
                .filter_by(project_id=project_id)
                .order_by(Video.video_id)
            ]

    def get_assignment_distance_species_options(self, project_id, lang="en") -> dict[str, str]:
        """Use scientific names as portable target keys, including saved vocabulary."""
        options = self.get_distance_target_species_options(project_id, lang)
        with self.Session() as session:
            return {
                row.scientific_name: options[row.id]
                for row in session.query(Species)
                .filter(Species.id.in_(options))
                .order_by(Species.scientific_name)
            }

    def preview_assignment_distance_selection(
        self, project_id, target_species, *, start_time=None, end_time=None
    ) -> dict:
        """List eligible work without the legacy format's AI/manual restriction."""
        with self.Session.begin() as session:
            rows, unknown = distance_selection(
                session, project_id, target_species, start_time, end_time
            )
            return {
                "videos": [
                    {
                        "video_id": video.video_id,
                        "video_path": video.video_path,
                        "camera_id": video.camera_id,
                        "recorded_start": task.recorded_start.isoformat()
                        if task.recorded_start
                        else None,
                        "is_missing": video.is_missing,
                    }
                    for task, video in rows
                ],
                "unknown_time_count": len(unknown),
            }

    def list_assignments(self, project_id: str) -> list[dict]:
        """Describe persisted handouts/bindings without rebuilding their saved scope."""
        with self.Session() as session:
            return [
                {
                    "id": row.id,
                    "assignment_id": row.assignment_id,
                    "role": row.role,
                    "recipient": row.recipient,
                    "stage": row.stage,
                    "target_species": row.target_species,
                    "video_count": len(row.scope["work_keys"]),
                    "reference_video_count": len(row.scope["media"]) - len(row.scope["work_keys"]),
                    "media_count": len(row.scope["media"]),
                    "created_at": row.created_at.isoformat(),
                }
                for row in session.query(HandoffAssignment)
                .filter_by(project_id=project_id, removed_at=None)
                .order_by(HandoffAssignment.created_at.desc())
            ]

    def remove_assignment(self, project_id: str, assignment_id: str) -> None:
        """Remove an origin handoff from delivery while retaining return authority."""
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(HandoffAssignment, assignment_id)
            if row is None or row.project_id != project_id or row.role != "origin":
                raise ValueError("Choose an originating assignment in this project")
            if row.removed_at is None:
                row.removed_at = datetime.now(timezone.utc)

    def get_assignment_media(self, project_id: str, assignment_id: str) -> list[dict]:
        """Describe frozen delivered media, distinguishing work from dependencies."""
        with self.Session() as session:
            row = session.get(HandoffAssignment, assignment_id)
            if row is None or row.project_id != project_id:
                raise ValueError("Assignment not found in this project")
            work = set(row.scope["work_keys"])
            return [
                {
                    "root_id": media["root_id"],
                    "video_path": media["video_path"],
                    "camera_id": media["camera_id"],
                    "assigned": media["video_id"] in work,
                }
                for media in row.scope["media"]
            ]

    def inspect_assignment(self, data: bytes) -> dict:
        """Validate setup and describe root requirements before creating a project."""
        manifest, context, _ = decode_archive(data)
        if manifest["direction"] != "setup":
            raise ValueError("Open an assignment setup package, not a return")
        return {
            "assignment_id": manifest["assignment_id"],
            "recipient": manifest["recipient"],
            "stage": manifest["stage"],
            "target_species": manifest["target_species"],
            "project_name": context["project_name"],
            "video_count": len(manifest["work_keys"]),
            "reference_video_count": len(context["media"]) - len(manifest["work_keys"]),
            "media_count": len(context["media"]),
            "roots": {
                root: [row["video_path"] for row in context["media"] if row["root_id"] == root]
                for root in manifest["roots"]
            },
        }

    def check_assignment_media(self, data: bytes, media_roots: dict) -> tuple[str, ...]:
        """Check proposed footage folders without creating or activating a project."""
        manifest, context, _ = decode_archive(data)
        if manifest["direction"] != "setup":
            raise ValueError("Open an assignment setup package, not a return")
        _, paths = handoff.locate_media(manifest, context, media_roots)
        return tuple(key for key, (_, missing) in paths.items() if missing)

    def open_assignment(self, data: bytes, project_name: str, media_roots: dict):
        """Open only packaged work in a fresh project, atomically; never scan whole folders."""
        manifest, context, work = decode_archive(data)
        if manifest["direction"] != "setup":
            raise ValueError("Open an assignment setup package, not a return")
        roots, paths = handoff.locate_media(manifest, context, media_roots)
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.open_setup(
                session, data, manifest, context, work, project_name, roots, paths
            )

    def export_assignment_return(self, working_project_id: str) -> bytes:
        """Return every saved in-scope video, including untouched and empty work."""
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.export_return(session, working_project_id)

    def preview_assignment_return(self, project_id: str, data: bytes, policy="use_imported"):
        """Read-only, policy-specific impact preview; invalid returns raise ValueError."""
        with self.Session() as session:
            session.connection().exec_driver_sql("BEGIN")
            return handoff.plan_return(session, project_id, data, policy)[3]

    def accept_assignment_return(
        self, project_id: str, data: bytes, policy: str, preview_token: str
    ):
        """Revalidate the preview under a write reservation, then apply all work atomically."""
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.accept_return(session, project_id, data, policy, preview_token)

    def copy_assignment_footage(self, assignment_id: str, output_dir, progress_callback=None):
        """Deliver only saved media, using current local paths and separate root namespaces."""
        with self.Session() as session:
            session.connection().exec_driver_sql("BEGIN")
            portable_id, roots, root_ids, media = delivery.resolve_delivery(session, assignment_id)
        return delivery.copy_media(
            portable_id, roots, root_ids, media, output_dir, progress_callback
        )

    def save_assignment_delivery(
        self,
        project_id: str,
        assignment_id: str,
        output_dir,
        *,
        include_footage=False,
        progress_callback=None,
    ):
        """Save the original ZIP and optional saved-scope footage in one new folder."""
        with self.Session() as session:
            session.connection().exec_driver_sql("BEGIN")
            binding = session.get(HandoffAssignment, assignment_id)
            if binding is None or binding.project_id != project_id:
                raise ValueError("Assignment not found in this project")
            archive = handoff.export(session, assignment_id)
            if include_footage:
                portable_id, roots, root_ids, media = delivery.resolve_delivery(
                    session, assignment_id
                )
            else:
                portable_id, root_ids, media = binding.assignment_id, [], []
                roots = [
                    Path(normalize_path_str(root.path)).expanduser().resolve()
                    for root in session.query(ProjectDir).filter_by(project_id=project_id)
                ]
        return delivery.copy_media(
            portable_id,
            roots,
            root_ids,
            media,
            output_dir,
            progress_callback,
            setup_archive=archive,
        )

    def export_assignment(self, assignment_id: str) -> bytes:
        """Download exactly the original prepared bytes, without querying live scope."""
        with self.Session() as session:
            return handoff.export(session, assignment_id)
