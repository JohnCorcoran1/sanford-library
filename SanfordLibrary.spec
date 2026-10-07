from pathlib import Path


project_root = Path(SPECPATH)
data_files = [
    (str(project_root / "index.html"), "."),
    (str(project_root / "setup.html"), "."),
    (str(project_root / "movies.json"), "."),
    (str(project_root / "skins.json"), "."),
    (str(project_root / "assets"), "assets"),
    (str(project_root / "posters"), "posters"),
]

a = Analysis(
    [str(project_root / "desktop.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=data_files,
    hiddenimports=["webview.platforms.edgechromium"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "cefpython3", "gtk"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Sanford Library",
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
)
