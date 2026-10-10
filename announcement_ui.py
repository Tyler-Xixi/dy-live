"""Optional public bulletin viewer. Does not access license credentials."""
import queue
import json
import threading
import urllib.request
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from zoneinfo import ZoneInfo
from license_config import LICENSE_ORIGIN

def show_announcements(app):
    window=tk.Toplevel(app); window.title('公告'); window.geometry('640x500')
    results=queue.Queue()
    status=tk.StringVar(value='点击重新加载获取公告')
    ttk.Label(window,textvariable=status).pack(anchor='w',padx=12,pady=8)
    text=tk.Text(window,wrap='word'); text.pack(fill='both',expand=True,padx=12,pady=8); text.configure(state='disabled')
    def finish(items=None,error=None):
        if not window.winfo_exists(): return
        button.configure(state='normal')
        if error: status.set('公告暂不可用，请检查网络后重新加载'); return
        status.set('公告已加载' if items else '暂无公告')
        text.configure(state='normal'); text.delete('1.0','end')
        for item in items:
            at=datetime.fromtimestamp(item['published_at'],ZoneInfo('Asia/Hong_Kong')).strftime('%Y-%m-%d %H:%M:%S')
            text.insert('end',item['title']+' · '+at+'\n'+item['content']+'\n\n')
        text.configure(state='disabled')
    def load():
        button.configure(state='disabled'); status.set('正在加载公告…')
        def work():
            try:
                request=urllib.request.Request(LICENSE_ORIGIN+'/api/v1/announcements',headers={'Accept':'application/json'})
                with urllib.request.urlopen(request,timeout=10) as response: raw=response.read(4*1024*1024+1)
                if len(raw)>4*1024*1024: raise ValueError('response too large')
                items=json.loads(raw)['items']
                if not isinstance(items,list) or len(items)>100: raise ValueError('invalid items')
                for item in items:
                    if not isinstance(item.get('title'),str) or not isinstance(item.get('content'),str) or type(item.get('published_at')) is not int: raise ValueError('invalid item')
                    datetime.fromtimestamp(item['published_at'],ZoneInfo('Asia/Hong_Kong'))
                results.put((items,None))
            except Exception: results.put((None,True))
        threading.Thread(target=work,daemon=True).start()
    timer=None
    def poll():
        nonlocal timer
        if not window.winfo_exists(): return
        try:
            items,error=results.get_nowait(); finish(items,error)
        except queue.Empty: pass
        timer=window.after(100,poll)
    timer=window.after(100,poll)
    def close_poll(event):
        if event.widget is window and timer is not None:
            window.after_cancel(timer)
    window.bind('<Destroy>',close_poll,add='+')
    button=ttk.Button(window,text='重新加载',command=load); button.pack(pady=8); load()
