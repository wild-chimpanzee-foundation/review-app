import pytest
import review_app.app.state as state


@pytest.fixture(autouse=True)
def reset_state():
    state.reset_app_state()
    yield
    state.reset_app_state()


class TestParseBool:
    def test_none_returns_default_true(self):
        assert state._parse_bool(None, True) is True

    def test_none_returns_default_false(self):
        assert state._parse_bool(None, False) is False

    def test_string_true(self):
        assert state._parse_bool("True", False) is True

    def test_string_false(self):
        assert state._parse_bool("False", True) is False

    def test_arbitrary_string_is_false(self):
        assert state._parse_bool("anything", True) is False


class TestDefaultFilters:
    def test_reset_filters_equals_default(self):
        state.update_filters(search_query="changed", selected_camera="SomeCam")
        assert state.get_filters()["search_query"] == "changed"
        state.reset_filters()
        assert state.get_filters() == state._DEFAULT_FILTERS

    def test_reset_filters_returns_copy_not_reference(self):
        state.reset_filters()
        f = state.get_filters()
        f["search_query"] = "mutated"
        assert state.get_filters()["search_query"] == ""

    def test_update_filters_merges(self):
        state.reset_filters()
        state.update_filters(search_query="hello", selected_camera="Cam1")
        f = state.get_filters()
        assert f["search_query"] == "hello"
        assert f["selected_camera"] == "Cam1"
        assert f["selected_species"] == []


class TestResetAppState:
    def test_resets_everything_when_keep_prefs_is_false(self):
        state.set_active_project("proj-x")
        state.set_current_idx(10)
        state.set_annotator_name("bob")
        state.set_dark_mode(False)
        state.set_language("fr")
        state.set_autoplay(False)
        state.update_filters(search_query="hello")

        state.reset_app_state(keep_prefs=False)

        assert state.get_active_project_id() is None
        assert state.get_current_idx() == 0
        assert state.get_annotator_name() == ""
        assert state.is_dark_mode() is True
        assert state.get_language() == "en"
        assert state.is_autoplay() is True
        assert state.get_queue() == []
        assert state.get_selections() == []
        assert state.get_state_val("foo") is None
        assert state.get_filters() == state._DEFAULT_FILTERS

    def test_preserves_prefs_by_default(self):
        state.set_dark_mode(False)
        state.set_language("fr")
        state.set_autoplay(False)
        state.set_active_project("proj-x")

        state.reset_app_state()  # keep_prefs=True by default

        assert state.is_dark_mode() is False
        assert state.get_language() == "fr"
        assert state.is_autoplay() is False
        assert state.get_active_project_id() is None  # Should be cleared as it's session state


class TestAnnotatorProject:
    def test_shared_browser_restores_each_annotators_own_project(
        self, provider_with_project, tmp_path
    ):
        dp, first, _ = provider_with_project
        second_dir = tmp_path / "second"
        second_dir.mkdir()
        second = dp.create_project("Second", str(second_dir))
        dp.set_setting("active_project_id", first.id)  # Legacy shared default must not leak.
        state.set_data_provider(dp)

        state.set_annotator_name("alice")
        state.set_active_project(first.id)
        state.set_queue(["video"])
        state.update_filters(search_query="chimp")
        state.clear_session()

        assert state.get_active_project_id() is None
        assert state.get_annotator_name() == ""
        assert state.get_queue() == []
        assert state.get_filters() == state._DEFAULT_FILTERS

        state.set_annotator_name("bob")
        state.load_session_defaults(dp, restore_project=True)
        assert state.get_active_project_id() is None
        state.set_active_project(second.id)

        # Login must replace any selection remaining in browser storage.
        state.set_annotator_name("alice")
        state.load_session_defaults(dp, restore_project=True)
        assert state.get_active_project_id() == first.id
        state.set_annotator_name("bob")
        state.load_session_defaults(dp, restore_project=True)
        assert state.get_active_project_id() == second.id

    def test_fresh_browser_and_provider_restore_annotator_project(self, provider_with_project):
        from review_app.backend.provider.local_data_provider import LocalDataProvider

        dp, project, _ = provider_with_project
        state.set_data_provider(dp)
        state.set_annotator_name("alice")
        state.set_active_project(project.id)
        state.reset_app_state(keep_prefs=False)

        reopened = LocalDataProvider()
        try:
            state.set_data_provider(reopened)
            state.set_annotator_name("alice")
            state.load_session_defaults(reopened, restore_project=True)
            assert state.get_active_project_id() == project.id
        finally:
            reopened.engine.dispose()

    def test_explicit_clear_forgets_only_current_annotators_project(self, provider_with_project):
        dp, project, _ = provider_with_project
        state.set_data_provider(dp)
        dp.set_annotator_active_project("bob", project.id)
        state.set_annotator_name("alice")
        state.set_active_project(project.id)
        state.set_active_project(None)
        assert dp.get_annotator_active_project("alice") is None
        assert dp.get_annotator_active_project("bob") == project.id


class TestLoadSettingsFromDb:
    def test_loads_all_settings_from_provider(self):
        class MockDP:
            def get_setting(self, key, default=None):
                values = {
                    "annotator_name": "alice",
                    "blank_threshold": "0.5",
                    "species_threshold": "0.6",
                    "active_project_id": "proj-123",
                    "dark_mode": "False",
                    "language": "fr",
                    "autoplay": "False",
                    "muted": "False",
                    "auto_transcode": "False",
                    "tour_completed": "True",
                }
                return values.get(key, default)

        state.load_settings_from_db(MockDP())
        state.load_session_defaults(MockDP())

        assert state.get_annotator_name() == ""
        assert state.get_blank_threshold() == 0.5
        assert state.get_species_threshold() == 0.6
        # A legacy global project is not another annotator's preference.
        assert state.get_active_project_id() is None
        assert state.is_dark_mode() is False
        assert state.get_language() == "fr"
        assert state.is_autoplay() is False
        assert state.is_muted() is False
        assert state.is_auto_transcode() is False
        assert state.is_tour_completed() is True

    def test_uses_defaults_when_settings_missing(self):
        class MockDP:
            def get_setting(self, key, default=None):
                return default

        state.load_settings_from_db(MockDP())
        state.load_session_defaults(MockDP())

        assert state.get_annotator_name() == ""
        assert state.get_blank_threshold() == 0.75
        assert state.get_species_threshold() == 0.75
        assert state.get_active_project_id() is None
        assert state.is_dark_mode() is True
        assert state.get_language() == "en"
        assert state.is_autoplay() is True
        assert state.is_muted() is False
        assert state.is_auto_transcode() is True
        assert state.is_tour_completed() is False


class TestSaveUserPrefsToDb:
    def test_persists_all_pref_keys(self):
        calls = {}

        class MockDP:
            def set_setting(self, key, val):
                calls[key] = val

        state.set_dark_mode(False)
        state.set_language("fr")
        state.set_autoplay(False)
        state.set_muted(False)
        state.set_auto_transcode(False)
        state.set_tour_completed(True)

        state.save_user_prefs_to_db(MockDP())

        # In multi-user app, saving user preferences to the global DB table is a no-op
        assert not calls

    def test_does_not_persist_annotator_or_thresholds(self):
        calls = {}

        class MockDP:
            def set_setting(self, key, val):
                calls[key] = val

        state.save_user_prefs_to_db(MockDP())
        assert not calls
