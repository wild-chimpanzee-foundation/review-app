from pathlib import Path

import pytest
from review_app.backend.provider.local_data_provider import LocalDataProvider


@pytest.fixture
def temp_workspace(tmp_path, monkeypatch):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    db_dir = tmp_path / "db"
    db_dir.mkdir()

    species_csv = tmp_path / "species.csv"
    species_csv.write_text("scientific_name;english_name\ndeer;Red Deer\nfox;Red Fox\n")

    behavior_csv = tmp_path / "behaviors.csv"
    behavior_csv.write_text(
        "scientific_name;key;name_en;name_fr\n*;does_not_react;Does not react;\ndeer;reacts_to_camera;Reacts to camera;\ndeer;grazing;Grazing;\nfox;running;Running;\n"
    )

    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: db_dir
    )
    monkeypatch.setattr("review_app.app.config.get_bundled_species_csv", lambda: str(species_csv))
    monkeypatch.setattr(
        "review_app.app.config.get_bundled_behaviors_csv", lambda: str(behavior_csv)
    )

    yield {
        "root": tmp_path,
        "video_dir": video_dir,
    }


def _mock_probe(monkeypatch):
    from review_app.backend.provider import video as video_module

    monkeypatch.setattr(
        video_module,
        "_probe_many",
        lambda paths, **_: {p: (10.0, True, True, None) for p in paths},
    )


def test_provider_initialization(temp_workspace):
    dp = LocalDataProvider()
    assert "deer" in dp.get_valid_species()
    assert "fox" in dp.get_valid_species()
    assert dp.has_videos_in_db(active_project_id=None) is False


def test_get_behaviors(temp_workspace):
    dp = LocalDataProvider()
    deer_behaviors = dp.get_behaviors_for_species("deer")
    assert "reacts_to_camera" in deer_behaviors
    assert "grazing" in deer_behaviors
    assert "does_not_react" in deer_behaviors


def test_sync_videos(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)

    video_dir = temp_workspace["video_dir"]
    (video_dir / "cam1").mkdir()
    (video_dir / "cam1" / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)

    assert dp.has_videos_in_db(active_project_id=None) is True

    queue = dp.get_video_queue({}, active_project_id=None)
    assert len(queue) == 1

    detail = dp.get_video_detail(queue[0])
    assert detail is not None
    assert Path(detail["video_path"]).name == "test.mp4"
    assert detail["camera_id"] == "cam1"


def test_manual_review_update(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)

    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)

    queue = dp.get_video_queue({}, active_project_id=None)
    assert len(queue) == 1
    video_id = queue[0]

    selections = [
        {
            "species": "deer",
            "behavior": "grazing",
            "start_sec": 0,
            "end_sec": 5,
            "labeled_by": "test_user",
        }
    ]
    dp.update_manual_review(video_id, selections)

    detail = dp.get_video_detail(video_id)
    assert detail is not None
    assert detail["manual_selections"][0]["species"] == "deer"
    assert detail["manual_selections"][0]["labeled_by"] == "test_user"


def test_delete_all_annotations_on_empty_submit(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)

    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)

    queue = dp.get_video_queue({}, active_project_id=None)
    video_id = queue[0]

    selections = [
        {
            "species": "deer",
            "behavior": "grazing",
            "start_sec": 0,
            "end_sec": 5,
            "labeled_by": "test_user",
        }
    ]
    dp.update_manual_review(video_id, selections)

    detail = dp.get_video_detail(video_id)
    assert len(detail["manual_selections"]) == 1

    dp.update_manual_review(video_id, [], is_blank=None)

    detail = dp.get_video_detail(video_id)
    assert detail is not None
    assert detail["manual_selections"] == []
    assert detail["is_blank"] is None
