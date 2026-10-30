"""Import AI output as pending Distance samples, never as human answers."""

from __future__ import annotations

import math
from pathlib import PurePosixPath

from review_app.backend.camera_layout import deployment_key
from review_app.backend.db.models import (
    DistanceImport,
    DistanceIndividual,
    DistanceReviewTask,
    DistanceSample,
    Project,
    ProjectDir,
    ReferenceFrame,
    ReferenceVideo,
    Species,
    Video,
)
from review_app.backend.distance_ai_package import mask_bounding_box, read_distance_ai_package
from review_app.backend.distance_timing import infer_imported_schedule
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.base import ProviderBase
from review_app.backend.provider.distance_references import reference_tagged
from review_app.backend.provider.distance_review import reset_video_evidence
from review_app.backend.provider.distance_tasks import ensure_tasks


def _plan(session, project_id, package):
    if not project_id or session.get(Project, project_id) is None:
        raise ValueError("Choose an existing project before importing AI Distance data")
    existing = (
        session.query(DistanceImport)
        .filter_by(project_id=project_id, content_hash=package.package_hash)
        .first()
    )
    summary = dict(
        references=len(package.references),
        individuals=len(package.individuals),
        predictions=len(package.predictions),
        matched_videos=0,
        scheduled_samples=0,
        inferred_absences=0,
        sampling=[],
        new_reference_frames=0,
        reused_reference_frames=0,
        already_imported=existing is not None,
        import_id=existing.id if existing else None,
        errors=[],
        warnings=[],
        replace_video_ids=[],
    )
    # A retry is a true no-op, including after human corrections/deletions or later imports.
    if existing:
        return summary, {}, {}, {}
    errors = summary["errors"]
    roots = [
        (row.id, normalize_path_str(row.path))
        for row in session.query(ProjectDir).filter_by(project_id=project_id)
    ]
    lookup = {}
    for video in session.query(Video).filter_by(project_id=project_id, is_missing=False):
        stored = PurePosixPath(normalize_path_str(video.video_path))
        paths = {str(stored)} if not stored.is_absolute() and ":" not in str(stored) else set()
        for _, root in roots:
            try:
                paths.add(str(stored.relative_to(PurePosixPath(root))))
            except ValueError:
                pass
        for path in paths:
            lookup.setdefault(path, {})[video.video_id] = video
    videos = {}
    for path in sorted({r["video_path"] for r in package.references + package.individuals}):
        matches = lookup.get(path, {})
        if len(matches) != 1:
            errors.append(f"{'Ambiguous' if matches else 'Unmatched'} video_path: {path}")
        else:
            videos[path] = next(iter(matches.values()))
    summary["matched_videos"] = len({video.video_id for video in videos.values()})
    tagged = {
        vid
        for (vid,) in session.query(Video.video_id).filter(
            Video.project_id == project_id, reference_tagged(session)
        )
    }
    frames, keys = {}, set()
    for row in package.references:
        video = videos.get(row["video_path"])
        if video is None:
            continue
        if video.video_id not in tagged:
            errors.append(f"Tag the reference clip as Reference Video: {row['video_path']}")
        if not video.camera_id or not video.camera_id.strip():
            errors.append(f"Resolve the reference clip's camera: {row['video_path']}")
        _check_time(errors, video, row)
        precise = row["time_s"] * 1000
        if not math.isfinite(precise) or precise >= 2**63 - 1:
            errors.append("Reference time_s exceeds the supported playback range")
            continue
        offset = math.floor(precise + 0.5)
        key = video.video_id, offset
        if key in keys:
            errors.append(
                f"Multiple reference IDs identify the same millisecond frame: {row['reference_id']}"
            )
        keys.add(key)
        source = (
            session.query(ReferenceVideo)
            .filter_by(project_id=project_id, video_path=normalize_path_str(video.video_path))
            .first()
        )
        if source and source.camera_id != video.camera_id:
            errors.append(f"Reference camera mismatch; refresh references: {row['video_path']}")
        frame = (
            session.query(ReferenceFrame)
            .filter_by(reference_video_id=source.id, offset_ms=offset)
            .first()
            if source
            else None
        )
        if frame:
            position = (
                frame.playback_offset_ms
                if frame.playback_offset_ms is not None
                else frame.offset_ms
            )
            if frame.distance_m != row["known_distance_m"] or not math.isclose(
                position, precise, rel_tol=0, abs_tol=0.000001
            ):
                errors.append(
                    f"Conflicting prepared reference frame: {row['reference_id']}; existing calibration is unchanged"
                )
            summary["reused_reference_frames"] += 1
        else:
            summary["new_reference_frames"] += 1
        frames[row["reference_id"]] = frame
    ensure_tasks(session, project_id)
    species = {row.scientific_name: row.id for row in session.query(Species)}
    tasks = {
        (task.video_id, task.species_id): task
        for task in session.query(DistanceReviewTask)
        .join(Video, DistanceReviewTask.video_id == Video.video_id)
        .filter(Video.project_id == project_id)
    }
    tracks = {row["individual_id"]: row for row in package.individuals}
    references = {row["reference_id"]: row for row in package.references}
    schedules = {}
    for row in package.individuals:
        if row["species"] not in species:
            errors.append(f"Add/map species {row['species']!r} before importing.")
        video = videos.get(row["video_path"])
        if video:
            if (video.video_id, species.get(row["species"])) not in tasks:
                errors.append(
                    f"Confirm Species Review labels for {row['species']} in {row['video_path']} before importing."
                )
            schedules.setdefault(video.video_id, set())
            if video.video_id in tagged:
                errors.append(f"Animal track points to a Reference Video: {row['video_path']}")
            if deployment_key(project_id, video.camera_id, video.video_path, roots) is None:
                errors.append(f"Resolve the animal video's deployment: {row['video_path']}")
        if not any(p["individual_id"] == row["individual_id"] for p in package.predictions):
            errors.append(f"Individual {row['individual_id']} has no predictions.")
    for row in package.predictions:
        video = videos.get(tracks[row["individual_id"]]["video_path"])
        if video is None:
            continue
        _check_time(errors, video, row)
        schedules[video.video_id].add(row["time_s"])
        reference = references.get(row["suggested_reference_id"])
        ref_video = videos.get(reference["video_path"]) if reference else None
        if ref_video and deployment_key(
            project_id, video.camera_id, video.video_path, roots
        ) != deployment_key(project_id, ref_video.camera_id, ref_video.video_path, roots):
            errors.append(
                f"Suggested reference is from another deployment: {row['prediction_id']}"
            )
    for video_id, times in schedules.items():
        video = session.get(Video, video_id)
        try:
            track_times = [
                [
                    p["time_s"]
                    for p in package.predictions
                    if p["individual_id"] == row["individual_id"]
                ]
                for row in package.individuals
                if videos.get(row["video_path"]) is video
            ]
            full_times, cadence = infer_imported_schedule(track_times, video.duration_sec)
            schedules[video_id] = full_times
            sample_count = len(full_times) * len(track_times)
            summary["scheduled_samples"] += sample_count
            summary["inferred_absences"] += sample_count - sum(map(len, track_times))
            summary["sampling"].append(
                {
                    "video_path": video.video_path,
                    "cadence_s": cadence,
                    "samples": len(full_times),
                }
            )
            if summary["scheduled_samples"] > 100_000:
                errors.append("Package would create more than 100,000 review samples")
        except ValueError as exc:
            errors.append(f"{video.video_path}: {exc}")
        target_species = {
            species.get(row["species"])
            for row in package.individuals
            if videos.get(row["video_path"]) is video
        }
        reviews = [
            task for (vid, sid), task in tasks.items() if vid == video_id and sid in target_species
        ]
        for review in reviews:
            individuals = session.query(DistanceIndividual).filter_by(task_id=review.id).all()
            if (
                review.verified_at is None
                and not individuals
                and review.started_at is None
                and review.completed_at is None
            ):
                continue
            if (
                not review
                or review.alignment_mode != "imported"
                or not review.import_id
                or review.started_at
                or review.completed_at
                or any(row.import_id != review.import_id or row.labeled_at for row in individuals)
                or session.query(DistanceSample.id)
                .join(DistanceIndividual, DistanceSample.individual_id == DistanceIndividual.id)
                .filter(
                    DistanceIndividual.task_id == review.id, DistanceSample.status != "pending"
                )
                .first()
            ):
                errors.append(
                    f"Distance review already started or manually configured: {video.video_path}. Import cannot overwrite it."
                )
            else:
                summary["replace_video_ids"].append(video_id)
    summary["errors"] = list(dict.fromkeys(errors))
    return summary, videos, frames, {vid: sorted(times) for vid, times in schedules.items()}


def _check_time(errors, video, row):
    duration = video.duration_sec
    if duration is None or not math.isfinite(duration) or duration <= 0:
        errors.append(
            f"Video duration is unknown; rescan/probe before importing: {video.video_path}"
        )
    elif row["time_s"] >= duration:
        errors.append("time_s is outside the video duration")


class DistanceAIImportMixin(ProviderBase):
    def preview_distance_ai_package(self, project_id: str, zip_bytes: bytes) -> dict:
        package = read_distance_ai_package(zip_bytes)
        with self.Session() as session:
            summary, _, _, _ = _plan(session, project_id, package)
        return {
            **summary,
            "manifest": package.manifest,
            "preview": [
                {key: value for key, value in row.items() if key != "mask"}
                for row in package.predictions[:20]
            ],
        }

    def import_distance_ai_package(
        self, project_id: str, zip_bytes: bytes, *, replace_video_ids: list[str] | None = None
    ) -> dict:
        package = read_distance_ai_package(zip_bytes)
        with self.Session.begin() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            summary, videos, frames, schedules = _plan(session, project_id, package)
            if summary["errors"]:
                raise ValueError("\n".join(summary["errors"][:20]))
            if summary["already_imported"]:
                return summary
            if not set(summary["replace_video_ids"]).issubset(set(replace_video_ids or [])):
                raise ValueError(
                    "Preview again and explicitly confirm replacement of untouched AI drafts."
                )
            imported = DistanceImport(
                project_id=project_id,
                content_hash=package.package_hash,
                manifest=package.manifest,
                payload={},
            )
            session.add(imported)
            session.flush()
            refs = []
            for row in package.references:
                frame = frames[row["reference_id"]]
                if frame is None:
                    video = videos[row["video_path"]]
                    source = (
                        session.query(ReferenceVideo)
                        .filter_by(
                            project_id=project_id, video_path=normalize_path_str(video.video_path)
                        )
                        .first()
                    )
                    if source is None:
                        source = ReferenceVideo(
                            project_id=project_id,
                            camera_id=video.camera_id,
                            video_path=normalize_path_str(video.video_path),
                        )
                        session.add(source)
                        session.flush()
                    frame = ReferenceFrame(
                        reference_video_id=source.id,
                        offset_ms=math.floor(row["time_s"] * 1000 + 0.5),
                        playback_offset_ms=row["time_s"] * 1000,
                        distance_m=row["known_distance_m"],
                        mask=row["mask"],
                        import_id=imported.id,
                    )
                    session.add(frame)
                    session.flush()
                    frames[row["reference_id"]] = frame
                refs.append({**row, "reference_frame_id": frame.id})
            species = {row.scientific_name: row.id for row in session.query(Species)}
            tasks = {}
            for row in package.individuals:
                video_id = videos[row["video_path"]].video_id
                key = video_id, species[row["species"]]
                if key in tasks:
                    continue
                review = (
                    session.query(DistanceReviewTask)
                    .filter_by(video_id=video_id, species_id=key[1])
                    .one()
                )
                tasks[key] = review
                # Only untouched imported drafts can reach this branch; never human work.
                if review.import_id:
                    reset_video_evidence(session, review.id)
                review.started_at = None
                review.alignment_mode = "imported"
                review.imported_sample_times_s = schedules[video_id]
                review.import_id = imported.id
                review.verified_by = f"AI {package.manifest['model']['name']}"
                review.verified_at = self._utcnow_dt()
            identities = []
            for row in package.individuals:
                predictions = sorted(
                    (p for p in package.predictions if p["individual_id"] == row["individual_id"]),
                    key=lambda p: p["time_s"],
                )
                identifying, box = predictions[0], None
                for prediction in predictions:
                    candidate = (
                        mask_bounding_box(prediction["mask"]) if prediction["mask"] else None
                    )
                    if candidate:
                        identifying, box = prediction, candidate
                        break
                video_id = videos[row["video_path"]].video_id
                task = tasks[(video_id, species[row["species"]])]
                number = session.query(DistanceIndividual).filter_by(task_id=task.id).count() + 1
                individual = DistanceIndividual(
                    video_id=video_id,
                    task_id=task.id,
                    individual_number=number,
                    species_id=species[row["species"]],
                    import_id=imported.id,
                    ai_original=row,
                    # Proposed identifying position, NOT absence/first-sighting evidence.
                    first_seen_offset_ms=math.floor(identifying["time_s"] * 1000),
                    first_seen_playback_ms=identifying["time_s"] * 1000,
                    **{f"anchor_{key}": value for key, value in (box or {}).items()},
                )
                session.add(individual)
                session.flush()
                identities.append({**row, "distance_individual_id": individual.id})
                by_time = {prediction["time_s"]: prediction for prediction in predictions}
                for time_s in schedules[video_id]:
                    prediction = by_time.get(time_s)
                    if prediction is not None:
                        suggested = frames.get(prediction["suggested_reference_id"])
                        original = {
                            **prediction,
                            "reference_frame_id": suggested.id if suggested else None,
                        }
                    else:
                        original = {
                            "time_s": time_s,
                            "skip_reason": "individual absent",
                            "inferred": True,
                        }
                    session.add(
                        DistanceSample(
                            individual_id=individual.id,
                            offset_ms=math.floor(time_s * 1000 + 0.5),
                            status="pending",
                            ai_original=original,
                        )
                    )
            imported.payload = {
                "references": refs,
                "individuals": identities,
                "predictions": package.predictions,
                "sampling": summary["sampling"],
            }
            summary["import_id"] = imported.id
        return summary

    def list_distance_imports(self, project_id: str) -> list[dict]:
        with self.Session() as session:
            return [
                {
                    "id": row.id,
                    "manifest": row.manifest,
                    "imported_at": row.imported_at.isoformat(),
                }
                for row in session.query(DistanceImport)
                .filter_by(project_id=project_id)
                .order_by(DistanceImport.imported_at.desc())
            ]

    def get_distance_import(self, project_id: str, import_id: str) -> dict:
        with self.Session() as session:
            row = (
                session.query(DistanceImport)
                .filter_by(id=import_id, project_id=project_id)
                .one_or_none()
            )
            if row is None:
                raise ValueError("Distance import not found in this project")
            return {"manifest": row.manifest, **row.payload}
