"""Optional public bulletin viewer. Does not access license credentials."""
import hashlib
import os
from pathlib import Path
import queue
import json
import threading
import time
import urllib.request
import tkinter as tk
from window_position import center_window
from tkinter import ttk
from tkinter import font as tkfont
from ui_scrollbar import SmoothScrollbar
from datetime import datetime
from zoneinfo import ZoneInfo
from license_config import LICENSE_ORIGIN

def show_announcements(app, items=None, remember=None):
    window=tk.Toplevel(app); window.title('公告'); window.transient(app)
    width=min(1040,max(1,window.winfo_screenwidth()-40))
    height=min(720,max(1,window.winfo_screenheight()-60))
    window.geometry(f'{width}x{height}'); window.minsize(min(680,width),min(460,height))
    window.focusmodel('passive')
    window.configure(background='#f4f6fa')
    style=ttk.Style(window)
    style.configure('Bulletin.TFrame',background='#f4f6fa')
    style.configure('Bulletin.TLabel',background='#f4f6fa',foreground='#65758b',font=('Microsoft YaHei UI',10))
    style.configure('Bulletin.Title.TLabel',background='#f4f6fa',foreground='#142c50',font=('Microsoft YaHei UI',22,'bold'))
    style.configure('Bulletin.Treeview',background='#ffffff',fieldbackground='#ffffff',foreground='#334155',font=('Microsoft YaHei UI',11),rowheight=64,borderwidth=0)
    style.map('Bulletin.Treeview',background=[('selected','#dbeafe')],foreground=[('selected','#245fd6')])
    style.configure('Bulletin.TButton',padding=(14,8),font=('Microsoft YaHei UI',10))
    style.configure('Bulletin.Primary.TButton',background='#2865da',foreground='white',padding=(14,8),font=('Microsoft YaHei UI',10))
    style.map('Bulletin.Primary.TButton',background=[('active','#1d50b8'),('disabled','#a5bce7')])
    displayed=[]
    results=queue.Queue()
    status=tk.StringVar(value='点击重新加载获取公告')
    header=ttk.Frame(window,style='Bulletin.TFrame'); header.pack(fill='x',padx=20,pady=(18,12))
    ttk.Label(header,text='公告',style='Bulletin.Title.TLabel').pack(anchor='w')
    ttk.Label(header,text='查看最新通知与历史公告',style='Bulletin.TLabel').pack(anchor='w',pady=(4,0))
    panes=ttk.Panedwindow(window,orient='horizontal')
    listing=ttk.Frame(panes,style='Bulletin.TFrame',padding=(0,0,12,0)); panes.add(listing,weight=1)
    listing.columnconfigure(0,weight=1); listing.rowconfigure(1,weight=1)
    ttk.Label(listing,text='历史公告',style='Bulletin.TLabel').grid(row=0,column=0,sticky='w',pady=(0,8))
    history=ttk.Treeview(listing,show='tree',selectmode='browse',style='Bulletin.Treeview')
    history.column('#0',width=280,minwidth=160)
    title_font=tkfont.Font(window,font=('Microsoft YaHei UI',11))
    style.configure('Bulletin.Treeview',rowheight=max(64,title_font.metrics('linespace')*2+16))
    def wrap_titles(event=None):
        available=max(32,history.column('#0','width')-28)
        for index,item in enumerate(displayed):
            remaining=' '.join(item['title'].split())
            lines=[]
            for line in range(2):
                suffix='…' if line==1 and title_font.measure(remaining)>available else ''
                low,high=0,len(remaining)
                while low<high:
                    mid=(low+high+1)//2
                    if title_font.measure(remaining[:mid]+suffix)<=available: low=mid
                    else: high=mid-1
                lines.append(remaining[:low]+suffix)
                remaining=remaining[low:]
                if not remaining: break
            history.item(str(index),text='\n'.join(lines))
    history.bind('<Configure>',wrap_titles)
    history.grid(row=1,column=0,sticky='nsew')
    list_bar=SmoothScrollbar(listing,command=history.yview); list_bar.grid(row=1,column=1,sticky='ns')
    history.configure(yscrollcommand=list_bar.set)
    list_bar.bind('<MouseWheel>',lambda e:(history.yview_scroll(-1 if e.delta>0 else 1,'units'),'break')[-1])
    reader=ttk.Frame(panes,style='Bulletin.TFrame'); panes.add(reader,weight=3)
    reader.columnconfigure(0,weight=1); reader.rowconfigure(0,weight=1)
    text=tk.Text(reader,wrap='word',width=48,height=14,font=('Microsoft YaHei UI',12),background='#ffffff',foreground='#334155',relief='flat',borderwidth=0,highlightthickness=1,highlightbackground='#e2e8f0',padx=24,pady=24,spacing1=5,spacing3=12,selectbackground='#dbeafe')
    text.grid(row=0,column=0,sticky='nsew')
    text.tag_configure('title',font=('Microsoft YaHei UI',18,'bold'),foreground='#142c50',spacing3=16)
    text.tag_configure('time',font=('Microsoft YaHei UI',9),foreground='#7b8a9f',spacing3=18)
    text.tag_configure('body',font=('Microsoft YaHei UI',12),spacing3=12)
    text.tag_configure('empty',foreground='#7b8a9f')
    reader_bar=SmoothScrollbar(reader,command=text.yview); reader_bar.grid(row=0,column=1,sticky='ns')
    text.configure(yscrollcommand=reader_bar.set,state='disabled')
    reader_bar.bind('<MouseWheel>',lambda e:(text.yview_scroll(-1 if e.delta>0 else 1,'units'),'break')[-1])
    def render(item=None):
        text.configure(state='normal'); text.delete('1.0','end')
        if item:
            at=datetime.fromtimestamp(item['published_at'],ZoneInfo('Asia/Hong_Kong')).strftime('%Y-%m-%d %H:%M:%S')
            text.insert('end',item['title']+'\n','title')
            text.insert('end','发布时间：'+at+'\n','time')
            text.insert('end',item['content'],'body')
        else: text.insert('end','暂无公告，可稍后重新加载。','empty')
        text.configure(state='disabled'); text.yview_moveto(0)
    def select_notice(event=None):
        selection=history.selection()
        if selection and int(selection[0])<len(displayed): render(displayed[int(selection[0])])
    history.bind('<<TreeviewSelect>>',select_notice)
    def finish(items=None,error=None):
        if not window.winfo_exists(): return
        button.configure(state='normal')
        if error: status.set('公告暂不可用，请检查网络后重新加载'); return
        displayed[:]=items
        status.set(f'共 {len(items)} 条公告 · 选择左侧标题查看' if items else '暂无公告')
        history.delete(*history.get_children())
        for index,item in enumerate(items): history.insert('', 'end',iid=str(index),text=item['title'])
        wrap_titles()
        render(items[0] if items else None)
        if items: history.selection_set('0'); history.focus('0')
    def load():
        button.configure(state='disabled'); status.set('正在加载公告…')
        def work():
            try:
                items=fetch_announcements()
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
    def close(mark=True):
        if mark and remember: remember(displayed)
        window.destroy()
    actions=ttk.Frame(window,style='Bulletin.TFrame'); actions.pack(side='bottom',fill='x',padx=20,pady=(12,18))
    button=ttk.Button(actions,text='重新加载',style='Bulletin.TButton',command=load); button.pack(side='left')
    ttk.Button(actions,text='关闭',style='Bulletin.Primary.TButton',command=close).pack(side='right')
    ttk.Button(actions,text='稍后查看',style='Bulletin.TButton',command=lambda:close(False)).pack(side='right',padx=(0,8))
    ttk.Label(window,textvariable=status,style='Bulletin.TLabel').pack(side='bottom',anchor='w',padx=20,pady=(10,0))
    panes.pack(fill='both',expand=True,padx=20)
    window.protocol('WM_DELETE_WINDOW',close)
    if items is None: load()
    else: finish(items)
    center_window(window,app)
    return window


def fetch_announcements():
    request=urllib.request.Request(LICENSE_ORIGIN+'/api/v1/announcements',headers={'Accept':'application/json'})
    with urllib.request.urlopen(request,timeout=10) as response: raw=response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024: raise ValueError('response too large')
    items=json.loads(raw)['items']
    if not isinstance(items,list) or len(items)>100: raise ValueError('invalid items')
    for item in items:
        if not isinstance(item.get('title'),str) or not isinstance(item.get('content'),str) or type(item.get('published_at')) is not int: raise ValueError('invalid item')
        datetime.fromtimestamp(item['published_at'],ZoneInfo('Asia/Hong_Kong'))
    return items


def announcement_key(item):
    """Edits to an existing announcement must also trigger a new notification."""
    identity = str(item.get('id', ''))
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True).encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()
    return identity or digest, digest


class AnnouncementController:
    """Public fetches run off Tk; unread notices wait until the app is idle."""
    CHECK_INTERVAL = 60
    def __init__(self, app, data_dir):
        self.app = app
        self.path = Path(data_dir) / 'announcements-seen.json'
        self.results = queue.Queue()
        self.timer = None
        self.closed = False
        self.window = None
        self.fetching = False
        self.next_check = 0
        self.pending = []
        self.offered = {}
        self._keys = {}
        self._resume_refresh = False
        try:
            self.seen = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.seen, dict): self.seen = {}
        except (OSError, ValueError):
            self.seen = {}

    def item_key(self, item):
        cached = self._keys.get(id(item))
        if cached is None or cached[0] is not item:
            cached = (item, announcement_key(item))
            self._keys[id(item)] = cached
        return cached[1]

    def remember(self, items):
        self.seen.update(dict(self.item_key(item) for item in items))
        # The public endpoint returns at most 100 current/historical entries.
        self.seen = dict(list(self.seen.items())[-100:])
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.seen, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self.path)
        except OSError:
            if hasattr(self.app, 'log'): self.app.log('warn', '公告已读记录未保存，下次启动可能再次提示')

    def open_history(self):
        if self.closed: return
        if self.window is not None and self.window.winfo_exists():
            self.window.destroy()
        self.window = show_announcements(self.app, remember=self.remember)

    def check_startup(self):
        if self.closed or self.fetching: return
        # An explicit check may reoffer a postponed notice; automatic checks do not.
        if self.window is None or not self.window.winfo_exists(): self.offered.clear()
        busy = getattr(self.app, 'update_blocked', lambda: False)()
        if busy: self._resume_refresh = True
        else: self._fetch()
        if self.timer is not None: self.app.after_cancel(self.timer)
        self.timer = self.app.after(1000 if busy else 100, self.poll)

    def _fetch(self):
        if self.closed or self.fetching: return
        self.fetching = True
        results, fetch = self.results, fetch_announcements
        def work():
            try: results.put((fetch(), None))
            except Exception: results.put((None, True))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        self.timer = None
        if self.closed or getattr(self.app, 'closing', False):
            self.close()
            return
        if getattr(self.app, 'update_blocked', lambda: False)():
            self._resume_refresh = True
            self.timer = self.app.after(1000, self.poll)
            return
        if self._resume_refresh:
            # A request started before ordering may finish during it. Discard
            # that result and fetch fresh data before offering deferred notices.
            if self.fetching:
                try: self.results.get_nowait()
                except queue.Empty:
                    self.timer = self.app.after(100, self.poll)
                    return
                self.fetching = False
            self.pending = []
            self._keys.clear()
            self._resume_refresh = False
            self._fetch()
        try:
            items, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.fetching = False
            self.next_check = time.monotonic() + self.CHECK_INTERVAL
            if error:
                if hasattr(self.app, 'log'): self.app.log('warn', '公告暂不可用，稍后自动重试')
            else:
                # Replace rather than append, so withdrawn/superseded notices disappear.
                self.pending = []
                self._keys.clear()
                for item in items:
                    identity, digest = self.item_key(item)
                    if self.seen.get(identity) != digest and self.offered.get(identity) != digest:
                        self.pending.append(item)
        self.pending = [item for item in self.pending if self.seen.get(self.item_key(item)[0]) != self.item_key(item)[1]]
        if self.pending and not (self.window is not None and self.window.winfo_exists()):
            fresh, self.pending = self.pending, []
            self.offered.update(dict(self.item_key(item) for item in fresh))
            self.window = show_announcements(self.app, fresh, self.remember)
        button = getattr(self.app, 'announcement_button', None)
        if button is not None and button.winfo_exists():
            label = '公告（新）' if self.pending else '公告'
            if button.cget('text') != label: button.configure(text=label)
        if not self.fetching and time.monotonic() >= self.next_check: self._fetch()
        delay = 100 if self.fetching else min(1000, max(100, int((self.next_check-time.monotonic())*1000)))
        self.timer = self.app.after(delay, self.poll)

    def close(self):
        self.closed = True
        if self.timer is not None:
            self.app.after_cancel(self.timer)
            self.timer = None
