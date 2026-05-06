from review_app.app.translations import TRANSLATIONS
from review_app.app.utils import get_probability_color, user_error_message


class TestUserErrorMessage:
    def test_with_user_message_key(self, monkeypatch):
        monkeypatch.setattr("review_app.app.state._language", "en")

        exc = Exception("detail")
        exc.user_message_key = "error_generic"
        result = user_error_message(exc)
        assert result == TRANSLATIONS["en"]["error_generic"]

    def test_without_user_message_key(self):
        exc = ValueError("plain error message")
        result = user_error_message(exc)
        assert result == "plain error message"

    def test_with_subclass_default_key(self, monkeypatch):
        monkeypatch.setattr("review_app.app.state._language", "en")

        from review_app.backend.errors import DataImportError, SpeciesError, VideoError

        for cls, expected_key in [
            (DataImportError, "data_import_error_generic"),
            (SpeciesError, "species_error_generic"),
            (VideoError, "video_error_generic"),
        ]:
            exc = cls("something")
            result = user_error_message(exc)
            assert result == TRANSLATIONS["en"][expected_key]

    def test_with_custom_user_message_key(self, monkeypatch):
        monkeypatch.setattr("review_app.app.state._language", "en")

        from review_app.backend.errors import AppError

        exc = AppError("detail", user_message_key="error_generic")
        result = user_error_message(exc)
        assert result == TRANSLATIONS["en"]["error_generic"]


class TestGetProbabilityColor:
    def test_none_returns_grey(self):
        assert get_probability_color(None) == "#9e9e9e"

    def test_non_numeric_returns_grey(self):
        assert get_probability_color("hello") == "#9e9e9e"

    def test_zero_returns_red(self):
        assert get_probability_color(0.0) == "#c10015"

    def test_half_returns_yellow(self):
        assert get_probability_color(0.5) == "#f2c037"

    def test_one_returns_green(self):
        assert get_probability_color(1.0) == "#21ba45"

    def test_quarter(self):
        # 0.25: t=0.5 for red→yellow leg
        # r=193+49*0.5=217, g=192*0.5=96, b=21+34*0.5=38
        assert get_probability_color(0.25) == "#d96026"

    def test_three_quarters(self):
        # 0.75: t=0.5 for yellow→green leg
        # r=242-209*0.5=137, g=192-6*0.5=189, b=55+14*0.5=62
        assert get_probability_color(0.75) == "#89bd3e"

    def test_negative_clamps_to_red(self):
        assert get_probability_color(-0.1) == "#c10015"

    def test_above_one_clamps_to_green(self):
        assert get_probability_color(1.5) == "#21ba45"

    def test_integer_values(self):
        assert get_probability_color(0) == "#c10015"
        assert get_probability_color(1) == "#21ba45"
