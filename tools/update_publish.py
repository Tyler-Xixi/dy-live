"""Explicit server-side signing; private keys never leave their directory."""
import argparse
import base64
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import shutil
import threading
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from update_client import atomic_json
from update_protocol import (UpdateError, assert_plain_path, strict_json, signed_bytes,
                             parse_manifest, validate_archive, version_tuple)
from update_incremental import (signed_incremental_bytes, parse_incremental_plan,
    parse_incremental_index, validate_incremental_archive)

KEY_ID='update-primary-v1'
_guard=threading.Lock()


def _checkpoint(phase): pass


def signed_incremental_envelope(payload,key):
    return {'key_id':KEY_ID,'payload':payload,
            'signature':base64.b64encode(key.sign(signed_incremental_bytes(payload))).decode()}


def prepare_incremental_publication(source,target_raw,keys,key,previous):
    inputs=list(source.glob('incremental-*.payload.json'))
    if not inputs: return None
    if len(inputs)!=1 or previous is None: raise UpdateError('增量发行必须有唯一正式基线')
    payload_file=assert_plain_path(inputs[0])
    if payload_file.stat().st_size>2*1024*1024: raise UpdateError('增量方案过大')
    payload=strict_json(payload_file.read_bytes())
    envelope=signed_incremental_envelope(payload,key)
    raw=json.dumps(envelope,ensure_ascii=False,sort_keys=True).encode()
    plan=parse_incremental_plan(raw,keys,target_raw,previous.version)
    if payload_file.name!=f'incremental-{plan.base_version}.payload.json': raise UpdateError('增量方案文件名不符')
    package=assert_plain_path(source/Path(plan.package_path).name)
    validate_incremental_archive(package,plan)
    index={'protocol':2,'kind':'index','product':plan.product,'platform':plan.platform,
        'base_version':plan.base_version,'target_version':plan.target_version,'sequence':plan.sequence,
        'target_manifest_sha256':plan.target_manifest_sha256,
        'plan_path':f'/updates/releases/{plan.target_version}/incremental-{plan.base_version}.json',
        'plan_sha256':hashlib.sha256(raw).hexdigest()}
    index_envelope=signed_incremental_envelope(index,key)
    index_raw=json.dumps(index_envelope,ensure_ascii=False,sort_keys=True).encode()
    parse_incremental_index(index_raw,keys,target_raw,plan.base_version)
    _checkpoint('incremental_verified')
    return plan,raw,index_envelope,package


def _key(secrets_dir):
    root=assert_plain_path(secrets_dir); path=assert_plain_path(root/'update-ed25519.key')
    if os.name=='posix':
        if root.stat().st_mode&0o077 or path.stat().st_mode&0o077: raise UpdateError('签名密钥目录或文件权限不安全')
    if path.stat().st_size!=32: raise UpdateError('更新签名密钥格式错误')
    return Ed25519PrivateKey.from_private_bytes(path.read_bytes())


def init_key(secrets_dir):
    root=assert_plain_path(secrets_dir)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=assert_plain_path(root/'update-ed25519.key')
    if not path.exists():
        if os.name=='posix' and root.stat().st_mode&0o077: raise UpdateError('签名秘密目录权限必须为0700')
        key=Ed25519PrivateKey.generate()
        data=key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
    key=_key(root)
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    return {'key_id':KEY_ID,'public_key':base64.b64encode(public).decode()}


@contextmanager
def publish_lock(root):
    with _guard:
        path=assert_plain_path(root/'.publish.lock')
        with path.open('a+b') as stream:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            yield


def publish_release(source_dir,public_root,secrets_dir):
    source=assert_plain_path(source_dir); public=assert_plain_path(public_root); secrets=assert_plain_path(secrets_dir)
    if public==secrets or public in secrets.parents or secrets in public.parents: raise UpdateError('公开目录不能与私钥目录重叠')
    key=_key(secrets)
    key_bytes=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    keys={KEY_ID:key_bytes}
    with publish_lock(secrets):
        payload_file=assert_plain_path(source/'payload.json')
        if payload_file.stat().st_size>2*1024*1024: raise UpdateError('发行清单过大')
        payload=strict_json(payload_file.read_bytes())
        signature=key.sign(signed_bytes(payload))
        envelope={'key_id':KEY_ID,'payload':payload,'signature':base64.b64encode(signature).decode()}
        raw=json.dumps(envelope,ensure_ascii=False,sort_keys=True).encode()
        manifest=parse_manifest(raw,keys)
        package=assert_plain_path(source/Path(manifest.package_path).name)
        validate_archive(package,manifest); _checkpoint('verified')
        latest=assert_plain_path(public/'stable'/'latest.json')
        previous=None
        if latest.exists():
            previous=parse_manifest(latest.read_bytes(),keys)
            if manifest.sequence<=previous.sequence or version_tuple(manifest.version)<=version_tuple(previous.version): raise UpdateError('发布版本和序号必须严格递增')
        incremental=prepare_incremental_publication(source,raw,keys,key,previous)
        target=assert_plain_path(public/'releases'/manifest.version)
        if target.exists(): raise UpdateError('已存在的发行版不可覆盖')
        pending=assert_plain_path(public/'releases'/('.pending-'+uuid.uuid4().hex))
        pending.mkdir(parents=True,exist_ok=False)
        copied=pending/package.name
        with package.open('rb') as src,copied.open('xb') as dest:
            shutil.copyfileobj(src,dest); dest.flush(); os.fsync(dest.fileno())
        validate_archive(copied,manifest)
        if incremental:
            plan,plan_raw,index_envelope,delta_package=incremental
            delta_copy=assert_plain_path(pending/delta_package.name)
            with delta_package.open('rb') as src,delta_copy.open('xb') as dest:
                shutil.copyfileobj(src,dest); dest.flush(); os.fsync(dest.fileno())
            validate_incremental_archive(delta_copy,plan)
            plan_file=assert_plain_path(pending/f'incremental-{plan.base_version}.json')
            with plan_file.open('xb') as stream:
                stream.write(plan_raw); stream.flush(); os.fsync(stream.fileno())
        _checkpoint('copied')
        # The signature is verified again against the bytes that will be public.
        parse_manifest(raw,keys); _checkpoint('signed')
        if latest.exists():
            history=secrets/'index-history'; history.mkdir(mode=0o700,exist_ok=True)
            backup=assert_plain_path(history/(uuid.uuid4().hex+'.json'))
            descriptor=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'wb') as stream: stream.write(latest.read_bytes()); stream.flush(); os.fsync(stream.fileno())
        _checkpoint('before_latest')
        os.replace(pending,target)
        if incremental:
            _checkpoint('incremental_resources')
            atomic_json(public/'stable'/'incremental.json',index_envelope)
            _checkpoint('incremental_index')
        # If this last switch fails, keep the immutable complete version for
        # operator investigation; never overwrite or silently retry that version.
        atomic_json(latest,envelope)
        return latest


def main():
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest='command',required=True)
    initialize=sub.add_parser('init-key'); initialize.add_argument('--secrets-dir',type=Path,required=True)
    publish=sub.add_parser('publish'); publish.add_argument('--source',type=Path,required=True)
    publish.add_argument('--public-root',type=Path,required=True); publish.add_argument('--secrets-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='init-key': print(json.dumps(init_key(args.secrets_dir)))
    else: print(publish_release(args.source,args.public_root,args.secrets_dir))


if __name__=='__main__': main()
