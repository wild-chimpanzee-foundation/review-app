"""
Tests for delete_project, import_annotations_csv, and get_overview_stats.
"""

import uuid

import pandas as pd
import pytest
from review_app.backend.errors import DataImportError
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import text


def _seed_builtin_tags(dp) -> None:
    """Insert built-in tags that are normally seeded by migration v7.

    In tests, run_migrations stamps fresh DBs as current and skips all migrations,
    so built-in tags must be seeded manually where needed.
    """
    builtin = [
        ("fire", "Fire", "Feu", "deep-orange", "local_fire_department"),
        ("nice_shot", "Nice Shot", "Belle image", "amber", "star"),
        ("broken_metadata", "Broken Metadata", "Métadonnées corrompues", "red", "report_problem"),
    ]
    with dp.engine.begin() as conn:
        for key, name_en, name_fr, color, icon in builtin:
            if not conn.execute(text("SELECT 1 FROM tags WHERE key=:k"), {"k": key}).fetchone():
                conn.execute(
                    text(
                        "INSERT INTO tags (id,key,name_en,name_fr,color,icon,is_custom) "
                        "VALUES (:id,:key,:name_en,:name_fr,:color,:icon,0)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "key": key,
                        "name_en": name_en,
                        "name_fr": name_fr,
                        "color": color,
                        "icon": icon,
                    },
                )


# ---------------------------------------------------------------------------
# delete_project
# ---------------------------------------------------------------------------


def test_delete_project_returns_correct_metadata(provider_with_project):
    dp, project, _ = provider_with_project
    result = dp.delete_project(project.id)
    assert result == {"deleted": True, "videos_removed": 2}


def test_delete_project_removes_project_from_list(provider_with_project):
    dp, project, _ = provider_with_project
    dp.delete_project(project.id)
    remaining = [p.id for p in dp.list_projects()]
    assert project.id not in remaining


def test_delete_project_removes_all_videos(provider_with_project):
    dp, project, _ = provider_with_project
    assert dp.get_project_video_count(project.id) == 2
    dp.delete_project(project.id)
    assert dp.get_project_video_count(project.id) == 0


def test_delete_project_removes_videos_from_queue(provider_with_project):
    dp, project, _ = provider_with_project
    dp.delete_project(project.id)
    assert dp.get_video_queue({}, active_project_id=None) == []


def test_delete_project_cascades_to_annotations(provider_with_project, tmp_db):
    """Annotations tied to deleted project's videos must not survive."""
    dp, project, _ = provider_with_project
    queue = dp.get_video_queue({}, active_project_id=project.id)
    dp.update_manual_review(
        queue[0],
        [{"species": "deer", "behavior": "grazing", "start_sec": 0.0, "end_sec": 5.0}],
        is_blank=False,
        active_project_id=project.id,
    )

    dp.delete_project(project.id)

    with dp.engine.connect() as conn:
        from sqlalchemy import text

        obs_count = conn.execute(text("SELECT COUNT(*) FROM individual_observations")).scalar()
        label_count = conn.execute(text("SELECT COUNT(*) FROM video_labels")).scalar()
    assert obs_count == 0
    assert label_count == 0


def test_delete_nonexistent_project_returns_not_deleted(tmp_db):
    dp = LocalDataProvider()
    result = dp.delete_project("nonexistent-id-xyz")
    assert result == {"deleted": False}


def test_delete_project_removes_transcoded_file(provider_with_project, tmp_db):
    """Transcoded video files on disk should be deleted along with the project."""
    dp, project, _ = provider_with_project
    queue = dp.get_video_queue({}, active_project_id=project.id)

    fake_transcode = tmp_db["root"] / "transcoded.mp4"
    fake_transcode.write_bytes(b"fake")

    from review_app.backend.db.models import Video

    with dp.Session() as s:
        video = s.get(Video, queue[0])
        video.transcoded_path = str(fake_transcode)
        s.commit()

    assert fake_transcode.exists()
    dp.delete_project(project.id)
    assert not fake_transcode.exists()


# ---------------------------------------------------------------------------
# import_annotations_csv
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_provider(tmp_db, mock_probe):
    """Provider with two videos but no annotations yet."""
    video_dir = tmp_db["video_dir"]
    (video_dir / "v1.mp4").touch()
    (video_dir / "v2.mp4").touch()
    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    return dp


def _video_paths(dp):
    queue = dp.get_video_queue({}, active_project_id=None)
    return {dp.get_video_detail(vid)["video_path"]: vid for vid in queue}


def test_import_blank_video_by_path(clean_provider):
    dp = clean_provider
    paths = _video_paths(dp)
    v1_path = next(p for p in paths if p.endswith("v1.mp4"))

    df = pd.DataFrame([{"video_path": v1_path, "is_blank": 1}])
    result = dp.import_annotations_csv(df, active_project_id=None)

    assert result["imported"] == 1
    assert result["skipped"] == []
    detail = dp.get_video_detail(paths[v1_path])
    assert detail["is_blank"] == 1  # get_video_detail returns CAST(is_blank AS INTEGER)


def test_import_non_blank_with_species_by_path(clean_provider):
    dp = clean_provider
    paths = _video_paths(dp)
    v1_path = next(p for p in paths if p.endswith("v1.mp4"))

    df = pd.DataFrame(
        [
            {
                "video_path": v1_path,
                "is_blank": 0,
                "species": "deer",
                "behavior": "grazing",
                "start_sec": 1.0,
                "end_sec": 4.0,
                "annotator": "alice",
            }
        ]
    )
    result = dp.import_annotations_csv(df, active_project_id=None)

    assert result["imported"] == 1
    detail = dp.get_video_detail(paths[v1_path])
    assert len(detail["manual_selections"]) == 1
    assert detail["manual_selections"][0]["species"] == "deer"
    assert detail["manual_selections"][0]["labeled_by"] == "alice"


def test_import_multiple_observations_same_video(clean_provider):
    dp = clean_provider
    paths = _video_paths(dp)
    v1_path = next(p for p in paths if p.endswith("v1.mp4"))

    df = pd.DataFrame(
        [
            {
                "video_path": v1_path,
                "is_blank": 0,
                "species": "deer",
                "behavior": "grazing",
                "start_sec": 0.0,
                "end_sec": 3.0,
            },
            {
                "video_path": v1_path,
                "is_blank": 0,
                "species": "fox",
                "behavior": "running",
                "start_sec": 5.0,
                "end_sec": 8.0,
            },
        ]
    )
    result = dp.import_annotations_csv(df, active_project_id=None)

    assert result["imported"] == 1
    detail = dp.get_video_detail(paths[v1_path])
    assert len(detail["manual_selections"]) == 2


def test_import_unknown_path_is_skipped(clean_provider):
    dp = clean_provider
    df = pd.DataFrame([{"video_path": "/nonexistent/ghost.mp4", "is_blank": 0, "species": "deer"}])
    result = dp.import_annotations_csv(df, active_project_id=None)

    assert result["imported"] == 0
    assert "/nonexistent/ghost.mp4" in result["skipped"]


def test_import_mixed_known_and_unknown(clean_provider):
    dp = clean_provider
    paths = _video_paths(dp)
    v1_path = next(p for p in paths if p.endswith("v1.mp4"))

    df = pd.DataFrame(
        [
            {"video_path": v1_path, "is_blank": 1},
            {"video_path": "/ghost/nope.mp4", "is_blank": 0, "species": "deer"},
        ]
    )
    result = dp.import_annotations_csv(df, active_project_id=None)

    assert result["imported"] == 1
    assert len(result["skipped"]) == 1


def test_import_raises_without_video_identifier(clean_provider):
    dp = clean_provider
    df = pd.DataFrame([{"is_blank": 1, "species": "deer"}])
    with pytest.raises(DataImportError, match="video_path"):
        dp.import_annotations_csv(df, active_project_id=None)


def test_import_raises_without_is_blank(clean_provider):
    dp = clean_provider
    df = pd.DataFrame([{"video_path": "/some/path.mp4", "species": "deer"}])
    with pytest.raises(DataImportError, match="is_blank"):
        dp.import_annotations_csv(df, active_project_id=None)


def test_export_import_round_trip(populated_provider):
    """Export annotations then re-import them and verify the DB state is unchanged."""
    dp, ids = populated_provider

    exported = dp.export_annotations_csv(active_project_id=None)

    # wipe all annotations
    for vid in ids.values():
        dp.update_manual_review(vid, [], is_blank=None)

    result = dp.import_annotations_csv(exported, active_project_id=None)
    assert result["imported"] == 4
    assert result["skipped"] == []

    detail = dp.get_video_detail(ids["v1"])
    assert detail["manual_selections"][0]["species"] == "deer"


# ---------------------------------------------------------------------------
# Round-trip: count, review_later, tags
# ---------------------------------------------------------------------------


def test_import_count_round_trip(clean_provider):
    """count field exported by export_annotations_csv must survive a reimport."""
    dp = clean_provider
    paths = _video_paths(dp)
    v1_path = next(p for p in paths if p.endswith("v1.mp4"))
    v1_id = paths[v1_path]

    dp.update_manual_review(v1_id, [{"species": "deer", "behavior": "grazing", "count": 3}])
    exported = dp.export_annotations_csv(active_project_id=None)
    dp.update_manual_review(v1_id, [], is_blank=None)

    dp.import_annotations_csv(exported, active_project_id=None)
    sel = dp.get_video_detail(v1_id)["manual_selections"][0]
    assert sel["count"] == 3


def test_import_review_later_round_trip(clean_provider):
    """review_later=True exported in CSV must be restored on reimport."""
    dp = clean_provider
    paths = _video_paths(dp)
    v1_id = paths[next(p for p in paths if p.endswith("v1.mp4"))]

    dp.update_manual_review(v1_id, [], is_blank=True)
    dp.set_review_later(v1_id, True)

    exported = dp.export_annotations_csv(active_project_id=None)
    dp.update_manual_review(v1_id, [], is_blank=None)
    dp.set_review_later(v1_id, False)

    dp.import_annotations_csv(exported, active_project_id=None)
    detail = dp.get_video_detail(v1_id)
    assert detail["review_later"] == 1


def test_import_tags_round_trip_override(clean_provider):
    """Built-in and custom tags must be restored in override mode."""
    dp = clean_provider
    _seed_builtin_tags(dp)
    paths = _video_paths(dp)
    v1_id = paths[next(p for p in paths if p.endswith("v1.mp4"))]

    dp.update_manual_review(v1_id, [], is_blank=True)
    dp.toggle_video_tag(v1_id, "fire")
    custom_key = dp.create_custom_tag(name_en="Interesting")
    dp.toggle_video_tag(v1_id, custom_key)

    exported = dp.export_annotations_csv(active_project_id=None)
    # wipe tags
    dp.toggle_video_tag(v1_id, "fire")
    dp.toggle_video_tag(v1_id, custom_key)
    assert dp.get_video_tags(v1_id) == []

    dp.import_annotations_csv(exported, active_project_id=None)
    tags = set(dp.get_video_tags(v1_id))
    assert "fire" in tags
    assert custom_key in tags


def test_import_tags_round_trip_append(clean_provider):
    """Append mode must add CSV tags without removing tags not in the CSV."""
    dp = clean_provider
    _seed_builtin_tags(dp)
    paths = _video_paths(dp)
    v1_id = paths[next(p for p in paths if p.endswith("v1.mp4"))]

    dp.update_manual_review(v1_id, [], is_blank=True)
    # nice_shot will appear in the exported CSV
    dp.toggle_video_tag(v1_id, "nice_shot")
    exported = dp.export_annotations_csv(active_project_id=None)

    # Wipe nice_shot so reimport has something to restore
    dp.toggle_video_tag(v1_id, "nice_shot")
    assert dp.get_video_tags(v1_id) == []

    # fire is set after export — append mode must not remove it
    dp.toggle_video_tag(v1_id, "fire")

    dp.import_annotations_csv(exported, active_project_id=None, mode="append")
    tags = set(dp.get_video_tags(v1_id))
    assert "nice_shot" in tags  # restored from CSV
    assert "fire" in tags  # was not in CSV, must survive


# ---------------------------------------------------------------------------
# get_overview_stats
# ---------------------------------------------------------------------------


def test_overview_stats_video_counts(populated_provider):
    dp, _ = populated_provider
    stats = dp.get_overview_stats()
    v = stats["videos"]
    assert v["total"] == 4
    assert v["valid"] == 4
    assert v["invalid"] == 0
    assert v["unprobed"] == 0


def test_overview_stats_labeling_counts(populated_provider):
    dp, _ = populated_provider
    stats = dp.get_overview_stats()
    lb = stats["labeling"]
    assert lb["labeled"] == 3  # v1, v2, v3 have a VideoLabel
    assert lb["blank"] == 1  # v2
    assert lb["non_blank"] == 2  # v1, v3
    assert lb["has_observations"] == 2  # v1, v3 have IndividualObservations
    assert lb["review_later"] == 1  # v3


def test_overview_stats_species_counts(populated_provider):
    dp, _ = populated_provider
    stats = dp.get_overview_stats()
    species = {row["species"]: row["observations"] for row in stats["species_counts"]}
    assert "deer" in species
    assert "fox" in species
    assert species["deer"] == 1
    assert species["fox"] == 1


def test_overview_stats_model_coverage(populated_provider):
    dp, _ = populated_provider
    stats = dp.get_overview_stats()
    models = {(r["model_name"], r["annotation_type"]) for r in stats["model_coverage"]}
    assert ("model_a", "species") in models
    assert ("model_a", "blank_non_blank") in models


def test_overview_stats_empty_db(tmp_db):
    dp = LocalDataProvider()
    stats = dp.get_overview_stats()
    assert stats["videos"]["total"] == 0
    assert stats["labeling"]["labeled"] == 0
    assert stats["species_counts"] == []
    assert stats["model_coverage"] == []


def test_overview_stats_project_filter(provider_with_project):
    """Stats scoped to a project should only count that project's videos."""
    dp, project, _ = provider_with_project
    stats = dp.get_overview_stats(active_project_id=project.id)
    assert stats["videos"]["total"] == 2


def test_overview_stats_camera_summary(populated_provider):
    dp, _ = populated_provider
    stats = dp.get_overview_stats()
    cameras = {row["camera_id"] for row in stats["camera_summary"]}
    assert cameras == {"cam_a", "cam_b"}
