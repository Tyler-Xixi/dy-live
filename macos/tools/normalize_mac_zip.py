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
        new.writestr('README.txt','Mac test application. Extract this ZIP on a Mac and open the .app.\nDo not extract on Windows and transfer loose app files.\nApple Silicon: arm64. Intel: x86_64. Not notarized; real platform testing is still required.\n')
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
