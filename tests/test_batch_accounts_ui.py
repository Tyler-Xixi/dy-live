import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
import dy_grab_gui as app
from batch_accounts import AccountRecord, AccountStore
from batch_queue import BatchOutcome


class Worker:
    def __init__(self,*args,**kwargs): self.active=False
    def start(self): self.active=True
    def is_alive(self): return self.active


class AccountUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(app,'APP_DATA_DIR',Path(self.temp.name)); self.patch.start()
        self.window=app.App(); self.window.withdraw()

    def tearDown(self):
        self.window.destroy(); self.patch.stop(); self.temp.cleanup()

    def accounts(self):
        store=self.window.account_store
        self.window.accounts=(replace(store.new_account('相同备注'),selected=True,buy_times=2),replace(store.new_account('相同备注'),selected=True,buy_quantity=3))
        self.window.save_accounts(self.window.accounts)
        return self.window.accounts

    def batch(self):
        self.window.task_tabs.select(1)
        self.window.vars['batch_live_url'].set('https://live.douyin.com/test')
        self.window.vars['batch_product_name'].set('测试商品')

    def test_off_and_flash_do_not_change_profile(self):
        original=self.window.config_from_form().profile_dir
        self.assertFalse(self.window.vars['batch_multi_account'].get())
        self.accounts(); self.window.vars['batch_multi_account'].set(True)
        self.assertEqual(self.window.config_from_form().profile_dir,original)
        self.assertEqual(self.window.prepare_batch_jobs(self.window.config_from_form()),())

    def test_empty_selection_rejected_before_worker(self):
        self.batch(); self.window.vars['batch_multi_account'].set(True)
        with patch.object(app.messagebox,'showerror'):
            self.window.start_task()
        self.assertIsNone(self.window.worker)

    def test_configuration_and_jobs_frozen_before_worker(self):
        self.batch(); records=self.accounts(); self.window.vars['batch_multi_account'].set(True)
        configurations=[]
        class Runner:
            def __init__(inner, config,*args,**kwargs): configurations.append(config); inner.config=config
            def run(inner): return 'completed'
            def batch_outcome(inner): return BatchOutcome('tested',0,'test')
        with patch.object(app.threading,'Thread',Worker),patch.object(app.messagebox,'askyesno',return_value=True):
            self.window.start_task()
        self.window.vars['batch_product_name'].set('后来修改')
        self.window.accounts=()
        with patch.object(app,'AutomationRunner',Runner): self.window.worker_main()
        self.assertEqual([(c.product_name,c.buy_times,c.buy_quantity) for c in configurations],[('测试商品',2,1),('测试商品',1,3)])
        self.assertNotEqual(configurations[0].profile_dir,configurations[1].profile_dir)
        self.assertNotEqual(configurations[0].diagnostics_dir,configurations[1].diagnostics_dir)

    def test_busy_blocks_manager_login_and_start(self):
        record=self.accounts()[0]
        self.window.worker=Worker(); self.window.worker.start()
        with patch.object(app.messagebox,'showwarning'):
            self.window.open_account_manager(); self.window.start_account_login(record.account_id); self.window.start_task()
        self.assertIsNone(self.window.login_session)
        self.assertIsNone(self.window.account_manager)

    def test_login_marker_targets_captured_id_only_on_saved(self):
        records=self.accounts()
        self.window.finish_account_login(records[0].account_id,'cancelled')
        self.assertFalse(self.window.accounts[0].login_saved)
        self.window.finish_account_login(records[0].account_id,'unverified')
        self.assertFalse(self.window.accounts[0].login_saved)
        self.window.finish_account_login(records[0].account_id,'saved')
        self.assertTrue(self.window.accounts[0].login_saved)
        self.assertFalse(self.window.accounts[1].login_saved)

    def test_login_saved_callback_waits_for_worker_release(self):
        import time
        records=self.accounts()
        self.window.worker=Worker(); self.window.worker.start()
        self.window.finish_account_login(records[0].account_id,'saved')
        self.assertFalse(self.window.accounts[0].login_saved)
        self.window.worker.active=False
        deadline=time.monotonic()+.2
        while time.monotonic()<deadline:
            self.window.update(); time.sleep(.01)
        self.assertTrue(self.window.accounts[0].login_saved)

    def test_duplicate_names_have_distinct_rows_and_remove_keeps_profile(self):
        records=self.accounts(); self.window.open_account_manager()
        dialog=self.window.account_manager
        rows=[dialog.tree.item(a.account_id,'values') for a in records]
        self.assertNotEqual(rows[0][0],rows[1][0])
        dialog.destroy()

    def test_all_account_actions_fit_at_default_size(self):
        self.window.deiconify()
        self.accounts(); self.window.open_account_manager()
        dialog=self.window.account_manager
        dialog.update()
        buttons=[w for w in dialog.controls if isinstance(w,app.ttk.Button)]
        self.assertEqual(len(buttons),7)
        for button in buttons:
            self.assertLessEqual(button.winfo_rootx()+button.winfo_reqwidth(),dialog.winfo_rootx()+dialog.winfo_width()-8,button.cget('text'))

    def test_corrupt_registry_disables_multi_only(self):
        self.window.destroy()
        path=Path(self.temp.name)/'batch_accounts.json'; path.write_text('broken')
        self.window=app.App(); self.window.withdraw()
        self.assertTrue(self.window.account_store_error)
        self.assertEqual(path.read_text(),'broken')
        self.assertEqual(self.window.config_from_form().mode,'flash')
        self.batch(); self.window.vars['batch_multi_account'].set(True)
        with self.assertRaises(ValueError): self.window.prepare_batch_jobs(self.window.config_from_form())

    def test_save_failure_keeps_edits_and_stored_records(self):
        records=self.accounts(); self.window.open_account_manager()
        dialog=self.window.account_manager
        edited=replace(records[0],name='修改后')
        with patch.object(self.window.account_store,'save',side_effect=ValueError('磁盘失败')),patch.object(app.messagebox,'showerror'):
            self.assertFalse(dialog.persist((edited,records[1])))
        self.assertEqual(dialog.accounts[0].name,'修改后')
        self.assertEqual(self.window.accounts,records)
