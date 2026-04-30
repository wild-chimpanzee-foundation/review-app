from pathlib import Path
import nicegui
from PyInstaller.utils.hooks import collect_submodules

project_root = Path.cwd()
nicegui_dir = Path(nicegui.__file__).parent

a = Analysis(
    [str(project_root / "review_app" / "app" / "entry_point.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "review_app"), "review_app"),
        (str(nicegui_dir), "nicegui"),
    ],
    hiddenimports=[
        "pygments.lexers",
        *collect_submodules("webview"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="VideoAnnotation",
    debug=True,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VideoAnnotation",
)
