from pathlib import Path

project_root = Path.cwd()

a = Analysis(
    [str(project_root / "review_app" / "app" / "entry_point.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "review_app"), "review_app"),
    ],
    hiddenimports=[
        "pygments.lexers",
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
