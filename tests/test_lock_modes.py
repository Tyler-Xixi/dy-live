"""All flash locking modes confirm a fresh pending order, never paid state."""
import time
from unittest.mock import patch
import unittest
import test_detail_lock as detail_fixture
import dy_grab_gui as app


class LockModesTests(unittest.TestCase):
    setUpClass=classmethod(detail_fixture.DetailLockTests.setUpClass.__func__)
    tearDownClass=classmethod(detail_fixture.DetailLockTests.tearDownClass.__func__)
    tearDown=detail_fixture.DetailLockTests.tearDown
    detail=detail_fixture.DetailLockTests.detail

    def setUp(self):
        detail_fixture.DetailLockTests.setUp(self)
        self.runner.config.detail_lock_mode=False
        self.runner.config.purchase_speed_priority=True
        self.runner.purchase_started=True
        self.runner.detail_open_started_at=time.monotonic()

    def test_normal_lock_submits_with_auto_pay_off(self):
        self.detail()
        self.assertTrue(self.runner.advance_order_flow(self.page))
        self.assertEqual(self.page.evaluate('payClicks'),1)
        self.assertTrue(self.page.evaluate('trusted'))
        self.assertTrue(self.runner.state['order_lock_confirmed'])
        self.assertEqual(self.runner.state['abandon_clicks'],0)
        self.assertFalse(self.runner.state['payment_confirmed'])

    def test_script_lock_final_submit_is_native_and_once(self):
        self.runner.config.experimental_purchase_click=True
        self.runner.config.purchase_speed_priority=False
        self.detail()
        self.assertTrue(self.runner.advance_order_flow(self.page))
        self.assertEqual(self.page.evaluate('payClicks'),1)
        self.assertTrue(self.page.evaluate('trusted'))
        self.assertTrue(self.runner.advance_order_flow(self.page))
        self.assertEqual(self.page.evaluate('payClicks'),1)

    def test_old_pending_order_is_not_confirmation(self):
        self.runner.pending_order_timeout_ms=40
        self.detail(ack=False)
        self.page.evaluate("() => {let p=document.createElement('section');p.textContent='待付款 订单号：OLD123456789';document.body.append(p)}")
        with self.assertRaises(app.GracefulStop): self.runner.advance_order_flow(self.page)
        self.assertEqual(self.page.evaluate('payClicks'),1)
        self.assertFalse(self.runner.state['order_lock_confirmed'])

    def test_result_unclear_never_submits_again(self):
        self.runner.pending_order_timeout_ms=40
        self.detail(ack=False)
        with self.assertRaises(app.GracefulStop): self.runner.advance_order_flow(self.page)
        with self.assertRaises(app.GracefulStop): self.runner.advance_order_flow(self.page)
        self.assertEqual(self.page.evaluate('payClicks'),1)

    def test_expired_window_does_not_submit(self):
        self.detail(); self.runner.monitor_deadline_ms=time.time()*1000-1
        self.assertFalse(self.runner.advance_order_flow(self.page))
        self.assertEqual(self.page.evaluate('payClicks'),0)

    def test_dry_run_never_submits(self):
        self.detail(); self.runner.config.dry_run=True
        self.assertTrue(self.runner.advance_order_flow(self.page))
        self.assertEqual(self.page.evaluate('payClicks'),0)

    def test_mode_priority_and_batch_isolation(self):
        config=app.AutomationConfig(detail_lock_mode=True,purchase_speed_priority=True,experimental_purchase_click=True)
        self.assertEqual(app.effective_flash_lock_mode(config),'detail')
        config.detail_lock_mode=False
        self.assertEqual(app.effective_flash_lock_mode(config),'script')
        config.experimental_purchase_click=False
        self.assertEqual(app.effective_flash_lock_mode(config),'normal')
        config.mode='batch'
        self.assertEqual(app.effective_flash_lock_mode(config),'none')

    def test_normal_and_script_reject_unsafe_checkout_without_submit(self):
        for script in (False,True):
            for values in ({'price':0},{'price':21},{'quantity':2},{'title':'另一个商品'}):
                with self.subTest(script=script,values=values):
                    self.runner.config.experimental_purchase_click=script
                    self.detail(**values)
                    try: self.runner.advance_order_flow(self.page)
                    except app.GracefulStop: pass
                    self.assertEqual(self.page.evaluate('payClicks'),0)
                    self.assertFalse(self.runner.state['order_lock_confirmed'])

    def test_normal_and_script_card_entry_then_native_pending_order(self):
        for script in (False,True):
            with self.subTest(script=script):
                self.runner.config.experimental_purchase_click=script
                self.runner.purchase_started=False
                self.runner.state['order_lock_confirmed']=False
                self.runner.state['order_submitted']=False
                self.runner.state['payment_clicks']=0
                self.runner.state['pending_payment_review']=False
                self.runner.detail_submit_attempted=False
                self.page.set_content('''<li>1 目标玩具 ¥20<button data-e2e="shop-buyBtn">去抢购</button></li>
                <script>window.payClicks=0;window.cardClicks=0;window.trusted=false;
                document.querySelector('button').onclick=e=>{
                  window.cardTrusted=e.isTrusted;
                  window.cardClicks++;
                  document.body.innerHTML='<section role="dialog"><h3>目标玩具</h3><p>购买数量 1</p><p>订单留言</p><div class="iHAKgO8B" style="width:300px;height:50px">支付 ¥20.00</div></section>';
                  document.querySelector('.iHAKgO8B').onclick=e=>{
                    window.payClicks++;window.trusted=e.isTrusted;
                    document.querySelector('section').innerHTML='<h3>待付款</h3><p>订单号：SIM123456789</p>';
                  };
                };</script>''')
                started=time.monotonic()
                self.assertTrue(self.runner.find_and_click_buy_action(self.page,self.page.locator('li'),'locked product buy button'))
                self.assertTrue(self.runner.advance_order_flow(self.page))
                self.assertEqual(self.page.evaluate('cardClicks'),1)
                self.assertEqual(self.page.evaluate('cardTrusted'),not script)
                self.assertEqual(self.page.evaluate('payClicks'),1)
                self.assertTrue(self.page.evaluate('trusted'))
                print(f'Local simulated {"script" if script else "normal"} card-to-pending: {(time.monotonic()-started)*1000:.1f} ms')


if __name__=='__main__': unittest.main()
