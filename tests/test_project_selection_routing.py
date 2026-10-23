"""Accounts without a remembered project must be able to choose an existing one."""

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app import state
from review_app.app.entry_point import GUI
from review_app.app.translations import t

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def project_routes(provider_with_project, monkeypatch):
    dp, project, _ = provider_with_project
    state.reset_app_state(keep_prefs=False)
    state.set_data_provider(dp)
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)
    gui = GUI()

    @ui.page("/")
    def home():
        gui.main_page()

    @ui.page("/login")
    async def login():
        await gui.login_page()

    @ui.page("/overview")
    async def overview():
        await gui.overview_page()
        ui.label("overview-ready").mark("overview-ready")

    @ui.page("/setup")
    def setup():
        ui.label("new-project-setup")

    yield dp, project
    state.reset_app_state(keep_prefs=False)


async def login_as(user, name):
    await user.open("/login")
    await user.should_see(ui.select)
    for select in user.find(ui.select).elements:
        select.options.append(name)
        select.set_value(name)
    user.find(kind=ui.button, content=t("login_continue")).click()


@pytest.mark.parametrize("name", ["alice", "bob", "new-annotator"])
async def test_login_without_preference_opens_picker_and_selection_is_remembered(
    user: User, project_routes, name
):
    dp, project = project_routes
    await login_as(user, name)
    await user.should_see(marker="project-picker")
    await user.should_not_see("new-project-setup")
    assert user.back_history[-1] == "/"
    assert any(
        isinstance(element, ui.dialog) and element.value
        for element in user.client.elements.values()
    )

    user.find(kind=ui.button, content=project.name).click()
    await user.should_see(marker="overview-ready")
    assert user.back_history[-1] == "/overview"
    assert dp.get_annotator_active_project(name) == project.id

    state.clear_session()
    await login_as(user, name)
    await user.should_see(marker="overview-ready")
    assert user.back_history[-1] == "/overview"
    assert state.get_active_project_id() == project.id


async def test_direct_overview_without_selection_keeps_header_picker(user: User, project_routes):
    _, project = project_routes
    state.set_annotator_name("alice")
    await user.open("/overview")
    await user.should_see(marker="project-picker")
    assert state.get_active_project_id() is None
    user.find(marker="project-picker").click()
    await user.should_see(project.name)


async def test_deleted_preference_returns_to_picker(user: User, project_routes, tmp_path):
    dp, _ = project_routes
    directory = tmp_path / "deleted"
    directory.mkdir()
    deleted = dp.create_project("Deleted", str(directory))
    dp.set_annotator_active_project("alice", deleted.id)
    dp.delete_project(deleted.id)
    await login_as(user, "alice")
    await user.should_see(marker="project-picker")
    assert user.back_history[-1] == "/"
    await user.should_not_see("new-project-setup")


async def test_no_projects_still_routes_to_setup(user: User, project_routes):
    dp, project = project_routes
    dp.delete_project(project.id)
    await login_as(user, "alice")
    await user.should_see("new-project-setup")
    assert user.back_history[-1] == "/setup"
