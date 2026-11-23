"""Backend regression budgets: no per-video SQL/filesystem work during page reads."""

import sqlite3
from pathlib import Path

import pytest
from review_app.backend.db.models import Video
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import event


@pytest.fixture
def legacy_inventory(tmp_db):
    dp = LocalDataProvider()
    project = dp.create_project("Performance", str(tmp_db["video_dir"]))
    with dp.Session.begin() as session:
        session.bulk_insert_mappings(
            Video,
            [
                {
                    "video_id": f"v{i}",
                    "project_id": project.id,
                    "video_path": str(tmp_db["video_dir"] / f"cam{i % 6}" / f"clip{i}.mp4"),
                    "camera_id": f"cam{i % 6}",
                    "deployment_assignment": "legacy",
                }
                for i in range(120)
            ],
        )
    return dp, project


def test_initial_inventory_materialization_uses_bounded_queries(legacy_inventory):
    dp, project = legacy_inventory
    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(dp.engine, "before_cursor_execute", count)
    try:
        summary = dp.get_deployment_summary(project.id)
    finally:
        event.remove(dp.engine, "before_cursor_execute", count)
    assert summary["deployments"] == 6
    assert summary["unresolved"] == 0
    assert len(statements) <= 150, f"{len(statements)} SQL calls for only 120 videos"


def test_management_reads_stored_paths_without_resolving_media(legacy_inventory, monkeypatch):
    dp, project = legacy_inventory
    dp.get_deployment_summary(project.id)

    def no_filesystem(*args, **kwargs):
        pytest.fail("Listing stored footage must not resolve paths on the media drive")

    monkeypatch.setattr(Path, "resolve", no_filesystem)
    result = dp.get_deployment_management(project.id)
    assert len(result["videos"]) == 120
    assert {row["folder"] for row in result["videos"]} == {f"cam{i}" for i in range(6)}


def test_initialized_page_reads_do_not_acquire_a_write_lock(legacy_inventory):
    dp, project = legacy_inventory
    dp.get_deployment_summary(project.id)
    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        statements.append(statement.upper())

    event.listen(dp.engine, "before_cursor_execute", count)
    try:
        dp.get_deployment_summary(project.id)
        dp.get_deployment_management(project.id)
        dp.get_project_deployments(project.id)
    finally:
        event.remove(dp.engine, "before_cursor_execute", count)
    assert not any("BEGIN IMMEDIATE" in statement for statement in statements)
    assert not any(
        statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements
    )


def test_folder_preview_and_apply_batch_queries_and_path_resolution(legacy_inventory, monkeypatch):
    dp, project = legacy_inventory
    dp.get_deployment_summary(project.id)
    root = dp.get_project_dirs(project.id)[0]
    target = dp.get_project_deployments(project.id)[0]["id"]
    statements, resolutions = [], []
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        resolutions.append(True)
        return original(path, *args, **kwargs)

    def count(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(Path, "resolve", resolve)
    event.listen(dp.engine, "before_cursor_execute", count)
    try:
        preview = dp.preview_deployment_assignment(
            project.id,
            target_id=target,
            action="folder",
            folder={"root_id": root.id, "path": "cam0"},
        )
        assert len(preview["rows"]) == 20
        dp.apply_deployment_assignment(preview)
    finally:
        event.remove(dp.engine, "before_cursor_execute", count)
    assert len(statements) <= 60, f"{len(statements)} SQL calls for a 20-file folder"
    assert len(resolutions) <= 6, f"{len(resolutions)} repeated path resolutions"


def test_unchanged_rescan_loads_assignment_inputs_once(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    for index in range(300):
        path = root / f"cam{index % 3}" / f"clip{index}.mp4"
        path.parent.mkdir(exist_ok=True)
        path.touch()
    dp = LocalDataProvider()
    project = dp.create_project("Rescan", str(root))
    dp.sync_videos(active_project_id=project.id)
    statements = []

    def count(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(dp.engine, "before_cursor_execute", count)
    try:
        dp.sync_videos(active_project_id=project.id)
    finally:
        event.remove(dp.engine, "before_cursor_execute", count)
    assert len(statements) <= 35, f"{len(statements)} SQL executions for an unchanged rescan"


@pytest.mark.parametrize("reference_count", [500, 1000])
def test_large_initial_sync_respects_sqlite_variable_limit(tmp_db, mock_probe, reference_count):
    root = tmp_db["video_dir"]
    reference_folder = root / "ref" / "cam_b"
    reference_folder.mkdir(parents=True)
    for index in range(reference_count):
        (reference_folder / f"reference{index}.mp4").touch()
    ordinary_folder = root / "videos" / "cam_a"
    ordinary_folder.mkdir(parents=True)
    (ordinary_folder / "clip.mp4").touch()

    dp = LocalDataProvider()
    project = dp.create_project("Large footage sync", str(root))
    with dp.engine.connect() as connection:
        connection.connection.driver_connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    assert dp.sync_videos(active_project_id=project.id)["added"] == reference_count + 1
    assert dp.get_deployment_summary(project.id)["unmatched_references"] == reference_count
    assert dp.sync_videos(active_project_id=project.id)["added"] == 0
