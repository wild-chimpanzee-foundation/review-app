"""Browser-scoped Distance queue resume, independent of Species Review."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.app.state import (
    get_distance_queue_state,
    get_filters,
    set_current_idx,
    set_distance_queue_state,
    update_filters,
)

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def queue_page():
    holder = {}
    dp = SimpleNamespace(
        get_distance_video_queue=Mock(
            return_value=[
                {"video_id": "one", "camera_id": "cam"},
                {"video_id": "two", "camera_id": "cam"},
            ]
        )
    )

    @ui.page("/distance-queue-test")
    async def route():
        page = DistanceReviewPage(dp, "project", {"species": "Species"}, [])
        page.load_video = AsyncMock()
        for name in ("render_workspace", "render_sidebar", "render_sample_navigation"):
            setattr(page, name, SimpleNamespace(refresh=Mock()))
        page.refresh_sample_ui = Mock()
        holder["page"] = page

    return holder, dp


async def test_reload_restores_filters_and_video_by_id(user: User, queue_page):
    holder, dp = queue_page
    await user.open("/distance-queue-test")
    page = holder["page"]
    with page.client:
        update_filters(search_query="ordinary review")
        set_current_idx(7)
        page.species_filter = "species"
        page.camera_filter = "cam"
        page.status_filter = "in_progress"
        await page.load_queue()
        await page.move_video(1)
        assert get_distance_queue_state("project")["video_id"] == "two"

    # Reordering the queue must not change the resumed identity.
    dp.get_distance_video_queue.return_value.reverse()
    await user.open("/distance-queue-test")
    page = holder["page"]
    with page.client:
        await page.load_queue(restore=True)
        assert page.video["video_id"] == "two"
        assert page.index == 0
        dp.get_distance_video_queue.assert_called_with(
            "project", "species", camera_id="cam", status="in_progress"
        )
        assert get_filters()["search_query"] == "ordinary review"
        assert get_distance_queue_state("different-project") == {}
        page.camera_options = ["cam"]
        page.render_filters()
        assert [
            element.value
            for element in page.client.elements.values()
            if isinstance(element, ui.select)
        ] == ["species", "cam", "in_progress"]


@pytest.mark.parametrize("rows", [[{"video_id": "one", "camera_id": "cam"}], []])
async def test_missing_selected_video_falls_back_safely(user: User, queue_page, rows):
    holder, dp = queue_page
    await user.open("/distance-queue-test")
    page = holder["page"]
    dp.get_distance_video_queue.return_value = rows
    with page.client:
        set_distance_queue_state("project", video_id="removed")
        await page.load_queue(restore=True)
        assert page.index == 0
        assert get_distance_queue_state("project")["video_id"] == ("one" if rows else None)


async def test_filter_change_starts_at_first_video(user: User, queue_page):
    holder, _ = queue_page
    await user.open("/distance-queue-test")
    page = holder["page"]
    with page.client:
        await page.load_queue()
        await page.move_video(1)
        page.status_filter = "completed"
        await page.change_queue()
        assert page.video["video_id"] == "one"
        assert get_distance_queue_state("project")["status_filter"] == "completed"


async def test_stale_species_and_status_filters_are_cleared(user: User, queue_page):
    holder, _ = queue_page
    await user.open("/distance-queue-test")
    with holder["page"].client:
        set_distance_queue_state("project", species_filter="deleted", status_filter="invalid")
    await user.open("/distance-queue-test")
    assert holder["page"].species_filter is None
    assert holder["page"].status_filter is None
