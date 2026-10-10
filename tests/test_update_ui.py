import queue
import threading
import time
import unittest
from unittest.mock import Mock,patch

from update_ui import UpdateController
from update_protocol import UpdateError
import test_update_client as client_fixtures


class FakeApp:
    def __init__(self):
        self.ui_queue=queue.Queue(); self.blocked=False; self.messages=[]; self.closing=False
        self.update_status=Mock(); self.controls=[]
    def update_blocked(self, *, ignore_updater=False): return self.blocked
    def set_update_busy(self,value): self.controls.append(value)
    def update_protected_paths(self): return ()
    def on_close(self): self.closing=True


class UpdateUITests(unittest.TestCase):
    def setUp(self):
        # Fixture signs 6.1.1; its simulated installed version must not follow
        # the production version bump or the "new version" branch disappears.
        self.version_patch=patch('update_ui.APP_VERSION','6.1.0'); self.version_patch.start()
        self.fixture=client_fixtures.UpdateClientTests(); self.fixture.setUp()
        self.app=FakeApp(); self.controller=UpdateController(self.app,self.fixture.client)

    def tearDown(self):
        self.controller.close(); self.fixture.tearDown(); self.version_patch.stop()

    def pump(self):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            while not self.app.ui_queue.empty(): self.app.ui_queue.get_nowait()()
            if not self.controller.busy(): return
            time.sleep(.01)
        self.fail('update operation did not complete')

    def test_defer_makes_zero_package_requests_and_manual_check_can_reopen(self):
        with patch('update_ui.UpdateDialog') as dialog, patch('update_ui.messagebox.showinfo'):
            self.controller.check(); self.pump()
            self.assertEqual(dialog.call_count,1)
            dialog.call_args.kwargs['on_defer']()
            self.controller.check(); self.pump(); self.assertEqual(dialog.call_count,1)
            self.controller.check(manual=True); self.pump(); self.assertEqual(dialog.call_count,2)
            self.assertTrue(all(url.endswith(('latest.json','incremental.json')) for url in self.fixture.calls))

    def test_download_uses_incremental_only_after_offer_confirmation(self):
        manifest=self.fixture.client.check('6.1.0',threading.Event())
        plan=Mock(package_size=123)
        with patch('update_ui.sys.frozen',True,create=True),patch.object(self.fixture.client,'prepare_incremental',side_effect=UpdateError('baseline')) as prepare,patch('update_ui.messagebox.showerror'):
            self.controller.request_download(manifest,plan=plan); self.pump()
        self.assertEqual(prepare.call_count,1)
        self.assertFalse(self.app.closing)

    def test_portable_install_does_not_close_old_app_or_start_order(self):
        from pathlib import Path
        manifest=self.fixture.client.check('6.1.0',threading.Event())
        with patch('update_ui.sys.frozen',True,create=True),patch('update_ui.filedialog.askdirectory',return_value=str(self.fixture.root)),patch('update_ui.messagebox.askyesno',return_value=True),patch('update_ui.messagebox.showinfo'),patch.object(self.fixture.client,'prepare_new_install',return_value=self.fixture.root/'new'):
            self.controller.request_new_install(manifest); self.pump()
        self.assertFalse(self.app.closing)
        self.assertFalse(self.controller.busy())

    def test_busy_or_became_busy_after_prompt_rejects_download(self):
        manifest=self.fixture.client.check('6.1.0',threading.Event())
        for scenario in ('monitor','batch','multi-account','verification','login','network','closing'):
            with self.subTest(scenario=scenario),patch('update_ui.messagebox.showwarning'):
                self.app.blocked=True
                self.controller.request_download(manifest)
                self.assertFalse(self.controller.busy())
        self.assertTrue(all(url.endswith('latest.json') for url in self.fixture.calls))

    def test_download_blocks_new_tasks_and_cancel_releases_controls(self):
        manifest=self.fixture.client.check('6.1.0',threading.Event())
        entered=threading.Event(); finish=threading.Event()
        def prepare(*args): entered.set(); finish.wait(2); raise UpdateError('cancelled')
        with patch('update_ui.sys.frozen',True,create=True),patch('update_ui.messagebox.showerror'),patch.object(self.fixture.client,'prepare',side_effect=prepare):
            self.controller.request_download(manifest)
            self.assertTrue(entered.wait(1)); self.assertTrue(self.controller.busy())
            self.assertEqual(self.app.controls[-1],True)
            self.controller.cancel(); finish.set(); self.pump()
            self.assertEqual(self.app.controls[-1],False)

    def test_source_mode_cannot_install_and_network_failure_keeps_offline_activation(self):
        manifest=self.fixture.client.check('6.1.0',threading.Event())
        with patch('update_ui.messagebox.showinfo'):
            self.controller.request_download(manifest)
        self.assertFalse(self.controller.busy())
        with patch.object(self.fixture.client,'check',side_effect=UpdateError('network')),patch('update_ui.messagebox.showerror') as error:
            self.controller.check(); self.pump(); self.assertFalse(error.called)
            self.controller.check(manual=True); self.pump(); self.assertTrue(error.called)

    def test_real_app_entry_points_block_during_update(self):
        import tempfile
        import dy_grab_gui as gui
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder,patch.object(gui,'APP_DATA_DIR',Path(folder)):
            window=gui.App(); window.withdraw()
            try:
                window.updater._operation='downloading'
                with patch.object(gui.messagebox,'showwarning') as warning:
                    window.start_task(); window.start_network_test()
                    window.launch_login(window.config_from_form())
                self.assertEqual(warning.call_count,3)
                self.assertIsNone(window.worker); self.assertIsNone(window.network_thread)
                window.set_update_busy(True)
                self.assertEqual(str(window.start_button['state']),'disabled')
                window.updater._operation=None; window.set_update_busy(False)
                self.assertEqual(str(window.start_button['state']),'normal')
            finally: window.updater.close(); window.destroy()

    def test_real_app_rechecks_after_modal_confirmation(self):
        import tempfile
        import dy_grab_gui as gui
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder,patch.object(gui,'APP_DATA_DIR',Path(folder)):
            window=gui.App(); window.withdraw()
            try:
                window.vars['dry_run'].set(False)
                def confirm(*args,**kwargs):
                    window.updater._operation='downloading'
                    return True
                # A modal Tk dialog runs a nested event loop. Update callbacks can
                # take occupancy there; no real ordering worker may start afterward.
                with patch.object(gui.messagebox,'askyesno',side_effect=confirm),patch.object(gui.messagebox,'showwarning'),patch.object(gui.threading.Thread,'start'):
                    window.start_task()
                self.assertIsNone(window.runner)
                self.assertIsNone(window.worker)
            finally: window.updater.close(); window.destroy()

    def test_busy_at_final_handoff_keeps_old_app_and_does_not_consent(self):
        from pathlib import Path
        from types import SimpleNamespace
        from update_client import atomic_json
        from update_process import ProcessIdentity
        manifest=self.fixture.client.check('6.1.0',threading.Event())
        job=self.fixture.root/'job.json'
        ready=self.fixture.root/'ready.json'
        parent=ProcessIdentity(123,456,str(self.fixture.root/'DYLiveAssistant.exe'))
        token='a'*64
        atomic_json(ready,{'protocol':1,'token':token,'parent':vars(parent)})
        launched=SimpleNamespace(request_file=job,ready_file=ready,token=token,process=SimpleNamespace(poll=lambda:None))
        with patch('update_ui.sys.frozen',True,create=True),patch.object(self.fixture.client,'prepare',return_value=None),patch('update_ui.process_identity',return_value=parent),patch('update_ui.launch_updater',return_value=launched),patch('update_ui.messagebox.showerror'):
            self.controller.request_download(manifest)
            deadline=time.monotonic()+2
            while self.app.ui_queue.empty() and time.monotonic()<deadline: time.sleep(.01)
            self.app.blocked=True
            self.pump()
        self.assertFalse(self.app.closing)
        self.assertFalse((self.fixture.root/'consent.json').exists())


if __name__=='__main__': unittest.main()
