"""Non-destructive timestamp corrections and explicitly destructive realignment."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app import config as app_config
from review_app.app.pages.distance_review import clock_dialog as clock_module
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.backend.db.models import Base, DistanceReviewTask
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
    for individual in dp.list_distance_individuals(video_id):
        dp.set_distance_individual_attributes(
            individual["id"],
            age=individual["age"] or "Unknown",
            sex="Unknown",
            tag_ids=individual["tag_ids"],
        )
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
    assert {k: v for k, v in before.items() if k != "distance_review_tasks"} == {
        k: v for k, v in after.items() if k != "distance_review_tasks"
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
        "distance_review_tasks",
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

    event.listen(DistanceReviewTask, "before_update", reject_clock_update)
    try:
        with pytest.raises(ValueError, match="Simulated"):
            dp.calibrate_distance_clock(
                data["videos"][0], CAMERA_TIME, 1120.5, "bob", reset_tracks=True
            )
    finally:
        event.remove(DistanceReviewTask, "before_update", reject_clock_update)
    assert snapshot(dp) == before


@pytest.fixture
def correction_page(clock_data, monkeypatch, tmp_path):
    data, dp = clock_data, clock_data["dp"]
    path = tmp_path / "clock.mp4"
    path.write_bytes(b"preview")
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    capture = AsyncMock(return_value=1120.5)
    monkeypatch.setattr(clock_module, "capture_offset", capture)
    seek = Mock()
    monkeypatch.setattr(clock_module, "seek_paused", seek)
    holder = {"capture": capture, "seek": seek}

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
                "created_at": datetime(2026, 10, 12, 16, 2, 49),
            }
        ]
        page.animal_player = SimpleNamespace(source=page.media_url(path), id=0)
        page.load_sample_timing = AsyncMock()
        page.animal_frame_rate = 25
        page.individuals = dp.list_distance_individuals(data["videos"][0])
        page.individual_id = data["individual"]["id"] if page.individuals else None
        await page.reload_review()
        page.sample_offset = 2920 if 2920 in page.offsets else page.sample_offset
        page.render_individuals()
        page.render_timing()
        page.render_sample_navigation()
        page.render_tracks()
        page.render_sample_actions()
        holder["page"] = page
        await page.clock_dialog()

    return holder, data


async def test_browsing_schedule_without_individuals_never_creates_evidence(
    user: User, correction_page, monkeypatch
):
    holder, data = correction_page
    dp, video_id = data["dp"], data["videos"][0]
    dp.align_distance_on_even_frame(video_id, 5000, "alice", reset_tracks=True)
    before = snapshot(dp)
    play = AsyncMock()
    monkeypatch.setattr(page_module, "play_to_sample", play)
    await user.open("/clock-correction")
    page = holder["page"]
    assert page.individual is None and page.sample_offset == 1000
    await user.should_see("Browse scheduled stops; add an individual to annotate.")
    chart = next(
        chart
        for chart in user.find(ui.echart).elements
        if chart.options["series"] and chart.options["series"][0]["id"] == "schedule"
    )
    point = chart.options["series"][0]["data"][2]
    click = user.find(ui.echart)
    click.elements = {chart}
    click.trigger(
        "componentClick",
        {
            "componentType": "series",
            "seriesType": "scatter",
            "seriesIndex": 0,
            "seriesName": "Scheduled samples",
            "name": "",
            "dataIndex": 2,
            "data": point,
            "value": point["value"],
        },
    )
    await wait_for_clock(page)
    assert page.sample_offset == 5000
    await page.move_sample(1)
    assert page.sample_offset == 7000
    await page.replay_interval()
    play.assert_awaited_once_with(page.animal_player, 5000, 7000)
    await page.commit_sample()
    assert not any(
        element.props.get("data-distance-action") in ("confirm", "apply-to-end")
        for element in page.client.elements.values()
    )
    assert snapshot(dp) == before
    assert dp.get_distance_review_state(video_id, "alice")["active_individual_id"] is None


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
    user.find("Camera timestamp at the saved alignment frame").clear().type("2026-10-12T16:02:51")
    user.find("Correct camera timestamp").click()
    await user.should_see("Timestamp corrections must use whole two-second steps.")
    assert snapshot(data["dp"]) == before
    holder["capture"].assert_not_awaited()


@pytest.mark.parametrize("timestamp", ["2026-10-12T24:02:52", "2026-10-12T04:02:52 PM"])
async def test_timestamp_ui_rejects_invalid_datetime_input(user: User, correction_page, timestamp):
    holder, data = correction_page
    await user.open("/clock-correction")
    before = snapshot(data["dp"])
    user.find("Camera timestamp at the saved alignment frame").clear().type(timestamp)
    user.find("Correct camera timestamp").click()
    await user.should_see("Choose a valid camera date and time.")
    assert snapshot(data["dp"]) == before
    holder["capture"].assert_not_awaited()


@pytest.mark.parametrize("confirm", [False, True])
async def test_realignment_ui_requires_destructive_confirmation(
    user: User, correction_page, confirm, monkeypatch
):
    monkeypatch.setattr(app_config, "DISTANCE_CLOCK_ALIGNMENT_MODE", "exact")
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
    assert page.sample_offset == 1120
    assert page.review["completed_at"] is None
    assert page.review["clock_anchor_playback_ms"] == 1120.5
    await user.should_not_see("Reset Distance review for this video?")


@pytest.mark.parametrize("opening_even,first", [(True, 0), (False, 1000)])
def test_parity_alignment_schedules_without_a_camera_time(distance_data, opening_even, first):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.align_distance_by_parity(video_id, opening_even, "alice")
    state = dp.get_distance_review_state(video_id, "alice")
    assert state["offsets"] == list(range(first, 10000, 2000))
    review = state["review"]
    assert review["alignment_mode"] == "simplified"
    assert review["recorded_start"] is None
    assert review["clock_anchor_offset_ms"] is None
    assert review["clock_anchor_playback_ms"] is None
    assert all(sample_camera_time(review, offset) is None for offset in state["offsets"])
    dp.apply_distance_to_end(
        data["individual"]["id"], first, skip_reason="individual absent", labeled_by="alice"
    )
    dp.set_distance_active_individual(video_id, "alice", data["individual"]["id"])
    dp.set_distance_individual_attributes(
        data["individual"]["id"], age="Unknown", sex="Unknown", tag_ids=[]
    )
    dp.finish_distance_video(video_id, "alice")
    before = dp.get_distance_review_state(video_id, "alice")
    dp.align_distance_by_parity(video_id, opening_even, "bob")
    after = dp.get_distance_review_state(video_id, "alice")
    assert after["samples"] == before["samples"]
    assert after["active_individual_id"] == before["active_individual_id"]
    assert after["review"]["completed_at"] == before["review"]["completed_at"]
    assert after["review"]["verified_by"] == "bob"


@pytest.mark.parametrize("opening_even", [True, False])
def test_switching_modes_requires_reset_and_only_clears_target_video(clock_data, opening_even):
    data, dp = clock_data, clock_data["dp"]
    video_id = data["videos"][0]
    other = dp.get_distance_review_state(data["videos"][1], "alice")
    before = snapshot(dp)
    with pytest.raises(ValueError, match="requires resetting"):
        dp.align_distance_by_parity(video_id, opening_even, "bob")
    assert snapshot(dp) == before
    dp.align_distance_by_parity(video_id, opening_even, "bob", reset_tracks=True)
    assert dp.list_distance_individuals(video_id) == []
    state = dp.get_distance_review_state(video_id, "alice")
    assert state["samples"] == []
    assert state["active_individual_id"] is None
    assert state["review"]["completed_at"] is None
    assert dp.get_distance_review_state(data["videos"][1], "alice") == other
    # Even an empty, previously verified video requires consent to switch modes.
    before = snapshot(dp)
    with pytest.raises(ValueError, match="requires resetting"):
        dp.calibrate_distance_clock(video_id, CAMERA_TIME, ANCHOR, "bob")
    assert snapshot(dp) == before
    dp.calibrate_distance_clock(video_id, CAMERA_TIME, ANCHOR, "bob", reset_tracks=True)
    assert dp.get_distance_review_state(video_id, "alice")["offsets"][0] == 920


@pytest.mark.parametrize("opening_even", [None, 0, 1, "even"])
def test_invalid_parity_reset_preserves_evidence(clock_data, opening_even):
    data, dp = clock_data, clock_data["dp"]
    before = snapshot(dp)
    with pytest.raises(ValueError, match="opening timestamp"):
        dp.align_distance_by_parity(data["videos"][0], opening_even, "bob", reset_tracks=True)
    assert snapshot(dp) == before


def test_changing_parity_requires_explicit_reset(distance_data):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.align_distance_by_parity(video_id, True, "alice")
    before = snapshot(dp)
    with pytest.raises(ValueError, match="requires resetting"):
        dp.align_distance_by_parity(video_id, False, "bob")
    assert snapshot(dp) == before
    dp.align_distance_by_parity(video_id, False, "bob", reset_tracks=True)
    assert dp.get_distance_review_state(video_id, "alice")["offsets"][0] == 1000


def seek_clock_test_player(user, offset_ms):
    interaction = user.find(ui.video)
    interaction.elements = {max(interaction.elements, key=lambda element: element.id)}
    interaction.trigger("seeked", {"offset_ms": offset_ms})


@pytest.mark.parametrize("confirm", [False, True])
async def test_simplified_button_confirms_even_frame_and_requires_reset(
    user: User, correction_page, confirm
):
    holder, data = correction_page
    holder["capture"].return_value = 5120.125
    await user.open("/clock-correction")
    before = snapshot(data["dp"])
    user.find("Change alignment frame and reset Distance review").click()
    assert holder["seek"].call_args.args[1] == 5000
    await user.should_see("Choose even frame")
    await user.should_see("Frame shows an even camera second.")
    await user.should_not_see("The displayed frame shows an even camera second.")
    await user.should_not_see("Use expected timestamp (I checked it)")
    await user.should_not_see("Choose a frame showing an even camera second, preferably away")
    heading = next(
        item
        for item in user.client.elements.values()
        if item.props.get("data-testid") == "distance-clock-choose-even"
    )
    controls = next(
        item
        for item in user.client.elements.values()
        if item.props.get("data-testid") == "distance-clock-frame-controls"
    )
    clock = next(iter(user.find("Camera timestamp correction (optional)").elements))
    assert "text-weight-bold" in heading._classes
    assert heading.id < controls.id < clock.id
    await user.should_see("Expected camera timestamp (estimate): 2026-10-12 16:02:54")
    holder["capture"].assert_not_awaited()
    seek_clock_test_player(user, 5120.125)
    user.find("Frame shows an even camera second.").click()
    await user.should_see("Reset Distance review for this video?")
    cancel = next(iter(user.find("Cancel").elements))
    assert cancel.props["autofocus"]  # Destructive reset never defaults to confirmation.
    reset = next(iter(user.find("Delete Distance tracks and realign").elements))
    assert reset.props["data-distance-dialog-action"] == "confirm"
    assert snapshot(data["dp"]) == before
    if not confirm:
        user.find("Cancel").click()
        await user.should_not_see("Reset Distance review for this video?")
        assert snapshot(data["dp"]) == before
        return
    user.find("Delete Distance tracks and realign").click()
    page = holder["page"]
    await wait_for_clock(page)
    holder["capture"].assert_awaited_once()
    assert page.review["alignment_mode"] == "simplified"
    assert page.review["clock_anchor_playback_ms"] == 5120.125
    assert page.review["recorded_start"] is None
    assert page.offsets == [1120, 3120, 5120, 7120, 9120]
    assert page.individuals == []
    assert "camera time unknown" in page.clock_description()
    with page.client:
        await page.clock_dialog()
    assert holder["seek"].call_args.args[1] == 5120.125
    await user.should_not_see("Camera timestamp at the saved alignment frame")
    user.find("Frame shows an even camera second.").click()
    await wait_for_clock(page)
    holder["capture"].assert_awaited_once()
    assert page.offsets == [1120, 3120, 5120, 7120, 9120]


async def test_disabling_reset_restores_the_saved_mode(user: User, correction_page):
    holder, data = correction_page
    await user.open("/clock-correction")
    user.find("Change alignment frame and reset Distance review").click()
    user.find("Change alignment frame and reset Distance review").click()
    await user.should_see("Correct camera timestamp")
    await user.should_not_see("Exact alignment")
    assert not any(
        element.props.get("data-testid") == "distance-clock-mode"
        for element in user.client.elements.values()
    )
    user.find("Correct camera timestamp").click()
    await wait_for_clock(holder["page"])
    assert holder["page"].review["alignment_mode"] == "exact"
    assert len(holder["page"].individuals) == 3
    holder["capture"].assert_not_awaited()


@pytest.mark.parametrize(
    "timestamp", [None, "2026-10-12 16:02:50", "2026-10-12 16:02", "2026-10-12 16:02:54"]
)
async def test_initial_even_frame_alignment_with_optional_camera_time(
    user: User, correction_page, timestamp
):
    holder, data = correction_page
    holder["capture"].return_value = 5000
    dp, video_id = data["dp"], data["videos"][0]
    dp.align_distance_by_parity(video_id, True, "alice", reset_tracks=True)
    with dp.Session.begin() as session:
        session.delete(session.get(DistanceReviewTask, video_id))
    await user.open("/clock-correction")
    assert app_config.DISTANCE_CLOCK_ALIGNMENT_MODE == "simplified"
    await user.should_not_see("Exact alignment")
    await user.should_not_see("The opening still already shows an even second.")
    assert not any(isinstance(element, ui.radio) for element in user.client.elements.values())
    assert holder["seek"].call_args.args[1] == 5000
    await user.should_see("Expected camera timestamp (estimate): 2026-10-12 16:02:54")
    correction = next(iter(user.find("Camera timestamp correction (optional)").elements))
    assert correction.value == "2026-10-12 16:02:54"
    assert correction.props["type"] == "text"
    assert correction.props["mask"] == "####-##-## ##:##:##"
    assert correction.props["placeholder"] == "YYYY-MM-DD HH:mm:ss"
    if timestamp:
        user.find("Camera timestamp correction (optional)").clear().type(timestamp)
    user.find("Frame shows an even camera second.").click()
    page = holder["page"]
    await wait_for_clock(page)
    holder["capture"].assert_awaited_once()
    assert page.offsets == [1000, 3000, 5000, 7000, 9000]
    assert page.review["clock_anchor_playback_ms"] == 5000
    assert page.review["alignment_mode"] == "simplified"
    assert page.sample_offset == 1000
    assert page.individual is None and page.samples == []
    await user.should_see("Browse scheduled stops; add an individual to annotate.")
    assert (page.review["recorded_start"] is None) == (timestamp is None)
    if timestamp:
        anchor_time = sample_camera_time(page.review, 5000)
        assert anchor_time == datetime.fromisoformat(timestamp)
    individual = dp.create_distance_individual(
        video_id, data["species_id"], 0, anchor=data["anchor"]
    )
    await page.reload_individuals()
    page.individual_id = individual["id"]
    page.sample_offset = 1000
    with page.client:
        page.render_sample_navigation()
    if timestamp is None:
        await user.should_see("camera time unknown")
    else:
        await user.should_see(sample_camera_time(page.review, 1000).isoformat(sep=" "))
    assert page.sample_frame_offset(1000) == 1000


async def test_moving_frame_refreshes_estimate_and_requires_fresh_button_confirmation(
    user: User, correction_page
):
    holder, data = correction_page
    await user.open("/clock-correction")
    before = snapshot(data["dp"])
    user.find("Change alignment frame and reset Distance review").click()
    user.find("Camera timestamp correction (optional)").clear().type("2026-10-12 16:02:50")
    seek_clock_test_player(user, 6000)
    await user.should_see("Expected camera timestamp (estimate): 2026-10-12 16:02:55")
    correction = next(iter(user.find("Camera timestamp correction (optional)").elements))
    assert correction.value == "2026-10-12 16:02:55"
    # The video advances after the seek event but before capture: don't associate
    # the previous frame's timestamp/click with this new frame.
    holder["capture"].return_value = 6000.125
    user.find("Frame shows an even camera second.").click()
    await user.should_see(
        "The frame changed. Check that it shows an even camera second and press again."
    )
    assert snapshot(data["dp"]) == before
    user.find("Frame shows an even camera second.").click()
    await user.should_see("Reset Distance review for this video?")
    user.find("Delete Distance tracks and realign").click()
    await wait_for_clock(holder["page"])
    assert holder["page"].review["clock_anchor_playback_ms"] == 6000.125
    assert holder["page"].review["recorded_start"] is None


@pytest.mark.parametrize(
    "timestamp,message",
    [
        ("2026-10-12T25:02:50", "Choose a valid camera date and time."),
        ("2026-10-12T16:02:51", "Enter a whole-second even camera timestamp."),
    ],
)
async def test_invalid_optional_datetime_does_not_change_alignment(
    user: User, correction_page, timestamp, message
):
    holder, data = correction_page
    await user.open("/clock-correction")
    before = snapshot(data["dp"])
    user.find("Change alignment frame and reset Distance review").click()
    user.find("Camera timestamp correction (optional)").clear().type(timestamp)
    user.find("Frame shows an even camera second.").click()
    await user.should_see(message)
    assert snapshot(data["dp"]) == before
    holder["capture"].assert_not_awaited()


async def test_simple_timestamp_correction_stays_on_the_saved_frame(user: User, correction_page):
    holder, data = correction_page
    dp, video_id = data["dp"], data["videos"][0]
    dp.align_distance_on_even_frame(video_id, 5120.125, "alice", reset_tracks=True)
    await user.open("/clock-correction")
    page = holder["page"]
    seek_clock_test_player(user, 6000)
    assert holder["seek"].call_args.args[1] == 5120.125
    await user.should_see("Expected camera timestamp (estimate): 2026-10-12 16:02:54")
    user.find("Camera timestamp correction (optional)").clear().type("2026-10-12T16:02:50")
    user.find("Frame shows an even camera second.").click()
    await wait_for_clock(page)
    holder["capture"].assert_not_awaited()
    assert page.review["clock_anchor_playback_ms"] == 5120.125
    assert sample_camera_time(page.review, 5120) == CAMERA_TIME
    assert page.offsets == [1120, 3120, 5120, 7120, 9120]


async def test_legacy_parity_review_is_not_silently_reanchored(user: User, correction_page):
    holder, data = correction_page
    dp, video_id = data["dp"], data["videos"][0]
    dp.align_distance_by_parity(video_id, False, "alice", reset_tracks=True)
    await user.open("/clock-correction")
    page = holder["page"]
    await user.should_see("This review uses the previous opening-parity schedule.")
    user.find("Frame shows an even camera second.").click()
    await wait_for_clock(page)
    holder["capture"].assert_not_awaited()
    assert page.review["clock_anchor_playback_ms"] is None
    assert page.offsets == [1000, 3000, 5000, 7000, 9000]


@pytest.mark.parametrize("mode", ["exact", "simplified"])
@pytest.mark.parametrize(
    "timestamp,button,expected",
    [
        ("2026-12-31 23:59:59", "+1 s", "2027-01-01 00:00:00"),
        ("2026-01-01 00:00", "-1 s", "2025-12-31 23:59:59"),
    ],
)
async def test_timestamp_second_buttons_roll_over_without_saving_or_moving_frames(
    user: User, correction_page, mode, timestamp, button, expected
):
    holder, data = correction_page
    await user.open("/clock-correction")
    if mode == "simplified":
        user.find("Change alignment frame and reset Distance review").click()
    label = (
        "Camera timestamp at the saved alignment frame"
        if mode == "exact"
        else "Camera timestamp correction (optional)"
    )
    field = next(iter(user.find(label).elements))
    assert field.props["type"] == "text"
    assert field.props["mask"] == "####-##-## ##:##:##"
    user.find(label).clear().type(timestamp)
    before = snapshot(data["dp"])
    seeks_before = holder["seek"].call_count
    user.find(button, kind=ui.button).click()
    assert field.value == expected
    assert snapshot(data["dp"]) == before
    assert holder["seek"].call_count == seeks_before
    holder["capture"].assert_not_awaited()


@pytest.mark.parametrize("mode", ["exact", "simplified"])
async def test_calendar_and_24_hour_time_picker_keep_field_and_saved_time_in_sync(
    user: User, correction_page, mode
):
    holder, data = correction_page
    if mode == "simplified":
        data["dp"].align_distance_on_even_frame(
            data["videos"][0], 5000, "alice", reset_tracks=True
        )
    await user.open("/clock-correction")
    prefix = "distance-clock-time"

    def element(suffix=""):
        return next(
            item
            for item in user.client.elements.values()
            if item.props.get("data-testid") == prefix + suffix
        )

    picker = element("-time")
    assert isinstance(picker, ui.time)
    assert picker.props["format24h"] is True
    assert picker.props["with-seconds"] is True
    assert picker.props["mask"] == "YYYY-MM-DD HH:mm:ss"
    assert picker.value == element().value
    click = user.find(ui.button)
    click.elements = {element("-time-button")}
    click.click()
    await user.should_see(ui.time)
    with user:
        picker.set_value("2026-10-12 23:02:52")
        assert element().value == "2026-10-12 23:02:52"
        element("-date").set_value("2026-10-13 23:02:52")
        assert element().value == picker.value == "2026-10-13 23:02:52"
        for menu in user.client.elements.values():
            if isinstance(menu, ui.menu):
                menu.close()
    user.find(
        "Correct camera timestamp" if mode == "exact" else "Frame shows an even camera second."
    ).click()
    await wait_for_clock(holder["page"])
    review = holder["page"].review
    assert sample_camera_time(review, review["clock_anchor_offset_ms"]) == datetime(
        2026, 10, 13, 23, 2, 52
    )
    holder["capture"].assert_not_awaited()


@pytest.mark.parametrize("mode", ["exact", "simplified"])
async def test_timestamp_second_buttons_save_explicit_correction_with_evidence_intact(
    user: User, correction_page, mode
):
    holder, data = correction_page
    dp, video_id = data["dp"], data["videos"][0]
    if mode == "simplified":
        dp.align_distance_on_even_frame(video_id, 5000, "alice", reset_tracks=True)
    before = dp.get_distance_review_state(video_id, "alice")
    await user.open("/clock-correction")
    user.find("+1 s", kind=ui.button).click()
    user.find("+1 s", kind=ui.button).click()
    user.find(
        "Correct camera timestamp" if mode == "exact" else "Frame shows an even camera second."
    ).click()
    await wait_for_clock(holder["page"])
    after = dp.get_distance_review_state(video_id, "alice")
    assert sample_camera_time(after["review"], after["review"]["clock_anchor_offset_ms"]) == (
        CAMERA_TIME + timedelta(seconds=2 if mode == "exact" else 6)
    )
    assert after["offsets"] == before["offsets"]
    assert after["samples"] == before["samples"]
    assert after["review"]["completed_at"] == before["review"]["completed_at"]
    holder["capture"].assert_not_awaited()
