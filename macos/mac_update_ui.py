"""Mac's initial local build never contacts or installs Windows updates."""
import json
import platform
from tkinter import messagebox,filedialog
from pathlib import Path
from app_version import APP_VERSION


class UpdateController:
    def __init__(self, app):
        self.app = app
        self.closed = False

    def busy(self): return False
    def close(self): self.closed = True
    def cancel(self): pass

    def check(self, manual=False):
        if self.closed or not manual: return
        messagebox.showinfo('Mac版本更新', 'Mac独立更新通道尚未发布。请从维护者获取对应芯片的Mac安装包，退出旧版后安装。不会下载或安装Windows更新。', parent=self.app)

    def export_diagnostics(self):
        if self.closed: return
        name = filedialog.asksaveasfilename(parent=self.app, title='导出Mac版本信息', defaultextension='.json', filetypes=[('JSON', '*.json')])
        if not name: return
        try:
            with Path(name).open('x',encoding='utf-8') as handle:
                json.dump({'version':APP_VERSION,'os':'macOS','architecture':platform.machine(),'update_channel':'not_published'},handle,ensure_ascii=False,indent=2)
        except OSError as exc:
            messagebox.showerror('导出失败', str(exc), parent=self.app)
