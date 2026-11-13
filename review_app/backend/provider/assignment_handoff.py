"""Assignment handoff interface. Graph and format mechanics stay inside the module."""

from review_app.backend.annotation_package import delivery, handoff
from review_app.backend.annotation_package.archive import decode_archive
from review_app.backend.annotation_package.scope import AssignmentRequest, PreparedAssignment
from review_app.backend.db.models import HandoffAssignment, Video
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
                    "video_count": len(row.scope["work_keys"]),
                    "created_at": row.created_at.isoformat(),
                }
                for row in session.query(HandoffAssignment)
                .filter_by(project_id=project_id)
                .order_by(HandoffAssignment.created_at.desc())
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
            "project_name": context["project_name"],
            "video_count": len(manifest["work_keys"]),
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

    def export_assignment(self, assignment_id: str) -> bytes:
        """Download exactly the original prepared bytes, without querying live scope."""
        with self.Session() as session:
            return handoff.export(session, assignment_id)
