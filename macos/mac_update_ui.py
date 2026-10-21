"""Mac signed-package update UI. No Windows updater or in-place replacement."""
import json
from pathlib import Path
import threading
from tkinter import messagebox,filedialog,ttk
import tkinter as tk
from app_version import APP_VERSION, PLATFORM
from mac_update import MacUpdateClient,version_tuple
from mac_platform import open_directory
from window_position import center_window

class UpdateController:
    def __init__(self,app,data_dir=None):
        self.app=app;self.closed=False;self.operation=None;self.cancelled=threading.Event();self.auto_checked=False
        self.client=MacUpdateClient(data_dir,PLATFORM) if data_dir is not None else None
        self.progress_window=None
    def busy(self):return self.operation is not None
    def close(self):self.closed=True;self.cancel()
    def cancel(self):self.cancelled.set()
    def post(self,action):self.app.ui_queue.put(lambda:action() if not self.closed else None)
    def begin(self,operation):
        self.operation=operation;self.cancelled=threading.Event();self.app.set_update_busy(True)
    def finish(self):
        self.operation=None;self.app.set_update_busy(False)
        if self.progress_window:self.progress_window.destroy();self.progress_window=None
    def check(self,manual=False):
        if self.closed or self.client is None or self.busy() or self.app.update_blocked():return
        if not manual and self.auto_checked:return
        self.auto_checked=True;self.begin('check')
        def work():
            try:
                manifest=self.client.check(self.cancelled)
                self.post(lambda:self.offer(manifest,manual))
            except Exception as exc:self.post(lambda exc=exc:self.failed('检查Mac更新失败',exc,manual))
        threading.Thread(target=work,daemon=True,name='mac-update-check').start()
    def failed(self,title,error,manual=True):
        self.finish();self.app.update_status.set('Mac更新服务暂不可用')
        if manual:messagebox.showerror(title,str(error),parent=self.app)
    def offer(self,manifest,manual):
        self.finish()
        newer=version_tuple(manifest.version)>version_tuple(APP_VERSION)
        self.app.update_status.set('发现Mac新版 '+manifest.version if newer else '已是最新Mac版本')
        if not newer and not manual:return
        status='发现新版' if newer else '当前已是最新版本，也可以重新下载完整包'
        warning='' if manifest.notarized else '\n本包未通过Apple公证，系统可能限制打开；不能保证直接运行。'
        text=f'{status}\n当前 {APP_VERSION} → 最新 {manifest.version}\n{manifest.package_size/1024/1024:.1f} MB · macOS {manifest.minimum_macos}及以上\n{manifest.notes}\n\n下载后请退出软件，在Finder解压，并替换原应用。不会自动付款或恢复任务。'+warning+'\n\n是否下载对应芯片的Mac完整安装包？'
        if messagebox.askyesno('Mac软件更新',text,parent=self.app):self.download(manifest)
    def download(self,manifest):
        if self.closed or self.busy() or self.app.update_blocked():return
        selected=filedialog.asksaveasfilename(parent=self.app,title='保存Mac完整安装包',initialfile=Path(manifest.package_path).name,defaultextension='.zip',filetypes=[('Mac安装包','*.zip')])
        if not selected or self.app.update_blocked():return
        self.begin('download')
        self.progress_window=tk.Toplevel(self.app);self.progress_window.title('下载Mac更新');self.progress_window.geometry('520x200');self.progress_window.transient(self.app)
        body=ttk.Frame(self.progress_window,padding=24);body.pack(fill='both',expand=True)
        self.progress_text=tk.StringVar(value='正在下载并校验Mac安装包…');ttk.Label(body,textvariable=self.progress_text).pack(anchor='w')
        self.progress=ttk.Progressbar(body,maximum=manifest.package_size);self.progress.pack(fill='x',pady=18)
        ttk.Button(body,text='取消',command=self.cancel).pack(anchor='e');self.progress_window.protocol('WM_DELETE_WINDOW',self.cancel);center_window(self.progress_window,self.app)
        def update(n,total):
            def apply():
                if self.progress_window:self.progress.configure(value=n);self.progress_text.set(f'{n/1024/1024:.1f} / {total/1024/1024:.1f} MB')
            self.post(apply)
        def done(result):
            self.finish();messagebox.showinfo('Mac安装包已校验',f'已保存：{result}\n\n请退出当前软件，再在Finder中解压并替换原应用。登录与卡密资料位于应用外，不要删除。',parent=self.app);open_directory(Path(result).parent)
        def work():
            try:
                result=self.client.download(manifest,Path(selected),self.cancelled,update);self.post(lambda:done(result))
            except Exception as exc:self.post(lambda exc=exc:self.failed('Mac更新下载失败',exc))
        threading.Thread(target=work,daemon=True,name='mac-update-download').start()
    def export_diagnostics(self):
        if self.closed:return
        name=filedialog.asksaveasfilename(parent=self.app,title='导出Mac更新信息',defaultextension='.json',filetypes=[('JSON','*.json')])
        if not name:return
        try:
            with Path(name).open('x',encoding='utf-8') as handle:json.dump({'version':APP_VERSION,'platform':PLATFORM,'update_channel':'macos','operation':self.operation or 'idle','notarized':False},handle,ensure_ascii=False,indent=2)
        except OSError as exc:messagebox.showerror('导出失败',str(exc),parent=self.app)
