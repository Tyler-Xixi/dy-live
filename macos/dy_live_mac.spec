# Build on native macOS Python, separately for arm64 and x86_64.
import os
from PyInstaller.utils.hooks import collect_all
datas,binaries,hiddenimports=collect_all('playwright')
arch=os.environ['DY_MAC_ARCH']
a=Analysis(['dy_grab_gui.py'],pathex=[],binaries=binaries,
           datas=datas+[('assets/app.png','assets'),('用户使用说明.md','.')],hiddenimports=hiddenimports,runtime_hooks=['mac_tls_hook.py'])
# Intel runner Python and cryptography can depend on different OpenSSL 3
# minor versions under the same dylib names. Bundle one matching, newer pair.
if arch == 'x86_64':
    import ctypes
    import subprocess
    from pathlib import Path
    prefix=Path(subprocess.check_output(['brew','--prefix','openssl@3'],text=True).strip())
    ssl=prefix/'lib/libssl.3.dylib'; crypto=prefix/'lib/libcrypto.3.dylib'
    assert ssl.is_file() and crypto.is_file()
    assert getattr(ctypes.CDLL(str(ssl)), 'SSL_get0_group_name')
    a.binaries=[entry for entry in a.binaries if Path(entry[0]).name not in ('libssl.3.dylib','libcrypto.3.dylib')]
    a.binaries += [('libssl.3.dylib',str(ssl),'BINARY'),('libcrypto.3.dylib',str(crypto),'BINARY')]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='DYLiveAssistantMac',
        console=False,target_arch=arch,codesign_identity=os.environ.get('DY_MAC_SIGNING_IDENTITY'))
coll=COLLECT(exe,a.binaries,a.datas,name='DYLiveAssistantMac')
app=BUNDLE(coll,name='DY直播间自助工具.app',icon='assets/app.icns',
           codesign_identity=os.environ.get('DY_MAC_SIGNING_IDENTITY'),
           bundle_identifier='cn.txblog.dyliveassistant.mac',
           info_plist={'CFBundleShortVersionString':'6.2.39','CFBundleVersion':'6239','LSMinimumSystemVersion':'15.0',
                       'NSHighResolutionCapable':True,'CFBundleDisplayName':'DY直播间自助工具'})
