# Build on native macOS Python, separately for arm64 and x86_64.
import os
from PyInstaller.utils.hooks import collect_all
datas,binaries,hiddenimports=collect_all('playwright')
arch=os.environ['DY_MAC_ARCH']
a=Analysis(['dy_grab_gui.py'],pathex=[],binaries=binaries,
           datas=datas+[('assets/app.png','assets'),('用户使用说明.md','.')],hiddenimports=hiddenimports)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='DYLiveAssistantMac',
        console=False,target_arch=arch,codesign_identity=os.environ.get('DY_MAC_SIGNING_IDENTITY'))
coll=COLLECT(exe,a.binaries,a.datas,name='DYLiveAssistantMac')
app=BUNDLE(coll,name='DY直播间自助工具.app',icon='assets/app.icns',
           codesign_identity=os.environ.get('DY_MAC_SIGNING_IDENTITY'),
           bundle_identifier='cn.txblog.dyliveassistant.mac',
           info_plist={'CFBundleShortVersionString':'6.1.21','CFBundleVersion':'1',
                       'NSHighResolutionCapable':True,'CFBundleDisplayName':'DY直播间自助工具'})
