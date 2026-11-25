"""On-demand calibration persists reference mappings, not animal evidence."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.app.pages.distance_review.session import ZERO_DISTANCE_ID
from review_app.app.translations import t
from review_app.backend.db.models import DistanceSample
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy.exc import SQLAlchemyError

pytest_plugins = ["nicegui.testing.user_plugin"]


def test_configure_is_persistent_and_never_overwrites(distance_data):
    dp, reference = distance_data["dp"], distance_data["reference"]
    frame = dp.configure_reference_distance(reference["id"], 333, 4, playback_offset_ms=333.334)
    assert frame["distance_m"] == 4
    assert frame["playback_offset_ms"] == 333.334
    assert dp.configure_reference_distance(reference["id"], 333, 4) == frame
    second = dp.configure_reference_distance(reference["id"], 800, 4)
    assert second["id"] != frame["id"]
    reopened = LocalDataProvider()
    assert reopened.list_reference_frames(reference["id"]) == [
        frame,
        second,
        distance_data["frame"],
    ]
    with dp.Session() as session:
        assert session.query(DistanceSample).count() == 0


def test_configure_is_scoped_to_reference_and_distances_can_share_positions(distance_data):
    dp, data = distance_data["dp"], distance_data
    other = dp.register_reference_video(data["project"].id, "cam_x", "references/other.mp4")
    frame = dp.configure_reference_distance(other["id"], 3500, 5)
    assert frame["id"] != data["frame"]["id"]
    same_position = dp.configure_reference_distance(other["id"], 3500, 6)
    assert same_position["offset_ms"] == frame["offset_ms"]
    assert dp.configure_reference_distance(other["id"], 3500, 6) == same_position
    assert dp.list_reference_frames(other["id"]) == [frame, same_position]
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
    moved = dp.update_reference_frame_position(frame["id"], 1000)
    assert moved == {**frame, "offset_ms": 1000, "playback_offset_ms": None}
    assert [row["offset_ms"] for row in dp.list_reference_frames(frame["reference_video_id"])] == [
        1000,
        1000,
    ]
    assert dp.get_distance_review_state(video_id, "alice")["samples"] == before
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
    await wait_for(lambda: distance_button(user, 4).props["data-reference-state"] == "configured")
    distance_button(user, 4).mark("four-saved")
    holder["seek"].reset_mock()
    user.find(marker="four-saved").click()
    await wait_for(lambda: holder["seek"].called)
    holder["seek"].assert_called_once_with(player, 333.334)
    assert holder["capture"].await_count == 1
    assert page.dp.list_reference_frames(page.reference["id"]) == page.frames


async def test_on_demand_distances_can_share_a_timestamp(user: User, on_demand_page):
    on_demand_page["capture"].return_value = 3500
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    distance_button(user, 4).mark("four-same-time")
    user.find(marker="four-same-time").click()
    await wait_for(lambda: not page.busy and len(page.frames) == 2)
    assert {row["offset_ms"] for row in page.frames} == {3500}
    assert {row["distance_m"] for row in page.frames} == {4, 5}
    assert page.selected_frame_id == next(
        row["id"] for row in page.frames if row["distance_m"] == 4
    )
    assert page.samples == []


async def test_uncalibrated_imported_estimate_highlights_rounded_distance(
    user: User, on_demand_page
):
    data = on_demand_page["data"]
    data["reference"] = data["dp"].register_reference_video(
        data["project"].id, "cam_x", "references/empty.mp4"
    )
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    page.samples = [
        {
            "individual_id": page.individual_id,
            "offset_ms": page.sample_offset,
            "status": "pending",
            "suggestion": {"predicted_distance_m": 6.9},
        }
    ]
    with user:
        await page.render_frames.refresh()
    button = distance_button(user, 7)
    assert not any(
        element.props.get("data-reference-distance") == "6.9"
        for element in user.client.elements.values()
    )
    assert button.props["data-distance-ai-suggestion"] == "true"
    assert button.props["data-reference-state"] == "unconfigured"
    assert button.props.get("aria-pressed") != "true"
    assert page.frames == [] and page.selected_frame_id is None
    with user:
        page.samples[0].update(status="annotated", distance_m=0)
        page.selected_frame_id = ZERO_DISTANCE_ID
        await page.render_frames.refresh()
    assert distance_button(user, 7).props["data-distance-ai-suggestion"] == "true"
    assert distance_button(user, 0).props["aria-pressed"] == "true"
    assert page.reference_sample is page.samples[0]
    with user:
        page.sample_offset = 3000
        await page.render_frames.refresh()
    assert not any(
        element.props.get("data-distance-ai-suggestion")
        for element in user.client.elements.values()
    )


async def test_review_can_start_with_zero_reference_frames(user: User, on_demand_page):
    data = on_demand_page["data"]
    data["reference"] = data["dp"].register_reference_video(
        data["project"].id, "cam_x", "references/empty.mp4"
    )
    await user.open("/reference-on-demand-test")
    page = on_demand_page["page"]
    assert page.selected_frame_id is None
    assert {
        e.props["data-reference-distance"]
        for e in user.find(ui.button).elements
        if "data-reference-distance" in e.props
    } == {str(meters) for meters in range(16)}
    assert distance_button(user, 0).enabled
    distance_button(user, 1).mark("one")
    user.find(marker="one").click()
    await wait_for(lambda: not page.busy and len(page.frames) == 1)
    assert page.frames[0]["distance_m"] == 1
    assert page.samples == []


@pytest.mark.parametrize("has_alternative", [True, False])
async def test_review_displays_saved_distance_after_reference_deletion(
    user: User, on_demand_page, has_alternative
):
    holder = on_demand_page
    data, dp = holder["data"], holder["data"]["dp"]
    video_id = data["videos"][0]
    frame_id = data["frame"]["id"]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.save_distance_sample(
        data["individual"]["id"], 1000, reference_frame_id=frame_id, labeled_by="alice"
    )
    dp.add_reference_frame(data["reference"]["id"], 1000, 6)
    alternative = (
        dp.add_reference_frame(data["reference"]["id"], 2000, 5) if has_alternative else None
    )
    dp.delete_reference_frame(frame_id)

    await user.open("/reference-on-demand-test")
    page = holder["page"]
    page.samples = dp.get_distance_review_state(video_id, "alice")["samples"]
    holder["seek"].reset_mock()
    with user:
        await page.restore_sample(seek=False)
    assert page.selected_frame_id == (alternative["id"] if alternative else None)
    assert page.sample["reference_frame_id"] == frame_id
    assert page.sample["distance_m"] == 5
    if alternative:
        holder["seek"].assert_called_once_with(page.reference_player, 2000)
        await user.should_see(t("distance_reference_alternative", distance="5"))
    else:
        holder["seek"].assert_not_called()
        await user.should_see(t("distance_reference_unavailable", distance="5"))
        assert "distance-reference-dimmed" in page.reference_player.classes


@pytest.mark.parametrize("failure", ["not-ready", "database"])
async def test_failed_save_leaves_distance_unconfigured(user: User, on_demand_page, failure):
    if failure == "not-ready":
        on_demand_page["capture"].side_effect = TimeoutError()
        message = "Wait for the video to load before saving a frame."
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
