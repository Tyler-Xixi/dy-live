"""Recoverable, same-volume replacement of explicitly owned program roots."""
import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import threading

from update_config import MANAGED_ROOTS, UPDATE_PUBLIC_KEYS
from update_protocol import UpdateError, assert_plain_path, file_hash, parse_manifest, strict_json
from update_client import PreparedUpdate, PreparedIncrementalUpdate, atomic_json, validate_protected_paths, tree_size

_active=set()
_guard=threading.Lock()


class InstallationLock:
    def __init__(self,install_dir, *, purpose='transaction'):
        if purpose not in ('transaction','handoff'): raise UpdateError('更新锁用途不合法')
        self.root=assert_plain_path(install_dir)
        self.purpose=purpose
        self.identity=os.path.normcase(str(self.root))+'|'+purpose; self.stream=None; self.mutex=None

    def __enter__(self):
        with _guard:
            if self.identity in _active: raise UpdateError('另一个更新程序正在运行')
            _active.add(self.identity)
        try:
            if os.name=='nt':
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
                kernel.CreateMutexW.restype=ctypes.c_void_p
                name='Global\\DYLiveUpdate-'+hashlib.sha256(self.identity.encode()).hexdigest()
                self.mutex=kernel.CreateMutexW(None,False,name)
                if not self.mutex: raise UpdateError('无法创建更新互斥锁')
                kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
                result=kernel.WaitForSingleObject(self.mutex,0)
                if result not in (0,0x80): raise UpdateError('另一个更新程序正在运行')
                self.owned_mutex=True
            lock_path=assert_plain_path(self.root/('.dy-update.lock' if self.purpose=='transaction' else '.dy-update-handoff.lock'))
            self.stream=lock_path.open('a+b'); self.stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return self
        except (OSError,UpdateError) as exc:
            self.__exit__(None,None,None)
            raise UpdateError('无法独占程序安装目录') from exc

    def __exit__(self,*args):
        if self.stream:
            self.stream.close(); self.stream=None
        if self.mutex:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.ReleaseMutex.argtypes=[ctypes.c_void_p]; kernel.CloseHandle.argtypes=[ctypes.c_void_p]
            if getattr(self,'owned_mutex',False): kernel.ReleaseMutex(self.mutex)
            kernel.CloseHandle(self.mutex); self.mutex=None
        with _guard: _active.discard(self.identity)


def snapshot(root):
    assert_plain_path(root)
    if not root.exists(): return None
    if root.is_file(): return {'kind':'file','files':{'':file_hash(root)}}
    if not root.is_dir(): raise UpdateError('程序路径不是普通文件或目录')
    files={}; directories=[]
    for item in sorted(root.rglob('*')):
        assert_plain_path(item)
        relative=item.relative_to(root).as_posix()
        if item.is_dir(): directories.append(relative)
        elif item.is_file(): files[relative]=file_hash(item)
        else: raise UpdateError('程序目录包含特殊文件')
    return {'kind':'directory','files':files,'directories':sorted(directories)}


def validate_snapshot(value,name):
    if value is None: return
    if not isinstance(value,dict) or value.get('kind') not in ('file','directory'): raise UpdateError('备份记录损坏')
    fields={'kind','files'} if value['kind']=='file' else {'kind','files','directories'}
    if set(value)!=fields or not isinstance(value['files'],dict): raise UpdateError('备份记录损坏')
    if value['kind']=='file' and set(value['files'])!={''}: raise UpdateError('备份记录损坏')
    from update_protocol import safe_relative, valid_hash
    for relative,digest in value['files'].items():
        if value['kind']=='directory': safe_relative(name+'/'+relative)
        if not valid_hash(digest): raise UpdateError('备份哈希不合法')
    if value['kind']=='directory':
        if not isinstance(value['directories'],list): raise UpdateError('备份目录记录损坏')
        for relative in value['directories']: safe_relative(name+'/'+relative)


class UpdateTransaction:
    def __init__(self,prepared,*,public_keys=None):
        self.prepared=prepared; self.keys=UPDATE_PUBLIC_KEYS if public_keys is None else public_keys
        if not re.fullmatch('[0-9a-f]{32}',prepared.transaction_id): raise UpdateError('更新事务身份错误')
        root=assert_plain_path(prepared.install_dir)
        expected=root/'.dy-update-transactions'/prepared.transaction_id
        if prepared.work_dir!=expected or prepared.archive!=expected/'package.zip' or prepared.manifest_file!=expected/'manifest.json' or prepared.staged_dir!=expected/'staged':
            raise UpdateError('更新事务路径越界')
        for path in (root,expected,prepared.archive,prepared.manifest_file,prepared.staged_dir): assert_plain_path(path)
        if prepared.manifest_file.stat().st_size>2*1024*1024: raise UpdateError('更新清单过大')
        self.manifest=parse_manifest(prepared.manifest_file.read_bytes(),self.keys)
        self.incremental_plan=None
        if isinstance(prepared,PreparedIncrementalUpdate):
            from update_incremental import parse_incremental_plan
            if prepared.plan_file!=expected/'incremental.json': raise UpdateError('增量事务路径越界')
            plan_file=assert_plain_path(prepared.plan_file)
            if plan_file.stat().st_size>2*1024*1024: raise UpdateError('增量清单过大')
            self.incremental_plan=parse_incremental_plan(plan_file.read_bytes(),self.keys,
                prepared.manifest_file.read_bytes(),prepared.base_version)
        self.journal=expected/'journal.json'; self.backup=expected/'backup'; self.discard=expected/'replaced'
        self.state=None

    def _checkpoint(self,phase,name,point): pass

    def _write(self): atomic_json(self.journal,self.state)

    def _new_snapshot(self,name):
        entries=[entry for entry in self.manifest.files if entry.path.split('/')[0]==name]
        if name!='_internal': return {'kind':'file','files':{'':entries[0].sha256}}
        files={entry.path[len(name)+1:]:entry.sha256 for entry in entries}
        directories=set()
        for relative in files:
            directories.update(str(parent).replace('\\','/') for parent in Path(relative).parents if str(parent)!='.')
        return {'kind':'directory','files':files,'directories':sorted(directories)}

    def preflight(self,protected_paths):
        root=validate_protected_paths(self.prepared.install_dir,protected_paths)
        self.validate_package()
        if self.incremental_plan:
            from update_incremental import validate_target_directory
            validate_target_directory(self.prepared.staged_dir,self.manifest)
        for name in MANAGED_ROOTS:
            if snapshot(self.prepared.staged_dir/name)!=self._new_snapshot(name): raise UpdateError('更新暂存程序发生变化')
            current=snapshot(root/name)
            if current and current['kind']!=('directory' if name=='_internal' else 'file'): raise UpdateError('安装目录结构冲突')
        needed=sum(tree_size(root/name) for name in MANAGED_ROOTS)+16*1024*1024
        if shutil.disk_usage(root).free<needed: raise UpdateError('更新备份空间不足')
        probe=self.prepared.work_dir/'write-probe'
        with probe.open('xb') as stream: stream.write(b'probe'); stream.flush(); os.fsync(stream.fileno())
        probe.unlink()

    def validate_package(self):
        if self.incremental_plan:
            from update_incremental import validate_incremental_archive
            validate_incremental_archive(self.prepared.archive,self.incremental_plan)
        else:
            from update_protocol import validate_archive
            validate_archive(self.prepared.archive,self.manifest)

    def _move(self,phase,name,source,target):
        self.state['entries'][name][phase]='intent'; self._write(); self._checkpoint(phase,name,'intent')
        self._checkpoint(phase,name,'before_move')
        if target.exists(): raise UpdateError('更新目标已存在，拒绝覆盖')
        assert_plain_path(source); assert_plain_path(target)
        os.replace(source,target); self._checkpoint(phase,name,'after_move')
        self.state['entries'][name][phase]='completed'; self._write(); self._checkpoint(phase,name,'completed')

    def install(self):
        with InstallationLock(self.prepared.install_dir):
            if os.name=='nt':
                from update_process import installation_processes
                if installation_processes(self.prepared.install_dir): raise UpdateError('请先关闭该目录的所有软件实例')
            if self.journal.exists(): raise UpdateError('更新事务已经开始，请先恢复')
            self.preflight(())
            self.backup.mkdir(); self.discard.mkdir()
            self.state={'protocol':1,'transaction_id':self.prepared.transaction_id,
                        'install_dir':str(self.prepared.install_dir),'phase':'prepared','entries':{}}
            for name in MANAGED_ROOTS:
                self.state['entries'][name]={'old':snapshot(self.prepared.install_dir/name),'new':self._new_snapshot(name),'backup':'none','replace':'none'}
            self._write(); self.state['phase']='backing_up'; self._write()
            for name in MANAGED_ROOTS:
                if self.state['entries'][name]['old'] is not None:
                    self._move('backup',name,self.prepared.install_dir/name,self.backup/name)
            for name in MANAGED_ROOTS:
                if snapshot(self.backup/name)!=self.state['entries'][name]['old']: raise UpdateError('旧程序备份校验失败')
            self.state['phase']='replacing'; self._write()
            for name in MANAGED_ROOTS:
                self._move('replace',name,self.prepared.staged_dir/name,self.prepared.install_dir/name)
                if snapshot(self.prepared.install_dir/name)!=self.state['entries'][name]['new']: raise UpdateError('新程序校验失败')
            self.state['phase']='installed'; self._write()
            return self.backup

    def _load(self):
        assert_plain_path(self.journal)
        if self.journal.stat().st_size>8*1024*1024: raise UpdateError('恢复日志过大')
        value=strict_json(self.journal.read_bytes())
        if not isinstance(value,dict) or set(value)!={'protocol','transaction_id','install_dir','phase','entries'}: raise UpdateError('恢复日志损坏')
        if type(value['protocol']) is not int or value['protocol']!=1 or value['transaction_id']!=self.prepared.transaction_id or value['install_dir']!=str(self.prepared.install_dir): raise UpdateError('恢复日志安装身份错误')
        if value['phase'] not in ('prepared','backing_up','replacing','installed','starting','committed','restoring','restored','awaiting_manual_close'): raise UpdateError('未知恢复阶段')
        if not isinstance(value['entries'],dict) or set(value['entries'])!=set(MANAGED_ROOTS): raise UpdateError('恢复记录不完整')
        for name,entry in value['entries'].items():
            if not isinstance(entry,dict) or set(entry)!={'old','new','backup','replace'}: raise UpdateError('恢复记录损坏')
            validate_snapshot(entry['old'],name)
            if entry['new']!=self._new_snapshot(name): raise UpdateError('恢复记录与签名清单不符')
            if entry['backup'] not in ('none','intent','completed') or entry['replace'] not in ('none','intent','completed'): raise UpdateError('恢复移动记录损坏')
        self.state=value

    def rollback(self):
        with InstallationLock(self.prepared.install_dir):
            if os.name=='nt':
                from update_process import installation_processes
                if installation_processes(self.prepared.install_dir): raise UpdateError('程序仍在运行，不能恢复')
            self._load()
            if self.state['phase']=='committed': return 'committed'
            if self.state['phase']=='restored': return 'already-restored'
            self.state['phase']='restoring'; self._write()
            for name in reversed(MANAGED_ROOTS):
                entry=self.state['entries'][name]; target=self.prepared.install_dir/name; backup=self.backup/name
                current=snapshot(target); old=entry['old']; saved=snapshot(backup)
                if current==old and saved is None: continue
                if saved!=old: raise UpdateError('恢复备份缺失或已变化，拒绝覆盖')
                if current is not None:
                    if current!=entry['new']: raise UpdateError('程序文件被其他操作修改，拒绝覆盖')
                    destination=assert_plain_path(self.discard/name)
                    if destination.exists(): raise UpdateError('恢复暂存冲突')
                    os.replace(target,destination)
                if saved is not None: os.replace(backup,target)
                if snapshot(target)!=old: raise UpdateError('恢复校验失败')
                self._write()
            self.state['phase']='restored'; self._write()
            return 'restored'

    def commit(self):
        with InstallationLock(self.prepared.install_dir):
            self._load()
            if self.state['phase'] not in ('installed','starting'): raise UpdateError('更新尚未安装完成')
            for name,entry in self.state['entries'].items():
                if snapshot(self.prepared.install_dir/name)!=entry['new']: raise UpdateError('新版程序校验失败')
            self.state['phase']='committed'; self._write()


def recover_transaction(journal_path,*,public_keys=None):
    journal=assert_plain_path(journal_path)
    work=journal.parent
    if journal.name!='journal.json' or work.parent.name!='.dy-update-transactions': raise UpdateError('恢复日志位置不合法')
    if (work/'job.json').exists():
        from dy_live_updater import validate_request
        prepared,_,_=validate_request(work/'job.json',public_keys=public_keys,require_parent=False)
    else:
        prepared=PreparedUpdate(work.name,work,work/'package.zip',work/'manifest.json',work/'staged',work.parent.parent)
    return UpdateTransaction(prepared,public_keys=public_keys).rollback()
