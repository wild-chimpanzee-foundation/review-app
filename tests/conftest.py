import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from review_app.backend.db.models import ModelAnnotation
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def seed_builtin_tags(dp) -> None:
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
# Shared low-level fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Minimal temp workspace: patched config paths, no videos on disk."""
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    video_dir = tmp_path / "videos"
    video_dir.mkdir()

    species_csv = tmp_path / "species.csv"
    species_csv.write_text("scientific_name;english_name\ndeer;Red Deer\nfox;Red Fox\n")

    behavior_csv = tmp_path / "behaviors.csv"
    behavior_csv.write_text(
        "scientific_name;key;name_en;name_fr\n"
        "*;does_not_react;Does not react;\n"
        "deer;grazing;Grazing;\n"
        "fox;running;Running;\n"
    )

    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: db_dir
    )
    monkeypatch.setattr("review_app.app.config.get_bundled_species_csv", lambda: str(species_csv))
    monkeypatch.setattr(
        "review_app.app.config.get_bundled_behaviors_csv", lambda: str(behavior_csv)
    )

    yield {"root": tmp_path, "video_dir": video_dir}


@pytest.fixture
def mock_probe(monkeypatch):
    """Stub out ffprobe so any .mp4 touch()-file is treated as a valid 10-second video."""
    from review_app.backend.provider import video as video_module

    monkeypatch.setattr(
        video_module,
        "_probe_many",
        lambda paths, **_: {p: (10.0, True, True, None, None, None, None) for p in paths},
    )


# ---------------------------------------------------------------------------
# Rich populated DB
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_provider(tmp_db, mock_probe):
    """
    LocalDataProvider pre-loaded with four videos, manual labels, and model
    predictions — enough to exercise every queue filter and the CSV export.

    Video layout
    ------------
    cam_a/v1.mp4  non-blank, deer/grazing (alice), model: deer 0.90, non_blank 0.80
    cam_a/v2.mp4  blank (is_blank=True),            model: blank 0.95
    cam_b/v3.mp4  non-blank, fox/running (bob),      model: fox 0.85 — review_later
    cam_b/v4.mp4  no label (never reviewed),         no model annotations
    """
    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    (video_dir / "cam_a" / "v1.mp4").touch()
    (video_dir / "cam_a" / "v2.mp4").touch()
    (video_dir / "cam_b").mkdir()
    (video_dir / "cam_b" / "v3.mp4").touch()
    (video_dir / "cam_b" / "v4.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)

    all_ids = dp.get_video_queue({}, active_project_id=None)
    assert len(all_ids) == 4, "fixture setup: expected 4 videos after sync"

    by_name = {Path(dp.get_video_detail(vid)["video_path"]).name: vid for vid in all_ids}
    v1, v2, v3, v4 = by_name["v1.mp4"], by_name["v2.mp4"], by_name["v3.mp4"], by_name["v4.mp4"]

    dp.update_manual_review(
        v1,
        [
            {
                "species": "deer",
                "behavior": "grazing",
                "start_sec": 0.0,
                "end_sec": 5.0,
                "labeled_by": "alice",
            }
        ],
        is_blank=False,
    )
    dp.update_manual_review(v2, [], is_blank=True)
    dp.update_manual_review(
        v3,
        [
            {
                "species": "fox",
                "behavior": "running",
                "start_sec": 2.0,
                "end_sec": 8.0,
                "labeled_by": "bob",
            }
        ],
        is_blank=False,
    )
    dp.set_review_later(v3, True)
    # v4 intentionally left unlabeled

    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    with dp.Session() as s:
        s.add_all(
            [
                ModelAnnotation(
                    id=str(uuid.uuid4()),
                    video_id=v1,
                    project_id=None,
                    annotation_type="species",
                    model_name="model_a",
                    value_text="deer",
                    probability=0.90,
                    updated_at=ts,
                ),
                ModelAnnotation(
                    id=str(uuid.uuid4()),
                    video_id=v1,
                    project_id=None,
                    annotation_type="blank_non_blank",
                    model_name="model_a",
                    value_text="non_blank",
                    probability=0.80,
                    updated_at=ts,
                ),
                ModelAnnotation(
                    id=str(uuid.uuid4()),
                    video_id=v2,
                    project_id=None,
                    annotation_type="blank_non_blank",
                    model_name="model_a",
                    value_text="blank",
                    probability=0.95,
                    updated_at=ts,
                ),
                ModelAnnotation(
                    id=str(uuid.uuid4()),
                    video_id=v3,
                    project_id=None,
                    annotation_type="species",
                    model_name="model_a",
                    value_text="fox",
                    probability=0.85,
                    updated_at=ts,
                ),
            ]
        )
        s.commit()

    return dp, {"v1": v1, "v2": v2, "v3": v3, "v4": v4}


@pytest.fixture
def provider_with_project(tmp_db, mock_probe):
    """Provider with one project, one dir, and two videos in that dir."""
    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_x").mkdir()
    (video_dir / "cam_x" / "a.mp4").touch()
    (video_dir / "cam_x" / "b.mp4").touch()

    dp = LocalDataProvider()
    project = dp.create_project("Test Project", str(video_dir))
    dp.sync_videos(
        progress_callback=None,
        video_dir=video_dir,
        active_project_id=project.id,
    )
    dirs = dp.get_project_dirs(project.id)
    assert len(dirs) == 1

    return dp, project, dirs[0]
