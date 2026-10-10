"""Protocol 2 signatures bind a partial ZIP to an independently signed target."""
import base64
from dataclasses import dataclass
import hashlib
import json
import shutil
from pathlib import Path
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from app_version import PRODUCT_ID, PLATFORM
from update_config import MAX_MANIFEST_SIZE, MAX_PACKAGE_SIZE, MAX_FILES
from update_protocol import (UpdateError, ReleaseFile, strict_json, parse_manifest,
    safe_relative, valid_hash, version_tuple, validate_archive)
from update_protocol import assert_plain_path, file_hash, extract_verified


@dataclass(frozen=True)
class IncrementalPlan:
    protocol: int
    kind: str
    product: str
    platform: str
    base_version: str
    target_version: str
    sequence: int
    target_manifest_sha256: str
    package_path: str
    package_size: int
    package_sha256: str
    changed_files: tuple[ReleaseFile, ...]

    @property
    def files(self): return self.changed_files


@dataclass(frozen=True)
class IncrementalIndex:
    protocol: int
    kind: str
    product: str
    platform: str
    base_version: str
    target_version: str
    sequence: int
    target_manifest_sha256: str
    plan_path: str
    plan_sha256: str


def signed_incremental_bytes(payload: dict) -> bytes:
    return b'DYLiveAssistant:update-incremental:v2\x00'+json.dumps(payload,
        sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def incremental_payload(raw: bytes,public_keys: dict) -> dict:
    if len(raw)>MAX_MANIFEST_SIZE: raise UpdateError('增量清单过大')
    envelope=strict_json(raw)
    if not isinstance(envelope,dict) or set(envelope)!={'key_id','payload','signature'}: raise UpdateError('增量签名封装错误')
    try:
        key_id=envelope['key_id']; payload=envelope['payload']
        if not isinstance(key_id,str) or key_id not in public_keys or not isinstance(payload,dict): raise ValueError()
        signature=base64.b64decode(envelope['signature'],validate=True)
        Ed25519PublicKey.from_public_bytes(public_keys[key_id]).verify(signature,signed_incremental_bytes(payload))
    except (InvalidSignature,ValueError,TypeError,KeyError) as exc: raise UpdateError('增量签名验证失败') from exc
    return payload


def validate_binding(payload,target_raw,keys):
    target=parse_manifest(target_raw,keys)
    if (type(payload.get('protocol')) is not int or payload['protocol']!=2 or
        payload.get('product')!=PRODUCT_ID or payload.get('platform')!=PLATFORM or
        payload.get('target_version')!=target.version or type(payload.get('sequence')) is not int or
        payload['sequence']!=target.sequence or payload.get('target_manifest_sha256')!=hashlib.sha256(target_raw).hexdigest()):
        raise UpdateError('增量方案与完整目标清单不符')
    base=payload.get('base_version')
    if version_tuple(base)>=version_tuple(target.version): raise UpdateError('增量基线必须早于目标版本')
    return target


def parse_incremental_plan(raw: bytes,public_keys: dict,target_raw: bytes,base_version: str) -> IncrementalPlan:
    payload=incremental_payload(raw,public_keys)
    if set(payload)!=set(IncrementalPlan.__dataclass_fields__) or payload.get('kind')!='plan': raise UpdateError('增量方案字段错误')
    target=validate_binding(payload,target_raw,public_keys)
    if payload['base_version']!=base_version: raise UpdateError('增量基线版本不符')
    expected=f'/updates/releases/{target.version}/DYLiveAssistant-{base_version}-to-{target.version}-windows-x64.zip'
    if (payload['package_path']!=expected or type(payload['package_size']) is not int or
        not 0<payload['package_size']<=MAX_PACKAGE_SIZE or not valid_hash(payload['package_sha256'])):
        raise UpdateError('增量包身份不合法')
    entries=payload['changed_files']
    if not isinstance(entries,list) or not 0<len(entries)<=MAX_FILES: raise UpdateError('增量文件数量不合法')
    target_files={f.path:f for f in target.files}; files=[]; seen=set()
    for item in entries:
        if not isinstance(item,dict) or set(item)!={'path','size','sha256'}: raise UpdateError('增量文件字段错误')
        name=safe_relative(item['path'])
        if name.casefold() in seen: raise UpdateError('增量文件重名')
        seen.add(name.casefold())
        if type(item['size']) is not int or item['size']<0 or not valid_hash(item['sha256']): raise UpdateError('增量文件身份错误')
        file=ReleaseFile(**item)
        if target_files.get(name)!=file: raise UpdateError('增量文件与目标清单不符')
        files.append(file)
    values=dict(payload); values['changed_files']=tuple(files)
    return IncrementalPlan(**values)


def parse_incremental_index(raw: bytes,public_keys: dict,target_raw: bytes,base_version: str):
    payload=incremental_payload(raw,public_keys)
    if set(payload)!=set(IncrementalIndex.__dataclass_fields__) or payload.get('kind')!='index': raise UpdateError('增量索引字段错误')
    target=parse_manifest(target_raw,public_keys)
    indexed_version=version_tuple(payload['target_version'])
    indexed_base=version_tuple(payload['base_version'])
    if (type(payload['protocol']) is not int or payload['protocol']!=2 or
        payload['product']!=PRODUCT_ID or payload['platform']!=PLATFORM or
        type(payload['sequence']) is not int or payload['sequence']<1 or
        indexed_base>=indexed_version or not valid_hash(payload['target_manifest_sha256']) or
        not valid_hash(payload['plan_sha256']) or
        payload['plan_path']!=f"/updates/releases/{payload['target_version']}/incremental-{payload['base_version']}.json"):
        raise UpdateError('增量索引身份不合法')
    # A new full-only publication intentionally may leave the previous signed
    # index. It is not an offer for this release and must not block full updates.
    if indexed_version<version_tuple(target.version) and payload['sequence']<target.sequence:
        return None
    target=validate_binding(payload,target_raw,public_keys)
    if (payload['plan_path']!=f"/updates/releases/{target.version}/incremental-{payload['base_version']}.json" or
        not valid_hash(payload['plan_sha256'])): raise UpdateError('增量索引路径或哈希不合法')
    if payload['base_version']!=base_version: return None
    return IncrementalIndex(**payload)


def validate_incremental_archive(archive,plan: IncrementalPlan) -> None:
    # Reuse ZIP safety checks with the *signed partial inventory*, not a forged
    # full manifest. Full target-directory validation is a separate mandatory gate.
    validate_archive(archive,plan)


def validate_target_directory(root,manifest) -> None:
    root=assert_plain_path(root)
    if not root.is_dir(): raise UpdateError('完整目标目录缺失')
    expected={entry.path:entry for entry in manifest.files}; actual=set()
    directories=set()
    for name in expected:
        directories.update(p.as_posix() for p in Path(name).parents if str(p)!='.')
    for path in root.rglob('*'):
        path=assert_plain_path(path); name=path.relative_to(root).as_posix()
        if path.is_dir():
            if name not in directories: raise UpdateError('完整目标目录含清单以外目录')
            continue
        entry=expected.get(name)
        if not entry or not path.is_file() or path.stat().st_size!=entry.size or file_hash(path)!=entry.sha256:
            raise UpdateError('完整目标文件校验失败')
        actual.add(name)
    if actual!=set(expected): raise UpdateError('完整目标目录缺少文件')


def validate_incremental_baseline(plan,target_manifest,install_dir,cancel) -> Path:
    install=assert_plain_path(install_dir)
    if target_manifest.version!=plan.target_version or target_manifest.sequence!=plan.sequence: raise UpdateError('增量目标不符')
    metadata=assert_plain_path(install/'release-info.json')
    if metadata.stat().st_size>4096: raise UpdateError('基线版本记录过大')
    record=strict_json(metadata.read_bytes())
    if not isinstance(record,dict) or record.get('version')!=plan.base_version: raise UpdateError('基线版本变化，请确认完整更新包')
    expected={f.path:f for f in target_manifest.files}
    if any(expected.get(f.path)!=f for f in plan.changed_files): raise UpdateError('增量文件与目标清单不符')
    changed={f.path for f in plan.changed_files}
    # Validate reused sources before creating the stage; copy afterwards and
    # verify again, detecting changes between checking and copying.
    for entry in target_manifest.files:
        if cancel.is_set(): raise UpdateError('更新已取消')
        if entry.path in changed: continue
        source=assert_plain_path(install/entry.path)
        if not source.is_file() or source.stat().st_size!=entry.size or file_hash(source)!=entry.sha256:
            raise UpdateError('增量基线文件不符，请重新确认完整更新包')
    return install


def assemble_incremental(plan,target_manifest,install_dir,archive,staged_dir,cancel) -> Path:
    staged=assert_plain_path(staged_dir)
    if staged.exists(): raise UpdateError('更新暂存目录已存在，拒绝覆盖')
    validate_incremental_archive(archive,plan)
    install=validate_incremental_baseline(plan,target_manifest,install_dir,cancel)
    changed={f.path for f in plan.changed_files}
    extract_verified(archive,staged,plan)
    for entry in target_manifest.files:
        if cancel.is_set(): raise UpdateError('更新已取消')
        if entry.path in changed: continue
        source=assert_plain_path(install/entry.path); dest=assert_plain_path(staged/entry.path)
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists(): raise UpdateError('增量暂存文件冲突')
        shutil.copyfile(source,dest)
        if dest.stat().st_size!=entry.size or file_hash(dest)!=entry.sha256: raise UpdateError('增量源文件在组装时变化')
    validate_target_directory(staged,target_manifest)
    if cancel.is_set(): raise UpdateError('更新已取消')
    return staged
