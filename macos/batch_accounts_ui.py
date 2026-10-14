"""Small account editor; all persistence and login work belongs to the app."""
import tkinter as tk
from tkinter import ttk, messagebox
from dataclasses import replace
import uuid
from batch_accounts import AccountRecord


class AccountManagerDialog(tk.Toplevel):
    def __init__(self, master, accounts, on_save, on_login, busy):
        super().__init__(master)
        self.title('批量下单 · 账号管理')
        self.geometry('770x490')
        self.minsize(710,460)
        self.transient(master)
        self.accounts = tuple(accounts)
        self.on_save, self.on_login, self.busy = on_save, on_login, busy
        self.controls = []
        body = ttk.Frame(self,padding=16); body.pack(fill='both',expand=True)
        ttk.Label(body,text='本人登录 · 独立资料 · 从上到下依次执行',style='Hint.TLabel').pack(anchor='w',pady=(0,8))
        self.tree = ttk.Treeview(body,columns=('name','selected','times','quantity','login'),show='headings',height=9,selectmode='browse')
        for key,label,width in [('name','账号备注 / ID',250),('selected','加入队列',80),('times','订单数',75),('quantity','每单数量',85),('login','登录资料',155)]:
            self.tree.heading(key,text=label); self.tree.column(key,width=width,anchor='w' if key in ('name','login') else 'center')
        self.tree.pack(fill='both',expand=True)
        self.tree.bind('<<TreeviewSelect>>',self.select_record)
        form = ttk.Frame(body); form.pack(fill='x',pady=10)
        self.name=tk.StringVar(); self.times=tk.StringVar(value='1'); self.quantity=tk.StringVar(value='1'); self.selected=tk.BooleanVar()
        for label,var,width in [('备注',self.name,23),('订单数',self.times,7),('每单数量',self.quantity,7)]:
            ttk.Label(form,text=label).pack(side='left',padx=(0,5))
            control=ttk.Entry(form,textvariable=var,width=width); control.pack(side='left',padx=(0,10)); self.controls.append(control)
        check=ttk.Checkbutton(form,text='加入队列',variable=self.selected); check.pack(side='left'); self.controls.append(check)
        actions=ttk.Frame(body); actions.pack(fill='x')
        for label,callback in [('新增',self.add_record),('原有账号',self.add_legacy),('保存修改',self.apply_record),('登录',self.login_record),('上移',lambda:self.move(-1)),('下移',lambda:self.move(1)),('移除',self.remove_record)]:
            button=ttk.Button(actions,text=label,command=callback,width=6); button.pack(side='left',padx=(0,5)); self.controls.append(button)
        ttk.Label(body,text='登录后在主窗口点“完成登录并保存”。移除仅隐藏记录，登录资料不会删除。',style='Hint.TLabel',wraplength=720).pack(anchor='w',pady=(10,0))
        self.refresh()

    def writable(self):
        if self.busy():
            messagebox.showwarning('任务运行中','请先停止任务或结束登录，再修改账号。',parent=self)
            return False
        return True

    def set_busy(self, busy):
        for control in self.controls: control.configure(state='disabled' if busy else 'normal')

    def refresh(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for record in self.accounts:
            self.tree.insert('', 'end',iid=record.account_id,values=(f'{record.name} · {record.account_id[:8]}','✓' if record.selected else '—',record.buy_times,record.buy_quantity,'已保存登录资料' if record.login_saved else '未保存'))
        if select and self.tree.exists(select): self.tree.selection_set(select)
        self.set_busy(self.busy())

    def current(self):
        selection=self.tree.selection()
        return next((a for a in self.accounts if selection and a.account_id==selection[0]),None)

    def select_record(self,*_):
        record=self.current()
        if record:
            self.name.set(record.name); self.times.set(str(record.buy_times)); self.quantity.set(str(record.buy_quantity)); self.selected.set(record.selected)

    def persist(self, accounts):
        if not self.writable(): return False
        self.accounts=tuple(accounts)  # Retain edits on failure; never claim they were saved.
        try:
            self.on_save(self.accounts)
        except (ValueError,OSError) as exc:
            messagebox.showerror('保存失败',str(exc),parent=self)
            return False
        self.refresh()
        return True

    def add_record(self):
        if not self.writable(): return
        record=AccountRecord(uuid.uuid4().hex,'新账号',False,1,1,False)
        if self.persist(self.accounts+(record,)): self.refresh(record.account_id)

    def add_legacy(self):
        if not self.writable(): return
        if any(a.account_id=='legacy' for a in self.accounts):
            self.refresh('legacy'); return
        if self.persist(self.accounts+(AccountRecord('legacy','原有账号',False,1,1,False),)): self.refresh('legacy')

    def apply_record(self):
        record=self.current()
        if not record or not self.writable(): return False
        try:
            updated=replace(record,name=self.name.get().strip(),buy_times=int(self.times.get()),buy_quantity=int(self.quantity.get()),selected=self.selected.get())
        except ValueError:
            messagebox.showerror('配置错误','订单数和每单数量必须是正整数',parent=self); return False
        if self.persist(tuple(updated if a.account_id==record.account_id else a for a in self.accounts)):
            self.refresh(record.account_id); return True
        return False

    def login_record(self):
        record=self.current()
        if record and self.apply_record(): self.on_login(record.account_id)

    def move(self,direction):
        record=self.current()
        if not record or not self.writable(): return
        values=list(self.accounts); index=values.index(record); other=index+direction
        if 0<=other<len(values):
            values[index],values[other]=values[other],values[index]
            if self.persist(values): self.refresh(record.account_id)

    def remove_record(self):
        record=self.current()
        if record and self.writable() and messagebox.askyesno('移除账号','仅移除列表记录，不删除登录资料。确定移除？',parent=self):
            self.persist(tuple(a for a in self.accounts if a.account_id!=record.account_id))
