import threading
import time
import unittest
from playwright.sync_api import sync_playwright
import dy_grab_gui as app


class FlashFastPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.logs = []
        self.runner = app.AutomationRunner(app.AutomationConfig(
            product_name='目标玩具', product_id='1', target_price=20,
            dry_run=False, auto_pay=True, save_diagnostics=False,
        ), lambda level, message: self.logs.append(message), threading.Event())

    def tearDown(self):
        self.page.close()

    def checkout(self, status='', price='20.00', disabled=False):
        self.page.set_content(f'''<style>.iHAKgO8B{{width:300px;height:50px;background:red}}</style>
            <section id="checkout"><h3>目标玩具</h3><p>购买数量 1</p><p>订单留言</p><p>{status}</p>
            <div class="iHAKgO8B" aria-disabled="{str(disabled).lower()}">支付 ¥{price}</div></section>
            <script>window.payClicks=0;document.querySelector('.iHAKgO8B').onclick=()=>window.payClicks++;</script>''')
        self.runner.purchase_started = True
        self.runner.sale_seen = True

    def test_missing_ancestors_do_not_delay_product_lock(self):
        self.page.set_content('<li id="target">1 目标玩具 ¥20<button disabled>等待开售</button></li>')
        started = time.monotonic()
        containers = self.runner.find_tight_product_containers(self.page.locator('#target'))
        self.assertTrue(containers)
        self.assertLess(time.monotonic()-started, .5)

    def test_checkout_sold_out_is_classified_before_price_mismatch(self):
        self.checkout('商品已抢光，请选购其他商品', '0.00')
        with self.assertRaises(app.GracefulStop) as failure:
            self.runner.submit_payment_then_abandon(self.page)
        self.assertIn('已抢完', str(failure.exception))
        self.assertNotIn('不一致', str(failure.exception))
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_disabled_sold_out_checkout_is_not_silently_ignored(self):
        self.checkout('商品已抢光，请选购其他商品', '0.00', True)
        with self.assertRaises(app.GracefulStop):
            self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_normal_flash_checkout_uses_no_repeated_full_page_scan(self):
        self.checkout()
        original = self.runner.safe_exact_action_buttons
        scans = []
        def observed(*args):
            scans.append(True)
            return original(*args)
        self.runner.safe_exact_action_buttons = observed
        self.assertTrue(self.runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)
        self.assertLessEqual(len(scans), 1)

    def test_background_sold_out_goods_do_not_block_target_checkout(self):
        self.checkout()
        self.page.evaluate("() => {const li=document.createElement('li');li.textContent='其他商品已抢光，请选购其他商品';document.body.prepend(li)}")
        self.assertTrue(self.runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)

    def test_zero_price_without_stock_message_still_blocks_payment(self):
        self.checkout(price='0.00')
        with self.assertRaises(app.GracefulStop):
            self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_hidden_payment_control_is_never_selected(self):
        self.checkout()
        self.page.locator('.iHAKgO8B').evaluate("el=>el.style.visibility='hidden'")
        self.assertFalse(self.runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_auto_pay_does_not_sleep_after_submission_before_confirming(self):
        self.checkout()
        started = time.monotonic()
        self.assertTrue(self.runner.submit_payment_then_abandon(self.page))
        self.assertLess(time.monotonic()-started, .25)

    def test_nonlocked_purchase_timings_are_nonnegative(self):
        self.page.set_content('<button>立即购买</button>')
        self.runner.safe_click(self.page.locator('button'), 'target product buy button')
        self.runner.record_stock_state('ready')
        self.assertGreaterEqual(self.runner.phase_times['buy_clicked'], self.runner.phase_times['sale_detected'])

    def test_price_change_before_payment_blocks_fast_click(self):
        self.checkout()
        original = self.runner.flash_payment_snapshot
        calls = []
        def change_price(page):
            state = original(page)
            calls.append(True)
            if len(calls) == 1:
                page.locator('.iHAKgO8B').evaluate("el=>el.textContent='支付 ¥21.00'")
            return state
        self.runner.flash_payment_snapshot = change_price
        with self.assertRaises(app.GracefulStop):
            self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_media_container_cannot_be_used_as_payment_control(self):
        self.checkout()
        self.page.locator('.iHAKgO8B').evaluate("el=>el.appendChild(document.createElement('img'))")
        self.assertFalse(self.runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_sold_out_continuation_restarts_only_before_submission(self):
        # Exercise the legacy no-submit monitor, not the new locking contract.
        self.runner.config.purchase_speed_priority = False
        self.runner.config.continue_after_sold_out = True
        self.runner.config.auto_pay = False
        self.runner.config.monitor_duration_ms = 1000
        real = self.page
        class LocalPage:
            visits = 0
            def goto(self, *args, **kwargs):
                self.visits += 1
                real.set_content('<p>本地模拟</p>')
            def wait_for_timeout(self, *_):
                pass
            def __getattr__(self, name):
                return getattr(real, name)
        page = LocalPage()
        self.runner.dismiss_blocking_overlays = lambda _: None
        self.runner.open_commerce_panel = lambda *_: None
        rounds = []
        def scan(_):
            rounds.append(True)
            if len(rounds) == 1:
                self.runner.stock_state = 'ready'
                self.runner.sale_seen = True
                self.runner.purchase_started = True
                raise app.CheckoutSoldOut('本轮售罄')
            return True
        self.runner.scan_dom_and_order = scan
        self.runner.run_flash_sale(page, 0)
        self.assertEqual(page.visits, 2)
        self.assertEqual(len(rounds), 2)
        self.assertFalse(self.runner.purchase_started)
        self.assertEqual(sum('未抢到' in message for message in self.logs), 1)

    def test_timing_log_does_not_claim_success_without_confirmation(self):
        self.runner.mark_phase('sale_detected')
        self.runner.mark_phase('buy_clicked')
        self.runner.log_phase_timing('未成功')
        self.assertIn('ms', self.logs[-1])
        self.assertNotIn('成功确认', self.logs[-1])

    def test_missing_primary_control_does_not_fall_back_to_other_payment(self):
        self.checkout(price='0.00')
        original = self.runner.flash_payment_snapshot
        removed = []
        def remove_primary(page):
            state = original(page)
            if not removed:
                removed.append(True)
                page.evaluate("""() => {
                    document.querySelector('.iHAKgO8B').remove();
                    const other=document.createElement('button');other.textContent='支付 ¥20.00';
                    other.style='width:300px;height:50px';other.onclick=()=>window.payClicks++;
                    document.body.append(other);
                }""")
            return state
        self.runner.flash_payment_snapshot = remove_primary
        with self.assertRaises(app.GracefulStop):
            self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_stock_failure_after_submission_never_restarts_purchase(self):
        self.runner.config.continue_after_sold_out = True
        self.runner.config.auto_pay = False
        real = self.page
        class LocalPage:
            visits = 0
            def goto(self, *args, **kwargs):
                self.visits += 1
                real.set_content('<p>本地模拟</p>')
            def wait_for_timeout(self, *_):
                pass
            def __getattr__(self, name):
                return getattr(real, name)
        page = LocalPage()
        self.runner.dismiss_blocking_overlays = lambda _: None
        self.runner.open_commerce_panel = lambda *_: None
        def scan(_):
            self.runner.state['order_submitted'] = True
            raise app.CheckoutSoldOut('库存错误不能导致重复提交')
        self.runner.scan_dom_and_order = scan
        with self.assertRaises(app.GracefulStop):
            self.runner.run_flash_sale(page, 0)
        self.assertEqual(page.visits, 1)

    def test_matching_primary_disappearance_cannot_retry_other_payment(self):
        self.checkout()
        original = self.runner.flash_payment_snapshot
        calls = []
        def remove_before_final_snapshot(page):
            calls.append(True)
            if len(calls) == 2:
                page.evaluate("""() => {
                    document.querySelector('.iHAKgO8B').remove();
                    const other=document.createElement('button');other.textContent='支付 ¥20.00';
                    other.style='width:300px;height:50px';other.onclick=()=>window.payClicks++;
                    document.body.append(other);
                }""")
            return original(page)
        self.runner.flash_payment_snapshot = remove_before_final_snapshot
        with self.assertRaises(app.GracefulStop):
            for _ in range(2):
                self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_checkout_stock_failure_without_sale_seen_respects_stop_switch(self):
        self.runner.config.continue_after_sold_out = False
        self.runner.sale_seen = False
        real = self.page
        class LocalPage:
            visits = 0
            def goto(self, *args, **kwargs):
                self.visits += 1
                real.set_content('<p>本地模拟</p>')
            def wait_for_timeout(self, *_):
                pass
            def __getattr__(self, name):
                return getattr(real, name)
        page = LocalPage()
        self.runner.dismiss_blocking_overlays = lambda _: None
        self.runner.open_commerce_panel = lambda *_: None
        rounds = []
        def scan(_):
            rounds.append(True)
            if len(rounds) == 1:
                raise app.CheckoutSoldOut('本轮售罄')
            return True
        self.runner.scan_dom_and_order = scan
        with self.assertRaises(app.GracefulStop):
            self.runner.run_flash_sale(page, 0)
        self.assertEqual(page.visits, 1)

    def test_primary_selection_survives_separate_checkout_attempts(self):
        self.checkout(disabled=True)
        self.assertFalse(self.runner.submit_payment_then_abandon(self.page))
        self.page.evaluate("""() => {
            document.querySelector('.iHAKgO8B').remove();
            const other=document.createElement('button');other.textContent='支付 ¥20.00';
            other.style='width:300px;height:50px';other.onclick=()=>window.payClicks++;
            document.body.append(other);
        }""")
        with self.assertRaises(app.GracefulStop):
            self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_outer_monitor_ignores_other_goods_stock_errors(self):
        self.runner.config.purchase_speed_priority = False
        self.checkout()
        self.page.evaluate("() => {const other=document.createElement('p');other.textContent='库存不足';document.body.prepend(other)}")
        self.runner.config.auto_pay = False
        self.runner.purchase_started_at = time.monotonic()
        real = self.page
        class LocalPage:
            visits = 0
            def goto(self, *args, **kwargs):
                self.visits += 1
            def wait_for_timeout(self, *_):
                pass
            def __getattr__(self, name):
                return getattr(real, name)
        page = LocalPage()
        self.runner.dismiss_blocking_overlays = lambda _: None
        self.runner.open_commerce_panel = lambda *_: None
        self.runner.run_flash_sale(page, 0)
        self.assertEqual(page.visits, 1)
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)


if __name__ == '__main__':
    unittest.main()
