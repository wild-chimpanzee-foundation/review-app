"""Persistent browser state must not depend on where the app is launched."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from nicegui.storage import Storage
from review_app.app.session_storage import configure_user_storage


@pytest.fixture(autouse=True)
def isolate_storage_path(monkeypatch):
    monkeypatch.delenv("NICEGUI_STORAGE_PATH", raising=False)
    # The helper configures a class attribute; restore it after each test.
    monkeypatch.setattr(Storage, "path", Storage.path)


def test_migrates_legacy_browser_files_only_on_first_use(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / ".nicegui"
    legacy.mkdir()
    filename = "storage-user-browser.json"
    original = '{"active_project_id":"last-project","annotator_name":"alice"}'
    (legacy / filename).write_text(original)
    (legacy / "unrelated.txt").write_text("do not copy")

    target = configure_user_storage(tmp_path / "app-data")

    assert target == tmp_path / "app-data" / "nicegui"
    assert Storage.path == target
    assert (target / filename).read_text() == original
    assert not (target / "unrelated.txt").exists()

    # A later launch must not replace newer preferences with the legacy snapshot.
    (target / filename).write_text('{"active_project_id":"new-project"}')
    configure_user_storage(tmp_path / "app-data")
    assert json.loads((target / filename).read_text())["active_project_id"] == "new-project"


@pytest.mark.parametrize("preexisting", [False, True])
def test_explicit_storage_override_is_respected_without_migration(
    tmp_path, monkeypatch, preexisting
):
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / ".nicegui"
    legacy.mkdir()
    (legacy / "storage-user-browser.json").write_text('{"active_project_id":"legacy"}')
    target = tmp_path / "custom-storage"
    if preexisting:
        target.mkdir()
    monkeypatch.setenv("NICEGUI_STORAGE_PATH", "custom-storage")

    assert configure_user_storage(tmp_path / "app-data") == target
    assert Storage.path == target
    assert target.is_dir()
    assert not list(target.iterdir())


def test_browser_state_survives_process_restart_from_another_directory(tmp_path):
    first = tmp_path / "first-launch"
    second = tmp_path / "second-launch"
    first.mkdir()
    second.mkdir()
    data_dir = tmp_path / "app-data"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    script = """
import json
import sys
from pathlib import Path
from review_app.app.session_storage import configure_user_storage
configure_user_storage(Path(sys.argv[1]))
from nicegui.storage import Storage
storage = Storage._create_persistent_dict('user-browser')
storage.initialize_sync()
if sys.argv[2] == 'save':
    storage.update(active_project_id='last-project', annotator_name='alice')
else:
    assert storage['active_project_id'] == 'last-project'
    assert storage['annotator_name'] == 'alice'
print(json.dumps(dict(storage)))
"""
    for cwd, mode in ((first, "save"), (second, "load")):
        result = subprocess.run(
            [sys.executable, "-c", script, str(data_dir), mode],
            cwd=cwd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert json.loads(result.stdout) == {
            "active_project_id": "last-project",
            "annotator_name": "alice",
        }
