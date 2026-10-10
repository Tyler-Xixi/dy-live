import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import dy_grab_gui as app


class LockModeUITests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.settings=patch.object(app,'APP_DATA_DIR',Path(self.folder.name))
        self.settings.start()
        self.window=app.App(); self.window.withdraw()

    def tearDown(self):
        self.window.destroy(); self.settings.stop(); self.folder.cleanup()

    def texts(self,widget):
        result=[]
        try: result.append(str(widget.cget('text')))
        except app.tk.TclError: pass
        for child in widget.winfo_children(): result.extend(self.texts(child))
        return result

    def test_defaults_shared_spec_and_saved_preferences(self):
        self.assertEqual(self.window.title(), 'DY直播间自助工具')
        for key in ('continue_after_sold_out','detail_lock_mode'):
            self.assertTrue(self.window.vars[key].get())
        for key in ('dry_run','strict_price_match','strict_product_match','multi_option_enabled','purchase_speed_priority','experimental_purchase_click','batch_multi_account'):
            self.assertFalse(self.window.vars[key].get())
        self.assertIn('下单方式 · 两个模块共用',self.texts(self.window))
        self.assertIn('商品规格 · 两个模块共享',self.texts(self.window))
        self.window.vars['option_names'].set('颜色=红色')
        self.window.vars['multi_option_enabled'].set(True)
        self.window.save_preferences()
        self.window.vars['option_names'].set('')
        self.window.vars['multi_option_enabled'].set(False)
        self.window.restore_preferences()
        self.assertEqual(self.window.vars['option_names'].get(),'颜色=红色')
        self.assertTrue(self.window.vars['multi_option_enabled'].get())

    def test_labels_and_selected_mode(self):
        texts=self.texts(self.window)
        self.assertIn('普通锁单（提前绑定，开售后提交一次）',texts)
        self.assertIn('脚本锁单（页面事件，可能不被平台接受）',texts)
        self.assertIn('详情页锁单（推荐：提前打开，待付款后停止）',texts)
        for key in ('detail_lock_mode','experimental_purchase_click','purchase_speed_priority'):
            self.window.vars[key].set(True)
        self.assertIn('详情页锁单',self.window.mode_caption.get())

    def test_auto_payment_removed_from_both_modules(self):
        self.assertNotIn('auto_pay', self.window.vars)
        for tab in (0, 1):
            self.window.task_tabs.select(tab)
            self.assertFalse(hasattr(self.window.config_from_form(), 'auto_pay'))

    def test_old_normal_mode_requires_explicit_submit_confirmation(self):
        self.window.vars['dry_run'].set(False)
        self.window.vars['detail_lock_mode'].set(False)
        self.window.vars['purchase_speed_priority'].set(True)
        prompts=[]
        def decline(title,message,**kwargs):
            prompts.append(message); return False
        with patch.object(app.messagebox,'askyesno',side_effect=decline):
            self.window.start_task()
        self.assertIsNone(self.window.worker)
        self.assertEqual(len(prompts),1)
        self.assertIn('普通锁单',prompts[0])
        self.assertIn('点击一次',prompts[0])
        self.assertIn('扣款',prompts[0])

    def test_saved_relative_profile_is_absolute_for_future_helper(self):
        self.window.vars['profile_dir'].set('../profiles')
        self.window.save_preferences()
        values=json.loads(self.window.preferences_path.read_bytes())
        self.assertEqual(values['profile_dir'],str(app.resolve_profile_directory('../profiles')))


if __name__=='__main__': unittest.main()
