"""
Tests for LocalDataProvider methods not covered elsewhere:
settings, project management, model annotations, sort modes,
_normalize_annotation_type, get_csv_templates.
"""

import pytest
from review_app.backend.errors import DataImportError
from review_app.backend.local_data_provider import LocalDataProvider

# ---------------------------------------------------------------------------
# App settings (key-value store)
# ---------------------------------------------------------------------------


def test_get_setting_returns_default_when_missing(tmp_db):
    dp = LocalDataProvider()
    assert dp.get_setting("nonexistent") is None
    assert dp.get_setting("nonexistent", default="fallback") == "fallback"


def test_set_and_get_setting_roundtrip(tmp_db):
    dp = LocalDataProvider()
    dp.set_setting("theme", "dark")
    assert dp.get_setting("theme") == "dark"


def test_set_setting_overwrites_existing(tmp_db):
    dp = LocalDataProvider()
    dp.set_setting("theme", "dark")
    dp.set_setting("theme", "light")
    assert dp.get_setting("theme") == "light"


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------


def test_list_projects_empty(tmp_db):
    dp = LocalDataProvider()
    assert dp.list_projects() == []


def test_list_projects_returns_created_projects(tmp_db):
    dp = LocalDataProvider()
    dp.create_project("Alpha", "")
    dp.create_project("Beta", "")
    names = [p.name for p in dp.list_projects()]
    assert "Alpha" in names
    assert "Beta" in names


def test_get_most_recent_project_none_when_empty(tmp_db):
    dp = LocalDataProvider()
    assert dp.get_most_recent_project() is None


def test_get_most_recent_project_returns_last_opened(tmp_db):
    dp = LocalDataProvider()
    dp.create_project("Alpha", "")
    b = dp.create_project("Beta", "")
    dp.touch_project(b.id)
    recent = dp.get_most_recent_project()
    assert recent.id == b.id


def test_get_project_returns_none_for_unknown(tmp_db):
    dp = LocalDataProvider()
    assert dp.get_project("nonexistent-id") is None


def test_update_project_name(tmp_db):
    dp = LocalDataProvider()
    p = dp.create_project("Old Name", "")
    dp.update_project_name(p.id, "New Name")
    updated = dp.get_project(p.id)
    assert updated.name == "New Name"


def test_add_project_dir_sort_order(tmp_db, tmp_path):
    dp = LocalDataProvider()
    p = dp.create_project("Project", "")
    d1 = dp.add_project_dir(p.id, "/path/one")
    d2 = dp.add_project_dir(p.id, "/path/two")
    assert d1.sort_order < d2.sort_order


def test_has_videos_in_db_with_project_id(provider_with_project):
    dp, project, _ = provider_with_project
    assert dp.has_videos_in_db(active_project_id=project.id) is True
    assert dp.has_videos_in_db(active_project_id="nonexistent-id") is False


# ---------------------------------------------------------------------------
# get_model_annotations
# ---------------------------------------------------------------------------


def test_get_model_annotations_empty_when_no_data(populated_provider):
    dp, ids = populated_provider
    # v4 has no model annotations
    result = dp.get_model_annotations(ids["v4"])
    assert result.empty
    assert list(result.columns) == [
        "model_name",
        "annotation_type",
        "value_text",
        "probability",
        "created_at",
    ]


def test_get_model_annotations_returns_rows(populated_provider):
    dp, ids = populated_provider
    result = dp.get_model_annotations(ids["v1"])
    assert len(result) == 2
    types = set(result["annotation_type"])
    assert types == {"species", "blank_non_blank"}


# ---------------------------------------------------------------------------
# set_review_later — creates VideoLabel if none exists
# ---------------------------------------------------------------------------


def test_set_review_later_creates_label_for_unlabeled_video(populated_provider):
    dp, ids = populated_provider
    # v4 has no VideoLabel yet
    dp.set_review_later(ids["v4"], True)
    detail = dp.get_video_detail(ids["v4"])
    assert detail["review_later"] is True or detail["review_later"] == 1


def test_set_review_later_false_clears_flag(populated_provider):
    dp, ids = populated_provider
    dp.set_review_later(ids["v3"], False)
    result = dp.get_video_queue({"selected_is_review_later": True}, active_project_id=None)
    assert ids["v3"] not in result


# ---------------------------------------------------------------------------
# _normalize_annotation_type
# ---------------------------------------------------------------------------


def test_normalize_annotation_type_valid(tmp_db):
    dp = LocalDataProvider()
    assert dp._normalize_annotation_type("species") == "species"
    assert dp._normalize_annotation_type("BLANK_NON_BLANK") == "blank_non_blank"
    assert dp._normalize_annotation_type("  Behavior  ") == "behavior"


def test_normalize_annotation_type_invalid_raises(tmp_db):
    dp = LocalDataProvider()
    with pytest.raises(DataImportError, match="Invalid annotation type"):
        dp._normalize_annotation_type("unknown_type")


# ---------------------------------------------------------------------------
# get_csv_templates
# ---------------------------------------------------------------------------


def test_get_csv_templates_structure(populated_provider):
    dp, _ = populated_provider
    templates = dp.get_csv_templates()
    assert isinstance(templates, dict)
    assert "model_annotations" in templates


def test_get_csv_templates_contains_real_video_path(populated_provider):
    dp, _ = populated_provider
    templates = dp.get_csv_templates()
    assert ".mp4" in templates["model_annotations"]


def test_get_csv_templates_empty_db_uses_placeholder(tmp_db):
    dp = LocalDataProvider()
    templates = dp.get_csv_templates()
    assert "model_annotations" in templates
    assert "VIDEO_001" in templates["model_annotations"]  # placeholder path


# ---------------------------------------------------------------------------
# Queue — sort modes not yet covered
# ---------------------------------------------------------------------------


def test_queue_sort_species_prob(populated_provider):
    dp, ids = populated_provider
    result = dp.get_video_queue(
        {"selected_sort": "species_prob", "selected_sort_direction": "desc"},
        active_project_id=None,
    )
    assert set(result) == set(ids.values())


def test_queue_sort_random(populated_provider):
    dp, ids = populated_provider
    result = dp.get_video_queue({"selected_sort": "random"}, active_project_id=None)
    assert set(result) == set(ids.values())


def test_queue_sort_unreviewed_first(populated_provider):
    dp, ids = populated_provider
    # "desc" direction: sort_dir_inv = ASC → CASE(1=annotated, 0=not) ASC → unannotated (v4) first
    result = dp.get_video_queue(
        {"selected_sort": "unreviewed_first", "selected_sort_direction": "desc"},
        active_project_id=None,
    )
    assert set(result) == set(ids.values())
    assert result[0] == ids["v4"]

    # "asc" direction: sort_dir_inv = DESC → annotated first, v4 last
    result_asc = dp.get_video_queue(
        {"selected_sort": "unreviewed_first", "selected_sort_direction": "asc"},
        active_project_id=None,
    )
    assert result_asc[-1] == ids["v4"]
