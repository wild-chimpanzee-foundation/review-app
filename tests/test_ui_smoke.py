"""Minimal UI smoke tests: each page renders without raising.

These use NiceGUI's simulated User (no browser). They only guard against the
most common regression — a backend refactor breaking a page at render time —
and intentionally do not test interactions.
"""

from __future__ import annotations

import asyncio

import pytest
from nicegui import app, ui
from nicegui.testing import User
from review_app.app.entry_point import GUI
from review_app.app.state import get_filters, set_data_provider
from review_app.app.translations import t

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def smoke_app(provider_with_project):
    """Register the app's pages and prime a logged-in session with an active project."""
    dp, project, _ = provider_with_project
    set_data_provider(dp)

    gui = GUI()

    # Register wrappers defined in this module rather than the GUI methods directly:
    # NiceGUI's test teardown evicts each page handler's module (and parent packages)
    # from sys.modules, which would unload review_app between tests.
    def _register(path, handler):
        @ui.page(path)
        async def _page():
            await handler()

    _register("/overview", gui.overview_page)
    _register("/review", gui.review_page)
    _register("/distance-review", gui.distance_review_page)
    _register("/settings", gui.settings_page)
    _register("/model-import", gui.model_import_page)
    _register("/distribution", gui.distribution_page)

    @ui.page("/test-login")
    def _test_login():
        app.storage.user["annotator_name"] = "tester"
        app.storage.user["active_project_id"] = project.id

    yield
    set_data_provider(None)


@pytest.mark.parametrize(
    "path",
    ["/overview", "/review", "/distance-review", "/settings", "/model-import", "/distribution"],
)
async def test_page_renders(user: User, smoke_app, path: str) -> None:
    await user.open("/test-login")
    await user.open(path)
    # require_login()/setup redirects would land us somewhere else
    assert user.back_history[-1] == path


@pytest.mark.parametrize("saved_focus", [False, True])
async def test_review_toolbar_preserves_draft_and_updates_filter_badge(
    user: User, smoke_app, monkeypatch, saved_focus
):
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)
    await user.open("/test-login")
    with user:
        app.storage.user["tour_completed"] = True
        previous = {"filters": True, "navigation": True, "details": False}
        app.storage.user["review_layout"] = {
            "panels": dict.fromkeys(previous, False) if saved_focus else previous,
            "before_focus": previous if saved_focus else None,
        }
        app.storage.user["filters"] = {
            "selected_camera": "cam_x",
            "selected_sort": "random",
            "selected_sort_direction": "asc",
        }
    await user.open("/review")
    if saved_focus:
        await user.should_see(t("workspace_exit_focus"))
        right = next(e for e in user.client.elements.values() if isinstance(e, ui.right_drawer))
        assert right.value and not right.props["mini"]
        user.find(t("workspace_exit_focus")).click()
    await user.should_see(t("camera_filter"))
    right = next(e for e in user.client.elements.values() if isinstance(e, ui.right_drawer))
    left = next(e for e in user.client.elements.values() if isinstance(e, ui.left_drawer))
    header = next(e for e in user.client.elements.values() if isinstance(e, ui.header))
    toolbar = next(
        e for e in user.client.elements.values() if "workspace-layout-toolbar" in e.classes
    )
    assert right.value and not right.props["mini"]
    assert any(isinstance(e, ui.button) and e.text == "Species" for e in header.descendants())
    assert any(
        isinstance(e, ui.item_section)
        and e.text == "Species"
        and isinstance(e.parent_slot.parent, ui.menu_item)
        for e in header.descendants()
    )
    workspace = next(e for e in user.client.elements.values() if "review-workspace" in e.classes)
    section = next(e for e in workspace.descendants() if "review-video-section" in e.classes)
    panel = next(e for e in section if "review-video-panel" in e.classes)
    assert "q-pa-none" in panel.classes
    assert "gap-1" in panel.classes
    assert "q-pa-xs" not in workspace.classes
    assert "w-full" in section.classes and "full-width" in panel.classes
    element = panel
    while element is not workspace:
        assert "max-width" not in element.style
        element = element.parent_slot.parent
    assert "w-full" in workspace.classes and "max-width" not in workspace.style
    assert {
        e.props.get("data-testid") for e in toolbar.descendants() if isinstance(e, ui.button)
    } == {"review-toggle-filters", "review-toggle-navigation", "review-toggle-focus"}
    with user, right:
        draft = ui.input("Layout test draft", value="keep me")
    badge = next(e for e in toolbar.descendants() if isinstance(e, ui.badge))
    assert badge.visible and badge.text == "1"  # Sorting does not count as a filter.

    # Changing a real filter updates the toolbar, including when it is later hidden.
    camera = user.find(t("camera_filter"), kind=ui.select)
    camera.click()
    camera.target = t("all_option")
    camera.click()
    for _ in range(100):
        if not badge.visible:
            break
        await asyncio.sleep(0.01)
    assert get_filters()["selected_camera"] == "All" and not badge.visible

    def click_control(name):
        interaction = user.find(ui.button)
        interaction.elements = {
            e
            for e in interaction.elements
            if e.props.get("data-testid") == f"review-toggle-{name}"
        }
        assert len(interaction.elements) == 1
        interaction.click()

    click_control("filters")
    assert right.value and not left.value
    click_control("navigation")
    assert right.value and not left.value and not header.value
    assert toolbar.visible
    click_control("navigation")
    assert right.value and header.value and not left.value
    click_control("focus")
    assert right.value and not right.props["mini"]
    assert not header.value and not left.value
    assert toolbar.visible and draft.value == "keep me"
    click_control("focus")
    assert right.value and header.value and not left.value
    assert draft.value == "keep me"
    with user:
        assert app.storage.user["review_layout"]["panels"] == {
            "filters": False,
            "navigation": True,
            "details": True,
        }
        assert "distance_layout" not in app.storage.user
