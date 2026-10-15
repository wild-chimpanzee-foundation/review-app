"""Reference preparation restores saved frames and prefills whole-number distances."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review import reference_preparation as preparation_module
from review_app.app.pages.distance_review.page import DistanceReviewPage

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def preparation_page(distance_data, monkeypatch):
    holder = {}
    capture = AsyncMock(return_value=1000)
    seek = Mock()
    step = AsyncMock()
    monkeypatch.setattr(page_module, "_probe_frame_rate", lambda _path: 25)
    monkeypatch.setattr(preparation_module, "capture_offset", capture)
    monkeypatch.setattr(preparation_module, "seek_paused", seek)
    monkeypatch.setattr(preparation_module, "step_frames", step)
    monkeypatch.setattr(preparation_module, "playback_shortcuts", Mock())
    monkeypatch.setattr(
        preparation_module, "render_custom_video_player", lambda *a, **kw: ui.video("/test.mp4")
    )

    @ui.page("/reference-preparation-test")
    async def preparation():
        data = distance_data
        page = DistanceReviewPage(data["dp"], data["project"].id, {}, [])
        page.reference = data["reference"]
        page.reference_duration = "10.0"  # ffprobe's JSON duration is a string.
        page.reference_player = SimpleNamespace(source="/test.mp4")
        page.render_frames = SimpleNamespace(refresh=Mock())
        await page.prepare_reference_dialog()
        holder["page"] = page

    return holder, distance_data, capture, seek, step


async def wait_for(predicate):
    for _ in range(100):
        await asyncio.sleep(0.01)
        if predicate():
            return
    raise AssertionError("Reference preparation did not finish")


@pytest.mark.parametrize("offset", [1000, 9500, 333.334])
async def test_saving_prefills_next_integer_and_keeps_selected_frame(
    user: User, preparation_page, offset
):
    holder, data, capture, seek, _ = preparation_page
    capture.return_value = offset
    await user.open("/reference-preparation-test")
    number = next(iter(user.find(ui.number).elements))
    assert number.props["step"] == 1 and number.precision == 0
    assert number.value == 6
    seek.reset_mock()
    user.find("Save current frame").click()
    await wait_for(lambda: number.value == 7 and number.enabled)
    frame = next(
        row
        for row in data["dp"].list_reference_frames(data["reference"]["id"])
        if row["offset_ms"] == int(offset + 0.5)
    )
    assert frame["distance_m"] == 6
    assert frame["playback_offset_ms"] == offset
    capture.assert_awaited_once_with(next(iter(user.find(ui.video).elements)), exact=True)
    seek.assert_not_called()
    assert holder["page"].frames == data["dp"].list_reference_frames(data["reference"]["id"])


async def test_failed_save_keeps_number_and_playback_offset(user: User, preparation_page):
    _, data, capture, seek, _ = preparation_page
    capture.return_value = data["frame"]["offset_ms"]
    await user.open("/reference-preparation-test")
    number = next(iter(user.find(ui.number).elements))
    seek.reset_mock()
    user.find("Save current frame").click()
    await user.should_see("A reference frame is already saved at this time.")
    assert number.value == 6 and number.enabled
    seek.assert_not_called()
    assert len(data["dp"].list_reference_frames(data["reference"]["id"])) == 1


async def test_frame_buttons_operate_on_preparation_player(user: User, preparation_page):
    _, _, _, _, step = preparation_page
    await user.open("/reference-preparation-test")
    player = next(iter(user.find(ui.video).elements))
    for delta in (-5, -1, 1, 5):
        user.find(f"{delta:+d}").click()
        await wait_for(lambda: step.await_count == (-5, -1, 1, 5).index(delta) + 1)
        step.assert_awaited_with(player, delta, 25)


async def test_loading_displays_saved_frames_and_opens_at_latest_annotation(
    user: User, preparation_page
):
    _, data, _, seek, _ = preparation_page
    data["dp"].add_reference_frame(data["reference"]["id"], 1000, 9)
    await user.open("/reference-preparation-test")
    await user.should_see("Prepared reference frames")
    await user.should_see("9 m · 00:01.000")
    await user.should_see("5 m · 00:03.500")
    assert next(iter(user.find(ui.number).elements)).value == 6
    player = next(iter(user.find(ui.video).elements))
    seek.assert_called_once_with(player, 3500)
    seek.reset_mock()
    user.find("9 m · 00:01.000").click()
    seek.assert_called_once_with(player, 1000)
    assert len(data["dp"].list_reference_frames(data["reference"]["id"])) == 2


async def test_new_reference_starts_at_first_frame_with_one_meter(user: User, preparation_page):
    _, data, _, seek, _ = preparation_page
    data["reference"] = data["dp"].register_reference_video(
        data["project"].id, "cam_x", "references/new.mp4"
    )
    await user.open("/reference-preparation-test")
    assert next(iter(user.find(ui.number).elements)).value == 1
    seek.assert_called_once_with(next(iter(user.find(ui.video).elements)), 0)
    await user.should_not_see("Prepared reference frames")


async def test_saving_updates_visible_annotations_and_skips_existing_numbers(
    user: User, preparation_page
):
    _, data, capture, seek, _ = preparation_page
    data["dp"].add_reference_frame(data["reference"]["id"], 1000, 7)
    capture.return_value = 5500
    await user.open("/reference-preparation-test")
    number = next(iter(user.find(ui.number).elements))
    assert number.value == 6
    seek.reset_mock()
    user.find("Save current frame").click()
    await wait_for(lambda: number.value == 8 and number.enabled)
    await user.should_see("6 m · 00:05.500")
    await user.should_see("7 m · 00:01.000")
    seek.assert_not_called()


@pytest.mark.parametrize(
    "distance,offset,expected_distance",
    [(7.25, 7500, 8), (12, 9500, 13), (5, 333.334, 6)],
)
async def test_loading_preserves_saved_distances_and_opens_at_saved_position(
    user: User, preparation_page, distance, offset, expected_distance
):
    _, data, _, seek, _ = preparation_page
    data["reference"] = data["dp"].register_reference_video(
        data["project"].id, "cam_x", "references/precise.mp4"
    )
    data["dp"].add_reference_frame(
        data["reference"]["id"], int(offset + 0.5), distance, playback_offset_ms=offset
    )
    await user.open("/reference-preparation-test")
    await user.should_see(f"{distance:g} m · {preparation_module.frame_time(offset)}")
    assert next(iter(user.find(ui.number).elements)).value == expected_distance
    seek.assert_called_once_with(next(iter(user.find(ui.video).elements)), offset)
