import gzip
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing.user_interaction import UserInteraction
from nicegui.testing.user_simulation import user_simulation
from review_app.app import setup_wizard, state
from review_app.app.translations import t
from review_app.backend.db.backup import create_backup, list_backups
from review_app.backend.provider.local_data_provider import LocalDataProvider
from review_app.backend.video_tools import VideoToolsStatus


class TestHasAiAnnotationsCheck:
    """The review-page tour checks ``dp.has_model_annotations()``
    to decide which text to show for the AI-predictions step."""

    def test_false_when_no_model_annotations_exist(self, provider_with_project):
        dp, *_ = provider_with_project
        assert dp.has_model_annotations() is False

    def test_true_when_model_annotations_exist(self, populated_provider):
        dp, *_ = populated_provider
        assert dp.has_model_annotations() is True


@pytest.mark.parametrize("source", ["listed", "db", "db.gz"])
async def test_setup_restores_database_backup(tmp_db, monkeypatch, source):
    provider = LocalDataProvider()
    provider.set_setting("restore_test", "restored value")
    candidate = create_backup(reason="test")
    db_path = Path(provider.db_path)
    provider.engine.dispose()
    db_path.unlink()
    compressed = candidate.read_bytes()
    if source == "listed":
        candidate.with_suffix("").write_bytes(gzip.decompress(compressed))
    else:
        candidate.unlink()

    monkeypatch.setattr(setup_wizard, "get_default_db_path", lambda: db_path)
    monkeypatch.setattr(
        setup_wizard,
        "check_video_tools",
        lambda: VideoToolsStatus({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}),
    )
    state.set_data_provider(None)
    try:
        async with user_simulation() as user:

            @ui.page("/restore-onboarding")
            def page():
                setup_wizard.SetupWizard(lambda: ui.label("Database opened")).build()

            await user.open("/restore-onboarding")
            await user.should_see(t("installed"))
            user.find(kind=ui.button, content=t("tour_next")).click()
            label = next(
                iter(user.find(kind=ui.label, content=t("wizard_restore_start")).elements)
            )
            card = label.parent_slot.parent.parent_slot.parent
            UserInteraction(user, {card}, None).click()
            await user.should_see(t("upload_backup_btn"))
            if source == "listed":
                backup = list_backups()[0]
                await user.should_see(backup["name"])
                options = [
                    button
                    for button in user.find(ui.button).elements
                    if button.props.get("icon") == "restore"
                ]
                assert len(options) == 1
                UserInteraction(user, set(options), None).click()
            else:
                uploader = next(
                    element
                    for element in user.client.elements.values()
                    if isinstance(element, ui.upload)
                    and element.props.get("accept") == ".db,.db.gz"
                )
                data = compressed if source == "db.gz" else gzip.decompress(compressed)
                with user:
                    await uploader.handle_uploads(
                        [SmallFileUpload(f"backup.{source}", "application/octet-stream", data)]
                    )

            await user.should_see("Database opened", retries=20)
            assert state.get_data_provider().get_setting("restore_test") == "restored value"
    finally:
        restored = state.get_data_provider()
        if restored is not None:
            restored.engine.dispose()
        state.set_data_provider(None)
