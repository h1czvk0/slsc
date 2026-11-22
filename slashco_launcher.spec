# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['slashco_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SlashCoSenseLauncher',
    console=False,
    upx=False,
    icon=['icon.ico'],
)
