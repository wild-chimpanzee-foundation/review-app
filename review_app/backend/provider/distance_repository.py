"""References, identities, camera-clock samples, and independent distance review."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import or_

from review_app.backend.db.models import (
    Behavior,
    DistanceIndividual,
    DistanceVideoReview,
    IndividualObservation,
    Species,
    Video,
)
from review_app.backend.provider.base import ProviderBase
from review_app.backend.provider.distance_camera_layout import repair_distance_camera_layout
from review_app.backend.provider.distance_identities import (
    create_distance_individual,
    delete_distance_individual,
    list_distance_individuals,
    set_distance_individual_anchor,
    set_distance_individual_attributes,
)
from review_app.backend.provider.distance_references import (
    add_reference_frame,
    get_distance_reference_videos,
    get_distance_video_queue,
    list_reference_frames,
    list_reference_videos,
    register_reference_video,
)
from review_app.backend.provider.distance_review import (
    finish_individual,
    finish_video,
    get_review_state,
    reconcile_video_completion,
    refresh_individual_completion,
    review_offsets,
    set_active_individual,
    verify_recording_time,
)
from review_app.backend.provider.distance_samples import save_distance_sample


def _offset(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Offset must be a non-negative integer in milliseconds")


class DistanceMixin(ProviderBase):
    def repair_distance_camera_layout(self, project_id: str) -> None:
        """Repair legacy category-folder camera IDs for one project's videos."""
        with self.Session.begin() as session:
            repair_distance_camera_layout(session, project_id)

    def _reconcile_distance_video_completion(self, session, video_id: str) -> None:
        """Refresh completion after a video duration change in its transaction."""
        reconcile_video_completion(session, video_id)

    def get_distance_species_options(
        self,
        project_id: str,
        lang: str = "en",
        *,
        include_species_ids: list[str] | None = None,
    ) -> dict[str, str]:
        """Return project choices, optionally including saved IDs outside its vocabulary."""
        catalog = self.get_species_catalog(lang, project_id=project_id)
        include_species_ids = include_species_ids or []
        with self.Session() as session:
            query = session.query(Species)
            conditions = [Species.scientific_name.in_(catalog.display)]
            if include_species_ids:
                conditions.append(Species.id.in_(include_species_ids))
            rows = query.filter(or_(*conditions)).order_by(Species.scientific_name).all()
            return {
                row.id: catalog.display.get(row.scientific_name)
                or catalog.global_display[row.scientific_name]
                for row in rows
            }

    def get_distance_reviewed_species(self, video_id: str) -> list[str]:
        """Use ordinary Review's saved species, without inferring identities or counts."""
        return [row["species_id"] for row in self.get_distance_reviewed_species_counts(video_id)]

    def get_distance_reviewed_species_counts(self, video_id: str) -> list[dict]:
        """Return ordinary Review species and the sum of their recorded individual counts."""
        with self.Session() as session:
            rows = (
                session.query(IndividualObservation.species_id, IndividualObservation.count)
                .filter_by(video_id=video_id)
                .order_by(IndividualObservation.id)
                .all()
            )
        counts: dict[str, int] = {}
        for species_id, count in rows:
            if species_id:
                counts[species_id] = counts.get(species_id, 0) + (count or 1)
        return [{"species_id": species_id, "count": count} for species_id, count in counts.items()]

    def get_distance_video_queue(
        self,
        project_id: str,
        species_id: str | None = None,
        *,
        camera_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        with self.Session() as session:
            return get_distance_video_queue(
                session, project_id, species_id, camera_id=camera_id, status=status
            )

    def register_reference_video(self, project_id: str, camera_id: str, video_path: str) -> dict:
        with self.Session.begin() as session:
            return register_reference_video(session, project_id, camera_id, video_path)

    def list_reference_videos(self, project_id: str, camera_id: str) -> list[dict]:
        with self.Session() as session:
            return list_reference_videos(session, project_id, camera_id)

    def get_distance_reference_videos(self, video_id: str) -> list[dict]:
        with self.Session.begin() as session:
            return get_distance_reference_videos(session, video_id)

    def add_reference_frame(
        self,
        reference_video_id: str,
        offset_ms: int,
        distance_m: float,
        *,
        playback_offset_ms: float | None = None,
    ) -> dict:
        with self.Session.begin() as session:
            return add_reference_frame(
                session,
                reference_video_id,
                offset_ms,
                distance_m,
                playback_offset_ms=playback_offset_ms,
            )

    def list_reference_frames(self, reference_video_id: str) -> list[dict]:
        with self.Session() as session:
            return list_reference_frames(session, reference_video_id)

    def create_distance_individual(
        self,
        video_id: str,
        species_id: str,
        first_seen_offset_ms: int,
        *,
        age: str | None = None,
        sex: str | None = None,
        anchor: dict[str, float] | None = None,
        labeled_by: str | None = None,
        create_infant: bool = False,
        tag_ids: list[str] | None = None,
    ) -> dict:
        with self.Session.begin() as session:
            return create_distance_individual(
                session,
                self._utcnow_dt,
                video_id,
                species_id,
                first_seen_offset_ms,
                age=age,
                sex=sex,
                anchor=anchor,
                labeled_by=labeled_by,
                create_infant=create_infant,
                tag_ids=tag_ids,
            )

    def list_distance_individuals(self, video_id: str) -> list[dict]:
        with self.Session.begin() as session:
            return list_distance_individuals(session, video_id)

    def delete_distance_individual(self, individual_id: str) -> None:
        with self.Session.begin() as session:
            delete_distance_individual(session, individual_id)

    def set_distance_individual_attributes(
        self,
        individual_id: str,
        *,
        age: str | None,
        sex: str | None,
        tag_ids: list[str],
        species_id: str | None = None,
        labeled_by: str | None = None,
    ) -> None:
        with self.Session.begin() as session:
            set_distance_individual_attributes(
                session,
                self._utcnow_dt,
                individual_id,
                age=age,
                sex=sex,
                tag_ids=tag_ids,
                species_id=species_id,
                labeled_by=labeled_by,
            )

    def set_distance_individual_anchor(
        self,
        individual_id: str,
        first_seen_offset_ms: int,
        *,
        anchor: dict[str, float] | None = None,
        labeled_by: str | None = None,
    ) -> None:
        with self.Session.begin() as session:
            set_distance_individual_anchor(
                session,
                self._utcnow_dt,
                individual_id,
                first_seen_offset_ms,
                anchor=anchor,
                labeled_by=labeled_by,
            )

    def verify_distance_recording_time(
        self,
        video_id: str,
        recorded_start: datetime,
        annotator_name: str,
        *,
        clock_anchor_offset_ms: int | None = None,
        clock_anchor_playback_ms: float | None = None,
        opening_even_second: bool = False,
    ) -> None:
        """Persist a deliberately verified camera wall clock, never infer it from metadata."""
        with self.Session.begin() as session:
            verify_recording_time(
                session,
                self._utcnow_dt,
                video_id,
                recorded_start,
                annotator_name,
                clock_anchor_offset_ms=clock_anchor_offset_ms,
                clock_anchor_playback_ms=clock_anchor_playback_ms,
                opening_even_second=opening_even_second,
            )

    def calibrate_distance_clock(
        self,
        video_id: str,
        camera_time: datetime,
        playback_offset_ms: float,
        annotator_name: str,
        *,
        opening_even_second: bool = False,
    ) -> None:
        """Align the selected even-second frame once for every individual in this video."""
        if (
            not isinstance(camera_time, datetime)
            or camera_time.tzinfo is not None
            or camera_time.microsecond
            or camera_time.second % 2
        ):
            raise ValueError("Enter the visible even whole-second camera timestamp")
        if (
            isinstance(playback_offset_ms, bool)
            or not isinstance(playback_offset_ms, (int, float))
            or not math.isfinite(playback_offset_ms)
            or playback_offset_ms < 0
        ):
            raise ValueError("Select a frame within the video")
        if not isinstance(opening_even_second, bool):
            raise ValueError("Check whether the opening timestamp is even")
        with self.Session() as session:
            video = session.get(Video, video_id)
            if video is None:
                raise ValueError("Video not found")
            if video.duration_sec is None or playback_offset_ms >= video.duration_sec * 1000:
                raise ValueError("Select a frame within the video")
        if playback_offset_ms == 0 and not opening_even_second:
            raise ValueError(
                "Select the frame where the timestamp first changes to an even second"
            )
        offset = math.floor(playback_offset_ms)
        self.verify_distance_recording_time(
            video_id,
            camera_time - timedelta(milliseconds=offset),
            annotator_name,
            clock_anchor_offset_ms=offset,
            clock_anchor_playback_ms=float(playback_offset_ms),
            opening_even_second=opening_even_second,
        )

    def get_distance_behavior_options(self, lang: str = "en") -> dict[str, str]:
        with self.Session() as session:
            return {
                row.id: (row.name_fr if lang == "fr" else row.name_en) or row.key
                for row in session.query(Behavior).order_by(Behavior.key)
            }

    def set_distance_active_individual(
        self, video_id: str, annotator_name: str, individual_id: str
    ) -> None:
        if not annotator_name or not annotator_name.strip():
            raise ValueError("Annotator is required")
        with self.Session.begin() as session:
            set_active_individual(
                session, video_id, annotator_name, individual_id, self._utcnow_dt()
            )

    def get_distance_review_state(self, video_id: str, annotator_name: str) -> dict:
        with self.Session() as session:
            return get_review_state(session, video_id, annotator_name)

    def apply_distance_to_end(
        self,
        individual_id: str,
        offset_ms: int,
        *,
        reference_frame_id: str | None = None,
        skip_reason: str | None = None,
        behavior_ids: list[str] | None = None,
        infant_id: str | None = None,
        create_infant: bool = False,
        creation_offset_ms: int | float | None = None,
        labeled_by: str,
    ) -> dict:
        """Overwrite the inclusive scheduled suffix with the supplied (possibly unsaved) choice.

        All sample writes, infant creation, and completion changes share one transaction.
        Neither the active selection nor explicit video completion is advanced.
        """
        if not isinstance(labeled_by, str) or not labeled_by.strip():
            raise ValueError("Annotator is required")
        if not isinstance(create_infant, bool):
            raise ValueError("Create infant must be a boolean")
        if not isinstance(individual_id, str) or not individual_id:
            raise ValueError("Individual ID must be a non-empty string")
        for value in (reference_frame_id, infant_id):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("Reference and infant IDs must be non-empty strings")
        if skip_reason is not None and not isinstance(skip_reason, str):
            raise ValueError("Skip reason must be a supported visibility status")
        if behavior_ids is not None and (
            not isinstance(behavior_ids, list)
            or any(not isinstance(bid, str) for bid in behavior_ids)
        ):
            raise ValueError("Behaviors must be a list of IDs")
        # Snapshot caller-owned choices before any writes.
        behaviors = list(behavior_ids or [])
        _offset(offset_ms)
        with self.Session.begin() as session:
            individual = session.get(DistanceIndividual, individual_id)
            if individual is None:
                raise ValueError("Distance individual not found")
            video = session.get(Video, individual.video_id)
            review = session.get(DistanceVideoReview, individual.video_id)
            if review is None:
                raise ValueError("Verify the camera recording time before annotating samples")
            schedule = review_offsets(review, video.duration_sec)
            if offset_ms not in schedule:
                raise ValueError("Offset is not a scheduled even camera second")
            scheduled_context = (individual, video)
            if not video.project_id or not video.camera_id or not video.camera_id.strip():
                raise ValueError("Resolve the video's project and camera before distance review")
            scheduled_offsets = set(schedule)
            affected_completion_ids: set[str] = set()
            rows = []
            for offset in schedule:
                if offset < offset_ms:
                    continue
                result = self._save_distance_sample(
                    session,
                    individual_id,
                    offset,
                    reference_frame_id=reference_frame_id,
                    skip_reason=skip_reason,
                    behavior_ids=behaviors,
                    infant_id=infant_id,
                    create_infant=create_infant,
                    creation_offset_ms=creation_offset_ms
                    if creation_offset_ms is not None
                    else offset_ms,
                    labeled_by=labeled_by,
                    scheduled_context=scheduled_context,
                    scheduled_offsets=scheduled_offsets,
                    deferred_completion_ids=affected_completion_ids,
                )
                infant_id = result.pop("infant_id")
                create_infant = False
                rows.append(result)
            for affected_id in affected_completion_ids:
                affected = session.get(DistanceIndividual, affected_id)
                if affected is not None:
                    refresh_individual_completion(session, affected, scheduled_offsets)
            review.completed_by = review.completed_at = None
            return {"samples": rows, "infant_id": infant_id}

    def save_distance_sample(
        self,
        individual_id: str,
        offset_ms: int,
        *,
        reference_frame_id: str | None = None,
        skip_reason: str | None = None,
        behavior_ids: list[str] | None = None,
        infant_id: str | None = None,
        create_infant: bool = False,
        creation_offset_ms: int | float | None = None,
        labeled_by: str,
    ) -> dict:
        """Confirm or replace a sample, including its carried infant, atomically.

        Replacing a carrier's sample removes its previous linked infant sample;
        standalone infant samples at other offsets are preserved.
        """
        with self.Session.begin() as session:
            return self._save_distance_sample(
                session,
                individual_id,
                offset_ms,
                reference_frame_id=reference_frame_id,
                skip_reason=skip_reason,
                behavior_ids=behavior_ids,
                infant_id=infant_id,
                create_infant=create_infant,
                creation_offset_ms=creation_offset_ms
                if creation_offset_ms is not None
                else offset_ms,
                labeled_by=labeled_by,
            )

    def _save_distance_sample(self, session, individual_id, offset_ms, **choices) -> dict:
        return save_distance_sample(self._utcnow_dt, session, individual_id, offset_ms, **choices)

    def finish_distance_individual(self, individual_id: str, annotator_name: str) -> None:
        with self.Session.begin() as session:
            finish_individual(session, individual_id, annotator_name)

    def finish_distance_video(self, video_id: str, annotator_name: str) -> None:
        with self.Session.begin() as session:
            finish_video(session, video_id, annotator_name, self._utcnow_dt())
