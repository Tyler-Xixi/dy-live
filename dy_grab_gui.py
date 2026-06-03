# -*- coding: utf-8 -*-
"""Python GUI for the live-room product monitor.

This is a Python/Tkinter port of the existing Node Playwright runner.  It keeps
the same core flow: open live room, open all products, match the target product,
click buy when ready, submit the order to the payment state, then close/abandon
payment so the order remains submitted.
"""

from __future__ import annotations

import email.utils
import json
import math
import os
import queue
import random
import re
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import messagebox, ttk


BUY_TEXT_RE = re.compile(
    r"(抢购|立即抢|马上抢|立即购买|去购买|购买|下单|加入购物车|提交订单|去结算)"
)
BUY_READY_TEXT_RE = re.compile(r"(立即购买|去抢购|抢购|立即抢|马上抢|购买|下单)")
WAITING_SALE_TEXT_RE = re.compile(
    r"(等待开售|待开售|即将开售|未开售|开售提醒|开抢提醒|预约|已预约|提醒我|距开售)"
)
RESERVATION_TEXT_RE = re.compile(r"(预约|提醒我|开售提醒|开抢提醒)")
PAYMENT_TEXT_RE = re.compile(r"(付款|支付|立即支付|确认支付|输入密码|收银台|支付方式)")
PAYMENT_SUBMIT_TEXT_RE = re.compile(
    r"(付款|支付|立即支付|确认支付|确认付款|提交支付|去支付)"
)
PAYMENT_AMOUNT_TEXT_RE = re.compile(
    r"^(付款|支付|立即支付|确认支付|确认付款|提交支付|去支付)\s*(?:¥|￥)?\s*\d+(?:\.\d{1,2})?$"
)
CLOSE_PAYMENT_TEXT_RE = re.compile(r"(关闭|取消|返回|×|✕|X)")
ABANDON_PAYMENT_TEXT_RE = re.compile(
    r"(放弃|确认放弃|放弃支付|确认离开|离开|仍要离开|确定放弃|暂不支付)"
)
COMMERCE_PANEL_TEXT_RE = re.compile(
    r"(小黄车|购物车|购物袋|商品|商品列表|全部商品|讲解商品|正在讲解|橱窗|去看看)"
)
ALL_PRODUCTS_TEXT_RE = re.compile(r"^\s*全部商品\s*$")
ORDER_STEP_TEXT_RE = re.compile(
    r"(确定|确认|选好了|完成|下一步|提交订单|提交|去结算|立即购买|下单)"
)
DISMISS_TEXT_RE = re.compile(
    r"(我知道了|知道了|同意|允许|稍后再说|以后再说|关闭|继续看播|继续观看|继续看直播)"
)
UNAVAILABLE_TEXT_RE = re.compile(
    r"(售罄|已售罄|抢光|已抢光|抢完|已抢完|缺货|补货中|已结束|已下架|不可购买|卖光)"
)


DEFAULT_LIVE_URL = "https://live.douyin.com/952520575686?from_search=true"
DEFAULT_PRODUCT_NAME = "16G 6000 C40 ddr5"


class GracefulStop(Exception):
    pass


@dataclass
class SchedulePoint:
    label: str
    hour: int
    minute: int
    second: int


@dataclass
class AutomationConfig:
    mode: str = "flash"
    live_url: str = DEFAULT_LIVE_URL
    product_url: str = ""
    product_name: str = DEFAULT_PRODUCT_NAME
    product_id: str = "24"
    target_price: float = 799.0
    dry_run: bool = False
    headless: bool = False
    poll_ms: int = 35
    jitter_ms: int = 0
    monitor_duration_ms: int = 30 * 60 * 1000
    prewarm_ms: int = 1000
    strict_price_match: bool = True
    allow_reservation_click: bool = False
    open_panel_interval_ms: int = 180
    max_order_steps: int = 8
    post_buy_delay_ms: int = 80
    order_step_delay_ms: int = 80
    click_timeout_ms: int = 450
    submit_payment_and_abandon: bool = True
    save_diagnostics: bool = True
    max_retries: int = 5
    retry_base_ms: int = 80
    circuit_breaker_429: int = 5
    buy_quantity: int = 1
    buy_times: int = 1
    schedule_windows_raw: str = ""
    profile_dir: str = field(
        default_factory=lambda: str(Path("live-room-profile").resolve())
    )
    network_log_path: str = field(
        default_factory=lambda: str(Path("live_room_network_hits.jsonl").resolve())
    )
    diagnostics_dir: str = field(default_factory=lambda: str(Path("diagnostics").resolve()))
    close_browser_on_finish: bool = False

    def schedule_windows(self) -> list[SchedulePoint]:
        return parse_schedule_windows(self.schedule_windows_raw)


def now_text() -> str:
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")


def compact_text(value: object, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def escape_regex(value: str) -> str:
    return re.escape(str(value))


def as_int(value: object, fallback: int, minimum: Optional[int] = None) -> int:
    try:
        number = int(float(str(value).strip()))
    except Exception:
        return fallback
    if minimum is not None and number < minimum:
        return minimum
    return number


def as_float(value: object, fallback: float, minimum: Optional[float] = None) -> float:
    try:
        number = float(str(value).strip())
    except Exception:
        return fallback
    if minimum is not None and number < minimum:
        return minimum
    return number


def keyword_tokens(config: AutomationConfig) -> list[str]:
    parts = re.split(r"[\s,，;；/()（）【】\[\]\-_/]+", config.product_name or "")
    values: list[str] = []
    for raw in parts:
        value = raw.strip()
        if len(value) < 2:
            continue
        if re.fullmatch(r"\d+", value):
            continue
        values.append(value)
    if values:
        return list(dict.fromkeys(values))
    return ["ddr5", "6000", "C40"]


def product_matches(text: str, config: AutomationConfig) -> bool:
    haystack = normalize_text(text)
    exact_name = normalize_text(config.product_name)
    if exact_name and exact_name in haystack:
        return True

    tokens = [normalize_text(token) for token in keyword_tokens(config)]
    hit_count = sum(1 for token in tokens if token and token in haystack)
    required_hits = min(max(3, math.ceil(len(tokens) * 0.75)), 5)
    return bool(tokens) and hit_count >= required_hits


def product_list_index_matches(text: str, config: AutomationConfig) -> bool:
    product_id = str(config.product_id or "").strip()
    if not product_id:
        return True
    if not product_id.isdigit():
        return normalize_text(product_id) in normalize_text(text)

    value = product_id.lstrip("0") or "0"
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    first_text = compact_text(" ".join(lines[:3]), 180)
    return re.search(rf"^{escape_regex(value)}(?:\s|【|\[)", first_text) is not None


def exact_target_price_matches(text: str, config: AutomationConfig) -> bool:
    target_price = float(config.target_price or 0)
    if target_price <= 0:
        return False
    prices = [
        float(match.group(1))
        for match in re.finditer(r"(?:¥|￥|RMB|CNY)?\s*(\d+(?:\.\d{1,2})?)", str(text or ""), re.I)
    ]
    return any(abs(price - target_price) < 0.01 for price in prices)


def price_matches(text: str, config: AutomationConfig) -> bool:
    if not config.strict_price_match:
        return True
    return exact_target_price_matches(text, config)


def product_unavailable(text: str) -> bool:
    return UNAVAILABLE_TEXT_RE.search(str(text or "")) is not None


def product_action_state(text: str) -> str:
    value = str(text or "")
    if product_unavailable(value):
        return "unavailable"
    if BUY_READY_TEXT_RE.search(value):
        return "ready"
    if WAITING_SALE_TEXT_RE.search(value):
        return "waiting"
    return "unknown"


def is_likely_list_product_card_text(text: str, config: AutomationConfig) -> bool:
    value = compact_text(text, 900)
    if not value or len(value) > 820:
        return False
    if re.search(r"(商品详情|产品参数|订单留言|优惠明细|购买数量|请打开抖音APP扫描二维码)", value):
        return False
    if not product_matches(value, config):
        return False

    index_matched = product_list_index_matches(value, config)
    keyword_price_fallback = (not index_matched) and exact_target_price_matches(value, config)
    if not index_matched and not keyword_price_fallback:
        return False

    return product_action_state(value) != "unknown" and price_matches(value, config)


def product_match_reason(text: str, config: AutomationConfig) -> str:
    if product_list_index_matches(text, config):
        return "编号命中"
    if product_matches(text, config) and exact_target_price_matches(text, config):
        return "关键词+价格命中"
    return "未知"


def parse_schedule_windows(value: str) -> list[SchedulePoint]:
    result: list[SchedulePoint] = []
    for item in re.split(r"[,，\n;；|]+", value or ""):
        raw = item.strip()
        if not raw:
            continue
        match = re.match(r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$", raw)
        if not match:
            continue
        hour = int(match.group(1))
        minute = int(match.group(2))
        second = int(match.group(3) or 0)
        if hour > 23 or minute > 59 or second > 59:
            continue
        result.append(
            SchedulePoint(
                label=f"{hour:02d}:{minute:02d}:{second:02d}",
                hour=hour,
                minute=minute,
                second=second,
            )
        )
    return result[:4]


def randomized_delay_ms(config: AutomationConfig) -> int:
    jitter = random.randint(0, max(0, int(config.jitter_ms)))
    return max(20, int(config.poll_ms) + jitter)


class AutomationRunner:
    def __init__(
        self,
        config: AutomationConfig,
        log: Callable[[str, str], None],
        stop_event: threading.Event,
    ) -> None:
        self.config = config
        self.log = log
        self.stop_event = stop_event
        self.playwright = None
        self.context = None
        self.page = None
        self.state = {
            "scans": 0,
            "candidate_nodes": 0,
            "matched_products": 0,
            "unavailable_matches": 0,
            "click_attempts": 0,
            "successful_clicks": 0,
            "payment_clicks": 0,
            "abandon_clicks": 0,
            "order_submitted": False,
            "last_match_text": "",
            "last_click_label": "",
            "last_panel_open_at": 0.0,
            "last_diagnostic_at": 0.0,
        }

    def assert_running(self) -> None:
        if self.stop_event.is_set():
            raise GracefulStop("任务已由用户停止")

    def wait_ms(self, milliseconds: int) -> None:
        end = time.monotonic() + max(0, milliseconds) / 1000
        while time.monotonic() < end:
            self.assert_running()
            time.sleep(min(0.03, max(0, end - time.monotonic())))

    def run(self) -> str:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: F401
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self.log("error", f"Python Playwright 未安装：{exc}")
            self.log("info", "请运行：python -m pip install -r requirements-python.txt")
            self.log("info", "然后运行：python -m playwright install chromium")
            raise

        self.log("info", f"运行模式：{'批量购买' if self.config.mode == 'batch' else '抢商品监控'}")
        self.log("info", f"DRY_RUN：{'开启' if self.config.dry_run else '关闭'}")
        self.log("info", f"商品关键词：{self.config.product_name or '(未填写)'}")
        if self.config.product_id:
            self.log("info", f"商品编号：{self.config.product_id}")
        if self.config.target_price > 0:
            mode = "严格匹配" if self.config.strict_price_match else "仅作参考"
            self.log("info", f"目标价格：{self.config.target_price:g}（{mode}）")

        try:
            Path(self.config.profile_dir).mkdir(parents=True, exist_ok=True)
            Path(self.config.diagnostics_dir).mkdir(parents=True, exist_ok=True)

            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                self.config.profile_dir,
                headless=self.config.headless,
                viewport={"width": 1365, "height": 900},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=["--no-sandbox"],
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(2000)
            self.capture_network_evidence(self.page)

            clock_offset_ms = 0
            if self.config.mode == "flash":
                clock_offset_ms = self.calibrate_clock()
                self.run_flash_sale(self.page, clock_offset_ms)
            else:
                self.run_batch_buy(self.page)
            return "completed"
        except GracefulStop as exc:
            self.log("warn", str(exc))
            return "stopped"
        except Exception:
            self.log("error", "任务失败：\n" + traceback.format_exc())
            raise
        finally:
            if self.context and self.config.close_browser_on_finish:
                try:
                    self.context.close()
                    self.log("info", "浏览器上下文已关闭")
                except Exception:
                    pass
                try:
                    if self.playwright:
                        self.playwright.stop()
                except Exception:
                    pass
            elif self.context:
                self.log("info", "任务结束，浏览器按配置保留打开")

    def calibrate_clock(self) -> int:
        started = time.time() * 1000
        try:
            response = self.context.request.get(
                self.config.live_url, timeout=15000, max_redirects=2, fail_on_status_code=False
            )
            ended = time.time() * 1000
            date_header = response.headers.get("date")
            if not date_header:
                self.log("warn", "服务器响应未包含 Date 头，本次使用本地时间")
                return 0
            server_dt = email.utils.parsedate_to_datetime(date_header)
            server_ms = server_dt.timestamp() * 1000
            midpoint_ms = (started + ended) / 2
            offset = int(server_ms - midpoint_ms)
            rtt = int(ended - started)
            if abs(offset) > 50:
                self.log("info", f"时间校准完成：offset={offset}ms, rtt={rtt}ms，按偏差补偿")
            else:
                self.log("info", f"时间校准完成：offset={offset}ms, rtt={rtt}ms")
            return offset
        except Exception as exc:
            self.log("warn", f"时间校准失败，使用本地时间：{exc}")
            return 0

    def capture_network_evidence(self, page) -> None:
        def on_response(response) -> None:
            try:
                url = response.url
                if not re.search(r"product|goods|sku|shop|cart|order|live|commerce|ecom", url, re.I):
                    return
                body = ""
                try:
                    body = response.text()
                except Exception:
                    body = ""
                if not (
                    product_matches(body, self.config)
                    or price_matches(body, self.config)
                    or "/promotions/" in url
                    or "/order/" in url
                ):
                    return
                entry = {
                    "time": now_text(),
                    "status": response.status,
                    "url": url,
                    "body": body[:6000],
                }
                with open(self.config.network_log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self.log("info", f"记录相关响应：{response.status} {url}")
            except Exception:
                pass

        page.on("response", on_response)

    def retry_action(self, label: str, action: Callable[[], object]) -> object:
        last_error: Optional[Exception] = None
        for attempt in range(1, max(1, self.config.max_retries) + 1):
            self.assert_running()
            try:
                return action()
            except Exception as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self.log("warn", f"{label} 第 {attempt}/{self.config.max_retries} 次失败：{exc}")
                self.wait_ms(self.config.retry_base_ms * attempt)
        raise last_error or RuntimeError(label)

    def safe_click(self, locator, label: str) -> bool:
        self.state["click_attempts"] += 1
        if self.config.dry_run:
            self.log("info", f"DRY_RUN 命中动作：{label}")
            self.state["last_click_label"] = label
            return True

        def do_click() -> bool:
            try:
                locator.scroll_into_view_if_needed(timeout=min(300, self.config.click_timeout_ms))
            except Exception:
                pass
            locator.click(timeout=self.config.click_timeout_ms)
            return True

        self.retry_action(label, do_click)
        self.log("info", f"已点击：{label}")
        self.state["successful_clicks"] += 1
        self.state["last_click_label"] = label
        return True

    def click_first_visible(self, candidates: list, label: str) -> bool:
        for candidate in candidates:
            self.assert_running()
            try:
                if candidate.count() == 0:
                    continue
                action = candidate.first()
                if not action.is_visible(timeout=120):
                    continue
                try:
                    if not action.is_enabled(timeout=120):
                        continue
                except Exception:
                    pass
                return self.safe_click(action, label)
            except Exception:
                continue
        return False

    def visible_text_element_candidates(self, page, pattern: re.Pattern) -> list[dict]:
        viewport = page.viewport_size or {"width": 1365, "height": 900}
        handles = page.locator("button, a, [role=button], div, span").filter(has_text=pattern).element_handles()
        candidates: list[dict] = []
        for handle in handles:
            self.assert_running()
            try:
                box = handle.bounding_box()
                if not box or box["width"] <= 8 or box["height"] <= 8:
                    continue
                if box["x"] + box["width"] < 0 or box["y"] + box["height"] < 0:
                    continue
                if box["x"] > viewport["width"] or box["y"] > viewport["height"]:
                    continue
                text = compact_text(
                    handle.evaluate("(node) => node.innerText || node.textContent || ''")
                )
                if len(text) > 120:
                    continue
                if not pattern.search(text):
                    continue
                candidates.append(
                    {
                        "handle": handle,
                        "box": box,
                        "text": text,
                        "area": box["width"] * box["height"],
                    }
                )
            except Exception:
                continue
        candidates.sort(key=lambda item: (item["area"], -item["box"]["y"], -item["box"]["x"]))
        return candidates

    def click_visible_text_element(self, page, pattern: re.Pattern, label: str) -> bool:
        for candidate in self.visible_text_element_candidates(page, pattern):
            self.assert_running()
            try:
                text = candidate["text"]
                box = candidate["box"]
                if self.config.dry_run:
                    self.log("info", f"DRY_RUN 命中动作：{label} ({text})")
                    return True
                try:
                    candidate["handle"].click(timeout=self.config.click_timeout_ms)
                except Exception:
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                self.state["click_attempts"] += 1
                self.state["successful_clicks"] += 1
                self.state["last_click_label"] = label
                self.log("info", f"已点击：{label} ({text})")
                return True
            except Exception:
                continue
        return False

    def click_top_right_icon(self, page, selectors: list[str], label: str) -> bool:
        viewport = page.viewport_size or {"width": 1365, "height": 900}
        handles = page.locator(", ".join(selectors)).element_handles()
        candidates: list[dict] = []
        for handle in handles:
            self.assert_running()
            try:
                box = handle.bounding_box()
                if not box or box["width"] < 10 or box["height"] < 10:
                    continue
                if box["width"] > 80 or box["height"] > 80:
                    continue
                if box["x"] < viewport["width"] * 0.45 or box["y"] > viewport["height"] * 0.35:
                    continue
                candidates.append({"handle": handle, "box": box, "score": box["y"] * 2 - box["x"]})
            except Exception:
                continue
        candidates.sort(key=lambda item: item["score"])
        for candidate in candidates:
            self.assert_running()
            try:
                box = candidate["box"]
                if self.config.dry_run:
                    self.log("info", f"DRY_RUN 命中动作：{label}")
                    return True
                try:
                    candidate["handle"].click(timeout=self.config.click_timeout_ms)
                except Exception:
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                self.state["click_attempts"] += 1
                self.state["successful_clicks"] += 1
                self.state["last_click_label"] = label
                self.log("info", f"已点击：{label}")
                return True
            except Exception:
                continue
        return False

    def dismiss_blocking_overlays(self, page) -> None:
        candidates = [
            page.get_by_role("button", name=DISMISS_TEXT_RE),
            page.get_by_text(DISMISS_TEXT_RE),
            page.locator("button, [role=button], a").filter(has_text=DISMISS_TEXT_RE),
        ]
        self.click_first_visible(candidates, "dismiss overlay")

    def has_visible_all_products_entry(self, page) -> bool:
        entry = page.locator("button, a, [role=button], div, span").filter(
            has_text=ALL_PRODUCTS_TEXT_RE
        ).first()
        try:
            return entry.is_visible(timeout=120)
        except Exception:
            return False

    def has_visible_product_list(self, page) -> bool:
        candidates = [
            page.locator('[data-e2e="promotion-title"], [data-e2e="shop-buyBtn"], [data-e2e="price-Area"]'),
            page.locator('[role="dialog"] li, [role="dialog"] [class*="product" i], [role="dialog"] [class*="goods" i]'),
        ]
        for candidate in candidates:
            try:
                if candidate.count() == 0:
                    continue
                if candidate.first().is_visible(timeout=120):
                    return True
            except Exception:
                continue
        return False

    def open_commerce_panel(self, page, force: bool = False) -> bool:
        now_ms = time.time() * 1000
        if not force and now_ms - self.state["last_panel_open_at"] < self.config.open_panel_interval_ms:
            return False
        self.state["last_panel_open_at"] = now_ms

        if self.click_visible_text_element(page, ALL_PRODUCTS_TEXT_RE, "open all products tab"):
            self.wait_ms(self.config.order_step_delay_ms)
            return True

        if self.click_visible_text_element(page, COMMERCE_PANEL_TEXT_RE, "open live commerce panel"):
            self.wait_ms(self.config.order_step_delay_ms)
            return True

        candidates = [
            page.get_by_role("button", name=ALL_PRODUCTS_TEXT_RE),
            page.get_by_role("link", name=ALL_PRODUCTS_TEXT_RE),
            page.locator("button, a, [role=button], div, span").filter(has_text=ALL_PRODUCTS_TEXT_RE),
            page.get_by_role("button", name=COMMERCE_PANEL_TEXT_RE),
            page.get_by_role("link", name=COMMERCE_PANEL_TEXT_RE),
            page.locator("button, a, [role=button], [aria-label], [title]").filter(
                has_text=COMMERCE_PANEL_TEXT_RE
            ),
            page.locator('[aria-label*="购物"], [aria-label*="商品"], [title*="购物"], [title*="商品"]'),
        ]
        opened = self.click_first_visible(candidates, "open live commerce panel")
        if opened:
            self.wait_ms(self.config.order_step_delay_ms)
        return opened

    def ensure_all_products_panel(self, page) -> bool:
        self.dismiss_blocking_overlays(page)
        if self.has_visible_product_list(page):
            return True
        if self.has_visible_all_products_entry(page):
            return self.open_commerce_panel(page, True)
        return self.open_commerce_panel(page)

    def scroll_likely_product_lists(self, page) -> bool:
        self.assert_running()
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                      const nodes = [
                        ...document.querySelectorAll('[role="dialog"], [role="list"], [class*="list" i], [class*="scroll" i], [class*="product" i], [class*="goods" i], [class*="sku" i]'),
                        document.scrollingElement,
                        document.body
                      ].filter(Boolean);
                      let scrolled = 0;
                      for (const node of nodes) {
                        if (!(node instanceof HTMLElement)) continue;
                        if (node.scrollHeight <= node.clientHeight + 20) continue;
                        const before = node.scrollTop;
                        node.scrollTop = Math.min(node.scrollHeight, node.scrollTop + Math.max(260, node.clientHeight * 0.85));
                        if (node.scrollTop !== before) scrolled += 1;
                      }
                      window.scrollBy(0, Math.max(260, window.innerHeight * 0.45));
                      return scrolled > 0;
                    }
                    """
                )
            )
        except Exception:
            return False

    def find_and_click_buy_action(self, page, container, label: str = "buy/order button") -> bool:
        actions = [
            container.get_by_role("button", name=BUY_TEXT_RE).first(),
            container.get_by_role("link", name=BUY_TEXT_RE).first(),
            container.locator("button, a, [role=button], [class*=button], [class*=btn]").filter(
                has_text=BUY_TEXT_RE
            ).first(),
            container.locator('[data-e2e*="buy" i], [data-e2e*="cart" i], [data-e2e*="order" i]').first(),
        ]
        if self.click_first_visible(actions, label):
            return True

        if self.config.allow_reservation_click:
            reservation_actions = [
                container.get_by_role("button", name=RESERVATION_TEXT_RE),
                container.get_by_role("link", name=RESERVATION_TEXT_RE),
                container.locator("button, a, [role=button]").filter(has_text=RESERVATION_TEXT_RE),
            ]
            if self.click_first_visible(reservation_actions, "reservation/reminder button"):
                return True

        return False

    def find_tight_product_containers(self, node) -> list[dict]:
        candidates = [
            node,
            node.locator("xpath=ancestor-or-self::*[self::div or self::li or self::section][1]"),
            node.locator("xpath=ancestor::*[self::div or self::li or self::section][2]"),
            node.locator("xpath=ancestor::*[self::div or self::li or self::section][3]"),
            node.locator("xpath=ancestor::*[self::div or self::li or self::section][4]"),
        ]
        matched: list[dict] = []
        seen: set[str] = set()
        for candidate in candidates:
            self.assert_running()
            try:
                text = compact_text(candidate.inner_text(timeout=350), 700)
            except Exception:
                continue
            if not is_likely_list_product_card_text(text, self.config):
                continue
            normalized = normalize_text(text)
            if normalized in seen:
                continue
            seen.add(normalized)
            matched.append({"locator": candidate, "text": text})
        matched.sort(key=lambda item: len(item["text"]))
        return matched[:3]

    def close_payment_layer(self, page) -> bool:
        candidates = [
            page.get_by_role("button", name=CLOSE_PAYMENT_TEXT_RE),
            page.get_by_role("link", name=CLOSE_PAYMENT_TEXT_RE),
            page.locator(
                "button[aria-label*='关闭'], button[title*='关闭'], "
                "[role=button][aria-label*='关闭'], [role=button][title*='关闭'], "
                "button, [role=button]"
            ).filter(has_text=CLOSE_PAYMENT_TEXT_RE),
        ]
        if self.click_first_visible(candidates, "close payment layer"):
            self.wait_ms(self.config.order_step_delay_ms)
            return True

        if self.click_top_right_icon(
            page,
            [
                'svg[class*="close" i]',
                'svg[class*="qk3" i]',
                '[class*="close" i] svg',
                '[class*="Close" i] svg',
                '[role=dialog] svg',
            ],
            "close payment layer icon",
        ):
            self.wait_ms(self.config.order_step_delay_ms)
            return True

        if not self.config.dry_run:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            self.wait_ms(self.config.order_step_delay_ms)
        return False

    def confirm_abandon_payment(self, page) -> bool:
        candidates = [
            page.get_by_role("button", name=ABANDON_PAYMENT_TEXT_RE),
            page.get_by_role("link", name=ABANDON_PAYMENT_TEXT_RE),
            page.locator("button, a, [role=button], [class*=button], [class*=btn]").filter(
                has_text=ABANDON_PAYMENT_TEXT_RE
            ),
            page.get_by_text(ABANDON_PAYMENT_TEXT_RE),
        ]
        if self.click_first_visible(candidates, "confirm abandon payment"):
            self.state["abandon_clicks"] += 1
            self.wait_ms(self.config.order_step_delay_ms)
            return True
        return False

    def submit_payment_then_abandon(self, page) -> bool:
        payment_button_visible = False
        try:
            payment_button_visible = bool(self.visible_text_element_candidates(page, PAYMENT_AMOUNT_TEXT_RE))
        except Exception:
            payment_button_visible = False
        if not payment_button_visible:
            try:
                payment_button_visible = page.locator(
                    "button, a, [role=button], [class*=button], [class*=btn]"
                ).filter(has_text=PAYMENT_SUBMIT_TEXT_RE).first().is_visible(timeout=160)
            except Exception:
                payment_button_visible = False
        if not payment_button_visible:
            return False

        if not self.config.submit_payment_and_abandon:
            self.log("info", "检测到支付页，已按配置停在支付前")
            return True

        self.log("info", "检测到支付页，开始点击支付按钮以提交订单")
        payment_candidates = [
            page.get_by_role("button", name=PAYMENT_SUBMIT_TEXT_RE),
            page.get_by_role("link", name=PAYMENT_SUBMIT_TEXT_RE),
            page.locator("button, a, [role=button], [class*=button], [class*=btn]").filter(
                has_text=PAYMENT_SUBMIT_TEXT_RE
            ),
        ]
        clicked_payment = self.click_first_visible(payment_candidates, "submit payment / create order")
        if not clicked_payment:
            clicked_payment = self.click_visible_text_element(
                page, PAYMENT_AMOUNT_TEXT_RE, "submit payment / create order"
            )
        if not clicked_payment:
            self.log("warn", "已进入支付页，但未找到可点击的支付按钮")
            return False

        self.state["payment_clicks"] += 1
        self.state["order_submitted"] = True
        self.wait_ms(self.config.order_step_delay_ms)

        closed = self.close_payment_layer(page)
        abandoned = self.confirm_abandon_payment(page)
        if closed or abandoned:
            self.log("info", "订单已提交到支付态，并已执行放弃支付确认流程")
            return True

        self.log("warn", "订单已进入支付态，但未确认找到关闭/放弃支付入口")
        return True

    def advance_order_flow(self, page) -> bool:
        if self.config.dry_run or self.config.max_order_steps <= 0:
            return True

        for step in range(1, self.config.max_order_steps + 1):
            self.assert_running()
            if self.submit_payment_then_abandon(page):
                return True
            self.dismiss_blocking_overlays(page)
            candidates = [
                page.get_by_role("button", name=ORDER_STEP_TEXT_RE),
                page.get_by_role("link", name=ORDER_STEP_TEXT_RE),
                page.locator("button, a, [role=button], [class*=button], [class*=btn]").filter(
                    has_text=ORDER_STEP_TEXT_RE
                ),
            ]
            advanced = self.click_first_visible(candidates, f"order flow step {step}")
            if not advanced:
                self.log("warn", f"下单流程第 {step} 步未找到可继续按钮")
                return False
            self.wait_ms(self.config.order_step_delay_ms)

        return self.submit_payment_then_abandon(page)

    def scan_dom_and_order(self, page) -> bool:
        self.state["scans"] += 1
        if self.submit_payment_then_abandon(page):
            return True
        self.ensure_all_products_panel(page)

        tokens = keyword_tokens(self.config)
        token_re = re.compile("|".join(escape_regex(token) for token in tokens), re.I)
        product_nodes = page.locator(
            "li, [role='dialog'] *, [class*='product' i], [class*='goods' i], "
            "[class*='sku' i], [data-e2e*='product' i], [data-e2e*='goods' i]"
        ).filter(has_text=token_re)

        try:
            total = product_nodes.count()
        except Exception:
            total = 0
        self.state["candidate_nodes"] = total
        count = min(total, 140)
        if self.state["scans"] % 10 == 1:
            self.log("info", f"扫描商品候选节点：{total} 个")

        for index in range(count):
            self.assert_running()
            node = product_nodes.nth(index)
            try:
                text = node.inner_text(timeout=350)
            except Exception:
                continue
            if not text:
                continue
            if not is_likely_list_product_card_text(text, self.config):
                continue

            self.state["matched_products"] += 1
            self.state["last_match_text"] = compact_text(text, 500)
            action_state = product_action_state(text)
            if action_state == "waiting":
                if self.state["scans"] % 10 == 1:
                    self.log("info", f"目标商品仍在等待开售：{compact_text(text, 180)}")
                continue

            if action_state != "ready" or product_unavailable(text) or not price_matches(text, self.config):
                self.state["unavailable_matches"] += 1
                self.log("warn", f"命中商品但状态或价格不满足：{compact_text(text, 220)}")
                continue

            containers = self.find_tight_product_containers(node)
            for container in containers:
                if not is_likely_list_product_card_text(container["text"], self.config):
                    continue
                self.state["last_match_text"] = container["text"]
                self.log(
                    "info",
                    "命中目标商品且可购买"
                    f"（{product_match_reason(container['text'], self.config)}）："
                    f"{compact_text(container['text'], 300)}",
                )
                if self.find_and_click_buy_action(page, container["locator"], "target product buy button"):
                    self.wait_ms(self.config.post_buy_delay_ms)
                    return self.advance_order_flow(page)

        self.scroll_likely_product_lists(page)
        self.log("info", "未在目标商品卡片内找到可点击购买按钮，跳过页面级兜底点击")
        return False

    def active_window_end_local_ms(self, clock_offset_ms: int) -> Optional[float]:
        points = self.config.schedule_windows()
        if not points:
            return None
        now_server = datetime.fromtimestamp((time.time() * 1000 + clock_offset_ms) / 1000)
        for point in points:
            start = now_server.replace(
                hour=point.hour, minute=point.minute, second=point.second, microsecond=0
            )
            start_ms = start.timestamp() * 1000
            end_ms = start_ms + self.config.monitor_duration_ms
            now_server_ms = now_server.timestamp() * 1000
            if start_ms - self.config.prewarm_ms <= now_server_ms < end_ms:
                return end_ms - clock_offset_ms
        return None

    def next_window_open_local_ms(self, clock_offset_ms: int) -> float:
        points = self.config.schedule_windows()
        now_server = datetime.fromtimestamp((time.time() * 1000 + clock_offset_ms) / 1000)
        candidates = []
        for point in points:
            candidate = now_server.replace(
                hour=point.hour, minute=point.minute, second=point.second, microsecond=0
            )
            if candidate <= now_server:
                candidate += timedelta(days=1)
            candidates.append(candidate.timestamp() * 1000 - clock_offset_ms)
        return min(candidates)

    def wait_for_schedule_window(self, clock_offset_ms: int) -> float:
        if not self.config.schedule_windows():
            self.log("info", "未配置监控时间点，立即进入监控窗口")
            return time.time() * 1000 + self.config.monitor_duration_ms

        while True:
            self.assert_running()
            active_end = self.active_window_end_local_ms(clock_offset_ms)
            if active_end:
                return active_end
            next_open = self.next_window_open_local_ms(clock_offset_ms)
            wake_at = max(time.time() * 1000, next_open - self.config.prewarm_ms)
            seconds = max(0, math.ceil((wake_at - time.time() * 1000) / 1000))
            self.log("info", f"等待下一个监控窗口，约 {seconds}s 后预热")
            while time.time() * 1000 < wake_at:
                self.wait_ms(min(500, int(wake_at - time.time() * 1000)))
            self.log("info", "已进入预热阶段，开始高频监控")
            return next_open + self.config.monitor_duration_ms

    def save_diagnostics(self, page, reason: str) -> None:
        if not self.config.save_diagnostics:
            return
        now = time.time() * 1000
        if now - self.state["last_diagnostic_at"] < 5000:
            return
        self.state["last_diagnostic_at"] = now
        try:
            Path(self.config.diagnostics_dir).mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
            safe_reason = re.sub(r"[^\w-]+", "_", reason or "snapshot")[:40]
            base = Path(self.config.diagnostics_dir) / f"{stamp}_{safe_reason}"
            page.screenshot(path=str(base) + ".png", full_page=True, timeout=5000)
            Path(str(base) + ".html").write_text(page.content(), encoding="utf-8")
            Path(str(base) + ".json").write_text(
                json.dumps(
                    {
                        "reason": reason,
                        "url": page.url,
                        "state": self.state,
                        "config": asdict(self.config),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.log("info", f"已保存诊断快照：{base}.png")
        except Exception as exc:
            self.log("warn", f"保存诊断快照失败：{exc}")

    def run_flash_sale(self, page, clock_offset_ms: int) -> None:
        self.log("info", f"打开直播间：{self.config.live_url}")
        page.goto(self.config.live_url, wait_until="domcontentloaded", timeout=60000)
        self.log("info", "页面已打开，如需登录请在浏览器中完成登录")
        self.dismiss_blocking_overlays(page)
        self.open_commerce_panel(page, True)

        ordered = False
        while not ordered:
            self.assert_running()
            window_ends_at = self.wait_for_schedule_window(clock_offset_ms)
            self.log("info", "监控窗口已激活，开始扫描商品")
            while time.time() * 1000 < window_ends_at and not ordered:
                self.assert_running()
                try:
                    ordered = self.scan_dom_and_order(page)
                except GracefulStop:
                    raise
                except Exception as exc:
                    self.log("warn", f"扫描异常：{exc}")
                if not ordered:
                    self.wait_ms(randomized_delay_ms(self.config))

            if not ordered:
                self.log(
                    "info",
                    "当前窗口结束，未完成目标动作。"
                    f"扫描次数={self.state['scans']}，"
                    f"候选节点={self.state['candidate_nodes']}，"
                    f"命中商品={self.state['matched_products']}，"
                    f"不可购买={self.state['unavailable_matches']}，"
                    f"点击尝试={self.state['click_attempts']}，"
                    f"支付点击={self.state['payment_clicks']}，"
                    f"订单提交={'是' if self.state['order_submitted'] else '否'}",
                )
                self.save_diagnostics(page, "window-ended-no-order")
                if not self.config.schedule_windows():
                    break

        if ordered:
            self.log("info", "已到达下单流程节点")

    def run_batch_buy(self, page) -> None:
        if not self.config.product_url:
            raise RuntimeError("批量购买模式需要填写商品链接")
        for batch in range(1, self.config.buy_times + 1):
            self.assert_running()
            self.log("info", f"批次 {batch}/{self.config.buy_times}：打开商品链接")
            page.goto(self.config.product_url, wait_until="domcontentloaded", timeout=60000)
            self.wait_ms(500)
            self.dismiss_blocking_overlays(page)
            clicked = self.find_and_click_buy_action(page, page.locator("body"))
            if not clicked:
                self.log("warn", f"批次 {batch} 未找到购买按钮")
            else:
                self.wait_ms(self.config.post_buy_delay_ms)
                self.advance_order_flow(page)
            if batch < self.config.buy_times:
                self.wait_ms(randomized_delay_ms(self.config))
        self.log("info", "批量购买流程已执行完毕")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DY 直播间商品监控 - Python版")
        self.geometry("1120x760")
        self.minsize(980, 680)
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.runner: Optional[AutomationRunner] = None
        self.vars: dict[str, tk.Variable] = {}
        self.build_ui()
        self.after(120, self.drain_logs)

    def add_field(self, parent, row: int, label: str, name: str, default: str, width: int = 48) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        var = tk.StringVar(value=default)
        self.vars[name] = var
        ttk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="ew", padx=8, pady=4
        )

    def add_check(self, parent, row: int, col: int, label: str, name: str, default: bool) -> None:
        var = tk.BooleanVar(value=default)
        self.vars[name] = var
        ttk.Checkbutton(parent, text=label, variable=var).grid(
            row=row, column=col, sticky="w", padx=8, pady=4
        )

    def build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        form = ttk.LabelFrame(root, text="任务配置", padding=8)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        self.add_field(form, 0, "直播间链接", "live_url", DEFAULT_LIVE_URL, 70)
        self.add_field(form, 1, "商品关键词", "product_name", DEFAULT_PRODUCT_NAME, 70)
        self.add_field(form, 2, "商品编号", "product_id", "24", 20)
        self.add_field(form, 3, "目标价格", "target_price", "799", 20)
        self.add_field(form, 4, "监控时间点", "schedule_windows", "12:29:00", 30)
        self.add_field(form, 5, "浏览器资料目录", "profile_dir", str(Path("live-room-profile").resolve()), 70)

        right = ttk.Frame(form)
        right.grid(row=2, column=2, rowspan=4, columnspan=2, sticky="nsew")
        self.add_field(right, 0, "轮询(ms)", "poll_ms", "35", 16)
        self.add_field(right, 1, "监控时长(ms)", "monitor_duration_ms", "1800000", 16)
        self.add_field(right, 2, "预热(ms)", "prewarm_ms", "1000", 16)
        self.add_field(right, 3, "点击超时(ms)", "click_timeout_ms", "450", 16)
        self.add_field(right, 4, "面板间隔(ms)", "open_panel_interval_ms", "180", 16)

        checks = ttk.LabelFrame(root, text="开关", padding=8)
        checks.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        for col in range(4):
            checks.columnconfigure(col, weight=1)
        self.add_check(checks, 0, 0, "严格匹配目标价格", "strict_price_match", True)
        self.add_check(checks, 0, 1, "DRY_RUN 只测试不下单", "dry_run", False)
        self.add_check(checks, 0, 2, "无头模式", "headless", False)
        self.add_check(checks, 0, 3, "成功后关闭浏览器", "close_browser_on_finish", False)
        self.add_check(checks, 1, 0, "支付后放弃支付", "submit_payment_and_abandon", True)
        self.add_check(checks, 1, 1, "允许点预约/提醒", "allow_reservation_click", False)
        self.add_check(checks, 1, 2, "保存诊断快照", "save_diagnostics", True)

        log_frame = ttk.LabelFrame(root, text="运行日志", padding=8)
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap="word", height=20)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(root)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(actions, text="启动监控", command=self.start_task)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop_task, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="清空日志", command=self.clear_logs).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="打开诊断目录", command=self.open_diagnostics).pack(side="left")

    def config_from_form(self) -> AutomationConfig:
        get = lambda name: self.vars[name].get()
        return AutomationConfig(
            live_url=str(get("live_url")).strip() or DEFAULT_LIVE_URL,
            product_name=str(get("product_name")).strip(),
            product_id=str(get("product_id")).strip(),
            target_price=as_float(get("target_price"), 0, 0),
            poll_ms=as_int(get("poll_ms"), 35, 20),
            monitor_duration_ms=as_int(get("monitor_duration_ms"), 1800000, 1000),
            prewarm_ms=as_int(get("prewarm_ms"), 1000, 0),
            click_timeout_ms=as_int(get("click_timeout_ms"), 450, 100),
            open_panel_interval_ms=as_int(get("open_panel_interval_ms"), 180, 50),
            schedule_windows_raw=str(get("schedule_windows")).strip(),
            profile_dir=str(get("profile_dir")).strip() or str(Path("live-room-profile").resolve()),
            strict_price_match=bool(get("strict_price_match")),
            dry_run=bool(get("dry_run")),
            headless=bool(get("headless")),
            close_browser_on_finish=bool(get("close_browser_on_finish")),
            submit_payment_and_abandon=bool(get("submit_payment_and_abandon")),
            allow_reservation_click=bool(get("allow_reservation_click")),
            save_diagnostics=bool(get("save_diagnostics")),
        )

    def log(self, level: str, message: str) -> None:
        self.log_queue.put((level, message))

    def drain_logs(self) -> None:
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                self.log_text.insert("end", f"[{now_text()}] {level.upper()} {message}\n")
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.after(120, self.drain_logs)

    def start_task(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("任务运行中", "已有任务正在运行")
            return
        config = self.config_from_form()
        if not config.product_name and not config.product_id:
            messagebox.showerror("配置错误", "请至少填写商品关键词或商品编号")
            return
        if config.strict_price_match and config.target_price <= 0:
            messagebox.showerror("配置错误", "严格价格匹配时必须填写目标价格")
            return
        self.stop_event = threading.Event()
        self.runner = AutomationRunner(config, self.log, self.stop_event)
        self.worker = threading.Thread(target=self.worker_main, daemon=True)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.worker.start()

    def worker_main(self) -> None:
        try:
            status = self.runner.run() if self.runner else "stopped"
            self.log("info", f"任务结束：{status}")
        except Exception:
            self.log("error", "任务异常退出")
        finally:
            self.after(0, lambda: self.start_button.configure(state="normal"))
            self.after(0, lambda: self.stop_button.configure(state="disabled"))

    def stop_task(self) -> None:
        self.stop_event.set()
        self.log("warn", "收到停止请求，正在中止任务")

    def clear_logs(self) -> None:
        self.log_text.delete("1.0", "end")

    def open_diagnostics(self) -> None:
        path = Path("diagnostics").resolve()
        path.mkdir(exist_ok=True)
        os.startfile(path)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
