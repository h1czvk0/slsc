# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['sponsor_standalone.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.ico', '.'),
        ('admin_helper.py', '.'),
        ('sponsors.txt', '.'),
        ('tools/mitmdump.exe', 'tools'),
        ('tools/caddy/caddy.exe', 'tools/caddy'),
        ('tools/cleanup_hosts.py', 'tools'),
        ('tools/reset_network.py', 'tools'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='SlashCoSponsor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
