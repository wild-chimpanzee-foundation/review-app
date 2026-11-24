"""Startup requires an annotator and offers existing projects before entering review."""

from threading import Event

import pytest
from nicegui import app, ui
from nicegui.testing.user_simulation import user_simulation
from review_app.app import state
from review_app.app.entry_point import GUI
from review_app.app.translations import t
from review_app.backend.db.models import Deployment, Video


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


async def test_opening_legacy_project_directly_in_distance_initializes_deployments(
    tmp_db, monkeypatch
):
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    dp = LocalDataProvider()
    root = tmp_db["video_dir"]
    project = dp.create_project("Legacy project", str(root))
    with dp.Session.begin() as session:
        for clip_id, folder, label in (
            ("animal", "videos", "cam_a/videos"),
            ("reference", "references", "cam_a/references"),
            ("unassigned", "unknown", None),
        ):
            session.add(
                Video(
                    video_id=clip_id,
                    project_id=project.id,
                    folder_camera_label=label,
                    video_path=str(root / "cam_a" / folder / f"{clip_id}.mp4"),
                )
            )
    dp.set_video_tags("reference", ["reference_video"])

    async def review():
        with dp.Session() as session:
            deployments = {row.deployment_id: row.id for row in session.query(Deployment)}
            assert set(deployments) == {"cam_a/videos", "cam_a/references"}
            assert session.get(Video, "animal").deployment_id == deployments["cam_a/videos"]
            assert session.get(Video, "reference").deployment_id == deployments["cam_a/references"]
            assert session.get(Video, "unassigned").deployment_assignment == "unresolved"
        ui.label("Distance Review ready")

    monkeypatch.setattr("review_app.app.pages.distance_review.setup_distance_review", review)
    state.set_data_provider(dp)
    try:
        async with user_simulation() as user:

            async def distance_review():
                await GUI().distance_review_page()

            ui.page("/distance-review")(distance_review)
            state.set_annotator_name("Alice")
            state.set_active_project(project.id)
            await user.open("/distance-review")
            await user.should_see("Distance Review ready")
            assert not any(
                isinstance(element, ui.dialog) for element in user.client.elements.values()
            )

            inventory = {
                row["deployment_id"]: row["id"] for row in dp.get_project_deployments(project.id)
            }
            assert dp.get_distance_reference_videos("animal") == []
            preview = dp.preview_deployment_assignment(
                project.id, ["reference"], inventory["cam_a/videos"]
            )
            dp.apply_deployment_assignment(preview)
            references = dp.get_distance_reference_videos("animal")
            assert [row["source_video_id"] for row in references] == ["reference"]
            assert references[0]["deployment_id"] == inventory["cam_a/videos"]
    finally:
        state.set_data_provider(None)
        dp.engine.dispose()


@pytest.mark.parametrize("succeeds", [True, False])
async def test_slow_deployment_initialization_shows_and_closes_dialog(
    provider_with_project, monkeypatch, succeeds
):
    dp, project, _ = provider_with_project
    proceed = Event()

    def initialize(project_id):
        assert project_id == project.id
        assert proceed.wait(timeout=10)
        if not succeeds:
            raise ValueError("Test initialization failure")
        return True

    async def review():
        ui.label("Distance Review ready")

    monkeypatch.setattr(dp, "initialize_project_deployments", initialize)
    monkeypatch.setattr("review_app.app.pages.distance_review.setup_distance_review", review)
    state.set_data_provider(dp)
    try:
        async with user_simulation() as user:

            @ui.page("/distance-review")
            async def distance_review():
                state.set_annotator_name("Alice")
                state.set_active_project(project.id)
                await GUI().distance_review_page()

            try:
                await user.open("/distance-review")
                await user.should_see(t("deployment_initialization_progress"))
                (dialog,) = (
                    element
                    for element in user.client.elements.values()
                    if isinstance(element, ui.dialog)
                )
                assert dialog.value and dialog.props["persistent"]
                await user.should_not_see("Distance Review ready")
            finally:
                proceed.set()

            if succeeds:
                await user.should_see("Distance Review ready")
            else:
                await user.should_see(
                    t("deployment_initialization_failed", error="Test initialization failure")
                )
                await user.should_not_see("Distance Review ready")
            assert not dialog.value
    finally:
        state.set_data_provider(None)
