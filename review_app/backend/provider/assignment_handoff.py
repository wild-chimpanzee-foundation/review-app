"""Assignment handoff interface. Graph and format mechanics stay inside the module."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from review_app.backend.annotation_package import delivery, handoff
from review_app.backend.annotation_package.archive import decode_archive
from review_app.backend.annotation_package.naming import assignment_reference, package_stem
from review_app.backend.annotation_package.scope import (
    AssignmentRequest,
    PreparedAssignment,
    distance_selection,
)
from review_app.backend.db.models import HandoffAssignment, ProjectDir, Species, Video
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.assignment_service import write_video_allocations
from review_app.backend.provider.base import ProviderBase


def _assignment_naming_fields(row):
    project_name = row.scope.get("project_name")
    number = row.scope.get("assignment_number")
    if project_name is None:
        manifest, context, _ = decode_archive(row.setup_archive)
        project_name = context["project_name"]
        number = manifest.get("assignment_number")
    return {
        "project_name": project_name,
        "assignment_reference": assignment_reference(row.assignment_id, number),
        "recipient": row.recipient,
        "stage": row.stage,
        "target_species": row.target_species,
        "created_at": row.created_at.isoformat(),
    }


def summarize_workload(rows):
    work_cameras = sorted({media["camera_id"] for media in rows if media["camera_id"]})
    durations = [media["duration_sec"] for media in rows]
    return {
        "video_count": len(rows),
        "camera_ids": work_cameras,
        "camera_count": len(work_cameras),
        "hours": sum(value for value in durations if value is not None) / 3600,
        "duration_count": sum(value is not None for value in durations),
    }


class AssignmentHandoffMixin(ProviderBase):
    def prepare_assignment(
        self, project_id: str, request: AssignmentRequest
    ) -> PreparedAssignment:
        """Freeze selected stage work and dependencies; never reconstruct saved scope."""
        with self.Session.begin() as session:
            # Freeze collection and persistence together despite concurrent editor saves.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.prepare(session, project_id, request)

    def prepare_species_assignment(
        self, project_id: str, request: AssignmentRequest, clear_video_ids: tuple[str, ...] = ()
    ) -> PreparedAssignment:
        """Freeze Species work and update the live allocation in one transaction."""
        if request.stage != "species":
            raise ValueError("Choose a Species assignment")
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            prepared = handoff.prepare(session, project_id, request)
            write_video_allocations(
                session.connection(),
                project_id,
                {prepared.recipient: request.video_ids},
                clear_video_ids,
            )
            return prepared

    def get_assignment_workload(self, video_ids: list[str] | tuple[str, ...]) -> dict:
        """Summarize selected footage metadata without estimating unknown durations."""
        if not video_ids:
            return summarize_workload([])
        with self.Session() as session:
            rows = [
                {"camera_id": video.camera_id, "duration_sec": video.duration_sec}
                for video in session.query(Video).filter(Video.video_id.in_(set(video_ids)))
            ]
        return summarize_workload(rows)

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
            rows, unknown, without_camera = distance_selection(
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
                "unresolved_camera_count": len(without_camera),
            }

    def list_assignments(self, project_id: str) -> list[dict]:
        """Describe persisted handouts/bindings without rebuilding their saved scope."""
        with self.Session() as session:
            assignments = []
            for row in (
                session.query(HandoffAssignment)
                .filter_by(project_id=project_id, removed_at=None)
                .order_by(HandoffAssignment.created_at.desc())
            ):
                work = set(row.scope["work_keys"])
                footage = [media for media in row.scope["media"] if media["video_id"] in work]
                workload = summarize_workload(footage)
                assignments.append(
                    {
                        "id": row.id,
                        "assignment_id": row.assignment_id,
                        "role": row.role,
                        **_assignment_naming_fields(row),
                        "video_count": len(row.scope["work_keys"]),
                        **workload,
                        "reference_video_count": len(row.scope["media"])
                        - len(row.scope["work_keys"]),
                        "media_count": len(row.scope["media"]),
                    }
                )
            return assignments

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
                    "duration_sec": media["duration_sec"],
                    "assigned": media["video_id"] in work,
                }
                for media in row.scope["media"]
            ]

    def inspect_assignment(self, data: bytes) -> dict:
        """Validate setup and describe root requirements before creating a project."""
        manifest, context, work = decode_archive(data)
        if manifest["direction"] != "setup":
            raise ValueError("Open an assignment setup package, not a return")
        distance_work = context.get("distance", {})
        species_work = distance_work.get("species_work", [])
        source_species_work = species_work if manifest["stage"] == "distance" else work
        existing_species_work_count = sum(
            bool(unit.get("label") is not None or unit.get("observations") or unit.get("tags"))
            for unit in source_species_work
        )
        distance_individual_count = sum(len(unit.get("individuals", [])) for unit in work)
        distance_sample_count = sum(len(unit.get("samples", [])) for unit in work)
        distance_draft_count = sum(
            int(bool(row.get("ai_original")))
            for unit in work
            for row in unit.get("individuals", []) + unit.get("samples", [])
        ) + sum(int(bool(unit.get("task") and unit["task"].get("rejected_ai"))) for unit in work)
        return {
            "assignment_id": manifest["assignment_id"],
            "assignment_reference": assignment_reference(
                manifest["assignment_id"], manifest.get("assignment_number")
            ),
            "recipient": manifest["recipient"],
            "stage": manifest["stage"],
            "target_species": manifest["target_species"],
            "project_name": context["project_name"],
            "video_count": len(manifest["work_keys"]),
            "reference_video_count": len(context["media"]) - len(manifest["work_keys"]),
            "media_count": len(context["media"]),
            "species_count": len(context.get("species", [])),
            "behavior_count": len(context.get("behaviors", [])),
            "tag_count": len(context.get("tags", [])),
            "prediction_count": len(context.get("predictions", [])),
            "existing_species_work_count": existing_species_work_count,
            "distance_individual_count": distance_individual_count,
            "distance_sample_count": distance_sample_count,
            "distance_ai_import_count": len(distance_work.get("imports", [])),
            "distance_draft_count": distance_draft_count,
            "distance_task_count": sum(unit.get("task") is not None for unit in work)
            if manifest["stage"] == "distance"
            else 0,
            "distance_reference_count": len(distance_work.get("references", [])),
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

    def open_assignment(
        self, data: bytes, project_name: str, media_roots: dict, *, local_recipient=None
    ):
        """Open only packaged work in a fresh project, atomically; never scan whole folders."""
        manifest, context, work = decode_archive(data)
        if manifest["direction"] != "setup":
            raise ValueError("Open an assignment setup package, not a return")
        roots, paths = handoff.locate_media(manifest, context, media_roots)
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.open_setup(
                session,
                data,
                manifest,
                context,
                work,
                project_name,
                roots,
                paths,
                local_recipient=local_recipient,
            )

    def export_assignment_return(self, working_project_id: str) -> bytes:
        """Return every saved in-scope video, including untouched and empty work."""
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            return handoff.export_return(session, working_project_id)

    def inspect_assignment_return(self, data: bytes) -> dict:
        """Read return metadata for policy choices without inspecting project state."""
        manifest, _, _ = decode_archive(data)
        if manifest["direction"] != "return":
            raise ValueError("Inspect an assignment return package")
        return {
            "stage": manifest["stage"],
            "target_species": manifest["target_species"],
            "recipient": manifest["recipient"],
        }

    def preview_assignment_return(self, project_id: str, data: bytes, policy="use_imported"):
        """Read-only, policy-specific impact preview; invalid returns raise ValueError."""
        with self.Session() as session:
            session.connection().exec_driver_sql("BEGIN")
            binding, _, _, preview, _ = handoff.plan_return(session, project_id, data, policy)
            return replace(
                preview,
                assignment_reference=_assignment_naming_fields(binding)["assignment_reference"],
                recipient=binding.recipient,
            )

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
            binding = session.get(HandoffAssignment, assignment_id)
            roots, folders, media = delivery.resolve_delivery(session, binding)
            stem = package_stem(_assignment_naming_fields(binding))
        return delivery.copy_media(stem, roots, folders, media, output_dir, progress_callback)

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
            stem = package_stem(_assignment_naming_fields(binding))
            if include_footage:
                roots, folders, media = delivery.resolve_delivery(session, binding)
            else:
                folders, media = {}, []
                roots = [
                    Path(normalize_path_str(root.path)).expanduser().resolve()
                    for root in session.query(ProjectDir).filter_by(project_id=project_id)
                ]
        return delivery.copy_media(
            stem,
            roots,
            folders,
            media,
            output_dir,
            progress_callback,
            setup_archive=archive,
        )

    def export_assignment(self, assignment_id: str) -> bytes:
        """Download exactly the original prepared bytes, without querying live scope."""
        with self.Session() as session:
            return handoff.export(session, assignment_id)

    def save_assignment_packages(self, project_id, assignment_ids, output_dir):
        """Write every frozen setup archive to one folder; never overwrite existing files."""
        if not isinstance(output_dir, (str, Path)) or not str(output_dir).strip():
            raise ValueError("Choose an output folder")
        destination = Path(output_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        rows = []
        with self.Session() as session:
            for assignment_id in assignment_ids:
                row = session.get(HandoffAssignment, assignment_id)
                if row is None or row.project_id != project_id:
                    raise ValueError("Assignment not found in this project")
                if row.removed_at is not None:
                    raise ValueError("Assignment was removed; choose a different assignment")
                rows.append((package_stem(_assignment_naming_fields(row)), row.setup_archive))
        saved = []
        for stem, archive in rows:
            target = destination / f"{stem}.zip"
            try:
                with target.open("xb") as file:
                    file.write(archive)
            except FileExistsError as exc:
                raise ValueError(f"File already exists: {target}") from exc
            saved.append(target)
        return saved
