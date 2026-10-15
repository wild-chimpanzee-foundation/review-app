"""Distance completion warnings, species context, and queue progression."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def completion_page(monkeypatch):
    holder = {}
    monkeypatch.setattr(page_module, "get_distance_queue_state", lambda _: {})
    monkeypatch.setattr(page_module, "set_distance_queue_state", Mock())
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(page_module.run, "io_bound", io_bound)

    @ui.page("/distance-completion-test")
    def show():
        dp = SimpleNamespace(finish_distance_video=Mock(), get_distance_video_queue=Mock())
        page = DistanceReviewPage(dp, "project", {"deer": "Deer", "fox": "Fox"}, [])
        page.queue = [{"video_id": "current"}, {"video_id": "next"}]
        dp.get_distance_video_queue.return_value = list(page.queue)
        page.individual_id = "animal"
        page.individuals = [{"id": "animal", "species_id": "deer", "review_status": "completed"}]
        page.review = {"completed_at": None}
        page.sample_offset = 1000
        page.species_filter = "deer"
        page.load_video = AsyncMock()
        page.refresh_sample_ui = Mock()
        for name in ("render_workspace", "render_sidebar", "render_sample_navigation"):
            setattr(page, name, SimpleNamespace(refresh=Mock()))
        page.render_sample_actions()
        holder["page"] = page

    return holder


async def wait_until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Completion did not settle")


async def test_finish_without_discrepancies_is_prominent_and_advances_directly(
    user: User, completion_page
):
    await user.open("/distance-completion-test")
    page = completion_page["page"]
    button = next(
        element
        for element in user.client.elements.values()
        if element.props.get("data-distance-action") == "finish-video"
    )
    assert button.props["color"] == "positive"
    assert button.props["size"] == "md"
    page.reviewed_species_counts = [{"species_id": "deer", "count": 1}]
    user.find("Finish distance review").click()
    await wait_until(lambda: page.load_video.await_count == 1 and not page.busy)
    assert page.video["video_id"] == "next"
    page.dp.finish_distance_video.assert_called_once_with("current", "alice")
    assert not any(isinstance(element, ui.dialog) for element in user.client.elements.values())


@pytest.mark.parametrize("action", ["Return to annotation", "Ignore and finish"])
async def test_warning_lists_all_reviewed_species_and_only_ignore_finishes(
    user: User, completion_page, action
):
    await user.open("/distance-completion-test")
    page = completion_page["page"]
    page.reviewed_species_counts = [
        {"species_id": "deer", "count": 2},
        {"species_id": "fox", "count": 1},
    ]
    with page.client:
        page.render_reviewed_species()
    await user.should_see("Deer: 2 recorded")
    await user.should_see("Fox: 1 recorded")
    user.find("Finish distance review").click()
    await user.should_see("Deer: 2 recorded in Review; 1 in Distance")
    await user.should_see("Fox: 1 recorded in Review; 0 in Distance")
    page.dp.finish_distance_video.assert_not_called()
    user.find(action).click()
    if action == "Ignore and finish":
        await wait_until(lambda: page.load_video.await_count == 1 and not page.busy)
        assert page.video["video_id"] == "next"
        page.dp.finish_distance_video.assert_called_once_with("current", "alice")
    else:
        await wait_until(
            lambda: all(
                not element.value
                for element in user.client.elements.values()
                if isinstance(element, ui.dialog)
            )
        )
        page.dp.finish_distance_video.assert_not_called()
        page.load_video.assert_not_awaited()
        assert page.video["video_id"] == "current"


@pytest.mark.parametrize("has_next", [False, True])
async def test_completion_handles_video_disappearing_from_filtered_queue(
    user: User, completion_page, has_next
):
    await user.open("/distance-completion-test")
    page = completion_page["page"]
    page.status_filter = "in_progress"
    if not has_next:
        page.queue = page.queue[:1]
    page.dp.get_distance_video_queue.return_value = [{"video_id": "next"}] if has_next else []
    user.find("Finish distance review").click()
    await wait_until(lambda: page.load_video.await_count == 1 and not page.busy)
    assert page.video["video_id"] == ("next" if has_next else "current")
    page.dp.get_distance_video_queue.assert_called_once_with(
        "project", "deer", camera_id=None, status="in_progress"
    )
    if not has_next:
        await user.should_see("Queue ended")


async def test_failed_completion_does_not_advance_or_reload(user: User, completion_page):
    await user.open("/distance-completion-test")
    page = completion_page["page"]
    page.dp.finish_distance_video.side_effect = ValueError("Review every scheduled sample")
    user.find("Finish distance review").click()
    await user.should_see("Review every scheduled sample")
    assert page.video["video_id"] == "current"
    page.dp.get_distance_video_queue.assert_not_called()
    page.load_video.assert_not_awaited()
    assert not page.busy


async def test_finish_stays_disabled_until_every_individual_is_complete(
    user: User, completion_page
):
    await user.open("/distance-completion-test")
    page = completion_page["page"]
    page.individuals[0]["review_status"] = "in_progress"
    with page.client:
        page.render_sample_actions.refresh()
    await wait_until(
        lambda: any(
            element.props.get("data-distance-action") == "finish-video" and not element.enabled
            for element in user.client.elements.values()
        )
    )
    page.dp.finish_distance_video.assert_not_called()
