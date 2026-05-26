import time
from pathlib import Path

import pandas as pd
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
        "scientific_name;key;name_en;name_fr\n*;reacts_to_camera;Reacts to camera;\n*;grazing;Grazing;\n*;running;Running;\n"
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
        lambda paths, **_: {p: (10.0, True, True, None, None, None, None) for p in paths},
    )


def test_provider_initialization(temp_workspace):
    dp = LocalDataProvider()
    assert "deer" in dp.get_valid_species()
    assert "fox" in dp.get_valid_species()
    assert dp.has_videos_in_db(active_project_id=None) is False


def test_get_behaviors(temp_workspace):
    dp = LocalDataProvider()
    behaviors = dp.get_behavior_display_map()
    assert "reacts_to_camera" in behaviors
    assert "grazing" in behaviors
    assert "does_not_react" not in behaviors


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


def test_surgical_update_preserves_labeled_at(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)
    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    video_id = dp.get_video_queue({}, active_project_id=None)[0]

    # 1. Create two observations
    dp.update_manual_review(
        video_id,
        [
            {"species": "deer", "tags": ["grazing"], "start_sec": 0, "end_sec": 5},
            {"species": "fox", "tags": [], "start_sec": 10, "end_sec": 15},
        ],
        labeled_by="User A",
    )

    detail1 = dp.get_video_detail(video_id)
    obs1_original = detail1["manual_selections"][0]
    obs2_original = detail1["manual_selections"][1]

    assert obs1_original["labeled_by"] == "User A"
    assert obs1_original["id"] is not None

    # Wait to ensure time difference (now using 1s precision)
    time.sleep(1.1)

    # 2. Update ONLY the second observation (change tags)
    # Pass back the IDs to enable surgical update
    dp.update_manual_review(
        video_id,
        [
            obs1_original,  # Unchanged
            {**obs2_original, "tags": ["grazing"]},  # Changed
        ],
        labeled_by="User B",
    )

    detail2 = dp.get_video_detail(video_id)
    obs1_after = next(o for o in detail2["manual_selections"] if o["id"] == obs1_original["id"])
    obs2_after = next(o for o in detail2["manual_selections"] if o["id"] == obs2_original["id"])

    # Verify obs1 is UNTOUCHED (same labeled_at)
    assert obs1_after["species"] == "deer"
    assert str(obs1_after["labeled_at"]) == str(obs1_original["labeled_at"])
    assert obs1_after["labeled_by"] == "User A"

    # Verify obs2 is UPDATED and has NEW labeled_at
    assert "grazing" in obs2_after["tags"]
    assert str(obs2_after["labeled_at"]) > str(obs2_original["labeled_at"])
    assert obs2_after["labeled_by"] == "User B"


def test_append_mode(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)
    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    video_id = dp.get_video_queue({}, active_project_id=None)[0]

    # 1. Start with one observation
    dp.update_manual_review(
        video_id,
        [{"species": "deer", "behavior": "grazing", "start_sec": 0, "end_sec": 5}],
        labeled_by="User A",
    )

    # 2. Append another one
    dp.update_manual_review(
        video_id,
        [{"species": "fox", "behavior": "does_not_react", "start_sec": 10, "end_sec": 15}],
        labeled_by="User B",
        append=True,
    )

    detail = dp.get_video_detail(video_id)
    assert len(detail["manual_selections"]) == 2

    deer_obs = next(o for o in detail["manual_selections"] if o["species"] == "deer")
    fox_obs = next(o for o in detail["manual_selections"] if o["species"] == "fox")

    assert deer_obs["labeled_by"] == "User A"
    assert fox_obs["labeled_by"] == "User B"


def test_is_blank_auto_transition(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)
    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    video_id = dp.get_video_queue({}, active_project_id=None)[0]

    # 1. Mark as blank
    dp.update_manual_review(video_id, [], is_blank=True, labeled_by="User A")
    assert dp.get_video_detail(video_id)["is_blank"] is True

    # 2. Add an observation via append
    dp.update_manual_review(
        video_id,
        [{"species": "deer", "behavior": "grazing", "start_sec": 0, "end_sec": 5}],
        labeled_by="User B",
        append=True,
    )

    detail = dp.get_video_detail(video_id)
    assert detail["is_blank"] is False
    assert len(detail["manual_selections"]) == 1


def test_import_annotations_append_mode(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)
    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    video_id = dp.get_video_queue({}, active_project_id=None)[0]
    video_path = dp.get_video_detail(video_id)["video_path"]

    # 1. First import: one observation
    df1 = pd.DataFrame(
        [
            {
                "video_path": video_path,
                "is_blank": 0,
                "species": "deer",
                "behavior": "grazing",
                "start_sec": 0,
                "end_sec": 5,
                "annotator": "User A",
            }
        ]
    )
    dp.import_annotations_csv(df1, active_project_id=None, mode="override")

    # 2. Second import: another observation in append mode
    df2 = pd.DataFrame(
        [
            {
                "video_path": video_path,
                "is_blank": 0,
                "species": "fox",
                "behavior": "does_not_react",
                "start_sec": 10,
                "end_sec": 15,
                "annotator": "User B",
            }
        ]
    )
    dp.import_annotations_csv(df2, active_project_id=None, mode="append")

    detail = dp.get_video_detail(video_id)
    assert len(detail["manual_selections"]) == 2
    assert any(
        o["species"] == "deer" and o["labeled_by"] == "User A" for o in detail["manual_selections"]
    )
    assert any(
        o["species"] == "fox" and o["labeled_by"] == "User B" for o in detail["manual_selections"]
    )


def test_import_append_mode_does_not_override_name(temp_workspace, monkeypatch):
    _mock_probe(monkeypatch)
    video_dir = temp_workspace["video_dir"]
    (video_dir / "test.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    video_id = dp.get_video_queue({}, active_project_id=None)[0]
    video_path = dp.get_video_detail(video_id)["video_path"]

    # 1. First import: Alice labels a deer
    df1 = pd.DataFrame(
        [
            {
                "video_path": video_path,
                "is_blank": 0,
                "species": "deer",
                "behavior": "grazing",
                "start_sec": 0,
                "annotator": "Alice",
            }
        ]
    )
    dp.import_annotations_csv(df1, active_project_id=None)

    original_obs = dp.get_video_detail(video_id)["manual_selections"][0]
    obs_id = original_obs["id"]

    # 2. Bob tries to append using the same observation_id — should be ignored, creating a new record.
    df2 = pd.DataFrame(
        [
            {
                "video_path": video_path,
                "is_blank": 0,
                "species": "deer",
                "behavior": "running",
                "start_sec": 0,
                "annotator": "Bob",
                "observation_id": obs_id,
            }
        ]
    )
    dp.import_annotations_csv(df2, active_project_id=None, mode="append")

    detail = dp.get_video_detail(video_id)
    assert len(detail["manual_selections"]) == 2

    alice_obs = next(o for o in detail["manual_selections"] if o["id"] == obs_id)
    bob_obs = next(o for o in detail["manual_selections"] if o["id"] != obs_id)

    assert alice_obs["labeled_by"] == "Alice"
    assert "grazing" in alice_obs["tags"]

    assert bob_obs["labeled_by"] == "Bob"
    assert "running" in bob_obs["tags"]


def test_annotation_sharing_round_trip(tmp_path, monkeypatch):
    """
    User A annotates videos and shares the exported CSV with User B.
    User B has the same videos at a different root path (different machine).
    Verifies: behavior names survive translation, annotator is preserved for
    both blank and non-blank videos, and path matching works across roots.
    """
    from review_app.backend.provider import video as video_module

    monkeypatch.setattr(
        video_module,
        "_probe_many",
        lambda paths, **_: {p: (10.0, True, True, None, None, None, None) for p in paths},
    )

    species_csv = tmp_path / "species.csv"
    species_csv.write_text("scientific_name;english_name\ndeer;Red Deer\nfox;Red Fox\n")
    behavior_csv = tmp_path / "behaviors.csv"
    behavior_csv.write_text(
        "scientific_name;key;name_en;name_fr\n"
        "*;does_not_react;Does not react;\n"
        "deer;grazing;Grazing;\n"
        "fox;running;Running;\n"
    )
    monkeypatch.setattr("review_app.app.config.get_bundled_species_csv", lambda: str(species_csv))
    monkeypatch.setattr(
        "review_app.app.config.get_bundled_behaviors_csv", lambda: str(behavior_csv)
    )

    # --- User A's machine ---
    db_a = tmp_path / "db_a"
    db_a.mkdir()
    videos_a = tmp_path / "machine_a" / "fieldwork" / "cam1"
    videos_a.mkdir(parents=True)
    (videos_a / "clip1.mp4").touch()
    (videos_a / "clip2.mp4").touch()

    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: db_a
    )
    dp_a = LocalDataProvider()
    dp_a.sync_videos(progress_callback=None, video_dir=videos_a.parent.parent)

    ids_a = dp_a.get_video_queue({}, active_project_id=None)
    by_name_a = {Path(dp_a.get_video_detail(v)["video_path"]).name: v for v in ids_a}
    clip1_a, clip2_a = by_name_a["clip1.mp4"], by_name_a["clip2.mp4"]

    # clip1: deer grazing (Alice)
    dp_a.update_manual_review(
        clip1_a,
        [{"species": "deer", "behavior": "grazing", "start_sec": 0.0, "end_sec": 5.0}],
        labeled_by="Alice",
    )
    # clip2: blank (Alice)
    dp_a.update_manual_review(clip2_a, [], is_blank=True, labeled_by="Alice")

    export_df = dp_a.export_annotations_csv(active_project_id=None)

    # Sanity-check the export: attributes column contains the tag key.
    clip1_row = export_df[export_df["video_path"].str.endswith("clip1.mp4")].iloc[0]
    assert "grazing" in str(clip1_row["attributes"])
    assert clip1_row["annotator"] == "Alice"
    clip2_row = export_df[export_df["video_path"].str.endswith("clip2.mp4")].iloc[0]
    assert clip2_row["annotator"] == "Alice"

    # --- User B's machine (different root path, same relative structure) ---
    db_b = tmp_path / "db_b"
    db_b.mkdir()
    videos_b = tmp_path / "machine_b" / "fieldwork" / "cam1"
    videos_b.mkdir(parents=True)
    (videos_b / "clip1.mp4").touch()
    (videos_b / "clip2.mp4").touch()

    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: db_b
    )
    dp_b = LocalDataProvider()
    dp_b.sync_videos(progress_callback=None, video_dir=videos_b.parent.parent)

    result = dp_b.import_annotations_csv(export_df, active_project_id=None, mode="append")
    assert result["skipped"] == [], f"Some videos were not matched: {result['skipped']}"
    assert result["imported"] == 2

    ids_b = dp_b.get_video_queue({}, active_project_id=None)
    by_name_b = {Path(dp_b.get_video_detail(v)["video_path"]).name: v for v in ids_b}
    clip1_b, clip2_b = by_name_b["clip1.mp4"], by_name_b["clip2.mp4"]

    # clip1: behavior key "grazing" restored, annotator preserved
    detail1 = dp_b.get_video_detail(clip1_b)
    assert detail1["is_blank"] is False
    assert len(detail1["manual_selections"]) == 1
    obs = detail1["manual_selections"][0]
    assert obs["species"] == "deer"
    assert "grazing" in obs["tags"]
    assert obs["labeled_by"] == "Alice"

    # clip2: blank status and annotator preserved
    detail2 = dp_b.get_video_detail(clip2_b)
    assert detail2["is_blank"] is True
    assert detail2["blank_labeled_by"] == "Alice"
