import threading,unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import dy_grab_gui as app
class NumberTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(channel='msedge',headless=True)
    @classmethod
    def tearDownClass(cls): cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.page=self.browser.new_page();self.page.route('**/*',lambda route:route.abort())
        self.runner=app.AutomationRunner(app.AutomationConfig(product_id='2',product_name='ALL IN',target_price=0),lambda *a:None,threading.Event())
        self.page.set_content('<li class="sLOeOa5R"><div class="hH83LEu1"><span class="s5ICtQCA">1</span></div><span data-e2e="promotion-title">ALL IN</span></li><li class="sLOeOa5R"><div class="hH83LEu1"><span class="s5ICtQCA">2</span></div><span data-e2e="promotion-title">ALL IN</span></li><section><h3>ALL IN</h3><p>购买数量 1</p><div class="iHAKgO8B" style="width:300px;height:50px">支付 ¥10</div></section>')
    def tearDown(self): self.page.close()
    def test_exact_number_and_detail_evidence(self):
        self.page.locator('[data-e2e="promotion-title"]').first.evaluate("el=>el.textContent='Other'")
        self.assertEqual(len(self.runner.numbered_cards(self.page)),1)
        self.assertEqual(self.runner.detail_snapshot(self.page)['matches'],1)
        self.page.locator('.s5ICtQCA').nth(1).evaluate("el=>el.textContent='3'")
        self.assertEqual(self.runner.detail_snapshot(self.page)['matches'],0)
    def test_duplicate_number_stops(self):
        self.page.locator('.s5ICtQCA').first.evaluate("el=>el.textContent='2'")
        with self.assertRaises(app.GracefulStop):self.runner.numbered_cards(self.page)
    def test_same_title_on_different_numbers_cannot_identify_detail(self):
        self.assertEqual(self.runner.detail_snapshot(self.page)['matches'],0)
    def test_click_checks_card_ownership(self):
        self.page.evaluate('window.clicks=0;document.querySelectorAll("[data-e2e=promotion-title]").forEach(el=>el.onclick=()=>clicks++)')
        with self.assertRaises(app.GracefulStop):self.runner.safe_click(self.page.locator('[data-e2e="promotion-title"]').first,'open target product detail')
        self.assertEqual(self.page.evaluate('clicks'),0)
    def test_list_disappearance_never_accepts_name_only(self):
        self.page.locator('li').evaluate_all('els=>els.forEach(el=>el.remove())')
        self.assertEqual(self.runner.detail_snapshot(self.page)['matches'],0)

    def test_all_modes_click_handle_and_reused_node_rejection(self):
        for mode in ('normal','script','detail'):
            self.runner.config.detail_lock_mode=mode=='detail'
            self.runner.config.purchase_speed_priority=mode=='normal'
            self.runner.config.experimental_purchase_click=mode=='script'
            action=self.page.locator('[data-e2e="promotion-title"]').nth(1).element_handle()
            self.assertTrue(self.runner.safe_click(action,'open target product detail'))
            self.page.locator('.s5ICtQCA').nth(1).evaluate("el=>el.textContent='3'")
            with self.assertRaises(app.GracefulStop):self.runner.safe_click(action,'open target product detail')
            self.page.locator('.s5ICtQCA').nth(1).evaluate("el=>el.textContent='2'")
    def test_optional_price_and_multi_spec_initial_price(self):
        self.assertTrue(app.exact_target_price_matches('支付 ¥10',self.runner.config))
        self.runner.config.target_price=999;self.runner.config.multi_option_enabled=True
        self.assertTrue(app.exact_target_price_matches('支付 ¥10',self.runner.config))

    def test_duplicate_number_with_different_name_rejects_detail(self):
        self.page.locator('.s5ICtQCA').first.evaluate("el=>el.textContent='2'")
        self.page.locator('[data-e2e="promotion-title"]').first.evaluate("el=>el.textContent='Other'")
        self.assertEqual(self.runner.detail_snapshot(self.page)['matches'],0)

    def test_number_resolution_overrides_conflicting_name(self):
        self.runner.config.product_name='Other'
        self.runner.config.target_price=999
        with patch.object(self.page,'goto'), patch.object(self.runner,'ensure_all_products_panel'), patch.object(self.runner,'save_diagnostics'):
            self.runner.resolve_number_target(self.page)
        self.assertEqual(self.runner.config.product_name,'ALL IN')
        self.assertEqual(self.runner.config.product_id,'2')
        self.assertTrue(self.runner.state['retain_browser'])
