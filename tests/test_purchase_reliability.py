import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import dy_grab_gui as app


class PurchaseReliabilityTests(unittest.TestCase):
    def test_strict_product_match_rejects_wrong_number_at_same_price(self):
        config=app.AutomationConfig(product_name='目标玩具',product_id='1',target_price=20)
        self.assertFalse(app.is_likely_list_product_card_text('2 目标玩具 ¥20 去抢购',config))
        config.strict_product_match=False
        self.assertTrue(app.is_likely_list_product_card_text('2 目标玩具 ¥20 去抢购',config))

    def test_buy_timeout_that_reached_checkout_is_not_clicked_twice(self):
        class Locator:
            page=object()
            clicks=0
            def scroll_into_view_if_needed(self,**kwargs): pass
            def click(self,**kwargs):
                self.clicks+=1
                raise TimeoutError('ambiguous timeout')
        runner=app.AutomationRunner(app.AutomationConfig(dry_run=False),lambda *_:None,threading.Event())
        runner.purchase_flow_visible=lambda _:True
        action=Locator()
        self.assertTrue(runner.safe_click(action,'locked product buy button'))
        self.assertEqual(action.clicks,1)

    def test_unconfirmed_buy_timeout_stops_without_retry(self):
        class Locator:
            page=object()
            clicks=0
            def scroll_into_view_if_needed(self,**kwargs): pass
            def click(self,**kwargs):
                self.clicks+=1
                raise TimeoutError('ambiguous timeout')
        runner=app.AutomationRunner(app.AutomationConfig(dry_run=False),lambda *_:None,threading.Event())
        runner.purchase_flow_visible=lambda _:False
        runner.wait_ms=lambda _:None
        action=Locator()
        with self.assertRaises(app.GracefulStop):
            runner.safe_click(action,'target product buy button')
        self.assertEqual(action.clicks,1)

    def test_stalled_monitor_is_shown_without_destroying_events(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(app,'APP_DATA_DIR',Path(folder)):
            window=app.App();window.withdraw()
            try:
                with patch.object(app.time,'monotonic',return_value=100):
                    window.log('monitor','持续监控中 | 状态 正常')
                    window.drain_logs()
                with patch.object(app.time,'monotonic',return_value=113):
                    window.check_monitor_stall()
                self.assertIn('检测反馈超时',window.log_text.get('1.0','end'))
                self.assertEqual(window.log_text.get('1.0','end').count('持续监控中'),1)
                window.log('info','目标商品已开放购买')
                window.drain_logs()
                with patch.object(app.time,'monotonic',return_value=130):
                    window.check_monitor_stall()
                self.assertIn('目标商品已开放购买',window.log_text.get('1.0','end'))
            finally:
                window.destroy()
