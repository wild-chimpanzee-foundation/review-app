"""Inspection filtering never silently changes the work being delegated."""

from nicegui import ui
from nicegui.testing import User
from review_app.app.components.assignment_video_table import AssignmentVideoTable
from review_app.app.translations import t

pytest_plugins = ["nicegui.testing.user_plugin"]


async def test_search_sort_camera_filter_and_selection_preserve_hidden_work(user: User):
    @ui.page("/video-selection")
    def page():
        status = ui.label("").mark("selected-ids")
        table = AssignmentVideoTable(
            [("path", "Video"), ("camera", "Camera")],
            filters=[("camera", "Camera")],
            selectable=True,
            on_selection=lambda ids: status.set_text(",".join(ids)),
            marker="videos",
        )
        table.set_rows(
            [
                {"id": "a", "path": "clip-a.mp4", "camera": "cam-a"},
                {"id": "b", "path": "clip-b.mp4", "camera": "cam-b"},
                {"id": "c", "path": "clip-c.mp4", "camera": "cam-a"},
            ],
            select_all=True,
        )

    await user.open("/video-selection")
    table = next(iter(user.find(marker="videos").elements))
    assert table.pagination["sortBy"] == "path"
    assert all(column["sortable"] for column in table.columns)
    user.find(t("assign_video_search"), kind=ui.input).type("clip-b")
    assert [row["id"] for row in table.rows] == ["b"]
    await user.should_see("a,b,c")
    user.find(t("assign_deselect_visible"), kind=ui.button).click()
    await user.should_see("a,c")
    with user:
        next(iter(user.find(t("assign_video_search"), kind=ui.input).elements)).set_value("")
        next(iter(user.find(kind=ui.select, content="Camera").elements)).set_value("cam-a")
    assert [row["id"] for row in table.rows] == ["a", "c"]
    assert [row["id"] for row in table.selected] == ["a", "c"]
    user.find(marker="videos").trigger(
        "selection",
        {
            "added": False,
            "rows": [table.rows[0]],
            "keys": ["a"],
        },
    )
    assert next(iter(user.find(marker="selected-ids").elements)).text == "c"
