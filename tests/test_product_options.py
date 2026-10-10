"""Offline browser regression tests: selecting a SKU must gate payment."""
import threading
import unittest

from playwright.sync_api import sync_playwright
import dy_grab_gui as app


class ProductOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1365, "height": 900})
        self.page.set_content('''
            <style>.ufz0AqTE {display:inline-block;padding:10px;cursor:pointer}
            .iHAKgO8B {width:300px;height:50px;background:red}</style>
            <div class="YTFcT_zp"><div class="R_G6ohly">补价</div>
              <div class="ufz0AqTE vZSOutR4">11</div>
              <div class="ufz0AqTE wlXQKgvo">12</div>
            </div>
            <div class="iHAKgO8B">支付 ¥11.00</div>
            <script>
              window.payClicks=0;
              document.querySelector('.iHAKgO8B').onclick=()=>window.payClicks++;
              document.querySelectorAll('.ufz0AqTE').forEach(el=>el.onclick=()=>{
                if(window.rejectSelection)return;
                el.closest('.YTFcT_zp').querySelectorAll('.ufz0AqTE').forEach(n=>n.classList.remove('vZSOutR4'));
                el.classList.add('vZSOutR4');
                document.querySelector('.iHAKgO8B').textContent='支付 ¥'+el.textContent+'.00';
              });
            </script>''')

    def tearDown(self):
        self.page.close()

    def runner(self, names="补价=12", price=12):
        return app.AutomationRunner(app.AutomationConfig(
            multi_option_enabled=True, option_names=names,
            target_price=price, auto_pay=True, dry_run=False,
            save_diagnostics=False, order_step_delay_ms=0,
        ), lambda *_: None, threading.Event())

    def assert_blocked(self, runner):
        with self.assertRaises(app.GracefulStop):
            runner.submit_payment_then_abandon(self.page)
        self.assertEqual(self.page.evaluate("window.payClicks"), 0)

    def test_selects_requested_sku_then_pays_at_correct_price(self):
        runner = self.runner()
        self.assertTrue(runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.locator('.vZSOutR4').inner_text(), '12')
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)

    def test_wrong_price_blocks_payment_after_selection(self):
        self.assert_blocked(self.runner(price=11))

    def test_missing_option_blocks_payment(self):
        self.assert_blocked(self.runner(names='补价=99'))

    def test_selection_not_acknowledged_blocks_payment(self):
        self.page.evaluate('window.rejectSelection=true')
        self.assert_blocked(self.runner())

    def test_disabled_option_blocks_payment(self):
        self.page.locator('.ufz0AqTE').nth(1).evaluate("el=>el.setAttribute('aria-disabled','true')")
        self.assert_blocked(self.runner())

    def test_multiple_choices_in_same_group_block_payment(self):
        self.assert_blocked(self.runner(names='补价=11 | 补价=12'))

    def test_unconfigured_second_group_blocks_payment(self):
        self.page.evaluate("""() => {
            const group=document.createElement('div'); group.className='YTFcT_zp';
            group.innerHTML='<div class="R_G6ohly">颜色</div><div class="ufz0AqTE vZSOutR4">红色</div><div class="ufz0AqTE">蓝色</div>';
            document.body.appendChild(group);
        }""")
        self.assert_blocked(self.runner())

    def add_fixed_group(self, attributes='', selected=True):
        self.page.evaluate('''args => {
            const group=document.createElement('div'); group.className='YTFcT_zp';
            group.innerHTML='<div class="R_G6ohly">网络类型</div><div class="ufz0AqTE '+
                (args.selected?'vZSOutR4':'')+'" '+args.attributes+'>全网通</div>';
            group.querySelector('.ufz0AqTE').onclick=()=>window.fixedClicks++;
            window.fixedClicks=0; document.body.appendChild(group);
        }''', {'attributes': attributes, 'selected': selected})

    def test_omitted_selected_single_option_allows_payment_without_clicking_it(self):
        self.add_fixed_group('style="pointer-events:none"')
        self.assertTrue(self.runner().submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)
        self.assertEqual(self.page.evaluate('window.fixedClicks'), 0)

    def test_omitted_single_option_not_selected_blocks_payment(self):
        self.add_fixed_group(selected=False)
        self.assert_blocked(self.runner())

    def test_omitted_selected_single_option_unavailable_blocks_payment(self):
        for attributes in ('aria-disabled="true"', 'disabled', 'class="soldout"'):
            with self.subTest(attributes=attributes):
                # Preserve the control classes while marking it unavailable.
                self.add_fixed_group()
                action=self.page.locator('.YTFcT_zp').last.locator('.ufz0AqTE')
                if attributes.startswith('class'):
                    action.evaluate("el=>el.classList.add('soldout')")
                else:
                    action.evaluate("(el, attr)=>el.setAttribute(attr, 'true')",
                                    'disabled' if attributes == 'disabled' else 'aria-disabled')
                self.assert_blocked(self.runner())
                self.page.locator('.YTFcT_zp').last.evaluate('el=>el.remove()')

    def test_omitted_multiple_options_with_only_one_available_blocks_payment(self):
        self.add_fixed_group()
        self.page.locator('.YTFcT_zp').last.evaluate('''el=>{
            const other=document.createElement('div'); other.className='ufz0AqTE soldout';
            other.textContent='其他网络'; el.appendChild(other);
        }''')
        self.assert_blocked(self.runner())

    def test_two_configured_groups_allow_payment(self):
        self.page.evaluate("""() => {
            const group=document.createElement('div'); group.className='YTFcT_zp';
            group.innerHTML='<div class="R_G6ohly">颜色</div><div class="ufz0AqTE vZSOutR4">红色</div>';
            document.body.appendChild(group);
        }""")
        self.assertTrue(self.runner(names='补价=12 | 颜色=红色').submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)

    def test_missing_spec_panel_blocks_payment(self):
        self.page.locator('.YTFcT_zp').evaluate('el=>el.remove()')
        self.assert_blocked(self.runner())

    def test_disabled_switch_preserves_original_payment_flow(self):
        runner = self.runner()
        runner.config.multi_option_enabled = False
        runner.config.target_price = 11
        self.assertTrue(runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)

    def test_ordinary_flash_rechecks_final_price(self):
        runner = self.runner()
        runner.config.multi_option_enabled = False
        self.assert_blocked(runner)

    def test_payment_is_not_clicked_twice(self):
        runner = self.runner()
        self.assertTrue(runner.submit_payment_then_abandon(self.page))
        self.assertTrue(runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)

    def test_disabled_payment_button_is_rejected(self):
        self.page.locator('.iHAKgO8B').evaluate("el=>el.setAttribute('aria-disabled','true')")
        self.assertFalse(self.runner().submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 0)

    def test_batch_quantity_one_resets_saved_quantity(self):
        self.page.evaluate("""() => {
            const input = document.createElement('input'); input.type='number'; input.value='2';
            document.body.appendChild(input);
        }""")
        runner = self.runner()
        runner.config.mode = 'batch'
        runner.config.buy_quantity = 1
        self.assertTrue(runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.locator('input').input_value(), '1')

    def test_payment_timeout_never_retries_click(self):
        class UncertainControl:
            attempts = 0
            def scroll_into_view_if_needed(self, **kwargs):
                pass
            def click(self, **kwargs):
                self.attempts += 1
                raise TimeoutError('uncertain delivery')
        runner = self.runner()
        control = UncertainControl()
        with self.assertRaises(app.GracefulStop):
            runner.safe_click(control, 'submit payment / create order')
        self.assertEqual(control.attempts, 1)
        self.assertTrue(runner.state['pending_payment_review'])

    def test_stop_event_blocks_payment(self):
        runner = self.runner()
        runner.stop_event.set()
        self.assert_blocked(runner)

    def test_asynchronous_price_update_is_awaited(self):
        self.page.evaluate("""() => {
            document.querySelectorAll('.ufz0AqTE').forEach(el=>el.onclick=()=>{
                el.closest('.YTFcT_zp').querySelectorAll('.ufz0AqTE').forEach(n=>n.classList.remove('vZSOutR4'));
                el.classList.add('vZSOutR4');
                setTimeout(()=>document.querySelector('.iHAKgO8B').textContent='支付 ¥12.00', 700);
            });
        }""")
        self.assertTrue(self.runner().submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)

    def test_one_cent_price_increase_is_rejected(self):
        config = app.AutomationConfig(target_price=6)
        self.assertFalse(app.exact_target_price_matches('支付 ¥6.01', config))

    def test_invalid_infinite_price_is_not_accepted(self):
        self.assertEqual(app.as_float('nan', 0), 0)
        self.assertEqual(app.as_float('inf', 0), 0)

    def test_product_image_overlay_cannot_intercept_payment(self):
        self.page.evaluate("""() => {
            window.imageClicks=0;
            const overlay=document.createElement('div'); overlay.className='rpiGKCVd';
            overlay.style='position:fixed;inset:0;z-index:99999;background:black';
            overlay.innerHTML='<img alt="product image">';
            overlay.onclick=()=>window.imageClicks++;
            document.body.appendChild(overlay);
        }""")
        self.assertTrue(self.runner().submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)
        self.assertEqual(self.page.evaluate('window.imageClicks'), 0)

    def test_stop_is_not_swallowed_by_candidate_search(self):
        runner = self.runner()
        runner.safe_click = lambda *args: (_ for _ in ()).throw(app.GracefulStop('stop'))
        with self.assertRaises(app.GracefulStop):
            runner.click_first_visible([self.page.locator('.iHAKgO8B')], 'test action')

    def test_payment_success_clears_pending_review(self):
        runner = self.runner()
        runner.state['pending_payment_review'] = True
        self.page.set_content('<div>支付成功</div>')
        runner.wait_for_payment_success(self.page)
        self.assertFalse(runner.state['pending_payment_review'])
        self.assertTrue(runner.state['payment_confirmed'])

    def test_flash_completion_requires_payment_confirmation(self):
        runner = self.runner()
        runner.config.purchase_speed_priority = False  # legacy automatic-payment flow
        class OfflinePage:
            def goto(self, *args, **kwargs):
                pass
            def wait_for_timeout(self, *args):
                pass
            def locator(self, *args):
                return self
            def inner_text(self, **kwargs):
                return ''
        runner.dismiss_blocking_overlays = lambda *args: None
        runner.open_commerce_panel = lambda *args: None
        runner.wait_for_schedule_window = lambda *args: app.time.time()*1000 + 1000
        runner.scan_dom_and_order = lambda *args: True
        confirmed = []
        runner.wait_for_payment_success = lambda *args: confirmed.append(True)
        # Arriving at an intermediate node is insufficient.
        with self.assertRaises(app.GracefulStop):
            runner.run_flash_sale(OfflinePage(), 0)
        self.assertEqual(confirmed, [])
        runner.state['order_submitted'] = True
        runner.run_flash_sale(OfflinePage(), 0)
        self.assertEqual(confirmed, [True])

    def locked_runner(self):
        self.page.set_content('''<li id="target">2 目标玩具 ¥6
            <button disabled>等待开售</button></li>
            <script>window.buyClicks=0;document.querySelector('button').onclick=()=>window.buyClicks++;</script>''')
        runner = app.AutomationRunner(app.AutomationConfig(
            product_name='目标玩具', product_id='2', target_price=6, dry_run=False,
            save_diagnostics=False,
        ), lambda *_: None, threading.Event())
        runner.locked_product = (self.page.locator('#target'), self.page.locator('#target').element_handle())
        runner.advance_order_flow = lambda _: True
        return runner

    def test_locked_card_reacts_to_delayed_opening_once(self):
        runner = self.locked_runner()
        self.page.evaluate("""() => setTimeout(() => {
            const b=document.querySelector('button');b.disabled=false;b.textContent='立即购买';
        }, 60)""")
        self.assertTrue(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 1)
        self.assertTrue(runner.purchase_started)
        self.assertIsNone(runner.locked_product)

    def test_locked_card_does_not_click_before_opening(self):
        runner = self.locked_runner()
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 0)

    def test_locked_observer_persists_across_detection_cycles(self):
        runner=self.locked_runner()
        self.assertFalse(runner.watch_locked_product(self.page))
        self.page.evaluate('window.originalWatcher=window.__dylaStockWatcher')
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertTrue(self.page.evaluate('!!window.originalWatcher && window.originalWatcher === window.__dylaStockWatcher'))

    def test_recycled_waiting_card_releases_lock_before_sale(self):
        runner=self.locked_runner()
        self.assertFalse(runner.watch_locked_product(self.page))
        self.page.locator('#target').evaluate("el=>el.firstChild.textContent='2 其他商品 ¥6'")
        runner.last_identity_check_at=0
        self.assertIsNone(runner.watch_locked_product(self.page))
        self.assertIsNone(runner.locked_product)
        self.assertEqual(self.page.evaluate('window.buyClicks'),0)

    def test_multiple_matching_cards_stop_before_click(self):
        runner=self.locked_runner()
        self.page.evaluate("() => {const other=document.querySelector('#target').cloneNode(true);other.id='second';document.body.append(other)}")
        with self.assertRaises(app.GracefulStop):
            runner.assert_unique_product(self.page)
        self.assertEqual(self.page.evaluate('window.buyClicks'),0)

    def test_persistent_observer_remembers_brief_sale_between_cycles(self):
        runner=self.locked_runner()
        self.assertFalse(runner.watch_locked_product(self.page))
        self.page.evaluate("""() => {
            setTimeout(()=>{const b=document.querySelector('button');b.disabled=false;b.textContent='去抢购'},10);
            setTimeout(()=>{const b=document.querySelector('button');b.disabled=true;b.textContent='已抢完'},30);
        }""")
        self.page.wait_for_timeout(90)
        with self.assertRaises(app.GracefulStop):
            runner.watch_locked_product(self.page)
        self.assertEqual(self.page.evaluate('window.buyClicks'),0)

    def test_offline_page_is_reported_without_buying(self):
        runner=self.locked_runner()
        self.page.evaluate("Object.defineProperty(navigator,'onLine',{get:()=>false})")
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertIn('离线',runner.monitor_scan_error)
        self.assertIsNone(runner.monitor_last_success)
        self.assertEqual(self.page.evaluate('window.buyClicks'),0)

    def test_initial_sold_out_card_waits_for_restock(self):
        runner = self.locked_runner()
        self.page.evaluate("""() => {
            const hint=document.createElement('div'); hint.textContent='- 已抢光 -'; hint.id='stock';
            document.querySelector('#target').appendChild(hint);
            const b=document.querySelector('button'); b.disabled=false; b.textContent='去抢购'; b.className='Yys40cl5';
        }""")
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 0)
        self.page.evaluate("""() => {
            document.querySelector('#stock').remove(); document.querySelector('button').className='';
        }""")
        self.assertTrue(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 1)

    def test_stock_lost_after_open_logs_once_and_continues(self):
        runner = self.locked_runner()
        runner.config.continue_after_sold_out=True
        logs=[]; runner.log=lambda level,msg: logs.append(msg)
        runner.record_stock_state('ready')
        self.page.locator('button').evaluate("el=>el.textContent='已抢完'")
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertEqual(sum('未抢到' in msg for msg in logs), 1)

    def test_window_end_prevents_locked_purchase(self):
        runner = self.locked_runner()
        runner.monitor_deadline_ms=0
        self.page.locator('button').evaluate("el=>{el.disabled=false;el.textContent='立即购买'}")
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 0)

    def test_disabled_buy_button_is_not_treated_as_open(self):
        runner = self.locked_runner()
        self.page.locator('button').evaluate("el=>el.textContent='立即购买'")
        self.assertFalse(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 0)

    def test_open_button_not_blocked_by_stale_countdown_text(self):
        runner = self.locked_runner()
        self.page.evaluate("""() => {
            const hint=document.createElement('span');hint.textContent='等待开售';
            document.querySelector('#target').appendChild(hint);
            setTimeout(()=>{
                const b=document.querySelector('button');b.disabled=false;b.textContent='立即购买';
                window.openedAt=performance.now();
                b.onclick=()=>{window.buyClicks++;window.clickedAt=performance.now();};
            },60);
        }""")
        self.assertTrue(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 1)
        self.assertLess(self.page.evaluate('window.clickedAt-window.openedAt'), 500)

    def test_detached_locked_card_returns_to_full_scan(self):
        runner = self.locked_runner()
        self.page.locator('#target').evaluate('el=>el.remove()')
        self.assertIsNone(runner.watch_locked_product(self.page))
        self.assertIsNone(runner.locked_product)

    def test_recycled_card_identity_is_checked_before_purchase(self):
        runner = self.locked_runner()
        self.page.evaluate("""() => {
            document.querySelector('#target').firstChild.textContent='2 其他商品 ¥6';
            const b=document.querySelector('button');b.disabled=false;b.textContent='立即购买';
        }""")
        self.assertIsNone(runner.watch_locked_product(self.page))
        self.assertEqual(self.page.evaluate('window.buyClicks'), 0)

    def test_batch_quantity_and_total_price_are_verified(self):
        self.page.evaluate("""() => {
            const n=document.createElement('input');n.type='number';n.value='1';
            n.onchange=()=>document.querySelector('.iHAKgO8B').textContent='支付 ¥'+12*Number(n.value)+'.00';
            document.body.appendChild(n);
        }""")
        runner=self.runner()
        runner.config.mode='batch'
        runner.config.buy_quantity=2
        self.assertTrue(runner.submit_payment_then_abandon(self.page))
        self.assertEqual(self.page.locator('input').input_value(), '2')
        self.assertEqual(self.page.evaluate('window.payClicks'), 1)

    def test_success_message_is_required_for_batch_completion(self):
        self.page.evaluate("""() => setTimeout(() => {
            const text=document.createElement('div');text.textContent='支付成功';document.body.appendChild(text);
        }, 80)""")
        self.runner().wait_for_payment_success(self.page)

    def batch_page(self, success=True):
        original=self.page.content()
        real=self.page
        class OfflinePage:
            visits=0
            def goto(self, *_args, **_kwargs):
                self.visits += 1
                real.set_content(original)
                if success:
                    real.evaluate("""() => {
                        document.querySelector('.iHAKgO8B').onclick=()=>{
                            const text=document.createElement('div');text.textContent='支付成功';
                            document.body.appendChild(text);
                        };
                    }""")
            def __getattr__(self, name):
                return getattr(real, name)
        return OfflinePage()

    def test_batch_runs_two_orders_with_fresh_spec_selection(self):
        page=self.batch_page()
        runner=self.runner()
        runner.config.mode='batch'
        runner.config.product_url='https://www.douyin.com/offline-test'
        runner.config.product_name=''
        runner.config.buy_times=2
        runner.config.batch_interval_ms=0
        runner.run_batch_buy(page)
        self.assertEqual(page.visits, 2)
        self.assertEqual(runner.state['batch_completed'], 2)

    def test_batch_does_not_start_next_order_when_payment_unconfirmed(self):
        page=self.batch_page(success=False)
        runner=self.runner()
        runner.config.mode='batch'
        runner.config.product_url='https://www.douyin.com/offline-test'
        runner.config.product_name=''
        runner.config.buy_times=2
        def unconfirmed(_):
            raise app.GracefulStop('payment unconfirmed')
        runner.wait_for_payment_success=unconfirmed
        with self.assertRaises(app.GracefulStop):
            runner.run_batch_buy(page)
        self.assertEqual(page.visits, 1)
        self.assertEqual(runner.state['batch_completed'], 0)


if __name__ == '__main__':
    unittest.main()
