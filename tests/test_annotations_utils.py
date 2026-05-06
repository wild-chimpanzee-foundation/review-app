import math

import pytest
from review_app.app.pages.review.annotations import _normalize_is_blank

np = pytest.importorskip("numpy")


class TestNormalizeIsBlank:
    def test_none_returns_none(self):
        assert _normalize_is_blank(None) is None

    def test_nan_returns_none(self):
        assert _normalize_is_blank(float("nan")) is None
        assert _normalize_is_blank(math.nan) is None

    def test_true_returns_true(self):
        assert _normalize_is_blank(True) is True

    def test_false_returns_false(self):
        assert _normalize_is_blank(False) is False

    def test_integer_zero_returns_false(self):
        assert _normalize_is_blank(0) is False

    def test_integer_one_returns_true(self):
        assert _normalize_is_blank(1) is True

    def test_float_zero_returns_false(self):
        assert _normalize_is_blank(0.0) is False

    def test_float_one_returns_true(self):
        assert _normalize_is_blank(1.0) is True

    def test_numpy_bool_true(self):
        assert _normalize_is_blank(np.bool_(True)) is True

    def test_numpy_bool_false(self):
        assert _normalize_is_blank(np.bool_(False)) is False

    def test_empty_string_returns_false(self):
        assert _normalize_is_blank("") is False

    def test_non_empty_string_returns_true(self):
        assert _normalize_is_blank("something") is True
