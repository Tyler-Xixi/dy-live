"""Main-thread dialogs; update I/O is isolated from ordering workers."""
import os
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from app_version import APP_VERSION
from update_protocol import UpdateError, strict_json
from update_process import launch_updater, process_identity


class UpdateDialog(tk.Toplevel):
    def __init__(self,master,manifest,current_version,*,on_confirm,on_defer,package_plan=None):
        super().__init__(master)
        self.title('发现新版本'); self.geometry('480x320'); self.resizable(False,False)
        self.transient(master)
        frame=ttk.Frame(self,padding=20); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text=f'{current_version} → {manifest.version}',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        size=package_plan.package_size if package_plan else manifest.package_size
        kind='增量更新包' if package_plan else '完整更新包'
        ttk.Label(frame,text=f'{kind} {size/1024/1024:.1f} MB · 完整包 {manifest.package_size/1024/1024:.1f} MB',wraplength=430).pack(anchor='w',pady=(8,12))
        notes=tk.Text(frame,height=6,wrap='word',relief='flat',background='#f4f6fa',font=('Microsoft YaHei UI',10))
        notes.insert('1.0',manifest.notes); notes.configure(state='disabled'); notes.pack(fill='both',expand=True)
        buttons=ttk.Frame(frame); buttons.pack(fill='x',pady=(12,0))
        def defer(): self.destroy(); on_defer()
        def confirm(): self.destroy(); on_confirm()
        ttk.Button(buttons,text='稍后再说',command=defer).pack(side='right')
        ttk.Button(buttons,text='确认下载并更新',command=confirm,style='Primary.TButton').pack(side='right',padx=8)
        self.protocol('WM_DELETE_WINDOW',defer)


class UpdateController:
    def __init__(self,app,client):
        self.app=app; self.client=client; self._operation=None; self._cancel=threading.Event()
        self._closed=False; self._auto_done=False; self._deferred=False; self.progress_window=None

    def busy(self): return self._operation is not None
    def cancel(self): self._cancel.set()
    def close(self): self._closed=True; self.cancel()

    def _post(self,callback):
        self.app.ui_queue.put(lambda:callback() if not self._closed else None)

    def _status(self,text):
        if getattr(self.app,'update_status',None): self.app.update_status.set(text)

    def _finish(self):
        self._operation=None
        self.app.set_update_busy(False)
        if self.progress_window:
            self.progress_window.destroy(); self.progress_window=None

    def check(self,manual=False):
        if self._closed or self.busy() or self.app.update_blocked():
            if manual: messagebox.showwarning('暂不能检查','请在任务、登录和测速结束后检查更新。',parent=self.app)
            return
        if not manual:
            if self._auto_done or self._deferred: return
            self._auto_done=True
        self._operation='checking'; self._cancel=threading.Event(); self._status('正在检查更新…')
        def finished(manifest,error=None,plan=None):
            self._finish()
            if error:
                self._status('检查失败，可稍后重试')
                if manual: messagebox.showerror('更新检查失败',str(error),parent=self.app)
            elif manifest is None:
                self._status('已是最新版本')
                if manual: messagebox.showinfo('检查更新','已是最新版本。',parent=self.app)
            elif self.app.update_blocked(): self._status('有新版，请空闲时检查更新')
            else:
                self._status('发现新版 '+manifest.version)
                def defer(): self._deferred=True; self._status('已暂缓更新')
                UpdateDialog(self.app,manifest,APP_VERSION,on_confirm=lambda:self.request_download(manifest,plan=plan),on_defer=defer,package_plan=plan)
        def work():
            try:
                manifest=self.client.check(APP_VERSION,self._cancel)
                plan=None
                if manifest:
                    try:
                        plan=self.client.incremental_offer(manifest,APP_VERSION,self._cancel)
                        reason=getattr(self.client,'incremental_reason','未找到适配增量')
                    except Exception as exc:
                        if self._cancel.is_set(): raise
                        reason='增量检查失败：'+str(exc)
                    self.client.diagnostics.record('check',{'result':'ok','package_type':'incremental' if plan else 'full','incremental_reason': 'selected' if plan else 'unavailable'})
                    if not plan:
                        self._post(lambda reason=reason:messagebox.showinfo('完整包回退',reason+'；保留签名完整包更新。',parent=self.app))
                self._post(lambda:finished(manifest,plan=plan))
            except Exception as exc: self._post(lambda exc=exc:finished(None,exc))
        threading.Thread(target=work,daemon=True).start()

    def request_download(self,manifest,*,plan=None):
        if self._closed or self.busy() or self.app.update_blocked():
            messagebox.showwarning('暂不能更新','请先正常结束任务、登录或测速；软件不会替你停止任务。',parent=self.app); return
        if not getattr(sys,'frozen',False):
            messagebox.showinfo('源码运行','源码模式不能原位更新，请使用完整发行版。',parent=self.app); return
        self._operation='downloading'; self._cancel=threading.Event(); self.app.set_update_busy(True)
        install=Path(sys.executable).parent
        protected=self.app.update_protected_paths()
        if isinstance(self.app,tk.Misc):
            self.progress_window=tk.Toplevel(self.app); self.progress_window.title('下载更新')
            self.progress_window.geometry('420x150'); self.progress_window.transient(self.app)
            self.download_text=tk.StringVar(value='正在预检、下载并验证更新…')
            ttk.Label(self.progress_window,textvariable=self.download_text).pack(padx=20,pady=15)
            self.download_progress=ttk.Progressbar(self.progress_window,maximum=plan.package_size if plan else manifest.package_size)
            self.download_progress.pack(fill='x',padx=20)
            ttk.Button(self.progress_window,text='取消',command=self.cancel).pack(pady=12)
            self.progress_window.protocol('WM_DELETE_WINDOW',self.cancel)
        last_progress=[0.0]
        handoff=[None]
        def progress(current,total):
            now=time.monotonic()
            if now-last_progress[0]<.1 and current!=total: return
            last_progress[0]=now
            def show():
                self._status(f'下载更新 {current*100//total}%')
                if self.progress_window:
                    self.download_progress['value']=current
                    self.download_text.set(f'已下载 {current/1024/1024:.1f} / {total/1024/1024:.1f} MB')
            self._post(show)
        def failed(exc):
            cancelled=self._cancel.is_set(); self._finish()
            self._status('更新已取消，旧版可继续使用' if cancelled else '更新失败，旧版保留')
            if not cancelled:
                messagebox.showerror('更新失败',str(exc),parent=self.app)
                if isinstance(self.app,tk.Misc) and not self._closed: self._offer_alternatives(manifest,plan)
        def takeover():
            if self._cancel.is_set(): failed(UpdateError('更新已取消')); return
            if self.app.update_blocked(ignore_updater=True):
                failed(UpdateError('任务状态已变化，取消更新接管，旧软件保持打开')); return
            launched,parent=handoff[0]
            try:
                from update_client import atomic_json
                atomic_json(launched.request_file.parent/'consent.json',{'protocol':1,'token':launched.token,'parent':vars(parent)})
            except Exception as exc:
                failed(exc); return
            self._finish(); self.app.on_close()
        def work():
            try:
                if plan:
                    prepared=self.client.prepare_incremental(manifest,plan,install,protected,self._cancel,progress)
                else: prepared=self.client.prepare(manifest,install,protected,self._cancel,progress)
                if self._cancel.is_set(): raise UpdateError('更新已取消')
                parent=process_identity(os.getpid())
                if parent is None: raise UpdateError('旧软件进程身份不能确认')
                launched=launch_updater(prepared,parent)
                handoff[0]=(launched,parent)
                deadline=time.monotonic()+30
                while True:
                    if self._cancel.is_set(): raise UpdateError('更新已取消')
                    if launched.process.poll() is not None: raise UpdateError('更新程序未能接管，旧软件保持打开')
                    if launched.ready_file.exists():
                        if launched.ready_file.stat().st_size>4096: raise UpdateError('更新握手数据异常')
                        ready=strict_json(launched.ready_file.read_bytes())
                        if ready!={'protocol':1,'token':launched.token,'parent':vars(parent)}: raise UpdateError('更新握手身份异常')
                        self._post(takeover); return
                    if time.monotonic()>=deadline: raise UpdateError('更新接管超时，旧软件保持打开')
                    time.sleep(.1)
            except Exception as exc: self._post(lambda exc=exc:failed(exc))
        threading.Thread(target=work,daemon=True).start()

    def _offer_alternatives(self,manifest,plan):
        window=tk.Toplevel(self.app); window.title('更新备用方式'); window.transient(self.app)
        frame=ttk.Frame(window,padding=20); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='旧版保留。可以选择完整包重试，或安装新版到新文件夹。\n账号资料不搬迁，不自动下单。',wraplength=420).pack(anchor='w',pady=10)
        def choose(callback): window.destroy(); callback()
        if plan:
            def full():
                if messagebox.askyesno('确认完整包',f'需要重新下载完整包 {manifest.package_size/1024/1024:.1f} MB，是否继续？',parent=self.app):
                    self.request_download(manifest)
            ttk.Button(frame,text='完整包重新更新',command=lambda:choose(full)).pack(fill='x',pady=4)
        ttk.Button(frame,text='安装新版到新文件夹',command=lambda:choose(lambda:self.request_new_install(manifest))).pack(fill='x',pady=4)
        ttk.Button(frame,text='暂不处理',command=window.destroy).pack(fill='x',pady=4)

    def request_new_install(self,manifest):
        if self._closed or self.busy() or self.app.update_blocked(): return
        if not getattr(sys,'frozen',False): return
        if not messagebox.askyesno('安装到新目录',f'下载完整包 {manifest.package_size/1024/1024:.1f} MB 到新文件夹。\n旧软件和登录资料保留；安装后请先关闭旧版再打开新版。是否继续？',parent=self.app): return
        selected=filedialog.askdirectory(title='选择新版安装的上级文件夹',parent=self.app)
        if not selected or self._closed or self.busy() or self.app.update_blocked(): return
        destination=Path(selected)/('DYLiveAssistant-'+manifest.version)
        protected=(*self.app.update_protected_paths(),Path(sys.executable).parent)
        self._operation='new-install'; self._cancel=threading.Event(); self.app.set_update_busy(True)
        def finished(result,error=None):
            self._finish()
            if error:
                self._status('新目录安装未完成，旧版保留')
                if not self._cancel.is_set(): messagebox.showerror('安装未完成',str(error),parent=self.app)
            else:
                self._status('新版已安装，请先关闭旧版')
                messagebox.showinfo('新版安装完成','请先关闭所有旧软件，再从以下文件夹打开新版：\n'+str(result)+'\n不要只复制 EXE。账号资料保留，自定义登录目录继续使用原路径。',parent=self.app)
        def progress(current,total): self._post(lambda:self._status(f'新目录安装：下载 {current*100//total}%'))
        def work():
            try:
                result=self.client.prepare_new_install(manifest,destination,protected,self._cancel,progress)
                self._post(lambda:finished(result))
            except Exception as exc: self._post(lambda exc=exc:finished(None,exc))
        threading.Thread(target=work,daemon=True).start()

    def export_diagnostics(self):
        if self._closed or self.busy(): return
        selected=filedialog.asksaveasfilename(title='导出脱敏更新诊断（请选择新文件名）',parent=self.app,defaultextension='.json',filetypes=[('JSON','*.json')])
        if not selected: return
        try:
            self.client.export_diagnostics(Path(selected),Path(sys.executable).parent)
            messagebox.showinfo('已导出','诊断只保存在本地，不含登录 Cookie、卡密或握手令牌。',parent=self.app)
        except Exception as exc: messagebox.showerror('导出失败',str(exc),parent=self.app)
