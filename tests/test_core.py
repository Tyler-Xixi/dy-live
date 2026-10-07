from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

import dy_grab_gui as app


class MatchingTests(unittest.TestCase):
    def test_click_action_patterns_reject_product_image_card_text(self) -> None:
        self.assertIsNotNone(app.BUY_ACTION_TEXT_RE.fullmatch("去抢购"))
        self.assertIsNone(
            app.BUY_ACTION_TEXT_RE.fullmatch("商品图片 [6]手工制品 去抢购")
        )
        self.assertIsNotNone(app.ORDER_STEP_TEXT_RE.fullmatch("提交订单"))
        self.assertIsNone(app.ORDER_STEP_TEXT_RE.fullmatch("商品图片 立即购买"))
        self.assertIsNone(app.ORDER_STEP_TEXT_RE.fullmatch("立即购买"))
        self.assertIsNotNone(app.PAYMENT_ACTION_TEXT_RE.fullmatch("立即支付 ￥6"))
        self.assertIsNotNone(app.PAYMENT_ACTION_TEXT_RE.fullmatch("支付¥6.00"))
        self.assertEqual(app.PAYMENT_PRIMARY_SELECTOR, "div.iHAKgO8B")
        self.assertEqual(app.PRODUCT_IMAGE_VIEWER_SELECTOR, "div.rpiGKCVd")
        self.assertIsNone(app.PAYMENT_ACTION_TEXT_RE.fullmatch("商品图片 立即支付"))
        self.assertIsNone(app.DISMISS_TEXT_RE.search("同意"))

    def test_two_keyword_segments_can_match(self) -> None:
        config = app.AutomationConfig(
            product_name="[6]手工制品硅胶捏捏玩具，默认微瑕",
            product_id="",
            target_price=6,
        )
        text = "手工制品硅胶捏捏玩具 默认微瑕\n活动价：￥6\n立即购买"
        self.assertTrue(app.product_matches(text, config))
        self.assertTrue(app.is_likely_list_product_card_text(text, config))
        self.assertEqual(app.product_match_reason(text, config), "关键词+价格命中")

    def test_product_id_only_does_not_require_default_keywords(self) -> None:
        config = app.AutomationConfig(
            product_name="", product_id="24", target_price=799, strict_price_match=True
        )
        text = "24 商品名称\n到手价：￥799\n立即购买"
        self.assertEqual(app.keyword_tokens(config), [])
        self.assertTrue(app.is_likely_list_product_card_text(text, config))

    def test_plain_numbers_are_not_treated_as_prices(self) -> None:
        config = app.AutomationConfig(target_price=24, strict_price_match=True)
        self.assertFalse(app.exact_target_price_matches("24号商品 DDR5 6000 立即购买", config))
        self.assertTrue(app.exact_target_price_matches("活动价 ￥24 立即购买", config))

    def test_waiting_state_wins_over_generic_purchase_text(self) -> None:
        self.assertEqual(app.product_action_state("等待开售，已有10人购买"), "waiting")

    def test_hidden_price_target_card_is_recognized(self) -> None:
        config = app.AutomationConfig()
        text = "2\n[6]手工制品硅胶捏捏玩具，默认微瑕\n7天无理由退货\n查看价格"
        self.assertTrue(app.is_hidden_price_target_card_text(text, config))

    def test_checkout_context_is_detected_before_generic_clicks(self) -> None:
        text = "购买数量 1 订单留言 选填 优惠明细 订单运费 包邮 支付¥6.00"
        self.assertTrue(app.is_checkout_context_text(text))


class ConfigurationTests(unittest.TestCase):
    def test_requested_initial_values(self) -> None:
        config = app.AutomationConfig()
        self.assertEqual(config.live_url, "https://live.douyin.com/749508379274")
        self.assertEqual(config.product_name, "[6]手工制品硅胶捏捏玩具，默认微瑕")
        self.assertEqual(config.product_id, "2")
        self.assertEqual(config.target_price, 6)

    def test_python_playwright_uses_locator_first_property(self) -> None:
        source = Path(app.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".first()", source)

    def test_logged_out_csrf_cookie_is_not_authentication(self) -> None:
        self.assertFalse(
            app.has_authenticated_douyin_cookie(
                [{"name": "passport_csrf_token", "domain": ".douyin.com"}]
            )
        )
        self.assertTrue(
            app.has_authenticated_douyin_cookie(
                [{"name": "sessionid_ss", "domain": ".douyin.com"}]
            )
        )

    def test_auto_pay_is_opt_in(self) -> None:
        self.assertFalse(app.AutomationConfig().auto_pay)

    def test_invalid_schedule_is_reported(self) -> None:
        self.assertEqual(app.invalid_schedule_entries("25:00:00"), ["25:00:00"])
        self.assertEqual(app.invalid_schedule_entries("12:00:00,bad"), ["bad"])
        self.assertTrue(app.invalid_schedule_entries("1:00,2:00,3:00,4:00,5:00"))

    def test_valid_schedule_and_url(self) -> None:
        self.assertEqual(len(app.parse_schedule_windows("10:29:00,12:29:00")), 2)
        self.assertTrue(app.is_allowed_douyin_url("https://live.douyin.com/123"))
        self.assertFalse(app.is_allowed_douyin_url("http://live.douyin.com/123"))
        self.assertFalse(app.is_allowed_douyin_url("https://example.com/123"))


class PaymentFlowTests(unittest.TestCase):
    def test_auto_pay_clicks_payment_without_abandoning(self) -> None:
        class DummyLocator:
            def inner_text(self, **_kwargs):
                return "立即支付 ¥6"

            @property
            def first(self):
                return self

            def filter(self, **_kwargs):
                return self

            def is_visible(self, **_kwargs):
                return True

        class DummyPage:
            def get_by_role(self, *_args, **_kwargs):
                return DummyLocator()

            def locator(self, *_args, **_kwargs):
                return DummyLocator()

        class TestRunner(app.AutomationRunner):
            def visible_text_element_candidates(self, *_args, **_kwargs):
                return [{"text": "立即支付 ¥6"}]

            def click_first_visible(self, *_args, **_kwargs):
                return True

            def safe_exact_action_buttons(self, *_args, **_kwargs):
                return [DummyLocator()]

            def click_safe_exact_action_button(self, *_args, **_kwargs):
                return True

            def wait_ms(self, _milliseconds):
                return None

            def close_payment_layer(self, _page):
                raise AssertionError("auto pay must not close the payment layer")

        config = app.AutomationConfig(auto_pay=True, dry_run=False)
        runner = TestRunner(config, lambda *_args: None, threading.Event())
        self.assertTrue(runner.submit_payment_then_abandon(DummyPage()))
        self.assertEqual(runner.state["payment_clicks"], 1)
        self.assertTrue(runner.state["order_submitted"])

class StorageTests(unittest.TestCase):
    def test_network_log_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "network.jsonl"
            path.write_text("123456", encoding="utf-8")
            app.rotate_file_if_needed(str(path), max_bytes=5)
            self.assertFalse(path.exists())
            self.assertTrue(path.with_name("network.jsonl.1").exists())

    def test_local_license_activation_and_binding(self) -> None:
        # Regression tests use synthetic keys, never an administrator's cards.
        card = "DYL-TEST-ONLY-NOT-A-REAL-CARD"
        card_hash = hashlib.sha256(card.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "VALID_CARD_HASHES", frozenset({card_hash})):
            client = app.LocalLicenseClient()
            client.state_path = Path(directory) / "license.json"
            client.used_path = Path(directory) / "used.json"
            client.activate(card)
            self.assertTrue(client.verify_saved())
            client.machine_id = "0" * 64
            self.assertFalse(client.verify_saved())


if __name__ == "__main__":
    unittest.main()
