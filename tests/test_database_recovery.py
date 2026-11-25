import pytest
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction
from nicegui.testing.user_simulation import user_simulation
from review_app.app import state
from review_app.app.components.backup_picker import is_backup_compatible
from review_app.app.entry_point import GUI
from review_app.backend.db.backup import create_backup
from review_app.backend.db.migrations import MIGRATIONS, SchemaVersionError
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.mark.parametrize(
    "version,expected",
    [(None, True), (1, True), (len(MIGRATIONS), True), (len(MIGRATIONS) + 1, False)],
)
def test_backup_compatibility(version, expected):
    assert is_backup_compatible({"schema_version": version}) is expected


async def test_startup_recovery_restores_and_returns_to_login(provider_with_project, monkeypatch):
    dp, project, _ = provider_with_project
    dp.set_setting("blank_threshold", "0.72")
    backup = create_backup(reason="manual", compress=False)
    with dp.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE _schema_version SET version = ?", (len(MIGRATIONS) + 1,))
    dp.engine.dispose()
    create_backup(reason="manual", compress=False)
    with pytest.raises(SchemaVersionError) as error:
        LocalDataProvider()

    gui = GUI()
    gui._startup_error = error.value
    monkeypatch.setattr("review_app.app.entry_point.shared_header", lambda: None)
    monkeypatch.setattr(state, "_data_provider", None)
    monkeypatch.setattr(state, "_blank_threshold", state.GLOBAL_DEFAULTS["blank_threshold"])
    media_dirs = []
    monkeypatch.setattr("review_app.app.entry_point.set_media_dirs", media_dirs.extend)
    try:
        async with user_simulation() as user:

            @ui.page("/", reconnect_timeout=1)
            def home():
                gui.main_page()

            @ui.page("/db-error", reconnect_timeout=1)
            def recovery():
                gui.db_error_page()

            @ui.page("/login", reconnect_timeout=1)
            def login():
                ui.label("Back at login")

            await user.open("/")
            await user.should_see("Restore a backup")
            await user.should_see(backup.name)
            await user.should_see("Backups requiring a newer version (1)")
            assert not next(iter(user.find(ui.expansion).elements)).value
            backup_button = next(
                button
                for button in user.find(ui.button).elements
                if button.props.get("icon") == "restore" and button.enabled
            )
            UserInteraction(user, {backup_button}, None).click()
            await user.should_see(kind=ui.button, content="Restore backup")
            user.find(kind=ui.button, content="Restore backup").click()
            await user.should_see("Back at login")

        assert gui._startup_error is None
        assert state.get_data_provider() is gui.dp
        assert gui.dp.get_project(project.id).name == project.name
        assert state.get_blank_threshold() == 0.72
        assert media_dirs
    finally:
        if gui.dp is not None:
            gui.dp.engine.dispose()
