from review_app.app.pages.model_import._helpers import (
    auto_suggest_mappings as _auto_suggest_mappings,
)
from review_app.app.pages.model_import._helpers import (
    auto_suggest_path_col as _auto_suggest_path_col,
)
from review_app.app.pages.model_import._helpers import (
    is_long_format as _is_long_format,
)


class TestIsLongFormat:
    def test_detects_required_columns(self):
        assert _is_long_format(["path", "annotation_type", "model_name"]) is True

    def test_detects_with_extra_columns(self):
        assert (
            _is_long_format(["path", "annotation_type", "model_name", "value_text", "probability"])
            is True
        )

    def test_rejects_wide_format(self):
        assert _is_long_format(["filepath", "top_1_species", "prob_species"]) is False

    def test_rejects_partial_match(self):
        assert _is_long_format(["path", "annotation_type"]) is False


class TestAutoSuggestMappings:
    def test_detects_top_1_species_with_prob_col(self):
        columns = ["top_1_species_a", "prob_species_a", "top_1_species_b", "prob_species_b"]
        result = _auto_suggest_mappings(columns)
        assert len(result) == 2
        assert result[0] == {
            "model_name": "species_a",
            "annotation_type": "species",
            "value_col": "top_1_species_a",
            "prob_col": "prob_species_a",
        }
        assert result[1] == {
            "model_name": "species_b",
            "annotation_type": "species",
            "value_col": "top_1_species_b",
            "prob_col": "prob_species_b",
        }

    def test_prob_col_empty_when_missing(self):
        columns = ["top_1_model_x"]
        result = _auto_suggest_mappings(columns)
        assert result[0]["prob_col"] == ""

    def test_detects_per_model_blank_with_prefix(self):
        columns = ["top_1_model_a", "prob_model_a", "blank_model_a"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["model_name"] == "model_a"
        assert blank_mappings[0]["prob_col"] == "blank_model_a"

    def test_detects_per_model_blank_with_suffix(self):
        columns = ["top_1_model_b", "prob_model_b", "model_b_blank"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["prob_col"] == "model_b_blank"

    def test_detects_p_blank_model(self):
        columns = ["top_1_model_c", "prob_model_c", "p_blank_model_c"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["prob_col"] == "p_blank_model_c"

    def test_detects_prob_blank_model(self):
        columns = ["top_1_model_d", "prob_model_d", "prob_blank_model_d"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["prob_col"] == "prob_blank_model_d"

    def test_falls_back_to_generic_blank(self):
        columns = ["top_1_model_e", "prob_model_e", "blank"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["model_name"] == "blank"
        assert blank_mappings[0]["prob_col"] == "blank"

    def test_generic_blank_prob_fallback(self):
        columns = ["top_1_model_f", "prob_model_f", "blank_prob"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["prob_col"] == "blank_prob"

    def test_generic_p_blank_fallback(self):
        columns = ["top_1_model_g", "prob_model_g", "p_blank"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["prob_col"] == "p_blank"

    def test_generic_prob_blank_fallback(self):
        columns = ["top_1_model_h", "prob_model_h", "prob_blank"]
        result = _auto_suggest_mappings(columns)
        blank_mappings = [m for m in result if m["annotation_type"] == "blank_non_blank"]
        assert len(blank_mappings) == 1
        assert blank_mappings[0]["prob_col"] == "prob_blank"

    def test_returns_empty_for_no_top_1_columns(self):
        columns = ["filepath", "camera", "duration"]
        result = _auto_suggest_mappings(columns)
        assert result == []

    def test_returns_only_species_when_no_blank_col(self):
        columns = ["top_1_species_a", "prob_species_a"]
        result = _auto_suggest_mappings(columns)
        assert len(result) == 1
        assert result[0]["annotation_type"] == "species"

    def test_ignores_non_matching_top_1_prefix(self):
        columns = ["top_1_species_a", "prob_species_a", "other_column"]
        result = _auto_suggest_mappings(columns)
        assert len(result) == 1


class TestAutoSuggestPathCol:
    def test_prefers_filepath(self):
        columns = ["filepath", "other", "path"]
        result = _auto_suggest_path_col(columns, [{"filepath": "/videos/a.mp4"}])
        assert result == "filepath"

    def test_prefers_original_filepath(self):
        columns = ["original_filepath", "video_path"]
        result = _auto_suggest_path_col(columns, [{"original_filepath": "/videos/a.mp4"}])
        assert result == "original_filepath"

    def test_prefers_video_path(self):
        columns = ["video_path", "file"]
        result = _auto_suggest_path_col(columns, [])
        assert result == "video_path"

    def test_prefers_path(self):
        columns = ["path", "file"]
        result = _auto_suggest_path_col(columns, [])
        assert result == "path"

    def test_prefers_file(self):
        columns = ["file", "other"]
        result = _auto_suggest_path_col(columns, [])
        assert result == "file"

    def test_fallbacks_to_path_like_column(self):
        columns = ["col_a", "col_b"]
        sample = [{"col_a": "some/thing", "col_b": "nopath"}]
        result = _auto_suggest_path_col(columns, sample)
        assert result == "col_a"

    def test_fallbacks_to_backslash_path(self):
        columns = ["col_a", "col_b"]
        sample = [{"col_a": "no", "col_b": "C:\\videos\\a.mp4"}]
        result = _auto_suggest_path_col(columns, sample)
        assert result == "col_b"

    def test_fallbacks_to_first_column(self):
        columns = ["first", "second"]
        sample = [{"first": "plain", "second": "text"}]
        result = _auto_suggest_path_col(columns, sample)
        assert result == "first"

    def test_empty_columns_returns_empty_string(self):
        result = _auto_suggest_path_col([], [])
        assert result == ""

    def test_no_sample_but_columns(self):
        result = _auto_suggest_path_col(["col_a", "col_b"], [])
        assert result == "col_a"
