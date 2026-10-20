"""On-demand calibration persists reference mappings, not animal evidence."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.backend.db.models import DistanceSample
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

pytest_plugins = ["nicegui.testing.user_plugin"]


def test_configure_is_persistent_and_never_overwrites(distance_data):
    dp, reference = distance_data["dp"], distance_data["reference"]
    frame = dp.configure_reference_distance(reference["id"], 333, 4, playback_offset_ms=333.334)
    assert frame["distance_m"] == 4
    assert frame["playback_offset_ms"] == 333.334
    assert dp.configure_reference_distance(reference["id"], 800, 4) == frame
    assert dp.configure_reference_distance(reference["id"], 900, 5) == distance_data["frame"]
    reopened = LocalDataProvider()
    assert reopened.list_reference_frames(reference["id"]) == [frame, distance_data["frame"]]
    with dp.Session() as session:
        assert session.query(DistanceSample).count() == 0


def test_configure_is_scoped_to_reference_and_duplicate_positions_roll_back(distance_data):
    dp, data = distance_data["dp"], distance_data
    other = dp.register_reference_video(data["project"].id, "cam_x", "references/other.mp4")
    frame = dp.configure_reference_distance(other["id"], 3500, 5)
    assert frame["id"] != data["frame"]["id"]
    with pytest.raises(IntegrityError):
        dp.configure_reference_distance(other["id"], 3500, 6)
    assert dp.list_reference_frames(other["id"]) == [frame]
    assert dp.list_reference_frames(data["reference"]["id"]) == [data["frame"]]


def test_explicit_edit_retains_reference_identity_and_distance(distance_data):
    dp, frame = distance_data["dp"], distance_data["frame"]
    video_id = distance_data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.save_distance_sample(
        distance_data["individual"]["id"], 1000, reference_frame_id=frame["id"], labeled_by="alice"
    )
    before = dp.get_distance_review_state(video_id, "alice")["samples"]
    updated = dp.update_reference_frame_position(frame["id"], 333, playback_offset_ms=333.334)
    assert updated == {**frame, "offset_ms": 333, "playback_offset_ms": 333.334}
    assert dp.get_distance_review_state(video_id, "alice")["samples"] == before
    dp.add_reference_frame(frame["reference_video_id"], 1000, 6)
    with pytest.raises(IntegrityError):
        dp.update_reference_frame_position(frame["id"], 1000)
    assert dp.list_reference_frames(frame["reference_video_id"])[0] == updated
    with pytest.raises(ValueError, match="Playback position"):
        dp.update_reference_frame_position(frame["id"], 333, playback_offset_ms=334)


@pytest.fixture
def on_demand_page(distance_data, monkeypatch):
    data, holder = distance_data, {}
    capture = AsyncMock(return_value=333.334)
    seek, render = Mock(), Mock(side_effect=lambda *a, **kw: ui.video("/test.mp4"))
    monkeypatch.setattr(page_module, "capture_offset", capture)
    monkeypatch.setattr(page_module, "seek_paused", seek)
    monkeypatch.setattr(page_module, "render_custom_video_player", render)
    monkeypatch.setattr(page_module, "playback_shortcuts", Mock())
    monkeypatch.setattr(page_module, "_probe_frame_rate", Mock(return_value=25))
    monkeypatch.setattr(page_module, "step_frames", AsyncMock())

    @ui.page("/reference-on-demand-test")
    async def on_demand():
        page = DistanceReviewPage(data["dp"], data["project"].id, {}, [])
        page.queue = [{"video_id": data["videos"][0], "camera_id": "cam_x"}]
        page.individuals = [data["individual"]]
        page.individual_id = data["individual"]["id"]
        page.offsets = [1000, 3000]
        page.sample_offset = 1000
        page.sample_values = {"behavior_ids": ["walking"], "carries": True, "infant_id": "_new"}
        page.reference = data["reference"]
        page.references = [{**data["reference"], "is_tagged": True}]
        page.reference_duration = 10
        page.frames = data["dp"].list_reference_frames(page.reference["id"])
        page.ensure_reference_selection()
        page.render_samples = Mock()
        page.render_sample_actions = Mock()
        page.refresh_sample_ui = Mock()
        page.reference_thumbnail_path = Mock(return_value=None)
        page.media_url = Mock(return_value="/test.mp4")
        page.render_references()
        holder["page"] = page

    holder.update(data=data, capture=capture, seek=seek, render=render)
    return holder


def distance_button(user, meters):
    return next(
        element
        for element in user.find(ui.button).elements
        if element.props.get("data-reference-distance") == str(meters)
    )


async def wait_for(predicate):
    for _ in range(100):
        await asyncio.sleep(0.01)
        if predicate():
            return
    raise AssertionError("On-demand calibration did not finish")


async def test_unconfigured_click_saves_then_configured_click_seeks(user: User, on_demand_page):
    await user.open("/reference-on-demand-test")
    holder, page = on_demand_page, on_demand_page["page"]
    player, values = page.reference_player, dict(page.sample_values)
    assert distance_button(user, 1).props["data-reference-state"] == "unconfigured"
    assert distance_button(user, 15).props["data-reference-state"] == "unconfigured"
    assert distance_button(user, 5).props["data-reference-state"] == "configured"
    holder["seek"].reset_mock()
    distance_button(user, 4).mark("four")
    user.find(marker="four").click()
    await wait_for(lambda: not page.busy and len(page.frames) == 2)
    frame = next(row for row in page.frames if row["distance_m"] == 4)
    assert page.selected_frame_id == frame["id"]
    assert frame["offset_ms"] == 333 and frame["playback_offset_ms"] == 333.334
    holder["capture"].assert_awaited_once_with(player, exact=True)
    assert page.graph_preview()["distance_m"] == 4
    assert page.samples == []
    assert page.sample_values == values
    assert page.reference_player is player
    holder["render"].assert_called_once()
    distance_button(user, 4).mark("four-saved")
    assert distance_button(user, 4).props["data-reference-state"] == "configured"
    holder["seek"].reset_mock()
    user.find(marker="four-saved").click()
    await wait_for(lambda: holder["seek"].called)
    holder["seek"].assert_called_once_with(player, 333.334)
    assert holder["capture"].await_count == 1
    assert page.dp.list_reference_frames(page.reference["id"]) == page.frames


async def test_review_can_start_with_zero_reference_frames(user: User, on_demand_page):
    data = on_demand_page["data"]
    data["reference"] = data["dp"].register_reference_video(
        data["project"].id, "cam_x", "references/empty.mp4"
    )
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    assert page.selected_frame_id is None
    assert (
        len([e for e in user.find(ui.button).elements if "data-reference-distance" in e.props])
        == 15
    )
    distance_button(user, 1).mark("one")
    user.find(marker="one").click()
    await wait_for(lambda: not page.busy and len(page.frames) == 1)
    assert page.frames[0]["distance_m"] == 1
    assert page.samples == []


@pytest.mark.parametrize("failure", ["not-ready", "duplicate", "database"])
async def test_failed_save_leaves_distance_unconfigured(user: User, on_demand_page, failure):
    if failure == "not-ready":
        on_demand_page["capture"].side_effect = TimeoutError()
        message = "Wait for the video to load before saving a frame."
    elif failure == "duplicate":
        on_demand_page["capture"].return_value = 3500
        message = "A reference frame is already saved at this time."
    else:
        on_demand_page["data"]["dp"].configure_reference_distance = Mock(
            side_effect=SQLAlchemyError("Database unavailable")
        )
        message = "Could not save the reference frame. Please try again."
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    distance_button(user, 4).mark("four-failed")
    user.find(marker="four-failed").click()
    await wait_for(lambda: on_demand_page["capture"].await_count and not page.busy)
    assert len(page.frames) == 1
    assert distance_button(user, 4).props["data-reference-state"] == "unconfigured"
    assert page.selected_frame_id == on_demand_page["data"]["frame"]["id"]
    assert page.samples == []
    await user.should_see(message)


async def test_full_reference_editor_opens_from_frame_step_controls(
    user: User, on_demand_page, monkeypatch
):
    open_editor = AsyncMock()
    monkeypatch.setattr(DistanceReviewPage, "prepare_reference_dialog", open_editor)
    await user.open("/reference-on-demand-test")
    buttons = list(user.find(ui.button).elements)
    editor = next(
        button
        for button in buttons
        if button.props.get("data-distance-action") == "edit-reference"
    )
    step = next(button for button in buttons if "data-reference-frame-step" in button.props)
    assert editor.parent_slot is step.parent_slot
    assert "distance-panel-heading" not in editor.parent_slot.parent.classes
    editor.mark("full-reference-editor")
    user.find(marker="full-reference-editor").click()
    await wait_for(lambda: open_editor.await_count == 1)


async def test_reference_steps_use_reference_player_and_cached_fps(user: User, on_demand_page):
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    for index, delta in enumerate((-1, 1)):
        user.find(f"{delta:+d}").click()
        await wait_for(lambda: page_module.step_frames.await_count == index + 1 and not page.busy)
        page_module.step_frames.assert_awaited_with(page.reference_player, delta, 25)
    page_module._probe_frame_rate.assert_called_once()


async def test_fractional_and_out_of_range_prepared_distances_remain_available(
    user: User, on_demand_page
):
    data = on_demand_page["data"]
    data["dp"].add_reference_frame(data["reference"]["id"], 500, 7.25)
    data["dp"].add_reference_frame(data["reference"]["id"], 900, 25)
    await user.open("/reference-on-demand-test")
    assert distance_button(user, 7.25).props["data-reference-state"] == "configured"
    assert distance_button(user, 25).props["data-reference-state"] == "configured"


async def test_add_custom_distance_with_enter_persists_and_suggests_next_meter(
    user: User, on_demand_page
):
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    player, values = page.reference_player, dict(page.sample_values)
    user.find("Add distance").click()
    await user.should_see("Known distance (m)")
    number = next(iter(user.find(ui.number).elements))
    assert number.value == 16
    number.set_value(25)
    user.find("Known distance (m)").trigger("keydown.enter")
    await wait_for(lambda: not page.busy and len(page.frames) == 2)
    await user.should_not_see("Known distance (m)")
    frame = next(frame for frame in page.frames if frame["distance_m"] == 25)
    assert frame["offset_ms"] == 333 and frame["playback_offset_ms"] == 333.334
    assert page.selected_frame_id == frame["id"]
    await user.should_see("25 m")
    assert distance_button(user, 25).props["data-reference-state"] == "configured"
    assert LocalDataProvider().list_reference_frames(page.reference["id"]) == page.frames
    assert page.samples == [] and page.sample_values == values
    assert page.reference_player is player
    on_demand_page["capture"].assert_awaited_once_with(player, exact=True)
    on_demand_page["render"].assert_called_once()
    user.find("Add distance").click()
    await user.should_see("Known distance (m)")
    assert next(iter(user.find(ui.number).elements)).value == 26


async def test_add_existing_distance_selects_without_overwriting(user: User, on_demand_page):
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    user.find("Add distance").click()
    await user.should_see("Known distance (m)")
    next(iter(user.find(ui.number).elements)).set_value(5)
    user.find("Save current frame").click()
    await user.should_not_see("Known distance (m)")
    assert page.frames == [on_demand_page["data"]["frame"]]
    assert page.selected_frame_id == page.frames[0]["id"]
    on_demand_page["capture"].assert_not_awaited()


@pytest.mark.parametrize("value", [None, 0, 20.5])
async def test_add_distance_rejects_missing_or_invalid_value(user: User, on_demand_page, value):
    await user.open("/reference-on-demand-test")
    user.find("Add distance").click()
    await user.should_see("Known distance (m)")
    number = next(iter(user.find(ui.number).elements))
    number.set_value(value)
    user.find("Save current frame").click()
    await user.should_see(
        "Enter the known distance shown in the reference frame."
        if value is None
        else "Enter a positive whole-number distance."
    )
    assert number.enabled
    on_demand_page["capture"].assert_not_awaited()
    assert len(on_demand_page["page"].frames) == 1


async def test_add_distance_failed_save_keeps_input_for_retry(user: User, on_demand_page):
    on_demand_page["capture"].side_effect = TimeoutError()
    await user.open("/reference-on-demand-test")
    user.find("Add distance").click()
    await user.should_see("Known distance (m)")
    number = next(iter(user.find(ui.number).elements))
    number.set_value(30)
    user.find("Save current frame").click()
    await user.should_see("Wait for the video to load before saving a frame.")
    await wait_for(lambda: number.enabled)
    assert number.value == 30
    assert len(on_demand_page["page"].frames) == 1
    on_demand_page["capture"].side_effect = None
    user.find("Save current frame").click()
    await user.should_not_see("Known distance (m)")
    assert distance_button(user, 30).props["data-reference-state"] == "configured"


async def test_cancel_add_distance_does_not_save(user: User, on_demand_page):
    await user.open("/reference-on-demand-test")
    user.find("Add distance").click()
    await user.should_see("Known distance (m)")
    user.find("Cancel").click()
    await user.should_not_see("Known distance (m)")
    on_demand_page["capture"].assert_not_awaited()
    assert len(on_demand_page["page"].frames) == 1
