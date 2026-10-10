import threading
import time
import unittest
from playwright.sync_api import sync_playwright
import dy_grab_gui as app


class FlashLatencyTests(unittest.TestCase):
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
        self.runner = app.AutomationRunner(app.AutomationConfig(
            product_name='目标玩具', product_id='1', target_price=20,
            dry_run=False, auto_pay=True, save_diagnostics=False), lambda *_: None, threading.Event())

    def tearDown(self):
        self.page.close()

    def checkout(self):
        self.page.set_content('''<section><p>购买数量 1</p><div class="iHAKgO8B"
            style="width:300px;height:50px" aria-disabled="true">支付 ¥0.00</div></section>''')

    def test_sandbox_blocks_requests_outside_its_exact_local_origin(self):
        from tests.flash_sandbox import isolated_context
        blocked = []
        context = isolated_context(self.browser, 'http://127.0.0.1:12345', blocked)
        try:
            page = context.new_page()
            for url in ('https://example.invalid/', 'http://127.0.0.1:12346/'):
                with self.assertRaises(Exception):
                    page.goto(url)
                self.assertIn(url, blocked)
        finally:
            context.close()

    def test_duplicate_primary_buy_actions_are_not_guessed(self):
        self.page.set_content('''<li>1 目标玩具 ¥20<button data-e2e="shop-buyBtn">去抢购</button>
            <button data-e2e="shop-buyBtn">去抢购</button></li>
            <script>window.clicks=0;document.onclick=()=>window.clicks++</script>''')
        with self.assertRaises(app.GracefulStop):
            self.runner.find_and_click_buy_action(self.page, self.page.locator('li'), 'locked product buy button')
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def test_primary_buy_still_scrolls_and_uses_native_click(self):
        self.page.set_content('''<div style="height:1600px"></div><li>1 目标玩具 ¥20
            <button data-e2e="shop-buyBtn">去抢购</button></li><script>
            window.clicks=0;document.querySelector('button').onclick=e=>{if(e.isTrusted)window.clicks++};</script>''')
        self.assertTrue(self.runner.find_and_click_buy_action(self.page, self.page.locator('li'), 'locked product buy button'))
        self.assertEqual(self.page.evaluate('clicks'), 1)
        self.assertGreater(self.page.evaluate('scrollY'), 0)

    def test_covered_payment_never_clicks_through_overlay(self):
        self.checkout()
        self.page.evaluate("""() => {const pay=document.querySelector('.iHAKgO8B');pay.textContent='支付 ¥20';
            pay.setAttribute('aria-disabled','false');window.clicks=0;pay.onclick=()=>window.clicks++;
            const cover=document.createElement('div');cover.style='position:fixed;inset:0;z-index:99';document.body.append(cover)}""")
        self.runner.config.click_timeout_ms = 80
        with self.assertRaises(app.GracefulStop):
            self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('clicks'), 0)
        self.assertTrue(self.runner.state['pending_payment_review'])

    def test_covered_primary_buy_stops_without_repeating_ambiguous_click(self):
        self.page.set_content('''<li>1 目标玩具 ¥20<button data-e2e="shop-buyBtn">去抢购</button></li>
            <div style="position:fixed;inset:0;z-index:99"></div><script>
            window.clicks=0;document.querySelector('button').onclick=()=>window.clicks++;</script>''')
        self.runner.config.click_timeout_ms = 80
        with self.assertRaises(app.GracefulStop):
            self.runner.find_and_click_buy_action(self.page, self.page.locator('li'), 'locked product buy button')
        self.assertEqual(self.page.evaluate('clicks'), 0)
        self.assertTrue(self.runner.state['pending_payment_review'])

    def test_checkout_change_between_snapshot_and_wait_is_not_missed(self):
        self.checkout()
        snapshot = self.runner.flash_payment_snapshot(self.page)
        self.page.locator('.iHAKgO8B').evaluate("el=>el.textContent='支付 ¥20'")
        self.assertTrue(self.runner.wait_for_payment_update(self.page, snapshot, 100))

    def replaced_card(self, replacement):
        self.page.set_content('<ul><li id="target">1 目标玩具 ¥20<button disabled>已抢完</button></li></ul>')
        locator = self.page.locator('#target')
        self.runner.lock_waiting_product(self.page, locator)
        self.runner.monitor_deadline_ms = time.time()*1000+5000
        locator.evaluate('(el, html)=>el.outerHTML=html', replacement)
        return self.runner.watch_locked_product(self.page)

    def test_replaced_matching_card_relocks_without_full_page_rescan(self):
        self.assertFalse(self.replaced_card('<li id="target">1 目标玩具 ¥20<button>去抢购</button></li>'))
        self.assertIsNotNone(self.runner.locked_product)
        self.assertTrue(self.runner.locked_product[1].evaluate('el=>el.isConnected'))

    def test_replaced_card_with_different_identity_is_not_relocked(self):
        self.assertIsNone(self.replaced_card('<li id="target">1 别的玩具 ¥20<button>去抢购</button></li>'))
        self.assertIsNone(self.runner.locked_product)

    def test_replacement_must_still_have_a_unique_target(self):
        with self.assertRaises(app.GracefulStop):
            self.replaced_card('<li id="target">1 目标玩具 ¥20<button>去抢购</button></li>'
                               '<li>1 目标玩具 ¥20<button>去抢购</button></li>')

    def test_checkout_update_wakes_on_price_hydration(self):
        self.checkout()
        snapshot = self.runner.flash_payment_snapshot(self.page)
        self.page.evaluate("() => setTimeout(()=>document.querySelector('.iHAKgO8B').textContent='支付 ¥20',25)")
        self.assertTrue(self.runner.wait_for_payment_update(self.page, snapshot, 100))
        self.assertIn('20', self.runner.flash_payment_snapshot(self.page)['text'])

    def test_prelocked_purchase_clicks_without_post_release_product_search(self):
        self.page.set_content('''<li id="target">1 目标玩具 ¥20
            <button data-e2e="shop-buyBtn" disabled>已抢完</button></li><script>
            window.clicks=0;document.querySelector('button').onclick=e=>{
                if(e.isTrusted)window.clicks++;};</script>''')
        self.runner.lock_waiting_product(self.page, self.page.locator('#target'))
        # The real native click must work even if the old post-release search
        # would fail. Checkout is intentionally absent: this is click, not order.
        def forbidden(*args, **kwargs):
            raise AssertionError('post-release product search')
        self.runner.find_and_click_buy_action = forbidden
        self.page.locator('button').evaluate("el=>{el.disabled=false;el.textContent='去抢购'}")
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('clicks'), 1)
        self.assertTrue(self.runner.purchase_started)

    def test_prelocked_dry_run_never_dispatches_purchase(self):
        self.runner.config.dry_run = True
        self.page.set_content('''<li id="target">1 目标玩具 ¥20
            <button data-e2e="shop-buyBtn" disabled>已抢完</button></li>
            <script>window.clicks=0;document.onclick=()=>window.clicks++</script>''')
        self.runner.lock_waiting_product(self.page, self.page.locator('#target'))
        self.page.locator('button').evaluate("el=>{el.disabled=false;el.textContent='去抢购'}")
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def test_prelocked_primary_reminder_is_not_mistaken_for_ready_buy_control(self):
        self.page.set_content('''<li id="target">1 目标玩具 ¥20
            <button data-e2e="shop-buyBtn">提醒我</button><button id="buy" disabled>已抢完</button></li>
            <script>window.reminders=0;document.querySelector('[data-e2e]').onclick=()=>window.reminders++;
            window.buys=0;document.querySelector('#buy').onclick=()=>window.buys++;</script>''')
        self.runner.lock_waiting_product(self.page, self.page.locator('#target'))
        self.page.locator('#buy').evaluate("el=>{el.disabled=false;el.textContent='去抢购'}")
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('reminders'), 0)
        self.assertEqual(self.page.evaluate('buys'), 1)

    def test_prelocked_expired_window_never_dispatches_purchase(self):
        self.page.set_content('''<li id="target">1 目标玩具 ¥20
            <button data-e2e="shop-buyBtn" disabled>已抢完</button></li>
            <script>window.clicks=0;document.onclick=()=>window.clicks++</script>''')
        self.runner.lock_waiting_product(self.page, self.page.locator('#target'))
        self.runner.monitor_deadline_ms = time.time()*1000-1
        self.page.locator('button').evaluate("el=>{el.disabled=false;el.textContent='去抢购'}")
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('clicks'), 0)

    def experimental_room(self):
        self.runner.config.experimental_purchase_click = True
        self.runner.config.max_order_steps = 0
        self.page.set_content('''<li id="target">1 目标玩具 ¥20
            <button data-e2e="shop-buyBtn" disabled>已抢完</button></li>
            <script>window.events=[];document.querySelector('button').onclick=e=>events.push(e.isTrusted)</script>''')
        self.runner.lock_waiting_product(self.page, self.page.locator('#target'))
        self.page.locator('button').evaluate("el=>{el.disabled=false;el.textContent='去抢购'}")

    def test_experimental_purchase_uses_page_event_only_when_enabled(self):
        self.experimental_room()
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('events'), [False])

    def test_experimental_disabled_preserves_native_purchase(self):
        self.experimental_room()
        self.runner.config.experimental_purchase_click = False
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('events'), [True])

    def test_experimental_switch_works_independently_of_native_priority_switch(self):
        self.runner.config.purchase_speed_priority = False
        self.experimental_room()
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('events'), [False])

    def test_experimental_dry_run_does_not_click(self):
        self.experimental_room()
        self.runner.config.dry_run = True
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('events'), [])

    def test_experimental_stop_does_not_click(self):
        self.experimental_room()
        self.runner.stop_event.set()
        with self.assertRaises(app.GracefulStop):
            self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('events'), [])

    def test_experimental_expired_window_does_not_click(self):
        self.experimental_room()
        self.runner.monitor_deadline_ms = time.time()*1000-1
        self.runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('events'), [])

    def test_ignored_experimental_event_stops_without_native_retry(self):
        self.experimental_room()
        self.runner.watch_locked_product(self.page)
        self.runner.purchase_started_at = time.monotonic()-16
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('events'), [False])
        self.assertTrue(self.runner.state['pending_payment_review'])

    def test_experimental_mode_keeps_payment_native(self):
        self.runner.config.experimental_purchase_click = True
        self.checkout()
        self.page.locator('.iHAKgO8B').evaluate("""el=>{el.textContent='支付 ¥20';el.setAttribute('aria-disabled','false');
            window.events=[];el.onclick=e=>events.push(e.isTrusted)}""")
        self.runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate('events'), [True])

    def test_checkout_wait_expires_and_disconnects_its_observer(self):
        self.checkout()
        self.page.evaluate("""() => {window.activeObservers=0;const Native=MutationObserver;
            window.MutationObserver=class extends Native {observe(...a){window.activeObservers++;return super.observe(...a)}
                disconnect(){window.activeObservers--;return super.disconnect()}}} """)
        snapshot = self.runner.flash_payment_snapshot(self.page)
        # Playwright installs its own long-lived observer on the first locator.
        baseline = self.page.evaluate('activeObservers')
        self.assertFalse(self.runner.wait_for_payment_update(self.page, snapshot, 50))
        self.assertEqual(self.page.evaluate('activeObservers'), baseline)


if __name__ == '__main__':
    unittest.main()
