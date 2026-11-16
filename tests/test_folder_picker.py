"""Native folder picker commands are tested without opening desktop dialogs."""

from subprocess import CompletedProcess

import pytest
from review_app.app.components import folder_picker


@pytest.mark.parametrize(
    "platform,tool",
    [
        ("linux", "zenity"),
        ("linux", "kdialog"),
        ("win32", "powershell.exe"),
        ("darwin", "osascript"),
    ],
)
def test_picker_uses_system_dialog(monkeypatch, tmp_path, platform, tool):
    monkeypatch.setattr(folder_picker.sys, "platform", platform)
    monkeypatch.setattr(folder_picker.shutil, "which", lambda name: name if name == tool else None)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, str(tmp_path) + "\n", "")

    monkeypatch.setattr(folder_picker.subprocess, "run", run)
    assert folder_picker.pick_folder(str(tmp_path)) == str(tmp_path)
    command, options = calls[0]
    assert command[0] == tool
    assert not options.get("shell")
    assert options["env"]["REVIEW_APP_PICKER_PATH"] == str(tmp_path)


def test_picker_cancel_preserves_no_selection(monkeypatch):
    monkeypatch.setattr(folder_picker.sys, "platform", "linux")
    monkeypatch.setattr(folder_picker.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        folder_picker.subprocess, "run", lambda command, **kw: CompletedProcess(command, 1, "", "")
    )
    assert folder_picker.pick_folder() is None


def test_picker_unavailable(monkeypatch):
    monkeypatch.setattr(folder_picker.sys, "platform", "linux")
    monkeypatch.setattr(folder_picker.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        folder_picker.pick_folder()
