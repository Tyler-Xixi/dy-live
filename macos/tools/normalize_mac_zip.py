"""Normalize ditto's UTF-8 names while preserving signed bytes and Unix modes."""
import copy
import hashlib
import json
from pathlib import Path
import zipfile


def normalize(source,target):
    source,target=Path(source),Path(target)
    with zipfile.ZipFile(source,metadata_encoding='utf-8') as old,zipfile.ZipFile(target,'x') as new:
        for entry in old.infolist():
            new.writestr(copy.copy(entry),old.read(entry))
        new.writestr('使用说明.txt','DY直播间助手 Mac 6.2.39\n要求macOS15及以上。请在Mac直接解压，把.app拖到应用程序，再双击打开。无需安装Python。\nM系列用arm64包，Intel用x86_64包。首次运行使用自己的卡密激活和自己的抖音登录。\n此包未通过Apple公证，系统可能限制打开，不能保证直接放行。\n检查更新会下载并校验对应芯片的Mac完整包；退出软件后，在Finder解压、替换原.app。保留Library/Application Support/DYLiveAssistantMac中的用户资料。\n完整操作说明见软件内使用指南。\n')
    with zipfile.ZipFile(source,metadata_encoding='utf-8') as old,zipfile.ZipFile(target) as new:
        assert new.testzip() is None
        for entry in old.infolist():
            assert old.read(entry)==new.read(entry.filename),entry.filename
            assert entry.external_attr==new.getinfo(entry.filename).external_attr,entry.filename
    return {'result':'PASS','source':str(source),'target':str(target),'files':len(old.infolist()),
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'payload_bytes_and_unix_modes_preserved':True,'utf8_names_normalized':True}


if __name__=='__main__':
    import sys
    print(json.dumps(normalize(*sys.argv[1:]),ensure_ascii=True))
