"""AI package imports preserve source-frame proposals and independent human reviews."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.app.pages.model_import._distance_ai_tab import setup_distance_ai_tab
from review_app.app.translations import t
from review_app.backend.db.models import (
    Base,
    DistanceImport,
    DistanceSample,
    ReferenceFrame,
    Video,
)
from review_app.backend.distance_ai_package import (
    COLUMNS,
    mask_bounding_box,
    read_distance_ai_package,
)
from sqlalchemy import event

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def ai_package(distance_data):
    dp = distance_data["dp"]
    project = distance_data["project"]
    root = Path(distance_data["directory"].path)
    # Keep unrelated human evidence on the other video; the imported video starts fresh.
    dp.delete_distance_individual(distance_data["individual"]["id"])
    from review_app.backend.db.models import DistanceReviewTask

    with dp.Session.begin() as session:
        session.query(DistanceReviewTask).filter_by(video_id=distance_data["videos"][0]).delete()
    distance_data["individual"] = dp.create_distance_individual(
        distance_data["videos"][1],
        distance_data["species_id"],
        500,
        anchor=distance_data["anchor"],
        labeled_by="alice",
    )
    with dp.Session.begin() as session:
        animal = session.get(Video, distance_data["videos"][0])
        animal_path = str(Path(animal.video_path).relative_to(root))
        session.add(
            Video(
                video_id="ai-reference-video",
                project_id=project.id,
                camera_id="cam_x",
                video_path=str(root / "cam_x" / "patrol.mp4"),
                duration_sec=10,
            )
        )
    dp.set_video_tags("ai-reference-video", ["reference_video"])
    return {
        "manifest.json": {
            "format": "distance-ai",
            "version": 1,
            "source_id": "test-pipeline",
            "run_id": "run-1",
            "model": {"name": "test-model", "version": None},
            "timing_basis": "video_playback_seconds",
        },
        "reference_frames.csv": [
            {
                "reference_id": "ref-1",
                "video_path": "cam_x/patrol.mp4",
                "time_s": "2.125125000",
                "known_distance_m": "3.5",
                "mask_path": "masks/ref.json",
            }
        ],
        "individuals.csv": [
            {"individual_id": "track-1", "video_path": animal_path, "species": "deer"}
        ],
        "predictions.csv": [
            {
                "prediction_id": "prediction-1",
                "individual_id": "track-1",
                "time_s": "1.250000001",
                "predicted_distance_m": "2.696",
                "suggested_reference_id": "ref-1",
                "mask_path": "masks/animal.json",
            },
            {
                "prediction_id": "prediction-2",
                "individual_id": "track-1",
                "time_s": "2.250000001",
                "predicted_distance_m": "3.1",
                "suggested_reference_id": "ref-1",
                "mask_path": "masks/animal.json",
            },
        ],
        "masks/ref.json": {"size": [2, 3], "counts": [0, 2, 4]},
        "masks/animal.json": {"size": [2, 3], "counts": "024"},
    }


def package_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            if name.endswith(".csv"):
                out = io.StringIO(newline="")
                writer = csv.DictWriter(out, fieldnames=COLUMNS[name])
                writer.writeheader()
                writer.writerows(value)
                data = out.getvalue()
            elif isinstance(value, (dict, list)):
                data = json.dumps(value)
            else:
                data = value
            archive.writestr(name, data)
    return buffer.getvalue()


def snapshot(dp, tables=None):
    with dp.engine.connect() as conn:
        return {
            table: conn.exec_driver_sql(f'SELECT * FROM "{table}"').all()
            for table in tables or Base.metadata.tables
        }


def imported_state(distance_data):
    return distance_data["dp"].get_distance_review_state(distance_data["videos"][0], "alice")


def imported_sample(distance_data, offset_ms=1250):
    return next(
        row for row in imported_state(distance_data)["samples"] if row["offset_ms"] == offset_ms
    )


def test_preview_and_import_create_pending_samples_with_provenance(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    before = snapshot(dp)
    data = package_zip(ai_package)
    preview = dp.preview_distance_ai_package(project_id, data)
    assert snapshot(dp) == before
    assert preview["errors"] == [] and preview["matched_videos"] == 2
    assert preview["new_reference_frames"] == 1
    assert preview["preview"][0]["time_s"] == 1.250000001
    result = dp.import_distance_ai_package(project_id, data)
    saved = dp.get_distance_import(project_id, result["import_id"])
    assert saved["manifest"] == ai_package["manifest.json"]
    prediction = saved["predictions"][0]
    assert prediction["predicted_distance_m"] == 2.696
    assert prediction["mask"] == ai_package["masks/animal.json"]
    assert dp.list_distance_imports(project_id)[0]["id"] == result["import_id"]
    state = imported_state(distance_data)
    assert state["offsets"] == [0, 1250, 2250, 3250, 4250, 5250, 6250, 7250, 8250, 9250]
    assert preview["scheduled_samples"] == 10
    assert preview["inferred_absences"] == 8
    assert state["review"]["alignment_mode"] == "imported"
    assert 1.250000001 in state["review"]["imported_sample_times_s"]
    assert state["review"]["started_at"] is None
    assert state["review"]["completed_at"] is None
    assert state["review"]["recorded_start"] is None
    sample = imported_sample(distance_data)
    assert sample["status"] == "pending"
    assert sample["ai_original"]["time_s"] == 1.250000001
    assert sample["ai_original"]["predicted_distance_m"] == 2.696
    assert all(
        sample[key] is None
        for key in (
            "distance_m",
            "reference_frame_id",
            "skip_reason",
            "labeled_at",
            "labeled_by",
        )
    )
    assert sample["behavior_ids"] == []
    assert sample["suggestion"]["distance_m"] == 3.5
    identity = dp.list_distance_individuals(distance_data["videos"][0])[0]
    assert identity["review_status"] == "not_started"
    assert identity["first_seen_playback_ms"] == pytest.approx(1250.000001)
    assert identity["ai_original"]["individual_id"] == "track-1"
    assert identity["age"] is None and identity["sex"] is None
    with dp.Session() as session:
        frame = session.get(ReferenceFrame, saved["references"][0]["reference_frame_id"])
        assert frame.offset_ms == 2125 and frame.playback_offset_ms == 2125.125
        assert frame.mask == ai_package["masks/ref.json"]
        assert frame.import_id == result["import_id"]
    for table in ("individual_observations", "video_labels", "video_tags", "model_annotations"):
        assert snapshot(dp, [table])[table] == before[table]


def test_ai_import_cannot_overwrite_absence_without_alignment(distance_data, ai_package):
    dp = distance_data["dp"]
    dp.record_distance_task_absence(distance_data["videos"][0], "alice")
    before = imported_state(distance_data)
    package = package_zip(ai_package)
    assert dp.preview_distance_ai_package(distance_data["project"].id, package)["errors"]
    with pytest.raises(ValueError, match="cannot overwrite"):
        dp.import_distance_ai_package(distance_data["project"].id, package)
    assert imported_state(distance_data) == before


def test_absence_requires_explicit_ai_rejection_and_retains_provenance(distance_data, ai_package):
    dp, project = distance_data["dp"], distance_data["project"]
    files = ai_package
    dp.import_distance_ai_package(project.id, package_zip(files))
    video_id = distance_data["videos"][0]
    state = dp.get_distance_review_state(video_id, "alice")
    task_id = state["task"]["id"]
    with pytest.raises(ValueError, match="Explicitly reject"):
        dp.record_distance_task_absence(task_id, "alice")
    assert dp.get_distance_review_state(task_id, "alice") == state
    dp.record_distance_task_absence(task_id, "alice", reject_ai=True)
    after = dp.get_distance_review_state(task_id, "alice")
    assert after["samples"] == []
    assert dp.list_distance_individuals(task_id) == []
    assert after["task"]["completion_outcome"] == "absent"
    assert after["task"]["rejected_ai"][0]["original"]
    assert after["task"]["rejected_ai"][0]["author"] == "alice"
    assert dp.list_distance_imports(project.id)


def test_pending_samples_do_not_count_as_review_or_graph_answers(distance_data, ai_package):
    from review_app.app.pages.distance_review.tracks import track_options

    dp, video_id = distance_data["dp"], distance_data["videos"][0]
    dp.import_distance_ai_package(distance_data["project"].id, package_zip(ai_package))
    state = imported_state(distance_data)
    identity = dp.list_distance_individuals(video_id)[0]
    with pytest.raises(ValueError, match="every scheduled sample"):
        dp.finish_distance_individual(identity["id"], "alice")
    with pytest.raises(ValueError, match="every scheduled sample"):
        dp.finish_distance_video(video_id, "alice")
    page = DistanceReviewPage.__new__(DistanceReviewPage)
    page.samples, page.individual_id = state["samples"], identity["id"]
    page.offsets = state["offsets"]
    assert page.reviewed_offsets() == set()
    assert page.sample_status_label(1250) == t("distance_unreviewed")
    options = track_options([identity], state["samples"], state["offsets"], 10, identity["id"])
    assert options["series"][0]["data"] == [[offset / 1000, None] for offset in state["offsets"]]
    assert options["series"][1]["data"] == []
    assert len(options["series"][2]["data"]) == len(state["offsets"])
    assert dp.list_distance_individuals(video_id)[0]["review_status"] == "not_started"


def test_confirmation_preserves_original_ai_values(distance_data, ai_package):
    dp, video_id = distance_data["dp"], distance_data["videos"][0]
    dp.import_distance_ai_package(distance_data["project"].id, package_zip(ai_package))
    sample = imported_sample(distance_data)
    identity = dp.list_distance_individuals(video_id)[0]
    dp.save_distance_sample(
        identity["id"],
        1250,
        reference_frame_id=sample["suggestion"]["reference_frame_id"],
        labeled_by="alice",
    )
    confirmed = imported_sample(distance_data)
    assert confirmed["id"] == sample["id"]
    assert confirmed["status"] == "annotated" and confirmed["distance_m"] == 3.5
    assert confirmed["labeled_by"] == "alice" and confirmed["labeled_at"] is not None
    assert confirmed["ai_original"] == sample["ai_original"]
    assert confirmed["ai_original"]["predicted_distance_m"] == 2.696
    assert imported_state(distance_data)["review"]["started_at"] is not None
    assert dp.list_distance_individuals(video_id)[0]["ai_original"] == identity["ai_original"]


def test_exact_times_survive_import_and_seeking(distance_data, ai_package):
    ai_package["predictions.csv"].append(
        dict(
            ai_package["predictions.csv"][0],
            prediction_id="later",
            time_s="4.123456789",
        )
    )
    dp = distance_data["dp"]
    dp.import_distance_ai_package(distance_data["project"].id, package_zip(ai_package))
    state = imported_state(distance_data)
    assert {1250, 2250, 4123}.issubset(state["offsets"])
    assert state["offsets"][-1] == 9250
    page = DistanceReviewPage.__new__(DistanceReviewPage)
    page.queue, page.index = [{"video_id": distance_data["videos"][0], "duration_sec": 10}], 0
    page.review, page.animal_frame_rate = state["review"], 24
    for offset, precise in [(1250, 1250.000001), (4123, 4123.456789)]:
        assert page.sample_frame_offset(offset) == pytest.approx(precise, abs=1e-7)
        assert page.sample_seek_offset(offset) == pytest.approx(precise, abs=1e-7)


def test_repeat_import_is_true_noop_even_after_edits_and_deletions(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    first = dp.import_distance_ai_package(project_id, package_zip(ai_package))
    identity = dp.list_distance_individuals(distance_data["videos"][0])[0]
    dp.set_distance_individual_attributes(
        identity["id"],
        age="Adult",
        sex="Female",
        tag_ids=[],
        labeled_by="alice",
    )
    saved = dp.get_distance_import(project_id, first["import_id"])
    dp.delete_reference_frame(saved["references"][0]["reference_frame_id"])
    dp.delete_distance_individual(identity["id"])
    before = snapshot(dp)
    ai_package["README.md"] = "Documentation and ZIP compression do not affect identity."
    retry = dp.import_distance_ai_package(project_id, package_zip(ai_package))
    assert retry["already_imported"] and retry["import_id"] == first["import_id"]
    assert snapshot(dp) == before


def test_only_untouched_drafts_can_be_replaced_and_old_import_stays_noop(
    distance_data, ai_package
):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    video_id = distance_data["videos"][0]
    original = package_zip(ai_package)
    first = dp.import_distance_ai_package(project_id, original)
    old_id = dp.list_distance_individuals(video_id)[0]["id"]
    ai_package["predictions.csv"][0].update(time_s="3.75", predicted_distance_m="9.1")
    changed = package_zip(ai_package)
    preview = dp.preview_distance_ai_package(project_id, changed)
    assert preview["errors"] == [] and preview["replace_video_ids"] == [video_id]
    before = snapshot(dp)
    for ids in (None, ["another-video"]):
        with pytest.raises(ValueError, match="confirm replacement"):
            dp.import_distance_ai_package(project_id, changed, replace_video_ids=ids)
        assert snapshot(dp) == before
    second = dp.import_distance_ai_package(project_id, changed, replace_video_ids=[video_id])
    assert second["reused_reference_frames"] == 1
    assert dp.list_distance_individuals(video_id)[0]["id"] != old_id
    assert 3750 in imported_state(distance_data)["offsets"]
    assert 1250 not in imported_state(distance_data)["offsets"]
    assert imported_state(distance_data)["samples"][0]["status"] == "pending"
    assert (
        dp.get_distance_import(project_id, first["import_id"])["predictions"][0][
            "predicted_distance_m"
        ]
        == 2.696
    )
    before = snapshot(dp)
    assert dp.import_distance_ai_package(project_id, original)["already_imported"]
    assert snapshot(dp) == before


@pytest.mark.parametrize("edit", ["attributes", "anchor", "sample", "completed", "manual"])
def test_human_work_cannot_be_overwritten_even_with_confirmation(distance_data, ai_package, edit):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    video_id = distance_data["videos"][0]
    if edit == "manual":
        dp.verify_distance_recording_time(video_id, datetime(2024, 1, 1), "alice")
    else:
        dp.import_distance_ai_package(project_id, package_zip(ai_package))
        identity = dp.list_distance_individuals(video_id)[0]
        if edit in ("attributes", "completed"):
            dp.set_distance_individual_attributes(
                identity["id"],
                age="Adult",
                sex="Unknown",
                tag_ids=[],
                labeled_by="alice",
            )
        if edit == "anchor":
            dp.set_distance_individual_anchor(
                identity["id"],
                1250,
                anchor=distance_data["anchor"],
                labeled_by="alice",
            )
        if edit in ("sample", "completed"):
            frame_id = imported_sample(distance_data)["suggestion"]["reference_frame_id"]
            dp.save_distance_sample(
                identity["id"], 1250, reference_frame_id=frame_id, labeled_by="alice"
            )
        if edit == "completed":
            for offset in imported_state(distance_data)["offsets"]:
                if offset != 1250:
                    dp.save_distance_sample(
                        identity["id"], offset, skip_reason="individual absent", labeled_by="alice"
                    )
            dp.finish_distance_video(video_id, "alice")
    ai_package["manifest.json"]["run_id"] = "changed"
    before = snapshot(dp)
    assert dp.preview_distance_ai_package(project_id, package_zip(ai_package))["errors"]
    with pytest.raises(ValueError, match="cannot overwrite"):
        dp.import_distance_ai_package(
            project_id, package_zip(ai_package), replace_video_ids=[video_id]
        )
    assert snapshot(dp) == before


def test_import_rechecks_human_edits_after_preview(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    dp.import_distance_ai_package(project_id, package_zip(ai_package))
    ai_package["manifest.json"]["run_id"] = "new"
    data = package_zip(ai_package)
    assert dp.preview_distance_ai_package(project_id, data)["replace_video_ids"]
    identity = dp.list_distance_individuals(distance_data["videos"][0])[0]
    dp.set_distance_individual_attributes(
        identity["id"],
        age="Adult",
        sex="Unknown",
        tag_ids=[],
        labeled_by="bob",
    )
    before = snapshot(dp)
    with pytest.raises(ValueError, match="cannot overwrite"):
        dp.import_distance_ai_package(project_id, data, replace_video_ids=distance_data["videos"])
    assert snapshot(dp) == before


def test_failure_after_draft_replacement_rolls_back_atomically(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    dp.import_distance_ai_package(project_id, package_zip(ai_package))
    ai_package["manifest.json"]["run_id"] = "new"
    before = snapshot(dp)

    def fail(*args):
        raise RuntimeError("interrupted pending insert")

    event.listen(DistanceSample, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            dp.import_distance_ai_package(
                project_id,
                package_zip(ai_package),
                replace_video_ids=distance_data["videos"],
            )
    finally:
        event.remove(DistanceSample, "before_insert", fail)
    assert snapshot(dp) == before


def test_project_scoping_and_delete_cascades(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    result = dp.import_distance_ai_package(project_id, package_zip(ai_package))
    other = dp.create_project("Other", "/other")
    assert dp.list_distance_imports(other.id) == []
    with pytest.raises(ValueError, match="not found"):
        dp.get_distance_import(other.id, result["import_id"])
    assert dp.preview_distance_ai_package(other.id, package_zip(ai_package))["errors"]
    dp.delete_project(project_id)
    with dp.Session() as session:
        assert session.query(DistanceImport).count() == 0
        assert session.query(DistanceSample).count() == 0


def test_missing_species_requires_mapping_before_import(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    ai_package["individuals.csv"][0]["species"] = "Unknown scientific species"
    before = snapshot(dp)
    assert (
        "Add/map species"
        in dp.preview_distance_ai_package(project_id, package_zip(ai_package))["errors"][0]
    )
    with pytest.raises(ValueError, match="Add/map species"):
        dp.import_distance_ai_package(project_id, package_zip(ai_package))
    assert snapshot(dp) == before


@pytest.mark.parametrize("mask", [None, {"size": [2, 3], "counts": [6]}])
def test_missing_usable_mask_keeps_pending_individual_without_invented_box(
    distance_data, ai_package, mask
):
    if mask is None:
        for row in ai_package["predictions.csv"]:
            row["mask_path"] = ""
    else:
        ai_package["masks/animal.json"] = mask
    dp = distance_data["dp"]
    dp.import_distance_ai_package(distance_data["project"].id, package_zip(ai_package))
    identity = dp.list_distance_individuals(distance_data["videos"][0])[0]
    assert all(identity[f"anchor_{key}"] is None for key in ("x", "y", "width", "height"))
    assert imported_state(distance_data)["samples"][0]["status"] == "pending"


@pytest.mark.parametrize("suggested,expected", [("", 7), ("ref-1", 3.5)])
def test_suggestion_uses_reference_or_nearest_distance(
    distance_data, ai_package, suggested, expected
):
    ai_package["reference_frames.csv"].append(
        dict(
            ai_package["reference_frames.csv"][0],
            reference_id="ref-2",
            time_s="4.5",
            known_distance_m="7",
        )
    )
    ai_package["predictions.csv"][0].update(
        predicted_distance_m="6.9", suggested_reference_id=suggested
    )
    distance_data["dp"].import_distance_ai_package(
        distance_data["project"].id, package_zip(ai_package)
    )
    sample = imported_sample(distance_data)
    assert sample["suggestion"]["distance_m"] == expected
    assert sample["ai_original"]["predicted_distance_m"] == 6.9
    assert sample["distance_m"] is None


async def test_upload_preview_import_and_saved_package_ui(user: User, distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id

    @ui.page("/test-ai-import")
    async def page():
        app.storage.user["active_project_id"] = project_id
        await setup_distance_ai_tab(dp, ui.dialog())

    await user.open("/test-ai-import")
    upload = next(e for e in user.client.elements.values() if isinstance(e, ui.upload))
    with user:
        await upload.handle_uploads(
            [SmallFileUpload("proposals.zip", "application/zip", package_zip(ai_package))]
        )
    await user.should_see(t("distance_ai_import"))
    assert dp.list_distance_imports(project_id) == []
    user.find(t("distance_ai_import"), kind=ui.button).click()
    await user.should_see(t("distance_ai_imported"))
    assert len(dp.list_distance_imports(project_id)) == 1
    user.find(t("distance_ai_view_run"), kind=ui.button).click()
    await user.should_see(t("distance_ai_saved_preview"))
    with user:
        await upload.handle_uploads([SmallFileUpload("broken.zip", "application/zip", b"broken")])
    await user.should_see("Invalid or unsupported AI Distance ZIP")
    await user.should_not_see(t("distance_ai_import"))
    assert len(dp.list_distance_imports(project_id)) == 1


async def test_ui_requires_draft_replacement_confirmation(user: User, distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    dp.import_distance_ai_package(project_id, package_zip(ai_package))
    ai_package["manifest.json"]["run_id"] = "new"

    @ui.page("/test-ai-replace")
    async def page():
        app.storage.user["active_project_id"] = project_id
        await setup_distance_ai_tab(dp, ui.dialog())

    await user.open("/test-ai-replace")
    upload = next(e for e in user.client.elements.values() if isinstance(e, ui.upload))
    with user:
        await upload.handle_uploads(
            [SmallFileUpload("proposals.zip", "application/zip", package_zip(ai_package))]
        )
    await user.should_see(t("distance_ai_replace_warning"))
    assert all(not e.enabled for e in user.find(t("distance_ai_import"), kind=ui.button).elements)
    user.find(t("distance_ai_replace_confirm"), kind=ui.checkbox).click()
    for _ in range(50):
        if all(e.enabled for e in user.find(t("distance_ai_import"), kind=ui.button).elements):
            break
        await asyncio.sleep(0.01)
    user.find(t("distance_ai_import"), kind=ui.button).click()
    await user.should_see(t("distance_ai_imported"))
    assert len(dp.list_distance_imports(project_id)) == 2


def test_import_revalidates_calibration_after_preview(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    data = package_zip(ai_package)
    assert dp.preview_distance_ai_package(project_id, data)["errors"] == []
    reference = dp.register_reference_video(
        project_id, "cam_x", str(Path(distance_data["directory"].path) / "cam_x" / "patrol.mp4")
    )
    dp.add_reference_frame(reference["id"], 2125, 10, playback_offset_ms=2125.125)
    before = snapshot(dp)
    with pytest.raises(ValueError, match="Conflicting prepared reference"):
        dp.import_distance_ai_package(project_id, data)
    assert snapshot(dp) == before


def test_failed_write_rolls_back_references_and_run(distance_data, ai_package):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    before = snapshot(dp)

    def fail(*args):
        raise RuntimeError("test interrupted prediction insert")

    event.listen(DistanceSample, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            dp.import_distance_ai_package(project_id, package_zip(ai_package))
    finally:
        event.remove(DistanceSample, "before_insert", fail)
    assert snapshot(dp) == before


@pytest.mark.parametrize("problem", ["unmatched", "duration", "deployment", "untagged", "missing"])
def test_invalid_project_matches_cannot_partially_import(distance_data, ai_package, problem):
    dp, project_id = distance_data["dp"], distance_data["project"].id
    if problem == "unmatched":
        ai_package["individuals.csv"][0]["video_path"] = "cam_x/not-present.mp4"
    elif problem == "duration":
        ai_package["predictions.csv"][0]["time_s"] = "10.0"
    elif problem == "untagged":
        dp.set_video_tags("ai-reference-video", [])
    else:
        with dp.Session.begin() as session:
            video = session.get(Video, "ai-reference-video")
            if problem == "deployment":
                video.camera_id = "other-camera"
            else:
                video.is_missing = True
    before = snapshot(dp)
    assert dp.preview_distance_ai_package(project_id, package_zip(ai_package))["errors"]
    with pytest.raises(ValueError):
        dp.import_distance_ai_package(project_id, package_zip(ai_package))
    assert snapshot(dp) == before


@pytest.mark.parametrize(
    "problem",
    [
        "version",
        "timing",
        "negative_time",
        "nan_distance",
        "duplicate_prediction",
        "missing_track",
        "missing_reference",
        "missing_mask",
        "bad_rle",
        "unsafe_mask_path",
    ],
)
def test_malformed_packages_are_rejected_without_writes(distance_data, ai_package, problem):
    row = ai_package["predictions.csv"][0]
    if problem == "version":
        ai_package["manifest.json"]["version"] = 2
    elif problem == "timing":
        ai_package["manifest.json"]["timing_basis"] = "camera_timestamp"
    elif problem == "negative_time":
        row["time_s"] = "-1"
    elif problem == "nan_distance":
        row["predicted_distance_m"] = "NaN"
    elif problem == "duplicate_prediction":
        ai_package["predictions.csv"].append(dict(row))
    elif problem == "missing_track":
        row["individual_id"] = "missing"
    elif problem == "missing_reference":
        row["suggested_reference_id"] = "missing"
    elif problem == "missing_mask":
        del ai_package["masks/animal.json"]
    elif problem == "bad_rle":
        ai_package["masks/animal.json"]["counts"] = [2, 1]
    else:
        row["mask_path"] = "../mask.json"
    dp = distance_data["dp"]
    before = snapshot(dp)
    with pytest.raises(ValueError):
        dp.import_distance_ai_package(distance_data["project"].id, package_zip(ai_package))
    assert snapshot(dp) == before


@pytest.mark.parametrize(
    "counts,expected",
    [
        ([0, 2, 4], {"x": 0, "y": 0, "width": 1 / 3, "height": 1}),
        ([1, 3, 2], {"x": 0, "y": 0, "width": 2 / 3, "height": 1}),
        ("312", {"x": 1 / 3, "y": 0.5, "width": 1 / 3, "height": 0.5}),
        ([0, 6], {"x": 0, "y": 0, "width": 1, "height": 1}),
        ([6], None),
    ],
)
def test_mask_boxes_use_coco_column_major_pixel_extents(counts, expected):
    box = mask_bounding_box({"size": [2, 3], "counts": counts})
    assert box == pytest.approx(expected) if expected is not None else box is None


def test_zip_paths_and_corruption_are_rejected(ai_package):
    with pytest.raises(ValueError, match="ZIP"):
        read_distance_ai_package(b"not a zip")
    ai_package["../escape.json"] = {}
    with pytest.raises(ValueError, match="Unsafe"):
        read_distance_ai_package(package_zip(ai_package))


def test_missing_predictions_are_pending_absent_for_every_individual(distance_data, ai_package):
    ai_package["individuals.csv"].append(
        dict(ai_package["individuals.csv"][0], individual_id="track-2")
    )
    for row in list(ai_package["predictions.csv"]):
        ai_package["predictions.csv"].append(
            dict(
                row,
                individual_id="track-2",
                prediction_id="second-" + row["prediction_id"],
                time_s=str(float(row["time_s"]) + 3),
            )
        )
    dp = distance_data["dp"]
    dp.import_distance_ai_package(distance_data["project"].id, package_zip(ai_package))
    state = imported_state(distance_data)
    assert state["offsets"][0] == 0 and state["offsets"][-1] == 9250
    identities = dp.list_distance_individuals(distance_data["videos"][0])
    assert len(state["samples"]) == len(state["offsets"]) * len(identities)
    for individual in identities:
        rows = [r for r in state["samples"] if r["individual_id"] == individual["id"]]
        assert len([r for r in rows if r["ai_original"].get("prediction_id")]) == 2
        assert all(r["status"] == "pending" and r["labeled_at"] is None for r in rows)
        for row in rows:
            if row["ai_original"].get("inferred"):
                assert row["suggestion"]["skip_reason"] == "individual absent"
                assert row["reference_frame_id"] is None and row["distance_m"] is None
    first = state["samples"][0]
    dp.save_distance_sample(
        first["individual_id"],
        0,
        skip_reason="individual absent",
        labeled_by="alice",
    )
    confirmed = next(r for r in imported_state(distance_data)["samples"] if r["id"] == first["id"])
    assert confirmed["status"] == "skipped"
    assert confirmed["ai_original"] == first["ai_original"]
