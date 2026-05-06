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
        assert f["selected_species"] == "All"


class TestResetAppState:
    def test_resets_to_defaults(self):
        state.set_active_project("proj-x")
        state.set_current_idx(10)
        state.set_annotator_name("bob")
        state.set_dark_mode(False)
        state.set_language("fr")
        state.set_autoplay(False)
        state.update_filters(search_query="hello")

        state.reset_app_state()

        assert state.get_active_project_id() is None
        assert state.get_current_idx() == 0
        assert state.get_annotator_name() == "default"
        assert state.is_dark_mode() is True
        assert state.get_language() == "en"
        assert state.is_autoplay() is True
        assert state.get_queue() == []
        assert state.get_selections() == []
        assert state.get_state_val("foo") is None
        assert state.get_filters() == state._DEFAULT_FILTERS


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

        assert state.get_annotator_name() == "alice"
        assert state.get_blank_threshold() == 0.5
        assert state.get_species_threshold() == 0.6
        assert state.get_active_project_id() == "proj-123"
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

        assert state.get_annotator_name() == "default"
        assert state.get_blank_threshold() == 0.75
        assert state.get_species_threshold() == 0.75
        assert state.get_active_project_id() is None
        assert state.is_dark_mode() is True
        assert state.get_language() == "en"
        assert state.is_autoplay() is True
        assert state.is_muted() is True
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

        assert calls["dark_mode"] is False
        assert calls["language"] == "fr"
        assert calls["autoplay"] is False
        assert calls["muted"] is False
        assert calls["auto_transcode"] is False
        assert calls["tour_completed"] is True

    def test_does_not_persist_annotator_or_thresholds(self):
        """Preferences that are not 'simple user prefs' should not be in this call."""
        calls = {}

        class MockDP:
            def set_setting(self, key, val):
                calls[key] = val

        state.save_user_prefs_to_db(MockDP())
        assert "annotator_name" not in calls
        assert "blank_threshold" not in calls
        assert "active_project_id" not in calls
