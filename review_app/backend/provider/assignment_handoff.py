"""Assignment handoff interface. Graph and format mechanics stay inside the module."""

from review_app.backend.annotation_package import handoff
from review_app.backend.annotation_package.scope import AssignmentRequest, PreparedAssignment
from review_app.backend.provider.base import ProviderBase


class AssignmentHandoffMixin(ProviderBase):
    def prepare_assignment(
        self, project_id: str, request: AssignmentRequest
    ) -> PreparedAssignment:
        """Freeze selected Species work and dependencies; never filter away carried labels."""
        with self.Session.begin() as session:
            # Freeze collection and persistence together despite concurrent editor saves.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.prepare(session, project_id, request)

    def export_assignment(self, assignment_id: str) -> bytes:
        """Download exactly the original prepared bytes, without querying live scope."""
        with self.Session() as session:
            return handoff.export(session, assignment_id)
