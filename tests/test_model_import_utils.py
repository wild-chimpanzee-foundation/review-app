from review_app.app.pages.model_import import _is_long_format


def test_is_long_format_detects_required_columns():
    assert _is_long_format(["path", "annotation_type", "model_name"]) is True


def test_is_long_format_detects_with_extra_columns():
    assert _is_long_format(
        ["path", "annotation_type", "model_name", "value_text", "probability"]
    ) is True


def test_is_long_format_rejects_wide_format():
    assert _is_long_format(["filepath", "top_1_species", "prob_species"]) is False


def test_is_long_format_rejects_partial_match():
    assert _is_long_format(["path", "annotation_type"]) is False
