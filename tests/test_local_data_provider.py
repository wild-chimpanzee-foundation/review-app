import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pytest
import yaml

from review_app.backend.local_data_provider import LocalDataProvider


@pytest.fixture
def temp_workspace():
    # Setup temporary directory structure
    tmp_dir = Path(tempfile.mkdtemp())
    video_dir = tmp_dir / "videos"
    video_dir.mkdir()
    db_dir = tmp_dir / "db"
    db_dir.mkdir()

    # Create species CSV
    species_csv = tmp_dir / "species.csv"
    with open(species_csv, "w") as f:
        f.write("Species;Common Name\ndeer;Red Deer\nfox;Red Fox\n")

    # Create behavior CSV
    behavior_csv = tmp_dir / "behaviors.csv"
    with open(behavior_csv, "w") as f:
        f.write("Species;Behavior\ndeer;reacts_to_camera\ndeer;grazing\nfox;running\n")

    # Create config YAML
    config_path = tmp_dir / "config.yaml"
    config = {
        "video_dir": str(video_dir),
        "db_dir": str(db_dir),
        "db_filename": "test.db",
        "species_csv_path": str(species_csv),
        "species_column": "Species",
        "species_behaviors_csv_path": str(behavior_csv),
        "behavior_defaults": ["unlabeled", "does_not_react"],
    }
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    yield {
        "root": tmp_dir,
        "video_dir": video_dir,
        "config_path": config_path,
        "species_csv": species_csv,
    }

    # Teardown
    shutil.rmtree(tmp_dir)


def test_provider_initialization(temp_workspace):
    dp = LocalDataProvider(temp_workspace["config_path"])
    assert "deer" in dp.get_valid_species()
    assert "fox" in dp.get_valid_species()
    assert dp.has_videos_in_db() is False


def test_get_behaviors(temp_workspace):
    dp = LocalDataProvider(temp_workspace["config_path"])
    deer_behaviors = dp.get_behaviors_for_species("deer")
    assert "reacts_to_camera" in deer_behaviors
    assert "grazing" in deer_behaviors
    assert "unlabeled" in deer_behaviors
    assert "does_not_react" in deer_behaviors


def test_sync_videos(temp_workspace, monkeypatch):
    # Mock _probe_many to avoid calling ffprobe
    from review_app.backend import local_data_provider

    def mock_probe_many(paths, progress_callback=None):
        return {p: (10.0, True, True, None) for p in paths}

    monkeypatch.setattr(local_data_provider, "_probe_many", mock_probe_many)

    video_dir = temp_workspace["video_dir"]
    # Create mock video file
    (video_dir / "cam1").mkdir()
    (video_dir / "cam1" / "test.mp4").touch()

    dp = LocalDataProvider(temp_workspace["config_path"])
    dp.sync_videos(progress_callback=None)

    assert dp.has_videos_in_db() is True
    queue = dp.get_video_queue({})
    assert len(queue) == 1
    assert queue[0] == "cam1/test"


def test_manual_review_update(temp_workspace, monkeypatch):
    from review_app.backend import local_data_provider

    monkeypatch.setattr(
        local_data_provider, "_probe_many", lambda paths, **kwargs: {p: (10.0, True, True, None) for p in paths}
    )

    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider(temp_workspace["config_path"])
    dp.sync_videos(None)
    video_id = "default/test"

    selections = [
        {"species": "deer", "behavior": "grazing", "start_sec": 0, "end_sec": 5}
    ]

    dp.update_manual_review(video_id, selections, annotator="test_user")

    detail = dp.get_video_detail(video_id)
    assert detail is not None
    assert detail["manual_selections"][0]["species"] == "deer"
    assert detail["labeled_by"] == "test_user"
