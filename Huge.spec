# -*- mode: python ; coding: utf-8 -*-
# PyInstaller ONEDIR spec for the standalone Huge Windows app.
# Packaging only — do not change application logic here.
#
# Output layout (required for clean PCs without Python installed):
#   Huge/
#     Huge.exe
#     _internal/
#       python313.dll
#       VCRUNTIME*.dll / MSVCP*.dll
#       ... all bundled Python + app assets ...

import os
import sys

from PyInstaller.utils.hooks import collect_all

block_cipher = None
root = os.path.abspath(SPECPATH)

datas = [
    (os.path.join(root, 'ui', 'dist'), os.path.join('ui', 'dist')),
    (os.path.join(root, 'huge.ico'), '.'),
    (os.path.join(root, 'huge-logo.png'), '.'),
    (os.path.join(root, 'VERSION.txt'), '.'),
]

binaries = []
hiddenimports = [
    'engine',
    'engine.session',
    'engine.updater',
    'engine.gif_image',
    'engine.install_location',
    'PIL',
    'PIL.Image',
    'PIL.ImageSequence',
    'webview',
    'webview.platforms.edgechromium',
]

# Bundle Pillow codecs / binary helpers.
_pil_ret = collect_all('PIL')
datas += _pil_ret[0]
binaries += _pil_ret[1]
hiddenimports += _pil_ret[2]

# Bundle Visual C++ runtime DLLs that python313.dll / extensions need.
# Clean Windows PCs may not have these on PATH; LoadLibrary then fails with
# "Failed to load Python DLL ... python313.dll" even when python313.dll exists.
_runtime_names = (
    'VCRUNTIME140.dll',
    'VCRUNTIME140_1.dll',
    'MSVCP140.dll',
    'MSVCP140_1.dll',
    'MSVCP140_2.dll',
    'MSVCP140_atomic_wait.dll',
    'MSVCP140_codecvt_ids.dll',
    'CONCRT140.dll',
)
_search_dirs = []
for _base in (
    getattr(sys, 'base_prefix', sys.prefix),
    sys.prefix,
    os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32'),
):
    if _base and os.path.isdir(_base):
        _search_dirs.append(_base)
for _name in _runtime_names:
    for _dir in _search_dirs:
        _path = os.path.join(_dir, _name)
        if os.path.isfile(_path):
            binaries.append((_path, '.'))
            break

# Always ship the interpreter DLL next to the bootloader's expected layout.
_py_dll = os.path.join(getattr(sys, 'base_prefix', sys.prefix), 'python313.dll')
if os.path.isfile(_py_dll):
    binaries.append((_py_dll, '.'))

# Bundle pywebview package data (JS bridge, etc.).
tmp_ret = collect_all('webview')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

a = Analysis(
    [os.path.join(root, 'desktop.py')],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name='Huge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(root, 'huge.ico'),
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Huge',
)
