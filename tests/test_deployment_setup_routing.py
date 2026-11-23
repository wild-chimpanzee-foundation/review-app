"""Project setup hands flagged folders to deployment review after the initial sync."""

import sqlite3
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation
from review_app.app import setup_wizard, state
from review_app.app.entry_point import GUI
from review_app.app.translations import t
from review_app.backend.video_tools import VideoToolsStatus
from sqlalchemy.exc import OperationalError


@pytest.mark.parametrize("review_now", [True, False])
async def test_setup_routes_flagged_reference_folders_after_sync(
    provider_with_project, tmp_path, monkeypatch, review_now
):
    dp, original, _ = provider_with_project
    root = tmp_path / "fieldwork"
    for relative in (
        "videos/cam_a/animal.mp4",
        "ref/cam_a/matched.mp4",
        "ref/cam_b/first.mp4",
        "ref/cam_c/second.mp4",
    ):
        clip = root / relative
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.touch()

    monkeypatch.setattr(setup_wizard, "get_default_db_path", lambda: Path(dp.db_path))
    monkeypatch.setattr(
        setup_wizard,
        "check_video_tools",
        lambda: VideoToolsStatus({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}),
    )
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)
    attempts = []

    async def sync_with_retry(provider, *, video_dir, active_project_id, **_):
        attempts.append(active_project_id)
        if review_now and len(attempts) == 1:
            raise OperationalError("SELECT 1", (), sqlite3.OperationalError("Test sync failure"))
        return provider.sync_videos(video_dir=Path(video_dir), active_project_id=active_project_id)

    monkeypatch.setattr(setup_wizard, "sync_with_progress", sync_with_retry)
    state.set_data_provider(dp)
    state.set_annotator_name("Alice")
    state.set_active_project(original.id)
    gui = GUI()

    try:
        async with user_simulation() as user:

            @ui.page("/setup")
            def setup():
                gui.setup_page()

            @ui.page("/")
            def home():
                gui.main_page()

            @ui.page("/overview")
            async def overview():
                await gui.overview_page()

            @ui.page("/deployments")
            async def deployments(review: str = ""):
                await gui.deployments_page(review=review)

            await user.open("/setup")
            user.find(kind=ui.input, marker="project-footage-folder").type(str(root))
            await user.should_see(t("deployment_setup_unmatched", count=2, folders=2), retries=20)
            assert any(
                isinstance(element, ui.expansion)
                and element.props.get("label") == t("deployment_setup_folders")
                and not element.value
                for element in user.client.elements.values()
            )
            flagged = {"ref/cam_b", "ref/cam_c"}
            assert any(
                isinstance(element, ui.table)
                and {row["folder"] for row in element.rows} == flagged
                for element in user.client.elements.values()
            )

            action = (
                "deployment_setup_create_review" if review_now else "deployment_setup_create_later"
            )
            user.find(kind=ui.button, content=t(action)).click()
            if review_now:
                await user.should_see(
                    t("deployment_setup_sync_failed", error="Test sync failure"), retries=20
                )
                assert user.back_history[-1] == "/setup"
                user.find(kind=ui.button, content=t("handoff_retry_sync")).click()
            else:
                await user.should_see(t("deployment_review_references"), retries=20)
                assert user.back_history[-1] == "/overview"
                user.find(kind=ui.button, content=t("deployment_review_references")).click()

            await user.should_see(t("deployment_review_position", current=1, total=2), retries=20)
            assert user.back_history[-1] == "/deployments?review=references"
            assert len(dp.list_projects()) == 2
            created = state.get_active_project_id()
            assert dp.get_project_video_count(created) == 4
            assert attempts == [created] * (2 if review_now else 1)
            await user.should_see(t("deployment_review_preview_help"), retries=20)
            user.find(kind=ui.button, content=t("deployment_review_skip")).click()
            await user.should_see(t("deployment_review_position", current=2, total=2), retries=20)
            await user.should_see(t("deployment_review_preview_help"), retries=20)
            await user.should_see(marker="deployment-review-workspace")
            user.find(kind=ui.button, content=t("deployment_manage")).click()
            await user.should_see(t("deployment_management_help"), retries=20)
            assert user.back_history[-1] == "/deployments"
    finally:
        state.reset_app_state(keep_prefs=False)
