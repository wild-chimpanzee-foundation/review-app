"""Non-destructive timestamp corrections and explicitly destructive realignment."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import clock_dialog as clock_module
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.backend.db.models import Base, DistanceVideoReview
from review_app.backend.distance_timing import sample_camera_time
from sqlalchemy import event, text

pytest_plugins = ["nicegui.testing.user_plugin"]
CAMERA_TIME = datetime(2026, 10, 12, 16, 2, 50)
ANCHOR = 920.125


def snapshot(dp):
    with dp.engine.connect() as connection:
        return {
            table.name: connection.execute(
                table.select().order_by(*table.primary_key.columns)
            ).all()
            for table in Base.metadata.sorted_tables
        }


async def wait_for_clock(page):
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not page.busy:
            return
    raise AssertionError("Clock update did not finish")


@pytest.fixture
def clock_data(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.calibrate_distance_clock(video_id, CAMERA_TIME, ANCHOR, "alice")
    dp.create_custom_tag("Clock test")
    dp.set_tag_individual_eligibility("clock_test", True)
    tag_id = next(tag["id"] for tag in dp.get_all_tags() if tag["key"] == "clock_test")
    carrier = dp.create_distance_individual(
        video_id,
        data["species_id"],
        0,
        anchor=data["anchor"],
        create_infant=True,
        tag_ids=[tag_id],
        labeled_by="bob",
    )
    infant = next(
        row
        for row in dp.list_distance_individuals(video_id)
        if row["initial_carrier_individual_id"] == carrier["id"]
    )
    dp.apply_distance_to_end(
        data["individual"]["id"], 920, skip_reason="individual absent", labeled_by="alice"
    )
    dp.apply_distance_to_end(
        carrier["id"],
        920,
        reference_frame_id=data["frame"]["id"],
        behavior_ids=[data["behavior_id"]],
        infant_id=infant["id"],
        labeled_by="bob",
    )
    dp.set_distance_active_individual(video_id, "alice", data["individual"]["id"])
    dp.set_distance_active_individual(video_id, "bob", carrier["id"])
    dp.finish_distance_video(video_id, "alice")
    dp.update_manual_review(
        video_id,
        [{"species": "deer", "start_sec": 0, "end_sec": 5, "labeled_by": "alice"}],
        is_blank=False,
    )
    # Another video's tracks, samples and resume state must survive realignment.
    other = dp.create_distance_individual(
        data["videos"][1], data["species_id"], 0, anchor=data["anchor"]
    )
    dp.calibrate_distance_clock(data["videos"][1], CAMERA_TIME, ANCHOR, "alice")
    dp.save_distance_sample(other["id"], 920, skip_reason="individual absent", labeled_by="alice")
    dp.set_distance_active_individual(data["videos"][1], "alice", other["id"])
    return data


@pytest.mark.parametrize("seconds", [2, -2, 86400])
def test_timestamp_correction_preserves_all_evidence_and_completion(clock_data, seconds):
    data, dp = clock_data, clock_data["dp"]
    video_id = data["videos"][0]
    before = snapshot(dp)
    old_state = dp.get_distance_review_state(video_id, "alice")
    dp.calibrate_distance_clock(video_id, CAMERA_TIME + timedelta(seconds=seconds), ANCHOR, "bob")
    after = snapshot(dp)
    assert {k: v for k, v in before.items() if k != "distance_video_reviews"} == {
        k: v for k, v in after.items() if k != "distance_video_reviews"
    }
    new_state = dp.get_distance_review_state(video_id, "alice")
    assert new_state["offsets"] == old_state["offsets"]
    assert new_state["review"]["completed_at"] == old_state["review"]["completed_at"]
    assert new_state["review"]["completed_by"] == old_state["review"]["completed_by"]
    assert new_state["review"]["verified_by"] == "bob"
    assert new_state["review"]["verified_at"] >= old_state["review"]["verified_at"]
    for offset in new_state["offsets"]:
        assert sample_camera_time(new_state["review"], offset) == (
            sample_camera_time(old_state["review"], offset) + timedelta(seconds=seconds)
        )


@pytest.mark.parametrize("offset,opening", [(920.5, False), (1020.125, False), (920.125, True)])
def test_alignment_changes_without_consent_are_atomic(clock_data, offset, opening):
    data, dp = clock_data, clock_data["dp"]
    before = snapshot(dp)
    with pytest.raises(ValueError, match="requires resetting"):
        dp.calibrate_distance_clock(
            data["videos"][0], CAMERA_TIME, offset, "bob", opening_even_second=opening
        )
    assert snapshot(dp) == before


def test_unreviewed_tracks_also_require_reset_for_realigning(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.calibrate_distance_clock(video_id, CAMERA_TIME, ANCHOR, "alice")
    before = snapshot(dp)
    with pytest.raises(ValueError, match="requires resetting"):
        dp.calibrate_distance_clock(video_id, CAMERA_TIME, 1120.5, "alice")
    assert snapshot(dp) == before
    dp.calibrate_distance_clock(video_id, CAMERA_TIME, 1120.5, "alice", reset_tracks=True)
    assert dp.list_distance_individuals(video_id) == []


def test_invalid_realignment_does_not_delete_evidence(clock_data):
    data, dp = clock_data, clock_data["dp"]
    before = snapshot(dp)
    with pytest.raises(ValueError, match="even whole-second"):
        dp.calibrate_distance_clock(
            data["videos"][0],
            CAMERA_TIME + timedelta(seconds=1),
            1120.5,
            "alice",
            reset_tracks=True,
        )
    assert snapshot(dp) == before


def test_realignment_resets_only_this_videos_distance_tracks(clock_data):
    data, dp = clock_data, clock_data["dp"]
    video_id = data["videos"][0]
    before = snapshot(dp)
    other_state = dp.get_distance_review_state(data["videos"][1], "alice")
    dp.calibrate_distance_clock(video_id, CAMERA_TIME, 1120.5, "bob", reset_tracks=True)
    state = dp.get_distance_review_state(video_id, "alice")
    assert state["samples"] == []
    assert state["active_individual_id"] is None
    assert state["offsets"] == [1120, 3120, 5120, 7120, 9120]
    assert state["review"]["clock_anchor_playback_ms"] == 1120.5
    assert state["review"]["completed_at"] is None
    assert state["review"]["completed_by"] is None
    assert dp.list_distance_individuals(video_id) == []
    assert dp.get_distance_review_state(video_id, "bob")["active_individual_id"] is None
    assert dp.get_distance_review_state(data["videos"][1], "alice") == other_state
    after = snapshot(dp)
    reset_tables = {
        "distance_individuals",
        "distance_samples",
        "distance_individual_tags",
        "distance_sample_behaviors",
        "distance_review_progress",
        "distance_video_reviews",
    }
    assert {k: v for k, v in after.items() if k not in reset_tables} == {
        k: v for k, v in before.items() if k not in reset_tables
    }
    assert after["distance_individual_tags"] == []
    assert after["distance_sample_behaviors"] == []
    with dp.engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []


def test_failed_realignment_rolls_back_deleted_tracks(clock_data):
    data, dp = clock_data, clock_data["dp"]
    before = snapshot(dp)

    def reject_clock_update(*_args):
        raise ValueError("Simulated clock write failure")

    event.listen(DistanceVideoReview, "before_update", reject_clock_update)
    try:
        with pytest.raises(ValueError, match="Simulated"):
            dp.calibrate_distance_clock(
                data["videos"][0], CAMERA_TIME, 1120.5, "bob", reset_tracks=True
            )
    finally:
        event.remove(DistanceVideoReview, "before_update", reject_clock_update)
    assert snapshot(dp) == before


@pytest.fixture
def correction_page(clock_data, monkeypatch, tmp_path):
    data, dp = clock_data, clock_data["dp"]
    path = tmp_path / "clock.mp4"
    path.write_bytes(b"preview")
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    capture = AsyncMock(return_value=1120.5)
    monkeypatch.setattr(clock_module, "capture_offset", capture)
    holder = {"capture": capture}

    @ui.page("/clock-correction")
    async def correction():
        page = DistanceReviewPage(
            dp,
            data["project"].id,
            dp.get_distance_species_options(data["project"].id),
            dp.get_all_tags(),
        )
        page.queue = [
            {
                "video_id": data["videos"][0],
                "camera_id": "cam_x",
                "video_path": str(path),
                "duration_sec": 10,
            }
        ]
        page.animal_player = SimpleNamespace(source=page.media_url(path), id=0)
        page.load_sample_timing = AsyncMock()
        page.animal_frame_rate = 25
        page.individuals = dp.list_distance_individuals(data["videos"][0])
        page.individual_id = data["individual"]["id"]
        await page.reload_review()
        page.sample_offset = 2920
        page.render_individuals()
        page.render_timing()
        holder["page"] = page
        await page.clock_dialog()

    return holder, data


@pytest.mark.parametrize("hour", [0, 16, 23])
async def test_timestamp_ui_preserves_tracks_without_capturing_a_new_frame(
    user: User, correction_page, hour
):
    holder, data = correction_page
    await user.open("/clock-correction")
    await user.should_see("Correct camera timestamp")
    page = holder["page"]
    timestamp = f"2026-10-12 {hour:02d}:02:52"
    user.find("Camera timestamp at the saved alignment frame").clear().type(timestamp)
    user.find("Correct camera timestamp").click()
    await wait_for_clock(page)
    assert f"Aligned camera time: {timestamp}" in page.clock_description()
    holder["capture"].assert_not_awaited()
    assert len(page.individuals) == 3
    assert page.sample_offset == 2920
    assert len(page.samples) == 15
    assert page.review["completed_at"] is not None


async def test_timestamp_ui_rejects_odd_second_correction(user: User, correction_page):
    holder, data = correction_page
    await user.open("/clock-correction")
    before = snapshot(data["dp"])
    user.find("Camera timestamp at the saved alignment frame").clear().type("2026-10-12 16:02:51")
    user.find("Correct camera timestamp").click()
    await user.should_see("Timestamp corrections must use whole two-second steps.")
    assert snapshot(data["dp"]) == before
    holder["capture"].assert_not_awaited()


@pytest.mark.parametrize("timestamp", ["2026-10-12 24:02:52", "2026-10-12 04:02:52 PM"])
async def test_timestamp_ui_rejects_invalid_24_hour_input(user: User, correction_page, timestamp):
    holder, data = correction_page
    await user.open("/clock-correction")
    before = snapshot(data["dp"])
    user.find("Camera timestamp at the saved alignment frame").clear().type(timestamp)
    user.find("Correct camera timestamp").click()
    await user.should_see("Enter a valid camera timestamp as YYYY-MM-DD HH:mm:ss (24-hour time).")
    assert snapshot(data["dp"]) == before
    holder["capture"].assert_not_awaited()


@pytest.mark.parametrize("confirm", [False, True])
async def test_realignment_ui_requires_destructive_confirmation(
    user: User, correction_page, confirm
):
    holder, data = correction_page
    await user.open("/clock-correction")
    before = snapshot(data["dp"])
    user.find("Change alignment frame and reset Distance review").click()
    user.find("Confirm clock alignment").click()
    await user.should_see("Reset Distance review for this video?")
    await user.should_see("including other annotators' work")
    assert snapshot(data["dp"]) == before
    if not confirm:
        user.find("Cancel").click()
        await user.should_not_see("Reset Distance review for this video?")
        assert snapshot(data["dp"]) == before
        return
    user.find("Delete Distance tracks and realign").click()
    page = holder["page"]
    await wait_for_clock(page)
    assert page.individuals == []
    assert page.samples == []
    assert page.individual_id is None
    assert page.sample_offset is None
    assert page.review["completed_at"] is None
    assert page.review["clock_anchor_playback_ms"] == 1120.5
    await user.should_not_see("Reset Distance review for this video?")
