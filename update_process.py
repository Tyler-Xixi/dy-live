"""Exact Windows process identity and independent updater handshake."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

from update_client import PreparedUpdate, PreparedIncrementalUpdate, atomic_json
from update_config import UPDATE_PUBLIC_KEYS
from update_protocol import UpdateError, assert_plain_path, file_hash, parse_manifest, strict_json


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created_at: int
    executable: str


def process_identity(pid):
    if os.name!='nt': raise UpdateError('更新程序仅支持 Windows')
    if type(pid) is not int or pid<=0: return None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle: return None
    try:
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        exit_code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(exit_code)) or exit_code.value!=259: return None
        creation=wintypes.FILETIME(); exited=wintypes.FILETIME(); system=wintypes.FILETIME(); user=wintypes.FILETIME()
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        if not kernel.GetProcessTimes(handle,ctypes.byref(creation),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user)): return None
        size=wintypes.DWORD(32768); buffer=ctypes.create_unicode_buffer(size.value)
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)): return None
        return ProcessIdentity(pid,(creation.dwHighDateTime<<32)|creation.dwLowDateTime,os.path.abspath(buffer.value))
    finally: kernel.CloseHandle(handle)


def process_has_exited(pid):
    """Prove exit; permission/query failures are never proof of absence."""
    if os.name!='nt': raise UpdateError('更新程序仅支持 Windows')
    if type(pid) is not int or not 0<pid<=0xffffffff: raise UpdateError('进程编号异常')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    # SYNCHRONIZE only: no terminate access and no query-path permission needed.
    handle=kernel.OpenProcess(0x100000,False,pid)
    if not handle:
        error=ctypes.get_last_error()
        if error==87: return True  # valid nonzero PID no longer exists
        raise UpdateError(f'无法确认软件进程 {pid} 是否退出（Windows 错误 {error}），未替换文件')
    try:
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        result=kernel.WaitForSingleObject(handle,0)
        if result==0: return True
        if result==258: return False
        raise UpdateError(f'无法确认软件进程 {pid} 是否退出（Windows 错误 {ctypes.get_last_error()}），未替换文件')
    finally: kernel.CloseHandle(handle)


def installation_processes(install_dir):
    if os.name!='nt': raise UpdateError('更新程序仅支持 Windows')
    # Toolhelp gives image names even if a process refuses query access.
    class ProcessEntry(ctypes.Structure):
        _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),
                  ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
                  ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]; kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateToolhelp32Snapshot(2,0)
    if handle==ctypes.c_void_p(-1).value: raise UpdateError('无法检查其他软件实例')
    try:
        kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]
        kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
        entry=ProcessEntry(); entry.dwSize=ctypes.sizeof(entry)
        found=[]; valid=kernel.Process32FirstW(handle,ctypes.byref(entry))
        expected=os.path.normcase(str(Path(install_dir)/'DYLiveAssistant.exe'))
        while valid:
            if entry.szExeFile.casefold()=='dyliveassistant.exe':
                ctypes.set_last_error(0)
                identity=process_identity(entry.th32ProcessID)
                query_error=ctypes.get_last_error()
                if identity is None:
                    if not process_has_exited(entry.th32ProcessID):
                        detail=f'（Windows 查询错误 {query_error}）' if query_error else ''
                        raise UpdateError(f'软件进程 {entry.th32ProcessID} 仍运行但身份不能确认{detail}，请关闭其他实例后重试；未替换文件')
                elif os.path.normcase(identity.executable)==expected: found.append(identity)
            valid=kernel.Process32NextW(handle,ctypes.byref(entry))
        return found
    finally: kernel.CloseHandle(handle)


def wait_for_installation_exit(parent,install_dir,timeout=30):
    deadline=time.monotonic()+timeout
    while True:
        error=None
        try:
            identity=process_identity(parent.pid)
            if identity!=parent and not installation_processes(install_dir): return True
        except UpdateError as exc:
            # A process may be closing between snapshot and query. Never treat
            # unknown identity as exit; allow only bounded further observation.
            error=exc
        if time.monotonic()>=deadline:
            if error is not None: raise error
            return False
        time.sleep(min(0.1,max(0,deadline-time.monotonic())))


@dataclass(frozen=True)
class UpdateLaunch:
    process: subprocess.Popen
    ready_file: Path
    request_file: Path
    token: str


def launch_updater(prepared,parent):
    if not getattr(sys,'frozen',False): raise UpdateError('源码运行不能原位更新，请使用发行版')
    expected=os.path.normcase(str(prepared.install_dir/'DYLiveAssistant.exe'))
    if parent!=process_identity(os.getpid()) or os.path.normcase(parent.executable)!=expected: raise UpdateError('旧软件进程身份不符')
    manifest=parse_manifest(prepared.manifest_file.read_bytes(),UPDATE_PUBLIC_KEYS)
    source=assert_plain_path(prepared.staged_dir/'DYLiveUpdater.exe')
    wanted=next(file.sha256 for file in manifest.files if file.path=='DYLiveUpdater.exe')
    if file_hash(source)!=wanted: raise UpdateError('独立更新程序校验失败')
    helper=assert_plain_path(prepared.work_dir/'DYLiveUpdater.exe')
    with source.open('rb') as src,helper.open('xb') as dest:
        shutil.copyfileobj(src,dest); dest.flush(); os.fsync(dest.fileno())
    if file_hash(helper)!=wanted: raise UpdateError('独立更新程序副本损坏')
    token=secrets.token_hex(32); request=prepared.work_dir/'job.json'
    job_protocol=2 if isinstance(prepared,PreparedIncrementalUpdate) else 1
    atomic_json(request,{'protocol':job_protocol,'token':token,'parent':vars(parent),'prepared':{key:str(value) for key,value in vars(prepared).items()}})
    # cmd expansion is unsafe for %/! even inside quotes; offer the recovery
    # command only for paths that can be represented without expansion.
    if not any(character in str(helper)+str(prepared.work_dir) for character in '%!^&|<>\r\n'):
        recovery=prepared.work_dir/'恢复更新.cmd'
        with recovery.open('x',encoding='utf-8-sig',newline='') as stream:
            stream.write('@echo off\r\nchcp 65001 >nul\r\n"'+str(helper)+'" --recover "'+str(prepared.work_dir/'journal.json')+'"\r\n')
    process=subprocess.Popen([str(helper),'--request',str(request)],cwd=str(prepared.work_dir),creationflags=0x08000000)
    return UpdateLaunch(process,prepared.work_dir/'ready.json',request,token)


def acknowledge_startup():
    if not getattr(sys,'frozen',False): return
    if '--dy-update-ack' not in sys.argv: return
    try:
        index=sys.argv.index('--dy-update-ack'); path=assert_plain_path(Path(sys.argv[index+1]))
        token=sys.argv[sys.argv.index('--dy-update-token')+1]
        if path.name!='job.json' or path.parent.parent.name!='.dy-update-transactions' or len(token)!=64: return
        if path.stat().st_size>16384: return
        job=strict_json(path.read_bytes())
        if job['token']!=token: return
        identity=process_identity(os.getpid())
        if identity is None or os.path.normcase(identity.executable)!=os.path.normcase(str(path.parent.parent.parent/'DYLiveAssistant.exe')): return
        from dy_live_updater import validate_request
        prepared,_,_=validate_request(path,require_parent=False)
        from update_transaction import UpdateTransaction,snapshot
        transaction=UpdateTransaction(prepared); transaction._load()
        if transaction.state['phase'] not in ('installed','starting'): return
        for name,entry in transaction.state['entries'].items():
            if snapshot(prepared.install_dir/name)!=entry['new']: return
        atomic_json(path.parent/'started.json',{'protocol':1,'token':token,'process':vars(identity)})
    except (UpdateError,OSError,ValueError,KeyError,IndexError):
        # Failed update acknowledgement never bypasses or breaks activation.
        return
