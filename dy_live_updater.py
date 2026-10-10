"""Independent updater. No purchase, browser or licensing task is resumed."""
import argparse
from dataclasses import fields
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from update_client import PreparedUpdate, PreparedIncrementalUpdate, atomic_json
from update_config import UPDATE_PUBLIC_KEYS
from update_protocol import UpdateError, assert_plain_path, strict_json, validate_archive
from update_process import ProcessIdentity, process_identity, installation_processes, wait_for_installation_exit
from update_transaction import UpdateTransaction, InstallationLock, snapshot
from update_diagnostics import UpdateDiagnosticLog, diagnostic_error_fields


def validate_request(path,*,public_keys=None,require_parent=True):
    path=assert_plain_path(path)
    if path.name!='job.json' or path.stat().st_size>16384: raise UpdateError('更新请求位置或大小异常')
    job=strict_json(path.read_bytes())
    if not isinstance(job,dict) or set(job)!={'protocol','token','parent','prepared'} or type(job['protocol']) is not int or job['protocol'] not in (1,2): raise UpdateError('更新请求格式错误')
    if not isinstance(job['token'],str) or not re.fullmatch('[0-9a-f]{64}',job['token']): raise UpdateError('更新请求令牌错误')
    values=job['prepared']
    prepared_type=PreparedUpdate if job['protocol']==1 else PreparedIncrementalUpdate
    if not isinstance(values,dict) or set(values)!={field.name for field in fields(prepared_type)} or any(not isinstance(value,str) for value in values.values()): raise UpdateError('更新请求路径错误')
    prepared=prepared_type(**{key:value if key in ('transaction_id','base_version') else Path(value) for key,value in values.items()})
    transaction=UpdateTransaction(prepared,public_keys=public_keys)
    if path!=prepared.work_dir/'job.json': raise UpdateError('更新请求不属于该事务')
    value=job['parent']
    if not isinstance(value,dict) or set(value)!={'pid','created_at','executable'} or type(value['pid']) is not int or value['pid']<=0 or type(value['created_at']) is not int or value['created_at']<=0 or not isinstance(value['executable'],str): raise UpdateError('旧软件身份错误')
    parent=ProcessIdentity(**value)
    if os.path.normcase(parent.executable)!=os.path.normcase(str(prepared.install_dir/'DYLiveAssistant.exe')): raise UpdateError('旧软件不属于安装目录')
    if require_parent and process_identity(parent.pid)!=parent: raise UpdateError('旧软件进程身份已变化')
    transaction.validate_package()
    return prepared,parent,job['token']


def verify_helper(transaction):
    if not getattr(sys,'frozen',False): return
    from update_protocol import file_hash
    helper=assert_plain_path(Path(sys.executable))
    expected=next(entry.sha256 for entry in transaction.manifest.files if entry.path=='DYLiveUpdater.exe')
    if helper!=transaction.prepared.work_dir/'DYLiveUpdater.exe' or file_hash(helper)!=expected:
        raise UpdateError('更新程序副本身份或哈希不符')


def portable_protected_paths(prepared,data):
    data=assert_plain_path(data)
    protected=[prepared.install_dir,data,data/'live-room-profile']
    preferences=assert_plain_path(data/'preferences.json')
    if preferences.is_file():
        if preferences.stat().st_size>65536: raise UpdateError('登录目录设置过大，暂不能确认备用安装位置')
        values=strict_json(preferences.read_bytes())
        if not isinstance(values,dict): raise UpdateError('登录目录设置无法确认')
        profile=values.get('profile_dir','')
        if not isinstance(profile,str): raise UpdateError('登录目录设置无法确认')
        if profile.strip():
            path=Path(profile.strip())
            if not path.is_absolute():
                raise UpdateError('旧登录目录为相对路径，请在软件中保存为完整路径后重试备用安装')
            protected.append(assert_plain_path(path))
    return tuple(protected)


def finish_startup(transaction,child,token,timeout=30):
    deadline=time.monotonic()+timeout
    acknowledgement=transaction.prepared.work_dir/'started.json'
    while True:
        live=process_identity(child.pid)==child
        if live and acknowledgement.exists():
            assert_plain_path(acknowledgement)
            if acknowledgement.stat().st_size<=4096:
                value=strict_json(acknowledgement.read_bytes())
                if value=={'protocol':1,'token':token,'process':vars(child)}:
                    transaction.commit(); return 'committed'
        if not live:
            if installation_processes(transaction.prepared.install_dir):
                transaction._load(); transaction.state['phase']='awaiting_manual_close'; transaction._write()
                return 'awaiting_manual_close'
            return transaction.rollback()
        if time.monotonic()>=deadline:
            transaction._load(); transaction.state['phase']='awaiting_manual_close'; transaction._write()
            return 'awaiting_manual_close'
        time.sleep(0.1)


def complete_result(transaction,result):
    if result=='restored':
        transaction._load()
        if transaction.state['phase']!='restored': raise UpdateError('旧版恢复尚未完成，不能重启')
        for name,entry in transaction.state['entries'].items():
            if snapshot(transaction.prepared.install_dir/name)!=entry['old']:
                raise UpdateError('恢复后的旧版校验失败，不能重启')
        if installation_processes(transaction.prepared.install_dir):
            raise UpdateError('旧版已恢复，但软件实例仍运行，请自行重新打开')
        main=assert_plain_path(transaction.prepared.install_dir/'DYLiveAssistant.exe')
        if not main.is_file(): raise UpdateError('旧版主程序缺失，请检查备份')
        # Plain launch only: never resume purchase tasks or skip activation.
        subprocess.Popen([str(main)],cwd=str(transaction.prepared.install_dir))
    atomic_json(transaction.prepared.work_dir/'result.json',{'result':result})
    return result


def run_request(path):
    prepared,parent,token=validate_request(path)
    # Session ownership spans ready, consent, replacement and startup/restore.
    # The distinct short transaction lock remains usable inside this lease.
    with InstallationLock(prepared.install_dir,purpose='handoff'):
        return _run_request_locked(path,prepared,parent,token)


def _run_request_locked(path,prepared,parent,token):
    diagnostic=UpdateDiagnosticLog(prepared.work_dir)
    diagnostic.record('handoff',{'pid':parent.pid,'result':'started','install_path':prepared.install_dir})
    transaction=UpdateTransaction(prepared)
    verify_helper(transaction)
    transaction.preflight(())
    atomic_json(prepared.work_dir/'ready.json',{'protocol':1,'token':token,'parent':vars(parent)})
    diagnostic.record('wait_exit',{'pid':parent.pid,'result':'started'})
    if not wait_for_installation_exit(parent,prepared.install_dir):
        atomic_json(prepared.work_dir/'result.json',{'result':'old_process_still_running'})
        return 2
    consent=assert_plain_path(prepared.work_dir/'consent.json')
    if (not consent.exists() or consent.stat().st_size>4096 or
            strict_json(consent.read_bytes())!={'protocol':1,'token':token,'parent':vars(parent)}):
        atomic_json(prepared.work_dir/'result.json',{'result':'handoff_not_confirmed'})
        return 2
    try:
        diagnostic.record('replace',{'result':'started'})
        transaction.install()
        transaction._load(); transaction.state['phase']='starting'; transaction._write()
        process=subprocess.Popen([str(prepared.install_dir/'DYLiveAssistant.exe'),'--dy-update-ack',str(path),'--dy-update-token',token],cwd=str(prepared.install_dir))
        diagnostic.record('startup',{'pid':process.pid,'result':'started'})
        child=process_identity(process.pid)
        if child is None:
            if process.poll() is None: raise UpdateError('新版进程身份不能确认，请手动关闭后恢复')
            result=transaction.rollback()
        else: result=finish_startup(transaction,child,token)
        complete_result(transaction,result)
        diagnostic.record('commit' if result=='committed' else 'recovery',{'result':result})
        return 0 if result=='committed' else 3
    except Exception as exc:
        diagnostic.record('failed',dict(diagnostic_error_fields(exc),result='failed'))
        if transaction.journal.exists() and not installation_processes(prepared.install_dir):
            result=transaction.rollback()
            complete_result(transaction,result)
            if result=='restored': raise UpdateError('更新失败，旧版已恢复并重新打开：'+str(exc)) from exc
        raise


def run_recovery(path):
    path=assert_plain_path(path)
    if path.name!='journal.json': raise UpdateError('恢复请求位置异常')
    prepared,parent,token=validate_request(path.parent/'job.json',require_parent=False)
    with InstallationLock(prepared.install_dir,purpose='handoff'):
        return _run_recovery_locked(prepared)


def _run_recovery_locked(prepared):
    if installation_processes(prepared.install_dir): raise UpdateError('请先关闭该目录中的软件再恢复')
    transaction=UpdateTransaction(prepared)
    verify_helper(transaction)
    # Recovery is deliberately not contingent on the current main EXE existing.
    return complete_result(transaction,transaction.rollback())


def recovery_details(work_dir):
    """Only advertise an existing, validated transaction's recovery command."""
    if work_dir is None: return ''
    try:
        work_dir=assert_plain_path(work_dir)
        if not (work_dir/'journal.json').is_file() or not (work_dir/'DYLiveUpdater.exe').is_file(): return ''
        prepared,_,_=validate_request(work_dir/'job.json',public_keys=UPDATE_PUBLIC_KEYS,require_parent=False)
        transaction=UpdateTransaction(prepared,public_keys=UPDATE_PUBLIC_KEYS)
        transaction._load()
        return ('恢复记录：'+str(transaction.journal)+'\n恢复工具：'+str(work_dir/'DYLiveUpdater.exe')+
                ' --recover "'+str(transaction.journal)+'"')
    except (OSError,ValueError,UpdateError): return ''


def portable_candidate(work_dir,*,public_keys=None):
    if work_dir is None: return None
    try:
        prepared,_,_=validate_request(assert_plain_path(work_dir)/'job.json',public_keys=public_keys,require_parent=False)
        transaction=UpdateTransaction(prepared,public_keys=public_keys)
        verify_helper(transaction)
        if transaction.journal.exists():
            transaction._load()
            if transaction.state['phase'] not in ('restored','committed'): return None
        return prepared,transaction.manifest
    except (OSError,ValueError,UpdateError): return None


def show_result(result,work_dir,message=''):
    """Windowless helper errors must still be actionable to the user."""
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import threading
    import queue
    messages={
        'restored':'更新未完成，旧版已恢复并重新打开。请核对状态后自行启动任务。',
        'already-restored':'旧版已经恢复；如尚未打开，请自行重新打开软件。',
        'awaiting_manual_close':'新版未确认启动。请手动关闭此安装目录中的软件，再运行恢复工具；程序不会强制结束进程。',
        'old_process_still_running':'旧软件仍运行，未替换文件。请关闭该目录中的其他实例后重试。',
        'handoff_not_confirmed':'更新接管未确认，未替换程序文件，旧版可以继续使用。',
        'failed':'更新失败。请查看下方原因及恢复位置；不要重复启动更新。',
    }
    window=tk.Tk(); window.title('DYLiveAssistant 更新结果')
    window.geometry('600x380'); window.minsize(480,300)
    frame=ttk.Frame(window,padding=20); frame.pack(fill='both',expand=True)
    ttk.Label(frame,text=messages.get(result,'更新操作已结束。'),wraplength=550).pack(anchor='w',pady=(0,12))
    if message: ttk.Label(frame,text=message[:1000],wraplength=550).pack(anchor='w',pady=(0,12))
    details=recovery_details(work_dir)
    if details:
        ttk.Label(frame,text=details,wraplength=550).pack(anchor='w')
    elif work_dir is not None:
        has_journal=(work_dir/'journal.json').exists()
        text=('恢复记录未能验证，请保留整个更新目录，不要重复更新。' if has_journal else
              '尚未开始替换文件，旧版保留。请正常关闭其他软件实例后重试；仍失败可使用完整包安装到新目录。')
        ttk.Label(frame,text=text,wraplength=550).pack(anchor='w')
    candidate=portable_candidate(work_dir)
    if candidate:
        prepared,manifest=candidate
        cancel=threading.Event(); events=queue.Queue()
        def close(): cancel.set(); window.destroy()
        window.protocol('WM_DELETE_WINDOW',close)
        def install_new():
            if not messagebox.askyesno('备用安装','将完整新版安装到新文件夹，保留旧软件和登录资料。\n完成后请先关闭旧软件，再手动打开新版。是否继续？',parent=window): return
            selected=filedialog.askdirectory(title='选择新版安装的上级目录',parent=window)
            if not selected: return
            button.configure(state='disabled')
            def work():
                try:
                    from update_portable_install import install_full_to_new_directory
                    from update_client import UpdateTransport
                    from update_config import UPDATE_ORIGIN
                    import uuid
                    data=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local'))/'DYLiveAssistant'
                    protected=portable_protected_paths(prepared,data)
                    archive=prepared.archive
                    if isinstance(prepared,PreparedIncrementalUpdate):
                        cache=assert_plain_path(prepared.work_dir/('portable-'+uuid.uuid4().hex)); cache.mkdir()
                        archive=cache/'package.zip'
                        UpdateTransport(UPDATE_ORIGIN).download(manifest,archive,cancel,lambda *_:None)
                    result=install_full_to_new_directory(manifest,archive,Path(selected)/('DYLiveAssistant-'+manifest.version),tuple(protected),cancel=cancel)
                    events.put((result,None))
                except Exception as exc: events.put((None,str(exc)))
            threading.Thread(target=work,daemon=True).start()
        def poll():
            try: result,error=events.get_nowait()
            except queue.Empty: pass
            else:
                button.configure(state='normal')
                if error: messagebox.showerror('备用安装未完成',error,parent=window)
                else: messagebox.showinfo('新版已安装','请先关闭所有旧软件，再手动打开：\n'+str(result/'DYLiveAssistant.exe')+'\n不要只复制 EXE；不自动启动下单任务。',parent=window)
            if not cancel.is_set(): window.after(100,poll)
        button=ttk.Button(frame,text='完整新版安装到新文件夹',command=install_new)
        button.pack(anchor='w',pady=(8,0)); window.after(100,poll)
    ttk.Button(frame,text='知道了',command=close if candidate else window.destroy).pack(side='bottom',anchor='e',pady=(12,0))
    window.attributes('-topmost',True); window.after(250,lambda:window.attributes('-topmost',False))
    window.mainloop()


def main():
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--request',type=Path); group.add_argument('--recover',type=Path)
    args=parser.parse_args()
    try:
        if args.request:
            code=run_request(args.request)
            if code and getattr(sys,'frozen',False):
                result=strict_json((args.request.parent/'result.json').read_bytes())
                show_result(result['result'],args.request.parent)
            return code
        result=run_recovery(args.recover)
        if getattr(sys,'frozen',False) and result!='committed': show_result(result,args.recover.parent)
        return 0
    except Exception as exc:
        parent=args.request.parent if args.request else args.recover.parent
        recovery=None
        try:
            requested=args.request if args.request else args.recover
            parent=assert_plain_path(parent)
            if (requested.name==('job.json' if args.request else 'journal.json') and
                    parent.parent.name=='.dy-update-transactions' and re.fullmatch('[0-9a-f]{32}',parent.name)):
                atomic_json(parent/'result.json',{'result':'failed','message':str(exc)[:1000]})
                UpdateDiagnosticLog(parent).record('failed',dict(diagnostic_error_fields(exc),result='failed'))
                recovery=parent
        except (OSError,UpdateError): pass
        if getattr(sys,'frozen',False): show_result('failed',recovery,str(exc))
        return 1


if __name__=='__main__': raise SystemExit(main())
