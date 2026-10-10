"""Consent-separated update checks and bounded HTTPS downloads."""
from dataclasses import dataclass
from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from update_config import (UPDATE_ORIGIN, MANIFEST_PATH, MAX_MANIFEST_SIZE,
                           CHECK_TIMEOUT, DOWNLOAD_TIMEOUT, MANAGED_ROOTS)
from update_protocol import (UpdateError, UpdateManifest, assert_plain_path,
                             strict_json, parse_manifest, is_newer, extract_verified)
from update_diagnostics import UpdateDiagnosticLog, diagnostic_error_fields
from update_download import response_chunks, content_length, RetryNetwork, verified_download, bounded_response


@dataclass(frozen=True)
class PreparedUpdate:
    transaction_id: str
    work_dir: Path
    archive: Path
    manifest_file: Path
    staged_dir: Path
    install_dir: Path


@dataclass(frozen=True)
class PreparedIncrementalUpdate(PreparedUpdate):
    plan_file: Path
    base_version: str


def atomic_json(path, value):
    path=assert_plain_path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as stream:
        stream.write(json.dumps(value,ensure_ascii=False,sort_keys=True).encode())
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)


@contextmanager
def state_write_lock(path):
    """Serialize the read/compare/write across independent application instances."""
    path=assert_plain_path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        deadline=time.monotonic()+5
        while True:
            try:
                stream.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic()>=deadline: raise UpdateError('更新状态正在由其他实例写入，请稍后重试') from exc
                time.sleep(.01)
        try: yield
        finally:
            stream.seek(0)
            if os.name=='nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


class UpdateStateStore:
    def __init__(self,data_dir):
        self.path=assert_plain_path(Path(data_dir)/'update_state.json')
        self._lock=threading.RLock()

    def load(self):
        with self._lock:
            assert_plain_path(self.path)
            if not self.path.exists(): return {'highest_sequence':0,'last_result':''}
            if self.path.stat().st_size>4096: raise UpdateError('更新状态损坏，请修复后重试')
            state=strict_json(self.path.read_bytes())
            if (not isinstance(state,dict) or set(state)!={'highest_sequence','last_result'} or
                    type(state['highest_sequence']) is not int or state['highest_sequence']<0 or
                    not isinstance(state['last_result'],str)):
                raise UpdateError('更新状态损坏，请修复后重试')
            return state

    def save(self,highest_sequence,last_result=''):
        with self._lock,state_write_lock(self.path.with_name('.update-state.lock')):
            old=self.load()
            if type(highest_sequence) is not int or highest_sequence<old['highest_sequence']:
                raise UpdateError('拒绝回退更新发布序号')
            atomic_json(self.path,{'highest_sequence':highest_sequence,'last_result':last_result[:1000]})


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self,origin): self.origin=origin
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        # Immutable endpoints need no redirect. Refuse before contacting its target.
        raise UpdateError('更新源重定向被拒绝')


class UpdateTransport:
    def __init__(self,origin,*,opener=None):
        if origin!=UPDATE_ORIGIN: raise UpdateError('更新源必须使用官方 HTTPS 地址')
        self.origin=origin
        self._opener=opener or urllib.request.build_opener(RestrictedRedirect(origin)).open

    def _read(self,path,limit,cancel,timeout,output=None,progress=None,exact=None):
        url=self.origin+path
        deadline=time.monotonic()+timeout
        def check():
            if cancel.is_set(): raise UpdateError('更新已取消')
            if time.monotonic()>=deadline: raise UpdateError('更新请求超时')
        check()
        request=urllib.request.Request(url,headers={'Accept-Encoding':'identity','Cache-Control':'no-cache'})
        try:
            with bounded_response(self._opener(request,timeout=min(10,timeout))) as response:
                if response.geturl()!=url: raise UpdateError('更新源重定向或地址异常')
                if response.headers.get('Content-Encoding','identity').lower()!='identity': raise UpdateError('更新响应编码不合法')
                length=content_length(response)
                if length is not None:
                    if length>limit or (exact is not None and length!=exact): raise UpdateError('更新响应大小不符')
                total=0; chunks=[]
                for chunk in response_chunks(response,cancel,deadline):
                    check()
                    total+=len(chunk)
                    if total>limit: raise UpdateError('更新下载超过大小限制')
                    if output is None: chunks.append(chunk)
                    else: output.write(chunk)
                    if progress: progress(total,exact)
                if (exact is not None and total!=exact) or (length is not None and total!=length):
                    raise UpdateError('更新下载不完整')
                return b''.join(chunks)
        except (OSError,urllib.error.URLError,RetryNetwork) as exc:
            raise UpdateError('更新网络请求失败或超时') from exc

    def fetch_manifest(self,cancel):
        return self._read(MANIFEST_PATH,MAX_MANIFEST_SIZE,cancel,CHECK_TIMEOUT)

    def download(self,manifest,output,cancel,progress):
        expected=f'/updates/releases/{manifest.version}/DYLiveAssistant-{manifest.version}-windows-x64.zip'
        if manifest.package_path!=expected: raise UpdateError('更新包路径不合法')
        return self.download_verified(expected,manifest.package_size,manifest.package_sha256,output,cancel,progress)

    def download_verified(self,path,size,sha256,output,cancel,progress):
        return verified_download(self._opener,self.origin,path,size,sha256,output,cancel,progress,timeout=DOWNLOAD_TIMEOUT)


def validate_protected_paths(install_dir,protected_paths):
    install_dir=assert_plain_path(install_dir)
    for name in MANAGED_ROOTS:
        managed=assert_plain_path(install_dir/name)
        for protected in protected_paths:
            protected=assert_plain_path(protected)
            if protected==managed or protected in managed.parents or managed in protected.parents:
                raise UpdateError('浏览器资料或用户数据与程序目录重叠，不能原位更新')
    return install_dir


def tree_size(root):
    if not root.exists(): return 0
    assert_plain_path(root)
    if root.is_file(): return root.stat().st_size
    total=0
    for item in root.rglob('*'):
        assert_plain_path(item)
        if item.is_file(): total+=item.stat().st_size
    return total


def preflight_install_dir(install_dir,protected_paths,required_bytes):
    root=validate_protected_paths(install_dir,protected_paths)
    if not root.is_dir(): raise UpdateError('程序安装目录不存在')
    if any(part.casefold() in ('360zip$temp','360$') for part in root.parts):
        raise UpdateError('软件位于压缩包预览临时目录，请将完整包解压到固定文件夹后打开')
    transactions=assert_plain_path(root/'.dy-update-transactions')
    if transactions.exists():
        for work in transactions.iterdir():
            work=assert_plain_path(work)
            if not work.is_dir(): raise UpdateError('更新工作区结构异常')
            journal=assert_plain_path(work/'journal.json')
            if not journal.exists(): continue
            if journal.stat().st_size>8*1024*1024: raise UpdateError('已有更新恢复记录异常，请先处理')
            value=strict_json(journal.read_bytes())
            if not isinstance(value,dict) or value.get('phase') not in ('committed','restored'):
                raise UpdateError('已有未完成更新，请先恢复，不能重复更新')
            try:
                from dy_live_updater import validate_request
                from update_transaction import UpdateTransaction
                prepared,_,_=validate_request(work/'job.json',require_parent=False)
                if prepared.install_dir!=root or prepared.work_dir!=work:
                    raise UpdateError('已有恢复记录安装身份错误')
                transaction=UpdateTransaction(prepared);transaction._load()
            except (OSError,ValueError,KeyError,TypeError,UpdateError) as exc:
                raise UpdateError('已有更新恢复记录无法验证，请先处理；未下载更新包') from exc
    if shutil.disk_usage(root).free<required_bytes: raise UpdateError('磁盘空间不足，不能更新')
    if os.name=='nt':
        from update_process import installation_processes
        other=[identity for identity in installation_processes(root) if identity.pid!=os.getpid()]
        if other: raise UpdateError('该安装目录还有其他软件实例，请先正常关闭后重试；未替换文件')
    probe=assert_plain_path(root/('.dy-write-probe-'+uuid.uuid4().hex))
    try:
        with probe.open('xb') as stream: stream.write(b'probe'); stream.flush(); os.fsync(stream.fileno())
    except OSError as exc: raise UpdateError('程序目录不可写，请选择有写入权限的固定文件夹') from exc
    finally:
        if probe.is_file(): probe.unlink()
    return root


class UpdateClient:
    def __init__(self,data_dir,public_keys,transport):
        self.data_dir=assert_plain_path(data_dir)
        self.keys=dict(public_keys); self.transport=transport
        self.state=UpdateStateStore(self.data_dir)
        self.diagnostics=UpdateDiagnosticLog(self.data_dir)
        self._verified_raw=None
        self._verified_incremental_raw=None

    def check(self,current_version,cancel):
        self.diagnostics.record('check',{'version':current_version,'result':'started'})
        try:
            state=self.state.load()
            raw=self.transport.fetch_manifest(cancel)
            manifest=parse_manifest(raw,self.keys,state['highest_sequence'])
            self.state.save(manifest.sequence,'checked')
            self._verified_raw=raw
            self.diagnostics.record('check',{'version':current_version,'target_version':manifest.version,
                                           'sequence':manifest.sequence,'result':'ok'})
            return manifest if is_newer(manifest,current_version) else None
        except Exception as exc:
            self.diagnostics.record('check',dict(diagnostic_error_fields(exc),result='failed'))
            raise

    def prepare(self,manifest,install_dir,protected_paths,cancel,progress):
        self.diagnostics.record('preflight',{'install_path':install_dir,'target_version':manifest.version,
                                            'package_type':'full','result':'started'})
        try:
            return self._prepare(manifest,install_dir,protected_paths,cancel,progress)
        except Exception as exc:
            self.diagnostics.record('failed',dict(diagnostic_error_fields(exc),result='failed'))
            raise

    def incremental_offer(self,manifest,current_version,cancel):
        from update_incremental import parse_incremental_index,parse_incremental_plan
        self._verified_incremental_raw=None
        if self._verified_raw is None or parse_manifest(self._verified_raw,self.keys,self.state.load()['highest_sequence'])!=manifest:
            raise UpdateError('请重新检查完整更新清单')
        try:
            raw=self.transport._read('/updates/stable/incremental.json',MAX_MANIFEST_SIZE,cancel,CHECK_TIMEOUT)
        except UpdateError as exc:
            if isinstance(exc.__cause__,urllib.error.HTTPError) and exc.__cause__.code==404: return None
            raise
        index=parse_incremental_index(raw,self.keys,self._verified_raw,current_version)
        if index is None: return None
        plan_raw=self.transport._read(index.plan_path,MAX_MANIFEST_SIZE,cancel,CHECK_TIMEOUT)
        if hashlib.sha256(plan_raw).hexdigest()!=index.plan_sha256: raise UpdateError('增量方案哈希不符')
        plan=parse_incremental_plan(plan_raw,self.keys,self._verified_raw,current_version)
        if plan.package_size>=manifest.package_size: return None
        self._verified_incremental_raw=plan_raw
        return plan

    def prepare_incremental(self,manifest,plan,install_dir,protected_paths,cancel,progress):
        from update_incremental import parse_incremental_plan,assemble_incremental,validate_incremental_baseline
        self.diagnostics.record('preflight',{'install_path':install_dir,'package_type':'incremental','result':'started'})
        try:
            if cancel.is_set(): raise UpdateError('更新已取消')
            if self._verified_raw is None or self._verified_incremental_raw is None: raise UpdateError('请先检查增量更新')
            target_raw=self._verified_raw; plan_raw=self._verified_incremental_raw
            verified=parse_manifest(target_raw,self.keys,self.state.load()['highest_sequence'])
            if verified!=manifest or parse_incremental_plan(plan_raw,self.keys,target_raw,plan.base_version)!=plan:
                raise UpdateError('更新方案变化，请重新确认')
            install_dir=validate_protected_paths(install_dir,protected_paths)
            if not install_dir.is_dir(): raise UpdateError('程序安装目录不存在')
            old_size=sum(tree_size(install_dir/name) for name in MANAGED_ROOTS)
            required=plan.package_size+sum(f.size for f in manifest.files)+old_size+16*1024*1024
            preflight_install_dir(install_dir,protected_paths,required)
            validate_incremental_baseline(plan,manifest,install_dir,cancel)
            transaction_id=uuid.uuid4().hex
            work=assert_plain_path(install_dir/'.dy-update-transactions'/transaction_id)
            work.mkdir(parents=True,exist_ok=False)
            manifest_file=work/'manifest.json'; plan_file=work/'incremental.json'; archive=work/'package.zip'; staged=work/'staged'
            for path,raw in ((manifest_file,target_raw),(plan_file,plan_raw)):
                with path.open('xb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            self.diagnostics.record('download',{'package_type':'incremental','total_bytes':plan.package_size,'result':'started'})
            self.transport.download_verified(plan.package_path,plan.package_size,plan.package_sha256,archive,cancel,progress)
            self.diagnostics.record('assemble',{'result':'started'})
            assemble_incremental(plan,manifest,install_dir,archive,staged,cancel)
            self.diagnostics.record('assemble',{'result':'ok'})
            return PreparedIncrementalUpdate(transaction_id,work,archive,manifest_file,staged,install_dir,plan_file,plan.base_version)
        except Exception as exc:
            self.diagnostics.record('failed',dict(diagnostic_error_fields(exc),result='failed'))
            raise

    def prepare_new_install(self,manifest,destination,protected_paths,cancel,progress):
        from update_portable_install import install_full_to_new_directory,preflight_new_directory
        if self._verified_raw is None or parse_manifest(self._verified_raw,self.keys,self.state.load()['highest_sequence'])!=manifest:
            raise UpdateError('更新清单变化，请重新检查')
        if cancel.is_set(): raise UpdateError('更新已取消')
        destination=assert_plain_path(destination)
        for protected in (*protected_paths,self.data_dir):
            protected=assert_plain_path(protected)
            if destination==protected or destination in protected.parents or protected in destination.parents:
                raise UpdateError('新目录与旧软件或资料重叠')
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise UpdateError('请选择全新的空目录')
        preflight_new_directory(manifest,destination,(*protected_paths,self.data_dir))
        self.data_dir.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(self.data_dir).free<manifest.package_size+16*1024*1024: raise UpdateError('下载缓存空间不足')
        work=assert_plain_path(self.data_dir/'update-new-install'/uuid.uuid4().hex)
        work.mkdir(parents=True,exist_ok=False)
        archive=work/'package.zip'
        self.diagnostics.record('download',{'package_type':'full','total_bytes':manifest.package_size,'result':'started'})
        self.transport.download(manifest,archive,cancel,progress)
        self.diagnostics.record('verify',{'result':'started'})
        result=install_full_to_new_directory(manifest,archive,destination,(*protected_paths,self.data_dir),cancel=cancel)
        self.diagnostics.record('verify',{'result':'ok'})
        return result

    def export_diagnostics(self,destination,install_dir):
        transactions=assert_plain_path(Path(install_dir)/'.dy-update-transactions')
        roots=[]
        if transactions.is_dir():
            candidates=[]
            for item in transactions.iterdir():
                import re
                if re.fullmatch('[0-9a-f]{32}',item.name):
                    item=assert_plain_path(item)
                    if item.is_dir(): candidates.append(item)
            roots=sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True)[:10]
        return self.diagnostics.export(destination,extra_roots=roots)

    def _prepare(self,manifest,install_dir,protected_paths,cancel,progress):
        if cancel.is_set(): raise UpdateError('更新已取消')
        if self._verified_raw is None: raise UpdateError('请先检查更新')
        verified=parse_manifest(self._verified_raw,self.keys,self.state.load()['highest_sequence'])
        if verified!=manifest: raise UpdateError('更新清单已变化，请重新确认')
        install_dir=validate_protected_paths(install_dir,protected_paths)
        if not install_dir.is_dir(): raise UpdateError('程序安装目录不存在')
        old_size=sum(tree_size(install_dir/name) for name in MANAGED_ROOTS)
        required=manifest.package_size+sum(file.size for file in manifest.files)+old_size+16*1024*1024
        preflight_install_dir(install_dir,protected_paths,required)
        transaction_id=uuid.uuid4().hex
        work=assert_plain_path(install_dir/'.dy-update-transactions'/transaction_id)
        work.mkdir(parents=True,exist_ok=False)
        archive=work/'package.zip'; manifest_file=work/'manifest.json'; staged=work/'staged'
        with manifest_file.open('xb') as stream:
            stream.write(self._verified_raw); stream.flush(); os.fsync(stream.fileno())
        self.diagnostics.record('download',{'package_type':'full','total_bytes':manifest.package_size,'result':'started'})
        self.transport.download(manifest,archive,cancel,progress)
        if cancel.is_set(): raise UpdateError('更新已取消')
        self.diagnostics.record('verify',{'result':'started'})
        extract_verified(archive,staged,manifest)
        if cancel.is_set(): raise UpdateError('更新已取消')
        self.diagnostics.record('verify',{'result':'ok'})
        return PreparedUpdate(transaction_id,work,archive,manifest_file,staged,install_dir)
