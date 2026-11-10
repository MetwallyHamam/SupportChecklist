# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules

# ------------------------------------------------------------------
# Hidden Imports
# ------------------------------------------------------------------

hiddenimports = []

hiddenimports += collect_submodules("gui")
hiddenimports += collect_submodules("modules")
hiddenimports += collect_submodules("utils")
hiddenimports += collect_submodules("core")
hiddenimports += collect_submodules("security")
hiddenimports += collect_submodules("win32")
hiddenimports += collect_submodules("win32com")

# ------------------------------------------------------------------
# Application Data Files
# ------------------------------------------------------------------

datas = [
    ("history.json", "."),
    ("michecklist.ico", "."),
    ("michecklist-icon.png", "."),
]

# ------------------------------------------------------------------
# Analysis
# ------------------------------------------------------------------

a = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# ------------------------------------------------------------------
# Python Archive
# ------------------------------------------------------------------

pyz = PYZ(a.pure)

# ------------------------------------------------------------------
# Executable
# ------------------------------------------------------------------

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MiChecklist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,

    # Change to True for troubleshooting
    console=False,
    icon="michecklist.ico",
    version="michecklist_version.txt",
    uac_admin=True,

    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
