"""Fail-closed release signatures and bounded, Windows-safe archive handling."""
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from app_version import PRODUCT_ID, PLATFORM
from update_config import (MAX_MANIFEST_SIZE, MAX_PACKAGE_SIZE, MAX_UNPACKED_SIZE,
                           MAX_FILES, MANAGED_ROOTS, UPDATE_PROTOCOL)


class UpdateError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateManifest:
    protocol: int
    product: str
    platform: str
    version: str
    sequence: int
    published_at: str
    notes: str
    package_path: str
    package_size: int
    package_sha256: str
    files: tuple[ReleaseFile, ...]
    updater_protocol: int


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value,str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',value) or len(value)>32:
        raise UpdateError('更新版本号不合法')
    return tuple(int(part) for part in value.split('.'))


def signed_bytes(payload: dict) -> bytes:
    return b'DYLiveAssistant:update-manifest:v1\x00'+json.dumps(
        payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def strict_json(raw: bytes):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise UpdateError('更新数据有重复字段')
            result[key]=value
        return result
    try:
        return json.loads(raw.decode('utf-8'),object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(UpdateError('非法数字')))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise UpdateError('更新数据格式损坏') from exc


def valid_hash(value):
    return isinstance(value,str) and bool(re.fullmatch('[0-9a-f]{64}',value))


def safe_relative(value: str) -> str:
    if not isinstance(value,str) or not value or len(value)>240 or '\\' in value:
        raise UpdateError('更新文件路径不合法')
    parts=value.split('/')
    for part in parts:
        if (not part or part in ('.','..') or part[-1:] in (' ','.') or
                re.search(r'[\x00-\x1f\x7f<>:"|?*]',part) or
                re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?',part)):
            raise UpdateError('更新包含不安全的文件路径')
    if parts[0] not in MANAGED_ROOTS or (parts[0]!='_internal' and len(parts)!=1):
        raise UpdateError('更新包包含程序白名单以外的文件')
    if parts[0]=='_internal' and len(parts)<2:
        raise UpdateError('程序依赖路径不合法')
    return value


def assert_plain_path(path: Path) -> Path:
    """Reject symlinks/junctions in any existing ancestor, before resolving."""
    path=Path(os.path.abspath(path))
    for ancestor in (path,*path.parents):
        if ancestor.exists() or ancestor.is_symlink():
            info=ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise UpdateError('更新路径包含链接或目录联接')
    if path == Path(path.anchor): raise UpdateError('更新不能操作磁盘根目录')
    return path


def parse_manifest(raw: bytes, public_keys: dict[str,bytes], highest_sequence: int=0) -> UpdateManifest:
    if len(raw)>MAX_MANIFEST_SIZE: raise UpdateError('更新清单过大')
    envelope=strict_json(raw)
    if not isinstance(envelope,dict) or set(envelope)!={'key_id','payload','signature'}:
        raise UpdateError('更新清单没有有效签名封装')
    try:
        key_id=envelope['key_id']
        if not isinstance(key_id,str) or key_id not in public_keys: raise UpdateError('更新签名密钥不受信任')
        payload=envelope['payload']
        if not isinstance(payload,dict): raise UpdateError('更新清单格式错误')
        signature=base64.b64decode(envelope['signature'],validate=True)
        Ed25519PublicKey.from_public_bytes(public_keys[key_id]).verify(signature,signed_bytes(payload))
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        raise UpdateError('更新签名验证失败') from exc
    fields=set(UpdateManifest.__dataclass_fields__)
    if set(payload)!=fields: raise UpdateError('更新清单字段不完整或未知')
    if (type(payload['protocol']) is not int or payload['protocol']!=UPDATE_PROTOCOL or
            type(payload['updater_protocol']) is not int or payload['updater_protocol']!=UPDATE_PROTOCOL or
            payload['product']!=PRODUCT_ID or payload['platform']!=PLATFORM):
        raise UpdateError('更新不适用于当前产品或平台')
    version_tuple(payload['version'])
    if type(payload['sequence']) is not int or payload['sequence']<1 or payload['sequence']<highest_sequence:
        raise UpdateError('更新发布序号不合法或已回退')
    if type(payload['package_size']) is not int or not 0<payload['package_size']<=MAX_PACKAGE_SIZE or not valid_hash(payload['package_sha256']):
        raise UpdateError('更新包大小或哈希不合法')
    version=payload['version']
    if payload['package_path']!=f'/updates/releases/{version}/DYLiveAssistant-{version}-windows-x64.zip':
        raise UpdateError('更新下载路径不合法')
    if not isinstance(payload['notes'],str) or len(payload['notes'])>8000:
        raise UpdateError('更新说明不合法')
    try:
        if not isinstance(payload['published_at'],str) or not payload['published_at'].endswith('Z'): raise ValueError()
        datetime.fromisoformat(payload['published_at'].replace('Z','+00:00'))
    except ValueError as exc: raise UpdateError('更新时间不合法') from exc
    entries=payload['files']
    if not isinstance(entries,list) or not 0<len(entries)<=MAX_FILES: raise UpdateError('更新文件数量不合法')
    files=[]; seen=set(); total=0
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)!={'path','size','sha256'}: raise UpdateError('文件清单字段错误')
        name=safe_relative(entry['path']); folded=name.casefold()
        if folded in seen: raise UpdateError('更新文件存在重名')
        seen.add(folded)
        if type(entry['size']) is not int or entry['size']<0 or not valid_hash(entry['sha256']): raise UpdateError('文件大小或哈希错误')
        total+=entry['size']; files.append(ReleaseFile(**entry))
    if total>MAX_UNPACKED_SIZE: raise UpdateError('更新解压大小超过限制')
    roots={PurePosixPath(file.path).parts[0] for file in files}
    if roots!=set(MANAGED_ROOTS): raise UpdateError('更新缺少完整程序文件')
    for file in files:
        parents=PurePosixPath(file.path).parents
        if any(str(parent).casefold() in seen for parent in parents if str(parent)!='.'):
            raise UpdateError('更新文件与目录路径冲突')
    values=dict(payload); values['files']=tuple(files)
    return UpdateManifest(**values)


def is_newer(manifest: UpdateManifest,current_version: str) -> bool:
    return version_tuple(manifest.version)>version_tuple(current_version)


def file_hash(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()


def validate_archive(zip_path: Path,manifest: UpdateManifest) -> None:
    zip_path=assert_plain_path(zip_path)
    if zip_path.stat().st_size!=manifest.package_size or manifest.package_size>MAX_PACKAGE_SIZE or file_hash(zip_path)!=manifest.package_sha256:
        raise UpdateError('更新包下载不完整或哈希不符')
    expected={file.path:file for file in manifest.files}
    seen=set(); total=0
    try:
        with zipfile.ZipFile(zip_path) as package:
            infos=package.infolist()
            if len(infos)>MAX_FILES: raise UpdateError('更新文件过多')
            for entry in infos:
                name=safe_relative(entry.filename)
                mode=entry.external_attr>>16
                if entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0,stat.S_IFREG)) or entry.flag_bits&1:
                    raise UpdateError('更新包包含链接或特殊文件')
                if name.casefold() in seen or name not in expected: raise UpdateError('更新包有重复或清单以外文件')
                seen.add(name.casefold()); wanted=expected[name]
                if entry.file_size!=wanted.size: raise UpdateError('更新文件大小不符')
                digest=hashlib.sha256(); size=0
                with package.open(entry) as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):
                        size+=len(chunk); total+=len(chunk)
                        if size>wanted.size or total>MAX_UNPACKED_SIZE: raise UpdateError('更新包解压大小超过限制')
                        digest.update(chunk)
                if size!=wanted.size or digest.hexdigest()!=wanted.sha256: raise UpdateError('更新文件哈希不符')
            if seen!={name.casefold() for name in expected}: raise UpdateError('更新包缺少文件')
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
        raise UpdateError('更新压缩包损坏或不可读取') from exc


def extract_verified(zip_path: Path,destination: Path,manifest: UpdateManifest) -> None:
    destination=assert_plain_path(destination)
    if destination.exists(): raise UpdateError('更新暂存目录已存在，拒绝覆盖')
    validate_archive(zip_path,manifest)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as package:
        for file in manifest.files:
            target=assert_plain_path(destination/file.path)
            target.parent.mkdir(parents=True,exist_ok=True)
            with package.open(file.path) as stream, target.open('xb') as output:
                digest=hashlib.sha256(); size=0
                for chunk in iter(lambda:stream.read(1024*1024),b''):
                    size+=len(chunk)
                    if size>file.size: raise UpdateError('更新文件在解压时发生变化')
                    digest.update(chunk); output.write(chunk)
                if size!=file.size or digest.hexdigest()!=file.sha256: raise UpdateError('更新文件在解压时校验失败')
