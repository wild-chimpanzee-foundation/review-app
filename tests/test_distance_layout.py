"""Layout controls remain usable independently of hidden navigation and drawers."""

import pytest
from nicegui import app, ui
from nicegui.testing import User
from review_app.app import entry_point
from review_app.app.components.review_layout import ReviewLayout

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture(params=["distance", "review"])
def layout_page(monkeypatch, request):
    monkeypatch.setattr(entry_point, "get_active_project_id", lambda: None)
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)
    holder = {}

    @ui.page("/distance-layout-test")
    def page():
        layout = ReviewLayout(
            ui.right_drawer(value=True),
            scope=request.param,
            compact_details=request.param == "distance",
        )
        entry_point.shared_header(show_drawer=True, review_layout=layout)
        holder["layout"] = layout

    return holder


def click(user, button):
    interaction = user.find(ui.button)
    interaction.elements = {button}
    interaction.click()


async def test_toolbar_distributes_controls_without_title(user: User, layout_page):
    await user.open("/distance-layout-test")
    layout = layout_page["layout"]
    children = list(layout.toolbar)
    assert children[0] is layout.buttons["filters"]
    assert "workspace-layout-center" in children[1].classes
    assert list(children[1]) == [layout.buttons["navigation"], layout.focus_button]
    assert children[2] is layout.buttons["details"]
    assert not any(
        isinstance(element, ui.label) and element.text == "Distance"
        for element in layout.toolbar.descendants()
    )
    listener = next(iter(layout.focus_button._event_listeners.values()))
    assert listener.handler == layout.toggle_focus
    assert f"getHtmlElement({layout.focus_button.id})" in listener.js_handler


async def test_layout_and_focus_restore_across_visits(user: User, layout_page):
    await user.open("/distance-layout-test")
    layout = layout_page["layout"]
    click(user, layout.buttons["navigation"])
    click(user, layout.buttons["details"])
    expected = {"filters": True, "navigation": False, "details": False}
    with user:
        assert app.storage.user[layout.storage_key]["panels"] == expected

    await user.open("/distance-layout-test")
    layout = layout_page["layout"]
    assert layout.panels == expected
    assert not layout.header.value
    assert layout.right_drawer.value is layout.compact_details
    assert layout.right_drawer.props["mini"] is layout.compact_details
    assert layout.toolbar.visible
    click(user, layout.focus_button)
    assert layout.right_drawer.value is layout.compact_details
    assert layout.right_drawer.props["mini"] is layout.compact_details

    await user.open("/distance-layout-test")
    layout = layout_page["layout"]
    assert layout.focused and layout.focus_button.text == "Exit focus"
    assert not layout.left_drawer.value and not layout.header.value
    assert layout.right_drawer.value is layout.compact_details
    assert layout.right_drawer.props["mini"] is layout.compact_details
    click(user, layout.focus_button)
    assert layout.panels == expected
    assert layout.left_drawer.value and not layout.header.value
    assert layout.right_drawer.value is layout.compact_details
    assert layout.right_drawer.props["mini"] is layout.compact_details


@pytest.mark.parametrize("name", ["filters", "navigation", "details"])
async def test_panel_changes_keep_focus_until_explicit_exit(
    user: User, layout_page, monkeypatch, name
):
    await user.open("/distance-layout-test")
    layout = layout_page["layout"]
    previous = layout.panels.copy()
    scripts = []
    monkeypatch.setattr(ui, "run_javascript", scripts.append)
    click(user, layout.focus_button)
    click(user, layout.buttons[name])
    assert not scripts  # Only the Focus button's browser-side handler changes fullscreen.
    assert layout.focused and layout.focus_button.text == "Exit focus"
    assert layout.focus_button.props["aria-pressed"] == "true"
    assert layout.before_focus == previous
    assert layout.left_drawer.value is (name == "filters")
    assert layout.header.value is (name == "navigation")
    assert layout.right_drawer.value is (layout.compact_details or name == "details")
    assert layout.right_drawer.props["mini"] is (layout.compact_details and name != "details")
    assert layout.buttons[name].props["aria-pressed"] == "true"
    with user:
        assert app.storage.user[layout.storage_key]["before_focus"] == previous

    click(user, layout.focus_button)
    assert not layout.focused and layout.focus_button.text == "Focus"
    assert layout.panels == previous
    assert layout.left_drawer.value and layout.header.value and layout.right_drawer.value
    assert not layout.right_drawer.props["mini"]


async def test_hidden_filters_keep_active_count_visible(user: User, layout_page):
    await user.open("/distance-layout-test")
    layout = layout_page["layout"]
    layout.set_filter_count(2)
    click(user, layout.buttons["filters"])
    assert not layout.left_drawer.value
    assert layout.filter_badge.visible and layout.filter_badge.text == "2"
    assert layout.buttons["filters"].props["aria-label"] == "Filters: 2 active filters"
    layout.set_filter_count(0)
    assert not layout.filter_badge.visible
