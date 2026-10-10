"""Owned release fixtures; ephemeral keys are never used as product keys."""
import base64
import hashlib
import json
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def sample_release(folder: Path, version='6.1.1', sequence=2, extras=None):
    contents={'DYLiveAssistant.exe':b'new-main', 'DYLiveUpdater.exe':b'new-helper',
              '_internal/runtime.dll':b'new-runtime', '用户使用说明.md':'说明'.encode(),
              'release-info.json':json.dumps({'version':version}).encode()}
    contents.update(extras or {})
    archive=folder/'release.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as package:
        for path,data in contents.items():
            package.writestr(path,data)
    payload={'protocol':1,'product':'DYLiveAssistant','platform':'windows-x64',
             'version':version,'sequence':sequence,'published_at':'2026-10-09T10:00:00Z',
             'notes':'修复与优化','package_path':f'/updates/releases/{version}/DYLiveAssistant-{version}-windows-x64.zip',
             'package_size':archive.stat().st_size,'package_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
             'files':[{'path':path,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for path,data in contents.items()],
             'updater_protocol':1}
    return archive,payload,contents


def envelope(payload, key=None):
    key=key or Ed25519PrivateKey.generate()
    # Literal independent protocol serialization, not the production helper.
    message=b'DYLiveAssistant:update-manifest:v1\x00'+json.dumps(
        payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
    raw=json.dumps({'key_id':'test','payload':payload,'signature':base64.b64encode(key.sign(message)).decode()},
                   ensure_ascii=False).encode('utf-8')
    return raw,{'test':key.public_key().public_bytes_raw()}
