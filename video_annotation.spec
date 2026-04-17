from pathlib import Path
import sys

block_cipher = None

project_root = Path.cwd()

a = Analysis(
    [str(project_root / "review_app" / "app" / "entry_point.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "review_app"), "review_app"),
    ],
    hiddenimports=[
        "nicegui",
        "nicegui.elements",
        "nicegui.frontends",
        "pygments",
        "pygments.lexers",
        "yaml",
        "pandas",
        "sqlalchemy",
        "matplotlib",
        "plotly",
        "thefuzz",
        "review_app",
        "review_app.app",
        "review_app.backend",
        "review_app.app.pages",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    exclude_binaries=False,
    name="VideoAnnotation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
