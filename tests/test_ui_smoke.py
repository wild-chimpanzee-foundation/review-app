"""Minimal UI smoke tests: each page renders without raising.

These use NiceGUI's simulated User (no browser). They only guard against the
most common regression — a backend refactor breaking a page at render time —
and intentionally do not test interactions.
"""

from __future__ import annotations

import pytest
from nicegui import app, ui
from nicegui.testing import User
from review_app.app.entry_point import GUI
from review_app.app.state import set_data_provider

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
    "path", ["/overview", "/review", "/settings", "/model-import", "/distribution"]
)
async def test_page_renders(user: User, smoke_app, path: str) -> None:
    await user.open("/test-login")
    await user.open(path)
    # require_login()/setup redirects would land us somewhere else
    assert user.back_history[-1] == path
