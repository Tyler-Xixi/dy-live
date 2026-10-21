"""User-selected feedback uploads; independent of licensing and order tasks."""
import base64
import json
from pathlib import Path
import queue
import threading
import urllib.error
import urllib.request
import tkinter as tk
from window_position import center_window
from tkinter import ttk, filedialog
from license_config import LICENSE_ORIGIN
from ui_scrollbar import SmoothScrollbar

ALLOWED = {'.png', '.jpg', '.jpeg', '.webp', '.txt', '.log', '.json', '.zip', '.pdf'}


def submit_feedback(description, contact, metadata, paths):
    description = description.strip()
    if not description or len(description) > 10000:
        raise ValueError('请填写问题描述，最多10000字')
    if len(contact) > 500 or len(paths) > 5:
        raise ValueError('联系方式最多500字，附件最多5个')
    attachments, total = [], 0
    for source in paths:
        path = Path(source)
        if path.suffix.lower() not in ALLOWED:
            raise ValueError('不支持此附件类型，请选择图片、日志、JSON、ZIP或PDF')
        with path.open('rb') as handle:
            data = handle.read(10*1024*1024+1)
        total += len(data)
        if len(data) > 10*1024*1024 or total > 20*1024*1024:
            raise ValueError('每个附件最多10MB，附件合计最多20MB')
        attachments.append({'name': path.name, 'data': base64.b64encode(data).decode('ascii')})
    payload = {'description': description, 'contact': contact.strip(), 'attachments': attachments}
    payload.update({key: str(metadata.get(key, ''))[:100] for key in ('version', 'module', 'browser')})
    request = urllib.request.Request(LICENSE_ORIGIN+'/api/v1/feedback',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('反馈响应过大，未能确认提交结果')
        result = json.loads(raw)
        if result.get('status') != 'received' or not isinstance(result.get('id'), str):
            raise ValueError('未能确认反馈已收到，请勿反复提交')
        return result['id']
    except urllib.error.HTTPError as exc:
        messages = {404:'反馈服务尚未开通，请稍后再试', 413:'附件超过服务器允许大小',
                    429:'反馈提交过于频繁，请稍后再试', 503:'反馈服务暂不可用，请稍后再试'}
        raise ValueError(messages.get(exc.code, '反馈未提交成功，请检查附件或稍后再试')) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError('网络异常，未能确认反馈已收到；填写内容已保留，请勿频繁重发') from exc


def show_feedback(app, metadata=None):
    current = getattr(app, 'feedback_window', None)
    if current is not None and current.winfo_exists():
        current.lift()
        return current
    window = tk.Toplevel(app)
    app.feedback_window = window
    window.title('向我反馈')
    window.geometry('760x640')
    window.minsize(620, 500)
    window.transient(app)
    body = ttk.Frame(window, padding=20)
    body.pack(fill='both', expand=True)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(2, weight=1)
    ttk.Label(body, text='向我反馈', font=('Microsoft YaHei UI', 18, 'bold')).grid(row=0,column=0,sticky='w')
    ttk.Label(body, text='请说明操作步骤、遇到的问题及发生时间。联系方式可留空。').grid(row=1,column=0,sticky='w',pady=(8,12))
    editor = ttk.Frame(body)
    editor.grid(row=2,column=0,sticky='nsew')
    editor.rowconfigure(0,weight=1); editor.columnconfigure(0,weight=1)
    description = tk.Text(editor, wrap='word', font=('Microsoft YaHei UI', 11), height=8,
                          relief='flat',padx=12,pady=12)
    description.grid(row=0,column=0,sticky='nsew')
    bar = SmoothScrollbar(editor,command=description.yview)
    bar.grid(row=0,column=1,sticky='ns'); description.configure(yscrollcommand=bar.set)
    ttk.Label(body,text='联系方式（选填）').grid(row=3,column=0,sticky='w',pady=(12,4))
    contact = ttk.Entry(body); contact.grid(row=4,column=0,sticky='ew')
    ttk.Label(body,text='附件：最多5个，每个10MB，合计20MB。提交前请遮盖个人信息。').grid(row=5,column=0,sticky='w',pady=(12,4))
    listing = tk.Listbox(body,height=4,font=('Microsoft YaHei UI',10))
    listing.grid(row=6,column=0,sticky='ew')
    paths = []
    def render_files():
        listing.delete(0,'end')
        for path in paths:
            listing.insert('end',path.name)
    def add_files():
        selected = filedialog.askopenfilenames(parent=window,title='选择图片或文件',
            filetypes=[('支持的附件','*.png *.jpg *.jpeg *.webp *.txt *.log *.json *.zip *.pdf')])
        for name in selected:
            path=Path(name)
            if path not in paths:
                paths.append(path)
        render_files()
    def remove_files():
        for index in reversed(listing.curselection()):
            paths.pop(index)
        render_files()
    tools=ttk.Frame(body); tools.grid(row=7,column=0,sticky='ew',pady=8)
    add=ttk.Button(tools,text='添加图片或文件',command=add_files); add.pack(side='left')
    remove=ttk.Button(tools,text='删除选中附件',command=remove_files); remove.pack(side='left',padx=8)
    status=tk.StringVar(value='仅发送你填写的内容、选择的附件和软件版本/模块/浏览器类型。')
    ttk.Label(body,textvariable=status,wraplength=650).grid(row=8,column=0,sticky='w',pady=8)
    results=queue.Queue(); timer=None
    def poll():
        nonlocal timer
        if not window.winfo_exists(): return
        try: receipt,error=results.get_nowait()
        except queue.Empty:
            timer=window.after(100,poll); return
        for control in (send,add,remove): control.configure(state='normal' if error else 'disabled')
        status.set(error or '反馈已收到，编号：'+receipt)
        timer=None
    def submit():
        nonlocal timer
        if getattr(app, 'update_blocked', lambda: False)():
            status.set('任务、更新或网络检测进行中，请结束后再提交反馈')
            return
        message=description.get('1.0','end').strip(); address=contact.get(); selected=tuple(paths)
        if not message:
            status.set('请先填写问题描述'); return
        for control in (send,add,remove):control.configure(state='disabled')
        status.set('正在提交反馈…')
        def work():
            try: results.put((submit_feedback(message,address,metadata or {},selected),None))
            except Exception as exc:results.put((None,str(exc)))
        threading.Thread(target=work,daemon=True).start(); timer=window.after(100,poll)
    send=ttk.Button(body,text='提交反馈',style='Primary.TButton',command=submit)
    send.grid(row=9,column=0,sticky='e')
    def closed(event):
        if event.widget is window and timer is not None:
            window.after_cancel(timer)
    window.bind('<Destroy>',closed,add='+')
    center_window(window,app)
    return window
