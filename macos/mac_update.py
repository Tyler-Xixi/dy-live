"""Mac-only signed release checks and verified download; no app replacement."""
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

ORIGIN = 'https://license.txblog.cn'
PUBLIC_KEYS = {'update-primary-v1': base64.b64decode('4K7xHj3n7StXxbiIlyvbEcxiDLqmOfhTjRSgoWxaq60=')}
MAX_SIZE = 512 * 1024 * 1024

class MacUpdateError(ValueError): pass

def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value) or len(value)>32:
        raise MacUpdateError('Mac更新版本号不合法')
    return tuple(map(int, value.split('.')))

def strict_json(raw):
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result: raise MacUpdateError('Mac更新数据有重复字段')
            result[k]=v
        return result
    try: return json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(MacUpdateError('更新数据非法数字')))
    except (ValueError, UnicodeError, RecursionError) as exc: raise MacUpdateError('Mac更新数据损坏') from exc

def signed_bytes(payload):
    return b'DYLiveAssistant:mac-update-manifest:v1\x00'+json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')

@dataclass(frozen=True)
class MacManifest:
    protocol: int
    product: str
    platform: str
    version: str
    sequence: int
    notes: str
    package_path: str
    package_size: int
    package_sha256: str
    minimum_macos: str
    notarized: bool

def parse_manifest(raw, expected_platform, keys=PUBLIC_KEYS, highest=0):
    if len(raw)>65536: raise MacUpdateError('Mac更新清单过大')
    envelope=strict_json(raw)
    if not isinstance(envelope,dict) or set(envelope)!={'key_id','payload','signature'}: raise MacUpdateError('Mac更新签名封装错误')
    try:
        payload=envelope['payload'];key=keys[envelope['key_id']]
        if not isinstance(payload,dict):raise ValueError()
        Ed25519PublicKey.from_public_bytes(key).verify(base64.b64decode(envelope['signature'],validate=True),signed_bytes(payload))
    except (KeyError, ValueError, TypeError, InvalidSignature) as exc: raise MacUpdateError('Mac更新签名验证失败') from exc
    if set(payload)!=set(MacManifest.__dataclass_fields__):raise MacUpdateError('Mac更新清单字段错误')
    if type(payload['protocol']) is not int or payload['protocol']!=1 or payload['product']!='DYLiveAssistant':raise MacUpdateError('不是本产品的Mac更新')
    if expected_platform not in ('macos-arm64','macos-x64') or payload['platform']!=expected_platform:raise MacUpdateError('更新芯片架构不匹配')
    version_tuple(payload['version'])
    if type(payload['sequence']) is not int or payload['sequence']<max(1,highest):raise MacUpdateError('Mac更新序号回退')
    arch='arm64' if expected_platform=='macos-arm64' else 'x86_64'
    if payload['package_path']!=f"/updates/macos/releases/{payload['version']}/DYLiveAssistantMac-{payload['version']}-{arch}.zip":raise MacUpdateError('Mac更新路径不合法')
    if type(payload['package_size']) is not int or not 0<payload['package_size']<=MAX_SIZE or not isinstance(payload['package_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',payload['package_sha256']):raise MacUpdateError('Mac更新包身份错误')
    if not isinstance(payload['notes'],str) or len(payload['notes'])>8000 or payload['minimum_macos']!='15.0' or type(payload['notarized']) is not bool:raise MacUpdateError('Mac更新说明或系统要求错误')
    return MacManifest(**payload)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise MacUpdateError('Mac更新不接受重定向')

class MacUpdateClient:
    def __init__(self,data_dir,platform,*,keys=PUBLIC_KEYS,opener=None):
        self.data_dir=Path(data_dir);self.platform=platform;self.keys=keys
        if platform not in ('macos-arm64','macos-x64'):raise MacUpdateError('不支持的Mac芯片')
        self.state=self.data_dir/'mac-update-state.json';self.verified_raw=None
        self.opener=opener or urllib.request.build_opener(NoRedirect()).open
    def highest(self):
        if not self.state.exists():return 0
        if self.state.stat().st_size>4096:raise MacUpdateError('Mac更新记录损坏')
        value=strict_json(self.state.read_bytes())
        if not isinstance(value,dict) or set(value)!={'highest_sequence'} or type(value['highest_sequence']) is not int or value['highest_sequence']<0:raise MacUpdateError('Mac更新记录损坏')
        return value['highest_sequence']
    def check(self,cancel):
        if cancel.is_set():raise MacUpdateError('更新已取消')
        url=ORIGIN+f'/updates/macos/stable/{self.platform}.json'
        with self.opener(urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=10) as response:
            if response.geturl()!=url:raise MacUpdateError('Mac更新源地址变化')
            raw=response.read(65537)
        manifest=parse_manifest(raw,self.platform,self.keys,self.highest())
        if cancel.is_set():raise MacUpdateError('更新已取消')
        self.data_dir.mkdir(parents=True,exist_ok=True)
        temporary=self.state.with_name('.mac-update-state-'+os.urandom(8).hex()+'.tmp')
        temporary.write_text(json.dumps({'highest_sequence':manifest.sequence}),encoding='utf-8');os.replace(temporary,self.state)
        self.verified_raw=raw
        return manifest
    def download(self,manifest,destination,cancel,progress=lambda *_:None):
        if self.verified_raw is None or parse_manifest(self.verified_raw,self.platform,self.keys,self.highest())!=manifest:raise MacUpdateError('请重新检查Mac更新清单')
        destination=Path(destination).absolute()
        # Never overwrite existing files, app bundles, or account storage.
        if destination.exists() or destination.is_symlink():raise MacUpdateError('下载目标已存在，请选择另一个文件名')
        data=self.data_dir.resolve();parent=destination.parent.resolve()
        if parent==data or data in parent.parents or any(p.suffix=='.app' for p in (parent,*parent.parents)):raise MacUpdateError('不能下载到软件或用户资料内部')
        url=ORIGIN+manifest.package_path;partial=destination.with_name(destination.name+'.part');created=False
        try:
            with partial.open('xb') as target:
                created=True;digest=hashlib.sha256();total=0;deadline=time.monotonic()+600
                with self.opener(urllib.request.Request(url,headers={'Accept-Encoding':'identity'}),timeout=10) as response:
                    if response.geturl()!=url or response.headers.get('Content-Encoding','identity')!='identity':raise MacUpdateError('Mac更新响应不合法')
                    if response.headers.get('Content-Length') and int(response.headers['Content-Length'])!=manifest.package_size:raise MacUpdateError('Mac更新包大小不符')
                    while True:
                        if cancel.is_set() or time.monotonic()>deadline:raise MacUpdateError('下载取消或超时')
                        chunk=response.read(65536)
                        if not chunk:break
                        total+=len(chunk)
                        if total>manifest.package_size:raise MacUpdateError('Mac更新包超过清单大小')
                        digest.update(chunk);target.write(chunk);progress(total,manifest.package_size)
                target.flush();os.fsync(target.fileno())
                if total!=manifest.package_size or digest.hexdigest()!=manifest.package_sha256:raise MacUpdateError('Mac更新包不完整或哈希不符')
            if cancel.is_set():raise MacUpdateError('下载已取消')
            # An exclusive final path prevents a raced-in file being overwritten.
            with destination.open('xb') as final,partial.open('rb') as source:
                import shutil
                shutil.copyfileobj(source,final);final.flush();os.fsync(final.fileno())
            return destination
        finally:
            if created and partial.exists():partial.unlink()
