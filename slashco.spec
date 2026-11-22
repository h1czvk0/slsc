# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['slashco.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.ico', '.'),
        ('locations.json', '.'),
        ('cover.png', '.'),
        ('admin_helper.py', '.'),
        ('sponsors.txt', '.'),
        ('tools/mitmdump.exe', 'tools'),
        ('tools/caddy/caddy.exe', 'tools/caddy'),
        ('tools/cleanup_hosts.py', 'tools'),
    ],
    hiddenimports=[
        'cryptography',
        'PIL.ImageTk',
        'websocket',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # mitmdump is a self-contained subprocess; do not package its Python stack twice.
    excludes=['mitmproxy', 'numpy'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SlashCoSenseApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name='SlashCoSenseApp',
    upx=False,
)
