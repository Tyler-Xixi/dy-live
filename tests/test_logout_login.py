"""Isolated browser/session fixtures: never log out a user's real account."""
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import dy_grab_gui as gui


class LogoutBrowserTests(unittest.TestCase):
    def test_reset_login_does_not_reuse_foreign_page(self):
        with tempfile.TemporaryDirectory() as folder:
            config=gui.AutomationConfig(profile_dir=folder,live_url='https://live.douyin.com/712159628601')
            finish=threading.Event(); holder={}
            def launch(playwright,*args):
                context=playwright.chromium.launch_persistent_context(folder,channel='msedge',headless=True)
                context.route('**/*',lambda route:route.fulfill(body='<html>owned fixture</html>',content_type='text/html'))
                foreign=context.pages[0]; foreign.goto('https://other.test/')
                holder.update(context=context,foreign=foreign); return context
            def ready():
                self.assertEqual(holder['foreign'].url,'https://other.test/')
                holder['context'].add_cookies([{'name':'sessionid','value':'new-fake','domain':'.douyin.com','path':'/'}])
                finish.set()
            session=gui.LoginSession(config,lambda *args:None,threading.Event(),finish,reset_login=True,on_ready=ready)
            with patch.object(gui,'launch_persistent_browser',side_effect=launch): self.assertEqual(session.run(),'saved')

    def test_foreign_page_with_douyin_iframe_keeps_its_state(self):
        with tempfile.TemporaryDirectory() as folder,sync_playwright() as pw:
            context=pw.chromium.launch_persistent_context(folder,channel='msedge',headless=True)
            try:
                context.route('**/*',lambda route:route.fulfill(body='<html>owned fixture</html>',content_type='text/html'))
                page=context.new_page(); page.goto('https://other.test/')
                page.evaluate("localStorage.setItem('keep','yes'); sessionStorage.setItem('keep','yes')")
                page.evaluate("""async () => { await new Promise(resolve => {
                    const r=indexedDB.open('keep',1); r.onupgradeneeded=()=>r.result.createObjectStore('data');
                    r.onsuccess=()=>{r.result.close();resolve()};
                }); const f=document.createElement('iframe'); f.src='https://www.douyin.com/'; document.body.append(f); }""")
                page.wait_for_selector('iframe')
                page.frames[1].wait_for_load_state()
                gui.clear_douyin_login(context)
                self.assertFalse(page.is_closed())
                self.assertEqual(page.evaluate("[localStorage.getItem('keep'),sessionStorage.getItem('keep')]"),['yes','yes'])
                self.assertEqual(page.evaluate('async () => (await indexedDB.databases()).map(x=>x.name)'),['keep'])
                self.assertFalse(any('douyin.com' in frame.url for frame in page.frames))
            finally: context.close()

    def test_existing_login_window_logs_out_then_saves_replacement_account(self):
        with tempfile.TemporaryDirectory() as folder:
            config=gui.AutomationConfig(profile_dir=str(Path(folder)/'profile'),live_url='https://live.douyin.com/712159628601')
            finish=threading.Event(); events=[]; holder={}
            def launch(playwright,*args):
                context=playwright.chromium.launch_persistent_context(config.profile_dir,channel='msedge',headless=True)
                context.route('**/*',lambda route:route.fulfill(body='<html>owned fixture</html>',content_type='text/html'))
                context.add_cookies([{'name':'sessionid','value':'old-fake','domain':'.douyin.com','path':'/','expires':2000000000}])
                holder['context']=context; return context
            def ready():
                events.append('ready')
                if events.count('ready')==1: session.logout_event.set()
                else:
                    holder['context'].add_cookies([{'name':'sessionid','value':'new-fake','domain':'.douyin.com','path':'/','expires':2000000000}])
                    finish.set()
            def logged_out():
                self.assertFalse(gui.has_authenticated_douyin_cookie(holder['context'].cookies()))
                events.append('logged_out')
            session=gui.LoginSession(config,lambda *args:None,threading.Event(),finish,on_ready=ready,on_logout=logged_out)
            with patch.object(gui,'launch_persistent_browser',side_effect=launch): self.assertEqual(session.run(),'saved')
            self.assertEqual(events,['ready','logged_out','ready'])
            with sync_playwright() as pw:
                context=pw.chromium.launch_persistent_context(config.profile_dir,channel='msedge',headless=True)
                try: self.assertEqual([c['value'] for c in context.cookies() if c['name']=='sessionid'],['new-fake'])
                finally: context.close()

    def test_logout_clears_only_douyin_data_in_current_profile(self):
        with tempfile.TemporaryDirectory() as folder,sync_playwright() as pw:
            profiles=[Path(folder)/'A',Path(folder)/'B']
            for profile in profiles:
                context=pw.chromium.launch_persistent_context(str(profile),channel='msedge',headless=True)
                try:
                    context.route('**/*',lambda route:route.fulfill(body='<html>owned login fixture</html>',content_type='text/html'))
                    page=context.new_page(); page.goto('https://www.douyin.com/')
                    page.evaluate("localStorage.setItem('owned_auth','fake'); sessionStorage.setItem('owned_auth','fake')")
                    page.evaluate("""async () => { await new Promise((resolve,reject) => {
                        const request=indexedDB.open('owned_auth',1);
                        request.onupgradeneeded=()=>request.result.createObjectStore('tokens');
                        request.onsuccess=()=>{request.result.close();resolve()}; request.onerror=reject;
                    }) }""")
                    context.add_cookies([
                        {'name':'sessionid','value':'fake-'+profile.name,'domain':'.douyin.com','path':'/','expires':2000000000},
                        {'name':'sessionid','value':'preserve','domain':'evil.douyin.com.test','path':'/','expires':2000000000},
                        {'name':'owned_other','value':'preserve','domain':'other.test','path':'/','expires':2000000000},
                    ])
                    if profile.name=='A':
                        gui.clear_douyin_login(context)
                        self.assertTrue(page.is_closed(),'old auth page must not repopulate cleared storage')
                        remaining={(cookie['domain'],cookie['name']) for cookie in context.cookies()}
                        self.assertEqual(remaining,{('evil.douyin.com.test','sessionid'),('other.test','owned_other')})
                        page=context.new_page(); page.goto('https://www.douyin.com/')
                        self.assertIsNone(page.evaluate("localStorage.getItem('owned_auth')"))
                        self.assertEqual(page.evaluate('async () => (await indexedDB.databases()).length'),0)
                        self.assertIsNone(page.evaluate("sessionStorage.getItem('owned_auth')"))
                finally: context.close()
            for profile in profiles:
                context=pw.chromium.launch_persistent_context(str(profile),channel='msedge',headless=True)
                try:
                    auth=[cookie for cookie in context.cookies() if cookie['domain']=='.douyin.com']
                    self.assertEqual(len(auth),0 if profile.name=='A' else 1)
                finally: context.close()


class LogoutUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.patch=patch.object(gui,'APP_DATA_DIR',Path(self.temp.name)); self.patch.start()
        self.window=gui.App(); self.window.withdraw()
    def tearDown(self):
        self.window.updater.close(); self.window.destroy(); self.patch.stop(); self.temp.cleanup()

    def test_cancel_and_busy_never_launch_or_clear_login(self):
        with patch.object(gui.messagebox,'askyesno',return_value=False),patch.object(gui.messagebox,'showwarning'):
            self.window.logout_current_account()
            self.window.updater._operation='downloading'
            self.window.logout_current_account()
        self.assertIsNone(self.window.login_session)
        self.assertIsNone(self.window.worker)

    def test_confirm_rechecks_busy_before_relogin(self):
        def confirm(*args,**kwargs):
            self.window.updater._operation='downloading'; return True
        with patch.object(gui.messagebox,'askyesno',side_effect=confirm),patch.object(gui.messagebox,'showwarning'):
            self.window.logout_current_account()
        self.assertIsNone(self.window.worker)

    def test_idle_logout_starts_reset_login_for_default_profile(self):
        with patch.object(gui.messagebox,'askyesno',return_value=True),patch.object(gui.threading.Thread,'start'):
            self.window.logout_current_account()
        self.assertTrue(self.window.login_session.reset_login)
        self.assertEqual(self.window.login_session.config.profile_dir,self.window.config_from_form().profile_dir)
        self.assertEqual(str(self.window.logout_button['state']),'disabled')

    def test_logout_marker_changes_only_captured_account(self):
        store=self.window.account_store
        records=tuple(replace(store.new_account(name),login_saved=True) for name in ('A','B'))
        self.window.save_accounts(records)
        self.window.mark_account_logged_out(records[0].account_id)
        self.assertEqual([a.login_saved for a in self.window.accounts],[False,True])
        self.assertEqual([a.login_saved for a in store.load()],[False,True])

    def test_corrupt_account_registry_is_not_overwritten_by_logout_marker(self):
        path=self.window.account_store.path; path.write_text('corrupt registry')
        self.window.account_store_error='账号文件损坏'
        self.window.mark_account_logged_out('legacy')
        self.assertEqual(path.read_text(),'corrupt registry')

    def test_stale_login_callbacks_cannot_change_new_login_controls(self):
        first=gui.LoginSession(self.window.config_from_form(),lambda *args:None,threading.Event(),threading.Event())
        second=gui.LoginSession(self.window.config_from_form(),lambda *args:None,threading.Event(),threading.Event())
        self.window.login_session=second; self.window.login_in_progress=True
        self.window.finish_login_button.configure(state='disabled')
        self.window.finish_login_controls(first)
        self.window.login_ready(first)
        self.assertTrue(self.window.login_in_progress)
        self.assertEqual(str(self.window.finish_login_button['state']),'disabled')

    def test_stale_saved_callback_cannot_restore_logged_out_marker(self):
        store=self.window.account_store; record=store.new_account('A')
        self.window.save_accounts((record,))
        previous=gui.LoginSession(self.window.config_from_form(),lambda *args:None,threading.Event(),threading.Event())
        current=gui.LoginSession(self.window.config_from_form(),lambda *args:None,threading.Event(),threading.Event())
        self.window.login_session=current
        self.window.finish_account_login(record.account_id,'saved',session=previous)
        self.assertFalse(self.window.accounts[0].login_saved)

    def test_idle_logout_after_multi_account_login_targets_only_last_profile(self):
        store=self.window.account_store
        records=tuple(store.new_account(name) for name in ('A','B')); self.window.save_accounts(records)
        jobs=store.jobs(tuple(replace(a,selected=True) for a in records),Path(self.window.config_from_form().profile_dir))
        self.window.login_account_id=records[0].account_id
        self.window.login_session=gui.LoginSession(replace(self.window.config_from_form(),profile_dir=jobs[0].profile_dir),lambda *args:None,threading.Event(),threading.Event())
        with patch.object(gui.messagebox,'askyesno',return_value=True),patch.object(gui.threading.Thread,'start'):
            self.window.logout_current_account()
        self.assertEqual(self.window.login_session.config.profile_dir,jobs[0].profile_dir)
        self.assertNotEqual(self.window.login_session.config.profile_dir,jobs[1].profile_dir)

    def test_live_login_logout_signals_existing_session_not_second_browser(self):
        session=gui.LoginSession(self.window.config_from_form(),lambda *args:None,self.window.stop_event,self.window.login_finish_event)
        self.window.login_session=session; self.window.login_in_progress=True
        class Alive:
            def is_alive(self): return True
        self.window.worker=Alive()
        with patch.object(gui.messagebox,'askyesno',return_value=True):
            self.window.logout_current_account()
        self.assertIs(self.window.login_session,session)
        self.assertTrue(session.logout_event.is_set())
        self.assertEqual(str(self.window.finish_login_button['state']),'disabled')

    def test_logout_button_fits_with_other_footer_controls_at_minimum_width(self):
        self.window.deiconify(); self.window.geometry('960x700'); self.window.update()
        button=self.window.logout_button; finish=self.window.finish_login_button; start=self.window.start_button
        self.assertGreaterEqual(button.winfo_rootx(),finish.winfo_rootx()+finish.winfo_width())
        self.assertLess(button.winfo_rootx()+button.winfo_width(),start.winfo_rootx())


if __name__=='__main__': unittest.main()
