"""Startup requires an annotator and offers existing projects before entering review."""

import pytest
from nicegui import app, ui
from nicegui.testing.user_simulation import user_simulation
from review_app.app import state
from review_app.app.entry_point import GUI
from review_app.app.translations import t


@pytest.mark.parametrize("entry", ["/", "/overview"])
async def test_startup_reselects_annotator_and_opens_project_picker(
    provider_with_project, monkeypatch, entry
):
    dp, project, _ = provider_with_project
    dp.add_annotator("Alice")
    dp.set_setting("active_project_id", project.id)
    state.set_data_provider(dp)
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)

    async def overview():
        ui.label("Overview ready")

    monkeypatch.setattr("review_app.app.pages.overview.setup_overview", overview)
    try:
        async with user_simulation() as user:
            gui = GUI()

            @ui.page("/")
            def home():
                gui.main_page()

            @ui.page("/login")
            async def login():
                await gui.login_page()

            @ui.page("/overview")
            async def overview_route():
                await gui.overview_page()

            @ui.page("/setup")
            def setup():
                ui.label("New-project setup")

            app.storage.user.update(annotator_name="Alice", active_project_id=project.id)
            await user.open(entry)
            await user.should_see(t("login_title"), retries=10)
            select = next(iter(user.find(ui.select).elements))
            assert select.value is None
            select.set_value("Alice")
            user.find(kind=ui.button, content=t("login_continue")).click()

            await user.should_see(project.name, retries=10)
            assert state.get_active_project_id() is None
            assert any(
                isinstance(element, ui.dialog) and element.value
                for element in user.client.elements.values()
            )
            user.find(kind=ui.button, content=project.name).click()
            await user.should_see("Overview ready", retries=10)
            assert state.get_annotator_name() == "Alice"
            assert state.get_active_project_id() == project.id

            await user.open("/")
            await user.should_see("Overview ready", retries=10)
    finally:
        state.set_data_provider(None)
