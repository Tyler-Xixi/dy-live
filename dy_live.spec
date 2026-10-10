# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all("playwright")

a = Analysis(
    ["dy_grab_gui.py"],
    pathex=[],
    binaries=playwright_binaries,
    datas=playwright_datas + [("assets/app.png", "assets"), ("assets/app.ico", "assets")],
    hiddenimports=playwright_hiddenimports,
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
    [],
    exclude_binaries=True,
    name="DYLiveAssistant",
    icon="assets/app.ico",
    version="assets/version_info.txt",
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DYLiveAssistant",
)

# Complete folder distribution: helper must be independent of _internal.
import hashlib
import json
from pathlib import Path
import shutil
from app_version import APP_VERSION, PRODUCT_ID, PLATFORM
from update_config import UPDATE_PUBLIC_KEYS
assert UPDATE_PUBLIC_KEYS and set(UPDATE_PUBLIC_KEYS)=={'update-primary-v1'}, 'Official update public key required'
helper=Path('build/manual-update/helper-dist/DYLiveUpdater.exe')
assert helper.is_file(), 'Build dy_updater.spec first'
destination=Path(coll.name)
shutil.copy2(helper,destination/'DYLiveUpdater.exe')
shutil.copy2('用户使用说明.md',destination/'用户使用说明.md')
metadata={'protocol':1,'version':APP_VERSION,'product':PRODUCT_ID,'platform':PLATFORM,
          'updater_sha256':hashlib.sha256(helper.read_bytes()).hexdigest()}
(destination/'release-info.json').write_text(json.dumps(metadata,ensure_ascii=False,sort_keys=True),encoding='utf-8')
