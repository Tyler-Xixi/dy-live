"""Local browser tests: detail entry is not order creation or payment success."""
import threading
import time
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright
import dy_grab_gui as app


class DetailLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        # No real profile, credentials, or external service can be reached.
        self.context.route('**/*', lambda route: route.abort())
        self.page = self.context.new_page()
        self.logs = []
        self.events = []
        def record(level, text):
            self.logs.append(text)
            self.events.append((level, text))
        self.runner = app.AutomationRunner(app.AutomationConfig(
            product_name='目标玩具', product_id='1', target_price=20,
            dry_run=False, save_diagnostics=False),
            record, threading.Event())
        self.runner.config.detail_lock_mode = True
        self.runner.monitor_deadline_ms = time.time()*1000+10000

    def tearDown(self):
        self.context.close()

    def detail(self, *, sold_out=False, price=20, title='目标玩具', quantity=1, ack=True):
        stock = '商品已抢光，请选购其他商品' if sold_out else ''
        self.page.set_content(f'''<section role="dialog"><h3>{title}</h3>
            <p id="stock">{stock}</p><p>购买数量 {quantity}</p><p>订单留言</p>
            <div class="iHAKgO8B" aria-disabled="{'true' if sold_out else 'false'}"
              style="width:300px;height:50px">支付 ¥{0 if sold_out else price}.00</div>
            </section><script>window.payClicks=0;window.trusted=false;
            document.querySelector('.iHAKgO8B').onclick=e=>{{
              window.payClicks++;window.trusted=e.isTrusted;
              {'document.querySelector("section").innerHTML="<h3>待付款</h3><p>订单号：SIM123456789</p><p>商品名称：目标玩具</p><p>购买数量：1</p><p>规格：默认单一规格</p>";' if ack else ''}
            }};</script>''')

    def test_lock_mode_submits_with_auto_pay_off_and_confirms_pending_order(self):
        self.detail()
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 1)
        self.assertTrue(self.page.evaluate('trusted'))
        self.assertTrue(self.runner.state.get('order_lock_confirmed'))
        self.assertFalse(self.runner.state['payment_confirmed'])
        self.assertEqual(self.runner.state['abandon_clicks'], 0)

    def test_legacy_mode_still_stops_before_payment(self):
        self.runner.config.detail_lock_mode = False
        self.detail()
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_initial_sold_out_detail_waits_then_submits_on_restock(self):
        self.detail(sold_out=True)
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 0)
        self.page.evaluate('''() => {document.querySelector('#stock').textContent='';
            const pay=document.querySelector('.iHAKgO8B');
            pay.textContent='支付 ¥20.00';pay.setAttribute('aria-disabled','false')}''')
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 1)

    def test_qr_only_detail_never_counts_as_order_or_payment(self):
        self.page.set_content('''<section role="dialog"><h3>目标玩具</h3><p>¥20</p>
            <p>请打开抖音APP扫描二维码 购买此商品</p></section>''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertFalse(self.runner.state['order_submitted'])
        self.assertEqual(self.runner.state['payment_clicks'], 0)

    def test_opens_sold_out_product_by_title_before_sale(self):
        self.page.set_content('''<ul><li>1 <span data-e2e="promotion-title">目标玩具</span>
            ¥20 已抢光<button disabled data-e2e="shop-buyBtn">去抢购</button></li></ul>
            <script>window.opened=0;document.querySelector('span').onclick=()=>{
              window.opened++;document.body.insertAdjacentHTML('beforeend',
              '<section role="dialog"><h3>目标玩具</h3><p>¥20</p><p>请打开抖音APP扫描二维码 购买此商品</p></section>')};</script>''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('opened'), 1)
        self.assertTrue(self.page.get_by_role('dialog').is_visible())

    def test_wrong_amount_is_blocked_even_when_legacy_price_check_is_off(self):
        self.runner.config.strict_price_match = False
        self.detail(price=21)
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_wrong_detail_product_is_never_submitted(self):
        self.detail(title='其它玩具')
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_quantity_two_is_not_submitted(self):
        self.detail(quantity=2)
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_missing_ack_stops_without_retry_or_false_success(self):
        self.detail(ack=False)
        self.runner.pending_order_timeout_ms = 80
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 1)
        self.assertTrue(self.runner.state['pending_payment_review'])
        self.assertFalse(self.runner.state.get('order_lock_confirmed', False))
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 1)

    def test_dry_run_never_submits_or_claims_pending_order(self):
        self.runner.config.dry_run = True
        self.detail()
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 0)
        self.assertFalse(self.runner.state.get('order_lock_confirmed', False))

    def test_elapsed_window_prevents_submission(self):
        self.detail()
        self.runner.monitor_deadline_ms = time.time()*1000-1
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_stopped_task_prevents_submission(self):
        self.detail()
        self.runner.stop_event.set()
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_qr_detail_reopens_via_verified_back_control_not_whole_room_reload(self):
        self.page.set_content('''<ul><li>1 <span data-e2e="promotion-title">目标玩具</span>
            ¥20 已抢光<button disabled>去抢购</button></li></ul>
            <script>window.opened=0;function openDetail(){window.opened++;
              document.body.insertAdjacentHTML('beforeend','<section role="dialog"><h3>目标玩具</h3><p>¥20</p><p>请打开抖音APP扫描二维码 购买此商品</p><button aria-label="返回">返回</button></section>');
              document.querySelector('[aria-label="返回"]').onclick=()=>document.querySelector('section').remove()}
              document.querySelector('span').onclick=openDetail;openDetail();</script>''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.runner.detail_refreshed_at = time.monotonic()-3
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('opened'), 2)
        self.assertEqual(self.runner.state['payment_clicks'], 0)

    def test_old_pending_order_is_not_mistaken_for_new_submission_ack(self):
        self.detail(ack=False)
        self.page.evaluate('''() => document.body.insertAdjacentHTML('beforeend',
            '<aside><h3>待付款</h3><p>订单号：OLD123456789</p></aside>')''')
        self.runner.pending_order_timeout_ms = 80
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 1)
        self.assertFalse(self.runner.state.get('order_lock_confirmed', False))

    def test_list_restock_reopens_stale_qr_detail_without_waiting_for_timer(self):
        self.page.set_content('''<ul><li>1 <span data-e2e="promotion-title">目标玩具</span>
            ¥20 <span id="list-stock">已抢光</span><button data-e2e="shop-buyBtn" class="Yys40cl5">去抢购</button></li></ul>
            <script>window.opened=0;window.payClicks=0;window.available=false;
            function openDetail(){window.opened++;
              const content=available?'<p>购买数量 1</p><div class="iHAKgO8B" style="width:300px;height:50px">支付 ¥20.00</div>':'<p>请打开抖音APP扫描二维码 购买此商品</p>';
              document.body.insertAdjacentHTML('beforeend','<section role="dialog"><h3>目标玩具</h3>'+content+'<button aria-label="返回">返回</button></section>');
              document.querySelector('[aria-label="返回"]').onclick=()=>document.querySelector('section').remove();
              const pay=document.querySelector('.iHAKgO8B');if(pay)pay.onclick=()=>{payClicks++;document.querySelector('section').innerHTML='<h3>待付款</h3><p>订单号：NEW123456789</p><p>商品名称：目标玩具</p><p>购买数量：1</p><p>规格：默认单一规格</p>'}}
            document.querySelector('[data-e2e="promotion-title"]').onclick=openDetail;openDetail();</script>''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.page.evaluate('''() => {available=true;document.querySelector('#list-stock').textContent='';
            document.querySelector('[data-e2e="shop-buyBtn"]').className=''}''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('opened'), 2)
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 1)

    def test_known_payment_class_with_nonpayment_label_is_not_clicked(self):
        self.detail()
        self.page.locator('.iHAKgO8B').evaluate("el=>el.textContent='查看余额 ¥20.00'")
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_list_restock_reopens_stale_sold_out_detail_without_false_stop(self):
        self.detail(sold_out=True)
        self.page.evaluate('''() => {
          window.opened=1;
          document.body.insertAdjacentHTML('afterbegin','<ul><li>1 <span data-e2e="promotion-title">目标玩具</span> ¥20 <span id="list-stock">已抢光</span><button data-e2e="shop-buyBtn" disabled>去抢购</button></li></ul>');
          document.querySelector('section').insertAdjacentHTML('beforeend','<button aria-label="返回">返回</button>');
          document.querySelector('[aria-label="返回"]').onclick=()=>document.querySelector('section').remove();
          document.querySelector('[data-e2e="promotion-title"]').onclick=()=>{
            opened++;document.body.insertAdjacentHTML('beforeend','<section role="dialog"><h3>目标玩具</h3><p>购买数量 1</p><div class="iHAKgO8B" style="width:300px;height:50px">支付 ¥20.00</div></section>');
            document.querySelector('.iHAKgO8B').onclick=()=>{payClicks++;document.querySelector('section').innerHTML='<h3>待付款</h3><p>订单号：NEW123456789</p><p>商品名称：目标玩具</p><p>购买数量：1</p><p>规格：默认单一规格</p>'}}
        }''')
        self.runner.config.continue_after_sold_out = False
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.page.evaluate('''() => {document.querySelector('#list-stock').textContent='';
          document.querySelector('[data-e2e="shop-buyBtn"]').disabled=false}''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('opened'), 2)
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 1)

    def test_old_order_revealed_during_wait_is_excluded_before_submission(self):
        self.detail(sold_out=True, ack=False)
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.page.evaluate('''() => {document.querySelector('#stock').textContent='';
            const pay=document.querySelector('.iHAKgO8B');pay.textContent='支付 ¥20.00';
            pay.setAttribute('aria-disabled','false');document.body.insertAdjacentHTML('beforeend',
            '<aside><h3>待付款</h3><p>订单号：OLD123456789</p></aside>')}''')
        self.runner.pending_order_timeout_ms = 80
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 1)
        self.assertFalse(self.runner.state.get('order_lock_confirmed', False))

    def test_native_click_wait_cannot_outlive_inventory_window(self):
        self.detail()
        self.page.evaluate('''() => {document.querySelector('.iHAKgO8B').style.visibility='hidden';
            setTimeout(()=>document.querySelector('.iHAKgO8B').style.visibility='visible', 180)}''')
        action = self.page.locator('.iHAKgO8B')
        self.runner.monitor_deadline_ms = time.time()*1000+40
        with self.assertRaises(app.GracefulStop):
            self.runner.safe_click(action, 'submit payment / create order')
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_recycled_detail_identity_after_snapshot_is_never_clicked(self):
        self.detail()
        original = self.runner.detail_snapshot
        def recycle_after_read(page):
            result = original(page)
            page.locator('h3').evaluate("el=>el.textContent='其它玩具'")
            return result
        self.runner.detail_snapshot = recycle_after_read
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_verification_appearing_after_snapshot_blocks_submission(self):
        self.detail()
        original = self.runner.detail_snapshot
        def verification_after_read(page):
            result = original(page)
            page.locator('section').evaluate("el=>el.insertAdjacentHTML('beforeend','<p>请完成验证</p>')")
            return result
        self.runner.detail_snapshot = verification_after_read
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 0)
        self.assertTrue(self.runner.state.get('manual_verification_waiting'))

    def test_options_in_another_panel_cannot_verify_target_sku(self):
        self.detail()
        self.runner.config.multi_option_enabled = True
        self.runner.config.option_names = '颜色=红色'
        self.page.evaluate('''() => document.body.insertAdjacentHTML('beforeend',
          '<section role="dialog"><h3>其它玩具</h3><p>购买数量 1</p><div class="YTFcT_zp"><span class="R_G6ohly">颜色</span><div class="ufz0AqTE vZSOutR4">红色</div></div></section>')''')
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_disappearing_auxiliary_list_does_not_block_available_detail(self):
        self.detail(sold_out=True)
        self.page.evaluate('''() => document.body.insertAdjacentHTML('afterbegin',
          '<ul><li>1 目标玩具 ¥20 <button data-e2e="shop-buyBtn">去抢购</button></li></ul>')''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.page.evaluate('''() => {document.querySelector('ul').remove();
          document.querySelector('#stock').textContent='';
          const pay=document.querySelector('.iHAKgO8B');pay.textContent='支付 ¥20.00';
          pay.setAttribute('aria-disabled','false')}''')
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 1)

    def show_challenge(self):
        self.page.evaluate('''() => document.body.insertAdjacentHTML('beforeend',
          '<div id="challenge" role="dialog"><p>请完成安全验证，拖动滑块完成拼图</p><button onclick="window.challengeClicks++">关闭</button></div>')''')
        self.page.evaluate('window.challengeClicks=0')

    def test_challenge_pauses_refresh_and_logs_only_one_notice(self):
        self.detail(sold_out=True)
        self.show_challenge()
        self.runner.detail_refreshed_at = time.monotonic()-3
        deadline = self.runner.monitor_deadline_ms
        for _ in range(2):
            self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.runner.monitor_deadline_ms, deadline)
        self.assertEqual(self.runner.state['detail_refreshes'], 0)
        self.assertEqual(self.runner.state['click_attempts'], 0)
        self.assertFalse(self.page.is_closed())
        self.assertEqual(sum(level == 'warn' and '等待人工验证' in text
                             for level, text in self.events), 1)
        self.runner.monitor_reported_at = float('-inf')
        self.runner.report_monitor_status(500, None)
        self.assertIn('监控暂停', self.logs[-1])

    def test_pause_status_replaces_recent_normal_heartbeat_immediately(self):
        self.detail(sold_out=True)
        self.runner.report_monitor_status(0, None)
        self.show_challenge()
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        monitor_events = [text for level, text in self.events if level == 'monitor']
        self.assertIn('监控暂停', monitor_events[-1])

    def test_manual_completion_still_blocks_a_changed_price(self):
        self.detail()
        self.show_challenge()
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.page.evaluate('''() => {document.querySelector('#challenge').remove();
          document.querySelector('.iHAKgO8B').textContent='支付 ¥21.00'}''')
        with self.assertRaises(app.GracefulStop):
            self.runner.scan_dom_and_order(self.page)
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_challenge_before_refresh_click_prevents_navigation(self):
        self.detail(sold_out=True)
        self.page.locator('section').evaluate('''el=>el.insertAdjacentHTML('beforeend',
          '<button aria-label="返回" onclick="window.backClicks++">返回</button>')''')
        self.page.evaluate('window.backClicks=0')
        original = self.runner.detail_snapshot
        def challenge_after_read(page):
            result = original(page)
            self.show_challenge()
            return result
        self.runner.detail_snapshot = challenge_after_read
        self.runner.reopen_waiting_detail(self.page, {})
        self.assertEqual(self.page.evaluate('backClicks'), 0)
        self.assertTrue(self.runner.state.get('manual_verification_waiting'))

    def test_challenge_during_native_navigation_wait_pauses_instead_of_stopping(self):
        self.detail(sold_out=True)
        self.runner.config.click_timeout_ms = 80
        self.page.locator('section').evaluate('''el=>{
          el.insertAdjacentHTML('beforeend','<button aria-label="返回" style="pointer-events:none">返回</button>');
          setTimeout(()=>document.body.insertAdjacentHTML('beforeend',
            '<div role="dialog"><p>请完成安全验证</p></div>'),20)}''')
        self.assertFalse(self.runner.safe_click(self.page.locator('[aria-label="返回"]'),
                                               'reopen target detail back', allow_dry_run_click=True))
        self.assertTrue(self.runner.state.get('manual_verification_waiting'))
        self.assertFalse(self.runner.state['order_submitted'])
        self.assertFalse(self.page.is_closed())

    def test_first_sku_challenge_pauses_remaining_sku_until_manual_completion(self):
        self.detail()
        self.runner.config.multi_option_enabled = True
        self.runner.config.option_names = '颜色=红色\n尺寸=大'
        self.page.locator('section').evaluate('''el=>{
          window.firstClicks=0;window.secondClicks=0;
          el.insertAdjacentHTML('beforeend','<div class="YTFcT_zp"><span class="R_G6ohly">颜色</span><div id="first" class="ufz0AqTE">红色</div></div><div class="YTFcT_zp"><span class="R_G6ohly">尺寸</span><div id="second" class="ufz0AqTE">大</div></div>');
          document.querySelector('#first').onclick=e=>{firstClicks++;e.target.classList.add('vZSOutR4');document.body.insertAdjacentHTML('beforeend','<div id="challenge" role="dialog"><p>请完成安全验证</p></div>')};
          document.querySelector('#second').onclick=e=>{secondClicks++;e.target.classList.add('vZSOutR4')}
        }''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('[firstClicks,secondClicks,payClicks]'), [1,0,0])
        self.assertTrue(self.runner.state.get('manual_verification_waiting'))
        self.page.locator('#challenge').evaluate('el=>el.remove()')
        self.page.locator('.iHAKgO8B').evaluate("el=>el.onclick=()=>{payClicks++;document.querySelector('section').innerHTML='<h3>待付款</h3><p>订单号：SKU123456789</p><p>商品名称：目标玩具</p><p>购买数量：1</p><p>颜色：红色</p><p>尺寸：大</p>'}")
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('[firstClicks,secondClicks,payClicks]'), [1,1,1])

    def test_active_challenge_stops_generic_sku_click_without_retry(self):
        self.detail()
        self.show_challenge()
        self.page.locator('section').evaluate('''el=>el.insertAdjacentHTML('beforeend',
          '<div class="ufz0AqTE" onclick="window.optionClicks++">红色</div>')''')
        self.page.evaluate('window.optionClicks=0')
        self.assertFalse(self.runner.safe_click(self.page.locator('.ufz0AqTE'),
                                               'select product option: 红色'))
        self.assertEqual(self.page.evaluate('optionClicks'), 0)
        self.assertTrue(self.runner.state.get('manual_verification_waiting'))

    def test_manual_completion_resumes_and_creates_one_pending_order(self):
        self.detail()
        self.show_challenge()
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.page.locator('#challenge').evaluate('el=>el.remove()')
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 1)
        self.assertTrue(self.runner.state['order_lock_confirmed'])
        self.assertFalse(self.runner.state.get('manual_verification_waiting'))

    def test_challenge_without_product_panel_does_not_trigger_navigation(self):
        self.page.set_content('<p>直播间</p>')
        self.show_challenge()
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertTrue(self.runner.state.get('manual_verification_waiting'))
        self.assertEqual(self.page.evaluate('challengeClicks'), 0)
        self.assertEqual(self.runner.state['click_attempts'], 0)

    def test_challenge_disappearing_without_target_does_not_resume(self):
        self.detail()
        self.show_challenge()
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.page.evaluate('document.body.innerHTML="<p>页面加载中</p>"')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertTrue(self.runner.state.get('manual_verification_waiting'))
        self.assertEqual(self.runner.state['click_attempts'], 0)

    def test_expired_window_after_manual_completion_does_not_submit(self):
        self.detail()
        self.show_challenge()
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.runner.monitor_deadline_ms = time.time()*1000-1
        self.page.locator('#challenge').evaluate('el=>el.remove()')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 0)

    def test_visible_verification_iframe_pauses_even_without_reading_its_document(self):
        self.detail()
        self.page.evaluate('''() => document.body.insertAdjacentHTML('beforeend',
          '<iframe title="人机验证" src="https://blocked.invalid/captcha"></iframe>')''')
        self.assertFalse(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 0)
        self.page.locator('iframe').evaluate("el=>el.style.display='none'")
        self.assertTrue(self.runner.scan_dom_and_order(self.page))

    def test_post_submit_challenge_waits_for_manual_ack_without_second_click(self):
        self.detail(ack=False)
        self.runner.pending_order_timeout_ms = 60
        self.page.locator('.iHAKgO8B').evaluate('''el=>el.onclick=()=>{
          payClicks++;document.querySelector('section').insertAdjacentHTML('beforeend',
            '<p id="verify">请完成安全验证</p>');
          setTimeout(()=>document.querySelector('section').innerHTML=
            '<h3>待付款</h3><p>订单号：NEW123456789</p><p>商品名称：目标玩具</p><p>购买数量：1</p><p>规格：默认单一规格</p>', 180)}''')
        self.assertTrue(self.runner.scan_dom_and_order(self.page))
        self.assertEqual(self.page.evaluate('payClicks'), 1)
        self.assertTrue(self.runner.state['order_lock_confirmed'])

    def test_window_end_keeps_challenge_browser_even_with_close_on_finish(self):
        # Only process startup/network calibration are doubled; real browser
        # navigation, monitoring, retention, and final context cleanup run here.
        self.runner.config.close_browser_on_finish = True
        self.runner.config.monitor_duration_ms = 500
        self.runner.config.live_url = 'http://local-owned.test/challenge'
        self.context.route(self.runner.config.live_url, lambda route: route.fulfill(
            content_type='text/html; charset=utf-8', body='<div role="dialog"><p>请完成安全验证</p></div>'))
        retained = []
        page = self.page
        class StopAfterRetention(threading.Event):
            def wait(self, timeout=None):
                retained.append(page.is_closed())
                self.set()
                return True
        class Driver:
            def stop(self): pass
        class Starter:
            def start(self): return Driver()
        self.runner.stop_event = StopAfterRetention()
        with tempfile.TemporaryDirectory() as folder:
            self.runner.config.profile_dir = folder
            self.runner.config.diagnostics_dir = folder
            with patch('playwright.sync_api.sync_playwright', return_value=Starter()), \
                 patch.object(app, 'launch_persistent_browser', return_value=self.context), \
                 patch.object(self.runner, 'calibrate_clock', return_value=0):
                self.runner.run()
        self.assertEqual(retained, [False], '\n'.join(self.logs))
        self.assertTrue(self.page.is_closed())


if __name__ == '__main__':
    unittest.main()
