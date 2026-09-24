# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

_root = Path(SPECPATH).resolve().parent
_vendor = _root / 'manager' / '_vendor'
sys.path.insert(0, str(_vendor))

def _vendor_datas():
    for source in _vendor.rglob('*'):
        if not source.is_file():
            continue
        relative = source.relative_to(_vendor)
        if (source.suffix.lower() in {'.pyc', '.pyo'} or
                '__pycache__' in relative.parts or relative.parts[0].lower() == 'bin'):
            continue
        yield str(source), str(Path('_vendor') / relative.parent)

a = Analysis(
    [str(_root / 'manager' / 'main.py')],
    pathex=[str(_root / 'manager'), str(_vendor)],
    binaries=[],
    datas=[(str(_root / 'manager' / 'assets'), 'manager/assets'),
           (str(_root / 'resources' / 'parameter-presets'), 'resources/parameter-presets'),
           (str(_root / 'resources' / 'guides'), 'resources/guides'),
           (str(_root / 'resources' / 'recipes'), 'resources/recipes'),
           (str(_root / 'resources' / 'component-update-manifest.json'), 'resources'),
           (str(_root / 'resources' / 'dsh-install' / 'package.json'), 'resources/dsh-install'),
           (str(_root / 'resources' / 'dsh-install' / 'package-lock.json'), 'resources/dsh-install'),
           *_vendor_datas()],
    hiddenimports=['pynvml', 'huggingface_hub', 'httpx', 'hf_xet', 'deploy_config'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PIL'],
    noarchive=False,
    optimize=0,
)
# Qt6Core on Windows loads the OS-provided ICU shim. PyInstaller otherwise
# picks an unrelated Poppler ICU DLL from this machine's PATH; its exports are
# incompatible and make PySide6.QtCore fail at startup (WinError 127).
_wrong_icu = {'icuuc.dll', 'icudt78.dll'}
_wrong_source = 'codex-primary-runtime/dependencies/native/poppler/library/bin/'
a.binaries[:] = [item for item in a.binaries
                 if not (item[0].lower() in _wrong_icu and
                         _wrong_source in item[1].replace('\\', '/').lower())]
# Qt Virtual Keyboard is GPLv3-only and unused by this Widgets application.
# PyInstaller may collect its plugin and runtime DLL transitively; omit both.
_unused_qt_components = {'qt6virtualkeyboard.dll', 'qtvirtualkeyboardplugin.dll',
                         'qt6pdf.dll', 'qpdf.dll'}
a.binaries[:] = [item for item in a.binaries
                 if Path(item[0]).name.lower() not in _unused_qt_components]
a.datas[:] = [item for item in a.datas
             if Path(item[0]).name.lower() not in _unused_qt_components]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='DSH-Companion',
    icon=str(_root / 'manager' / 'assets' / 'companion.ico'),
    version=str(_root / 'manager' / 'version_info.txt'),
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
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='DSH-Companion',
)
