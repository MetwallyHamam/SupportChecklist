# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
for package in ("core", "modules", "utils", "security", "win32", "win32com"):
    hiddenimports += collect_submodules(package)

a = Analysis(["windows_service.py"], pathex=["."], hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="MiChecklistService",
    console=True,
    icon="michecklist.ico",
    version="michecklist_version.txt",
    uac_admin=True,
)
