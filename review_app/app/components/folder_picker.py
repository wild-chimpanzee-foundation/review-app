"""Native folder selection on the app host, without blocking NiceGUI's event loop."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def pick_folder(initial_path: str = "") -> str | None:
    """Return a selected folder, None on cancel; raise when no picker is available."""
    if sys.platform == "win32":
        command = [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-Command",
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.SelectedPath = $env:REVIEW_APP_PICKER_PATH; "
            "if ($d.ShowDialog() -eq 'OK') { "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "[Console]::Write($d.SelectedPath) }",
        ]
    elif sys.platform == "darwin":
        command = [
            "osascript",
            "-e",
            'try\nPOSIX path of (choose folder)\non error number -128\nreturn ""\nend try',
        ]
    elif shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--directory"]
        if initial_path and Path(initial_path).is_dir():
            command.append(f"--filename={Path(initial_path).resolve()}/")
    elif shutil.which("kdialog"):
        command = ["kdialog", "--getexistingdirectory", initial_path or str(Path.home())]
    else:
        raise RuntimeError("Native folder picker unavailable")
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "REVIEW_APP_PICKER_PATH": initial_path},
    )
    if result.returncode == 1 and sys.platform not in {"win32", "darwin"}:
        return None  # Zenity and KDialog use exit status 1 for cancellation.
    if result.returncode:
        raise RuntimeError("Native folder picker failed")
    return result.stdout.strip() or None
