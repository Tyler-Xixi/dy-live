# -*- coding: utf-8 -*-
"""Python GUI for the live-room product monitor.

This is a Python/Tkinter port of the existing Node Playwright runner.  It keeps
the same core flow: open live room, open all products, match the target product,
click buy when ready, submit the order to the payment state, then close/abandon
payment so the order remains submitted.
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import math
import os
import platform
import queue
import random
import re
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import tkinter as tk
from tkinter import messagebox, ttk

try:
    from local_license_keys import VALID_CARD_HASHES
except ModuleNotFoundError as exc:
    if exc.name != "local_license_keys":
        raise
    # Public source excludes private card verifiers. No keys means activation
    # is denied until the maintainer generates their own local key module.
    VALID_CARD_HASHES = frozenset()


BUY_TEXT_RE = re.compile(
    r"(抢购|立即抢|马上抢|立即购买|去购买|购买|下单|加入购物车|提交订单|去结算)"
)
BUY_READY_TEXT_RE = re.compile(r"(立即购买|去抢购|抢购|立即抢|马上抢|购买|下单)")
BUY_ACTION_TEXT_RE = re.compile(
    r"^\s*(?:立即购买|去抢购|立即抢|马上抢|抢购|去购买|购买|下单)\s*$"
)
REVEAL_PRICE_TEXT_RE = re.compile(r"^\s*查看价格\s*$")
REAL_NAME_REQUIRED_RE = re.compile(
    r"(未实名|尚未实名|请先实名认证|请完成实名认证|实名认证后)"
)
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
    r"^\s*(?:确定|确认|选好了|完成|下一步|提交订单|提交|去结算)\s*$"
)
PAYMENT_ACTION_TEXT_RE = re.compile(
    r"^\s*(?:支付|立即支付|确认支付|确认付款|提交支付|去支付)"
    r"(?:\s*(?:¥|￥)?\s*\d+(?:\.\d{1,2})?\s*元?)?\s*$"
)
# Verified from a saved real Douyin checkout DOM on 2026-10-06. The parent
# checkout footer also contains a QR/product image, so never click the parent.
PAYMENT_PRIMARY_SELECTOR = "div.iHAKgO8B"
PRODUCT_IMAGE_VIEWER_SELECTOR = "div.rpiGKCVd"
PRODUCT_IMAGE_VIEWER_GUARD_ID = "dyla-product-image-viewer-guard"
PRODUCT_OPTION_SELECTOR = "div.ufz0AqTE"
DISMISS_TEXT_RE = re.compile(
    r"(我知道了|知道了|允许|稍后再说|以后再说|关闭|继续看播|继续观看|继续看直播)"
)
USER_AGREEMENT_TITLE_RE = re.compile(r"^\s*使用须知\s*$")
UNAVAILABLE_TEXT_RE = re.compile(
    r"(售罄|已售罄|抢光|已抢光|抢完|已抢完|缺货|补货中|已结束|已下架|不可购买|卖光)"
)


DEFAULT_LIVE_URL = "https://live.douyin.com/749508379274"
DEFAULT_PRODUCT_NAME = "[6]手工制品硅胶捏捏玩具，默认微瑕"


def application_data_dir() -> Path:
    """Return a writable data directory in source and packaged builds."""
    if getattr(sys, "frozen", False):
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "DYLiveAssistant"
    return Path(__file__).resolve().parent


APP_DATA_DIR = application_data_dir()
DEFAULT_PROFILE_DIR = APP_DATA_DIR / "live-room-profile"
DEFAULT_DIAGNOSTICS_DIR = APP_DATA_DIR / "diagnostics"
DEFAULT_NETWORK_LOG = APP_DATA_DIR / "live_room_network_hits.jsonl"
LOCAL_LICENSE_SECRET = "DYLiveAssistant-local-license-v1-7b4c52a1"
AUTOMATION_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_NETWORK_LOG_BYTES = 10 * 1024 * 1024
NETWORK_LOG_BACKUPS = 3


def machine_fingerprint() -> str:
    """Create an app-specific hash without sending the raw Windows MachineGuid."""
    machine_value = ""
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            access,
        ) as key:
            machine_value = str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except Exception:
        machine_value = f"{platform.node()}|{uuid.getnode()}|{platform.machine()}"
    return hashlib.sha256(
        f"DYLiveAssistant|{machine_value}".encode("utf-8")
    ).hexdigest()


def local_activation_proof(machine_id: str, card_hash: str) -> str:
    value = f"{LOCAL_LICENSE_SECRET}|{machine_id}|{card_hash}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LocalLicenseClient:
    def __init__(self) -> None:
        self.state_path = APP_DATA_DIR / "license.json"
        self.used_path = APP_DATA_DIR / "used_cards.json"
        self.machine_id = machine_fingerprint()

    def load_state(self) -> Optional[dict]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("machine_id") and data.get("card_hash") and data.get("proof"):
                return data
        except Exception:
            pass
        return None

    def load_used_hashes(self) -> set[str]:
        try:
            data = json.loads(self.used_path.read_text(encoding="utf-8"))
            return {str(value) for value in data.get("used_card_hashes", [])}
        except Exception:
            return set()

    def verify_saved(self) -> bool:
        state = self.load_state()
        if not state:
            return False
        card_hash = str(state.get("card_hash", ""))
        expected = local_activation_proof(self.machine_id, card_hash)
        return bool(
            state.get("machine_id") == self.machine_id
            and card_hash in VALID_CARD_HASHES
            and state.get("proof") == expected
        )

    def activate(self, card_key: str) -> None:
        normalized = card_key.strip().upper()
        card_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if card_hash not in VALID_CARD_HASHES:
            raise RuntimeError("卡密无效")
        used_hashes = self.load_used_hashes()
        if card_hash in used_hashes:
            raise RuntimeError("该卡密已经在本机使用过")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "mode": "local",
                    "machine_id": self.machine_id,
                    "card_hash": card_hash,
                    "proof": local_activation_proof(self.machine_id, card_hash),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        used_hashes.add(card_hash)
        self.used_path.write_text(
            json.dumps(
                {"used_card_hashes": sorted(used_hashes)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class ActivationDialog(tk.Tk):
    def __init__(self, client: LocalLicenseClient) -> None:
        super().__init__()
        self.client = client
        self.authorized = False
        self.title("DY 直播间助手 - 软件激活")
        self.geometry("520x260")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="软件激活", font=("Microsoft YaHei UI", 16, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="首次使用请输入管理员提供的卡密。卡密验证后将绑定当前电脑。",
            wraplength=470,
        ).pack(anchor="w", pady=(8, 14))
        self.card_var = tk.StringVar()
        self.card_entry = ttk.Entry(frame, textvariable=self.card_var, width=48)
        self.card_entry.pack(fill="x")
        self.card_entry.bind("<Return>", lambda _event: self.activate())
        self.status_var = tk.StringVar(value="正在检查本机激活状态……")
        ttk.Label(frame, textvariable=self.status_var, wraplength=470).pack(
            anchor="w", pady=(12, 8)
        )
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(4, 0))
        self.activate_button = ttk.Button(
            actions, text="激活并打开软件", command=self.activate, state="disabled"
        )
        self.activate_button.pack(side="left")
        ttk.Button(actions, text="退出", command=self.destroy).pack(side="right")
        self.after(150, self.verify_existing)

    def verify_existing(self) -> None:
        if self.client.verify_saved():
            self.allow_start()
            return
        self.ready_for_key("请输入卡密进行本机离线激活")

    def ready_for_key(self, message: str) -> None:
        self.status_var.set(message)
        self.activate_button.configure(state="normal")
        self.card_entry.focus_set()

    def activate(self) -> None:
        card_key = self.card_var.get().strip()
        if not card_key:
            self.status_var.set("请输入卡密")
            return
        self.activate_button.configure(state="disabled")
        self.status_var.set("正在验证卡密……")
        try:
            self.client.activate(card_key)
            self.allow_start()
        except Exception as exc:
            self.ready_for_key(str(exc))

    def allow_start(self) -> None:
        self.authorized = True
        self.destroy()


class GracefulStop(Exception):
    pass


class AccountVerificationRequired(RuntimeError):
    """Raised when Douyin blocks the commerce flow pending account verification."""


class UserAgreementRequired(RuntimeError):
    """Raised when a platform agreement must be accepted by the signed-in user."""


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
    product_id: str = "2"
    target_price: float = 6.0
    dry_run: bool = True
    headless: bool = False
    poll_ms: int = 35
    jitter_ms: int = 0
    monitor_duration_ms: int = 30 * 60 * 1000
    prewarm_ms: int = 1000
    strict_price_match: bool = True
    allow_reservation_click: bool = False
    open_panel_interval_ms: int = 180
    max_order_steps: int = 8
    post_buy_delay_ms: int = 500
    order_step_delay_ms: int = 300
    click_timeout_ms: int = 450
    auto_pay: bool = False
    multi_option_enabled: bool = False
    option_names: str = ""
    submit_payment_and_abandon: bool = False
    save_diagnostics: bool = True
    max_retries: int = 5
    retry_base_ms: int = 80
    circuit_breaker_429: int = 5
    buy_quantity: int = 1
    buy_times: int = 1
    batch_interval_ms: int = 800
    schedule_windows_raw: str = ""
    profile_dir: str = field(
        default_factory=lambda: str(DEFAULT_PROFILE_DIR)
    )
    network_log_path: str = field(
        default_factory=lambda: str(DEFAULT_NETWORK_LOG)
    )
    diagnostics_dir: str = field(default_factory=lambda: str(DEFAULT_DIAGNOSTICS_DIR))
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
    if not math.isfinite(number):
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
    return []


def product_matches(text: str, config: AutomationConfig) -> bool:
    haystack = normalize_text(text)
    exact_name = normalize_text(config.product_name)
    if not exact_name:
        return True
    if exact_name and exact_name in haystack:
        return True

    tokens = [normalize_text(token) for token in keyword_tokens(config)]
    hit_count = sum(1 for token in tokens if token and token in haystack)
    required_hits = min(len(tokens), max(1, math.ceil(len(tokens) * 0.75)))
    return bool(tokens) and hit_count >= required_hits


AUTHENTICATED_DOUYIN_COOKIE_NAMES = {
    "sessionid",
    "sessionid_ss",
    "sid_guard",
    "sid_tt",
    "sid_ucp_v1",
    "uid_tt",
    "uid_tt_ss",
    "login_status",
    "passport_auth_status",
}


def has_authenticated_douyin_cookie(cookies: list[dict]) -> bool:
    """Return true only for cookies that indicate an authenticated account.

    ``passport_csrf_token`` is intentionally excluded because Douyin also sets it
    for logged-out visitors.
    """
    names = {
        str(item.get("name", "")).lower()
        for item in cookies
        if "douyin.com" in str(item.get("domain", "")).lower()
    }
    return bool(names & AUTHENTICATED_DOUYIN_COOKIE_NAMES)


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
    value = str(text or "")
    patterns = (
        r"(?:¥|￥|RMB|CNY)\s*(\d+(?:\.\d{1,2})?)",
        r"(?:价格|售价|到手价|抢购价|券后价|活动价)\s*[:：]?\s*(?:¥|￥)?\s*(\d+(?:\.\d{1,2})?)",
    )
    prices = [
        float(match.group(1))
        for pattern in patterns
        for match in re.finditer(pattern, value, re.I)
    ]
    # Both values are currency amounts: even a one-cent increase must block.
    return any(math.isclose(price, target_price, rel_tol=0, abs_tol=0.000001) for price in prices)


def price_matches(text: str, config: AutomationConfig) -> bool:
    if not config.strict_price_match:
        return True
    return exact_target_price_matches(text, config)


def parse_product_options(value: str) -> list[tuple[str, str]]:
    """One exact option per group; optional group=option avoids duplicate names."""
    result = []
    for item in re.split(r"[|;；\n]+", value or ""):
        item = item.strip()
        if not item:
            continue
        group, sep, name = item.partition("=")
        pair = (group.strip(), name.strip()) if sep else ("", item)
        if not pair[1] or (sep and not pair[0]):
            raise ValueError("选项格式应为选项名称或规格组=选项名称")
        if pair in result:
            raise ValueError("请勿重复填写同一个选项")
        result.append(pair)
    return result


def product_unavailable(text: str) -> bool:
    return UNAVAILABLE_TEXT_RE.search(str(text or "")) is not None


def product_action_state(text: str) -> str:
    value = str(text or "")
    if product_unavailable(value):
        return "unavailable"
    if WAITING_SALE_TEXT_RE.search(value):
        return "waiting"
    if BUY_READY_TEXT_RE.search(value):
        return "ready"
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

    return product_action_state(value) != "unknown" and (
        product_action_state(value) == "waiting" or config.multi_option_enabled or price_matches(value, config)
    )


def is_hidden_price_target_card_text(text: str, config: AutomationConfig) -> bool:
    value = compact_text(text, 900)
    if not value or len(value) > 820 or product_unavailable(value):
        return False
    return bool(
        "查看价格" in value
        and product_matches(value, config)
        and product_list_index_matches(value, config)
    )


def is_checkout_context_text(text: str) -> bool:
    value = compact_text(text, 2400)
    return bool(
        "优惠明细" in value
        and ("订单留言" in value or "购买数量" in value)
    )


def product_match_reason(text: str, config: AutomationConfig) -> str:
    if str(config.product_id or "").strip() and product_list_index_matches(text, config):
        return "编号命中"
    if product_matches(text, config) and exact_target_price_matches(text, config):
        return "关键词+价格命中"
    return "未知"


def schedule_entries(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，\n;；|]+", value or "") if item.strip()]


def invalid_schedule_entries(value: str) -> list[str]:
    invalid: list[str] = []
    entries = schedule_entries(value)
    if len(entries) > 4:
        invalid.extend(entries[4:])
    for raw in entries[:4]:
        match = re.fullmatch(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", raw)
        if not match:
            invalid.append(raw)
            continue
        hour, minute, second = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
        if hour > 23 or minute > 59 or second > 59:
            invalid.append(raw)
    return invalid


def parse_schedule_windows(value: str) -> list[SchedulePoint]:
    result: list[SchedulePoint] = []
    for raw in schedule_entries(value)[:4]:
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


def is_allowed_douyin_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and (
            hostname == "douyin.com" or hostname.endswith(".douyin.com")
        )
    except Exception:
        return False


def rotate_file_if_needed(path_value: str, max_bytes: int = MAX_NETWORK_LOG_BYTES) -> None:
    path = Path(path_value)
    if not path.exists() or path.stat().st_size < max_bytes:
        return
    for index in range(NETWORK_LOG_BACKUPS, 0, -1):
        if index == NETWORK_LOG_BACKUPS:
            path.with_name(f"{path.name}.{index}").unlink(missing_ok=True)
            continue
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            source.replace(target)
    path.replace(path.with_name(f"{path.name}.1"))


def randomized_delay_ms(config: AutomationConfig) -> int:
    jitter = random.randint(0, max(0, int(config.jitter_ms)))
    return max(20, int(config.poll_ms) + jitter)


def launch_persistent_browser(playwright, profile_dir: str, headless: bool, log):
    """Use an installed Edge/Chrome first, then Playwright Chromium as fallback."""
    options = {
        "headless": headless,
        "viewport": {"width": 1365, "height": 900},
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        # Keep the dedicated Douyin browser off a Windows system proxy/VPN
        # while the desktop ChatGPT client can continue using it.
        "args": ["--no-proxy-server"],
    }
    errors: list[str] = []
    for label, channel in (("Microsoft Edge", "msedge"), ("Google Chrome", "chrome")):
        try:
            context = playwright.chromium.launch_persistent_context(
                profile_dir, channel=channel, **options
            )
            log("info", f"已使用系统浏览器：{label}")
            return context
        except Exception as exc:
            errors.append(f"{label}: {compact_text(exc, 160)}")

    try:
        context = playwright.chromium.launch_persistent_context(profile_dir, **options)
        log("info", "已使用 Playwright Chromium")
        return context
    except Exception as exc:
        errors.append(f"Playwright Chromium: {compact_text(exc, 160)}")
        raise RuntimeError(
            "无法启动浏览器。请安装 Microsoft Edge 或 Google Chrome；"
            "源码运行也可执行 python -m playwright install chromium。\n"
            + "\n".join(errors)
        ) from exc


class LoginSession:
    """Open the official site and persist a user-completed login session."""

    def __init__(self, config, log, stop_event, finish_event) -> None:
        self.config = config
        self.log = log
        self.stop_event = stop_event
        self.finish_event = finish_event

    def run(self) -> str:
        from playwright.sync_api import sync_playwright

        Path(self.config.profile_dir).mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        context = None
        try:
            context = launch_persistent_browser(
                playwright, self.config.profile_dir, False, self.log
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                self.config.live_url or "https://www.douyin.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            self.log("info", "登录浏览器已打开，请在抖音官方页面完成扫码或验证码登录")
            self.log("info", "确认页面已登录后，回到本程序点击“完成登录并保存”")

            while not self.finish_event.wait(0.2):
                if self.stop_event.is_set():
                    return "cancelled"
                try:
                    if not context.pages:
                        self.log("warn", "登录浏览器已被关闭，登录流程取消")
                        return "cancelled"
                except Exception:
                    return "cancelled"

            if self.stop_event.is_set():
                return "cancelled"

            try:
                page.wait_for_timeout(500)
            except Exception:
                pass
            cookies = context.cookies()
            logged_in = has_authenticated_douyin_cookie(cookies)
            if logged_in:
                self.log("info", "已检测到登录会话，登录态已保存到浏览器资料目录")
                return "saved"
            self.log(
                "warn",
                "未检测到已登录账号。请确认网页右上角不再显示“登录”，"
                "然后重新点击“完成登录并保存”",
            )
            return "unverified"
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass
            playwright.stop()


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
        self.locked_product = None
        self.purchase_started = False
        self.purchase_started_at = 0.0
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
            "unsafe_checkout": False,
            "options_verified": False,
            "batch_completed": 0,
            "pending_payment_review": False,
            "payment_confirmed": False,
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
        completed_normally = False
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
        if self.config.multi_option_enabled:
            if not parse_product_options(self.config.option_names):
                raise ValueError("启用商品多选项时必须填写选项名称")
            self.log("info", f"商品多选项已开启：{self.config.option_names}；选择后校验支付价格")
        if self.config.product_id:
            self.log("info", f"商品编号：{self.config.product_id}")
        if self.config.target_price > 0:
            mode = "严格匹配" if self.config.strict_price_match else "仅作参考"
            self.log("info", f"目标价格：{self.config.target_price:g}（{mode}）")

        try:
            Path(self.config.profile_dir).mkdir(parents=True, exist_ok=True)
            Path(self.config.diagnostics_dir).mkdir(parents=True, exist_ok=True)

            self.playwright = sync_playwright().start()
            self.context = launch_persistent_browser(
                self.playwright,
                self.config.profile_dir,
                self.config.headless,
                self.log,
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
            completed_normally = True
            return "completed"
        except GracefulStop as exc:
            self.log("warn", str(exc))
            return "stopped"
        except Exception:
            self.log("error", "任务失败：\n" + traceback.format_exc())
            raise
        finally:
            keep_open = bool(
                self.context
                and (completed_normally or self.state["pending_payment_review"])
                and (self.state["pending_payment_review"] or not self.config.close_browser_on_finish)
                and not self.stop_event.is_set()
            )
            if keep_open:
                self.log("info", "任务结束，浏览器将保留；关闭浏览器窗口或点击“停止”后释放资料目录")
                while not self.stop_event.wait(0.25):
                    try:
                        if not self.context.pages:
                            break
                    except Exception:
                        break
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    pass
                if self.config.close_browser_on_finish or self.stop_event.is_set():
                    self.log("info", "浏览器上下文已关闭")
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    pass

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
                headers = response.headers
                content_type = str(headers.get("content-type", "")).lower()
                if "json" not in content_type:
                    return
                content_length = as_int(headers.get("content-length", 0), 0, 0)
                if content_length > 1024 * 1024:
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
                Path(self.config.network_log_path).parent.mkdir(parents=True, exist_ok=True)
                rotate_file_if_needed(self.config.network_log_path)
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
            except GracefulStop:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                self.log("warn", f"{label} 第 {attempt}/{self.config.max_retries} 次失败：{exc}")
                self.wait_ms(self.config.retry_base_ms * attempt)
        raise last_error or RuntimeError(label)

    def safe_click(self, locator, label: str, allow_dry_run_click: bool = False) -> bool:
        self.state["click_attempts"] += 1
        if self.config.dry_run and not allow_dry_run_click:
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

        if label == "submit payment / create order":
            # Payment is not idempotent: an ambiguous timeout must never cause a
            # second click. Keep the page open so the user can inspect the order.
            self.assert_running()
            self.state["pending_payment_review"] = True
            try:
                do_click()
            except GracefulStop:
                raise
            except Exception as exc:
                raise GracefulStop("支付点击结果不明确，已停止重试；请在保留的页面核对订单") from exc
        else:
            self.retry_action(label, do_click)
        self.log("info", f"已点击：{label}")
        self.state["successful_clicks"] += 1
        self.state["last_click_label"] = label
        return True

    def click_first_visible(
        self, candidates: list, label: str, allow_dry_run_click: bool = False
    ) -> bool:
        for candidate in candidates:
            self.assert_running()
            try:
                count = min(candidate.count(), 12)
                for index in range(count):
                    action = candidate.nth(index)
                    if not action.is_visible(timeout=120):
                        continue
                    try:
                        if not action.is_enabled(timeout=120):
                            continue
                    except Exception:
                        pass
                    return self.safe_click(action, label, allow_dry_run_click)
            except GracefulStop:
                raise
            except Exception:
                continue
        return False

    def safe_exact_action_buttons(self, page, pattern: re.Pattern) -> list:
        """Return only compact, media-free controls whose complete text matches."""
        viewport = page.viewport_size or {"width": 1365, "height": 900}
        max_area = viewport["width"] * viewport["height"] * 0.22
        safe: list = []
        seen: set[tuple] = set()
        candidate_groups = [
            page.locator(PAYMENT_PRIMARY_SELECTOR),
            page.locator("button, [role=button], div, span").filter(has_text=pattern),
        ]
        for candidates in candidate_groups:
            try:
                count = min(candidates.count(), 20)
            except Exception:
                continue
            for index in range(count):
                action = candidates.nth(index)
                try:
                    text = compact_text(action.inner_text(timeout=180), 160)
                    if not pattern.fullmatch(text):
                        continue
                    if action.locator("img, picture, video, canvas").count() > 0:
                        continue
                    box = action.bounding_box(timeout=180)
                    if not box or box["width"] <= 48 or box["height"] <= 20:
                        continue
                    if box["height"] > 160 or box["width"] * box["height"] > max_area:
                        continue
                    if not action.is_visible(timeout=120):
                        continue
                    if not action.is_enabled(timeout=120):
                        continue
                    if not action.evaluate(r"""el => !el.closest('[aria-disabled="true"], [disabled]')
                        && getComputedStyle(el).pointerEvents !== 'none'
                        && !/(?:^|[\s_-])disabled(?:$|[\s_-])/i.test(el.className || '')"""):
                        continue
                    key = (
                        round(box["x"]),
                        round(box["y"]),
                        round(box["width"]),
                        round(box["height"]),
                        text,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    safe.append(action)
                except Exception:
                    continue
        return safe

    def click_safe_exact_action_button(self, page, pattern: re.Pattern, label: str) -> bool:
        actions = self.safe_exact_action_buttons(page, pattern)
        if not actions:
            return False
        try:
            details = actions[0].evaluate(
                "el => ({tag: el.tagName.toLowerCase(), className: el.className || '', "
                "role: el.getAttribute('role') || '', text: el.innerText || ''})"
            )
            self.log(
                "info",
                "支付控件已锁定："
                f"tag={details.get('tag')} class={details.get('className')} "
                f"text={compact_text(details.get('text'), 80)}",
            )
        except Exception:
            pass
        return self.safe_click(actions[0], label)

    def install_product_image_viewer_guard(self, page) -> bool:
        """Hide Douyin's full-screen product image before it can intercept payment."""
        try:
            result = page.evaluate(
                """
                ({ selector, styleId }) => {
                  let style = document.getElementById(styleId);
                  if (!style) {
                    style = document.createElement('style');
                    style.id = styleId;
                    style.textContent = `${selector} { display: none !important; visibility: hidden !important; opacity: 0 !important; pointer-events: none !important; }`;
                    (document.head || document.documentElement).appendChild(style);
                  }
                  const nodes = Array.from(document.querySelectorAll(selector));
                  for (const node of nodes) {
                    node.style.setProperty('display', 'none', 'important');
                    node.style.setProperty('visibility', 'hidden', 'important');
                    node.style.setProperty('opacity', '0', 'important');
                    node.style.setProperty('pointer-events', 'none', 'important');
                  }
                  return { found: nodes.length > 0, count: nodes.length };
                }
                """,
                {
                    "selector": PRODUCT_IMAGE_VIEWER_SELECTOR,
                    "styleId": PRODUCT_IMAGE_VIEWER_GUARD_ID,
                },
            )
            return bool(result and result.get("found"))
        except Exception:
            return False

    def dismiss_product_image_viewer(self, page) -> bool:
        """Close the known image overlay and keep it suppressed for the current page."""
        found = self.install_product_image_viewer_guard(page)
        if not found:
            return False

        self.log(
            "warn",
            "检测到抖音商品大图遮罩（rpiGKCVd），正在自动关闭后再支付",
        )
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        self.wait_ms(min(180, self.config.order_step_delay_ms))
        self.install_product_image_viewer_guard(page)
        self.log("info", "商品大图遮罩已自动抑制，继续检查支付按钮")
        return True

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

    def click_visible_text_element(
        self,
        page,
        pattern: re.Pattern,
        label: str,
        allow_dry_run_click: bool = False,
    ) -> bool:
        for candidate in self.visible_text_element_candidates(page, pattern):
            self.assert_running()
            try:
                text = candidate["text"]
                box = candidate["box"]
                if self.config.dry_run and not allow_dry_run_click:
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
        self.click_first_visible(candidates, "dismiss overlay", allow_dry_run_click=True)

    def has_visible_all_products_entry(self, page) -> bool:
        entry = page.locator("button, a, [role=button], div, span").filter(
            has_text=ALL_PRODUCTS_TEXT_RE
        ).first
        try:
            return entry.is_visible(timeout=120)
        except Exception:
            return False

    def has_visible_product_list(self, page) -> bool:
        candidates = [
            page.locator('[data-e2e="promotion-title"], [data-e2e="shop-buyBtn"], [data-e2e="price-Area"]'),
            page.locator('[role="dialog"] li, [role="dialog"] [class*="product" i], [role="dialog"] [class*="goods" i]'),
            page.locator("li").filter(
                has=page.get_by_role("button", name=BUY_TEXT_RE)
            ),
        ]
        for candidate in candidates:
            try:
                if candidate.count() == 0:
                    continue
                if candidate.first.is_visible(timeout=120):
                    return True
            except Exception:
                continue
        return False

    def open_commerce_panel(self, page, force: bool = False) -> bool:
        now_ms = time.time() * 1000
        if not force and now_ms - self.state["last_panel_open_at"] < self.config.open_panel_interval_ms:
            return False
        self.state["last_panel_open_at"] = now_ms

        if self.click_visible_text_element(
            page, ALL_PRODUCTS_TEXT_RE, "open all products tab", allow_dry_run_click=True
        ):
            self.wait_ms(self.config.order_step_delay_ms)
            return True

        if self.click_visible_text_element(
            page,
            COMMERCE_PANEL_TEXT_RE,
            "open live commerce panel",
            allow_dry_run_click=True,
        ):
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
        opened = self.click_first_visible(
            candidates, "open live commerce panel", allow_dry_run_click=True
        )
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
            container.locator('[data-e2e="shop-buyBtn"]').filter(has_text=BUY_ACTION_TEXT_RE),
            container.get_by_role("button", name=BUY_ACTION_TEXT_RE).first,
            container.get_by_role("link", name=BUY_ACTION_TEXT_RE).first,
            container.locator("button, a, [role=button]").filter(
                has_text=BUY_ACTION_TEXT_RE
            ).first,
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

    def lock_waiting_product(self, page, node) -> bool:
        for container in self.find_tight_product_containers(node):
            handle = container["locator"].element_handle(timeout=200)
            if handle:
                self.locked_product = (container["locator"], handle)
                self.log("info", "已提前锁定目标商品，监听开售按钮文字和可点击状态变化")
                return True
        return False

    def watch_locked_product(self, page) -> Optional[bool]:
        """Wait on the already matched card, avoiding full-page rescans while waiting."""
        if not self.locked_product:
            return None
        locator, handle = self.locked_product
        try:
            if not handle.evaluate("el => el.isConnected && el.getBoundingClientRect().height > 0"):
                self.locked_product = None
                return None
            ready = page.evaluate("""el => new Promise(resolve => {
                let timer, fallback, observer;
                const finish = value => {
                    observer?.disconnect(); clearTimeout(timer); clearInterval(fallback);
                    resolve(value);
                };
                const check = () => {
                    if (!el.isConnected) return finish({ detached: true });
                    if (/售罄|抢光|缺货/.test(el.innerText || '')) return;
                    const available = Array.from(el.querySelectorAll('button,a,[role=button],[data-e2e="shop-buyBtn"]')).some(btn => {
                        const r = btn.getBoundingClientRect();
                        return /^(立即购买|去抢购|立即抢|马上抢|抢购|去购买|购买|下单)$/.test((btn.innerText||'').trim()) &&
                            !btn.disabled && btn.getAttribute('aria-disabled') !== 'true' &&
                            !/disabled|unavailable/i.test(btn.className) && r.width > 0 && r.height > 0 &&
                            getComputedStyle(btn).visibility !== 'hidden' &&
                            getComputedStyle(btn).pointerEvents !== 'none';
                    });
                    if (available) finish({ ready: true });
                };
                observer = new MutationObserver(check);
                observer.observe(el.parentNode || el, {
                    subtree: true, childList: true, characterData: true, attributes: true
                });
                timer = setTimeout(() => finish(null), 250);
                fallback = setInterval(check, 50);
                check();
            })""", handle)
            if not ready:
                return False
            if ready.get("detached"):
                self.locked_product = None
                return None
            text = locator.inner_text(timeout=200)
            if not is_likely_list_product_card_text(text, self.config):
                self.locked_product = None
                return None
            self.log("info", "锁定商品已开放购买，立即执行购买")
            if self.find_and_click_buy_action(page, locator, "locked product buy button"):
                self.locked_product = None
                self.purchase_started = not self.config.dry_run
                self.purchase_started_at = time.monotonic()
                self.state["options_verified"] = False
                return self.advance_order_flow(page)
            return False
        except GracefulStop:
            raise
        except Exception:
            if self.purchase_started:
                raise GracefulStop("购买动作后页面状态异常，已停止重复点击；请核对订单状态")
            self.locked_product = None
            return None

    def set_purchase_quantity(self, page) -> None:
        if self.config.mode != "batch":
            return
        inputs = page.locator('input[type="number"], input[role="spinbutton"]')
        for i in range(min(inputs.count(), 8)):
            control = inputs.nth(i)
            if not control.is_visible():
                continue
            if self.config.dry_run:
                self.log("info", f"DRY_RUN 将设置每单数量：{self.config.buy_quantity}")
                return
            if control.input_value() != str(self.config.buy_quantity):
                control.fill(str(self.config.buy_quantity))
                control.press("Tab")
                self.wait_ms(self.config.order_step_delay_ms)
            if control.input_value() != str(self.config.buy_quantity) or not control.evaluate("el => el.checkValidity()"):
                raise GracefulStop("数量未设置成功，已停止批量下单")
            return
        if self.config.buy_quantity != 1:
            raise GracefulStop("未找到可验证的数量输入框，已停止批量下单")

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

    def find_hidden_price_containers(self, node) -> list[dict]:
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
            if not is_hidden_price_target_card_text(text, self.config):
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

    def select_product_options(self, page, required: bool = False) -> bool:
        if not self.config.multi_option_enabled:
            return True
        requested = parse_product_options(self.config.option_names)
        if not requested:
            raise GracefulStop("已停止：多选项功能已开启，但没有填写选项名称")
        controls = page.locator(PRODUCT_OPTION_SELECTOR)
        records = controls.evaluate_all("""els => els.slice(0,500).map((el,index) => ({
            index, text: (el.innerText || '').trim(),
            group: (el.closest('.YTFcT_zp')?.querySelector('.R_G6ohly')?.innerText || '').trim(),
            visible: el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0 &&
                getComputedStyle(el).visibility !== 'hidden'
        })).filter(data => data.visible)""")
        if not records:
            if self.state["options_verified"]:
                return True
            if required:
                self.save_diagnostics(page, "product-options-missing")
                raise GracefulStop("已停止：进入支付页但未确认指定规格选中，未点击支付")
            return False

        # Scope all matching to verified SKU option controls. Never search page-wide numbers.
        def details(action):
            return action.evaluate("""el => ({
                text: (el.innerText || '').trim(),
                group: (el.closest('.YTFcT_zp')?.querySelector('.R_G6ohly')?.innerText || '').trim(),
                selected: el.classList.contains('vZSOutR4') || el.getAttribute('aria-selected') === 'true',
                disabled: el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled') ||
                    /disabled|soldout|unavailable/i.test(el.className) || getComputedStyle(el).pointerEvents === 'none'
            })""")

        chosen = []
        used_groups = set()
        available_groups = {data["group"] for data in records}
        for group, name in requested:
            matches = [(controls.nth(data["index"]), data) for data in records
                       if data["text"] == name and (not group or data["group"] == group)]
            if len(matches) != 1:
                self.save_diagnostics(page, "product-option-not-unique")
                raise GracefulStop(f"已停止：规格“{group + '=' if group else ''}{name}”未找到或有重名，请填写规格组=选项名称")
            action, data = matches[0]
            data = details(action)
            if data["text"] != name or (group and data["group"] != group):
                raise GracefulStop("规格内容在选择时发生变化，已停止支付")
            if data["group"] in used_groups:
                raise GracefulStop("已停止：同一规格组只能选择一个选项；多选项填写的是不同规格组的组合")
            used_groups.add(data["group"])
            if data["disabled"]:
                raise GracefulStop(f"已停止：指定规格“{name}”不可选择或已售罄")
            if not data["selected"]:
                self.safe_click(action, f"select product option: {name}")
                self.wait_ms(max(150, self.config.order_step_delay_ms))
            chosen.append((action, name))

        if used_groups != available_groups:
            raise GracefulStop("已停止：请为每个规格组填写一个选项名称，未点击支付")

        if self.config.dry_run:
            self.log("info", "DRY_RUN 已匹配指定规格，未实际选择或支付")
            return False
        # Recheck all choices after every click; selecting another option can deselect earlier ones.
        deadline = time.monotonic() + 2
        while not all(details(action)["selected"] for action, _ in chosen):
            self.assert_running()
            if time.monotonic() >= deadline:
                break
            self.wait_ms(80)
        if not all(details(action)["selected"] for action, _ in chosen):
            self.save_diagnostics(page, "product-option-unconfirmed")
            raise GracefulStop("已停止：点击后未确认指定规格全部选中，未点击支付")
        if not self.state["options_verified"]:
            self.log("info", "指定商品规格已全部确认选中：" + " | ".join(name for _, name in chosen))
        self.state["options_verified"] = True
        return True

    def submit_payment_then_abandon(self, page) -> bool:
        self.assert_running()
        if self.state["order_submitted"]:
            return True
        # The checkout can be ready underneath Douyin's product-image viewer. Remove
        # that viewer before validating/clicking the real payment control.
        self.dismiss_product_image_viewer(page)
        payment_button_visible = bool(
            self.safe_exact_action_buttons(page, PAYMENT_ACTION_TEXT_RE)
        )
        if not payment_button_visible:
            return False

        if not self.select_product_options(page, required=True):
            return False
        self.set_purchase_quantity(page)
        if self.config.strict_price_match and self.config.target_price > 0:
            expected = self.config.target_price * (self.config.buy_quantity if self.config.mode == "batch" else 1)
            deadline = time.monotonic() + 2
            while True:
                self.assert_running()
                actions = self.safe_exact_action_buttons(page, PAYMENT_ACTION_TEXT_RE)
                payment_text = actions[0].inner_text(timeout=300) if actions else ""
                if exact_target_price_matches(payment_text, replace(self.config, target_price=expected)):
                    break
                if time.monotonic() >= deadline:
                    break
                self.wait_ms(80)
            if not exact_target_price_matches(payment_text, replace(self.config, target_price=expected)):
                self.save_diagnostics(page, "selected-option-price-mismatch")
                raise GracefulStop(
                    f"已停止：结算支付金额“{compact_text(payment_text)}”"
                    f"与预期支付金额 {expected:g} 不一致，未点击支付"
                )

        if not self.config.auto_pay and not self.config.submit_payment_and_abandon:
            self.log("info", "检测到支付页，已按配置停在支付前")
            return True

        if self.config.auto_pay:
            self.log("warn", "检测到支付页，自动支付已开启，准备点击“立即支付”")
        else:
            self.log("info", "检测到支付页，开始点击支付按钮以提交订单")
        clicked_payment = self.click_safe_exact_action_button(
            page, PAYMENT_ACTION_TEXT_RE, "submit payment / create order"
        )
        if not clicked_payment:
            self.log("warn", "已进入支付页，但未找到可点击的支付按钮")
            return False

        self.state["payment_clicks"] += 1
        self.state["order_submitted"] = True
        self.wait_ms(self.config.order_step_delay_ms)

        if self.config.auto_pay:
            self.log(
                "warn",
                "已自动点击“立即支付”。如平台要求支付密码、验证码、扫码或其他安全验证，"
                "仍需按平台要求完成，程序不会绕过安全验证",
            )
            return True

        self.state["pending_payment_review"] = False
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

        step = 1
        deadline = time.monotonic() + 5
        while step <= self.config.max_order_steps and time.monotonic() < deadline:
            self.assert_running()
            self.select_product_options(page)
            if self.submit_payment_then_abandon(page):
                return True
            try:
                body_text = page.locator("body").inner_text(timeout=300)
            except Exception:
                body_text = ""
            if is_checkout_context_text(body_text):
                self.wait_ms(35)
                continue
            self.dismiss_blocking_overlays(page)
            candidates = [
                page.get_by_role("button", name=ORDER_STEP_TEXT_RE),
                page.get_by_role("link", name=ORDER_STEP_TEXT_RE),
                page.locator("button, a, [role=button]").filter(
                    has_text=ORDER_STEP_TEXT_RE
                ),
            ]
            advanced = self.click_first_visible(candidates, f"order flow step {step}")
            if not advanced:
                self.wait_ms(35)
                continue
            step += 1
            self.wait_ms(min(80, self.config.order_step_delay_ms))

        return self.submit_payment_then_abandon(page)

    def scan_dom_and_order(self, page) -> bool:
        self.state["scans"] += 1
        if self.purchase_started:
            if time.monotonic() - self.purchase_started_at > 15:
                raise GracefulStop("购买后结算页超时，已停止重复购买；请核对订单状态")
            return self.advance_order_flow(page)
        watched = self.watch_locked_product(page)
        if watched is not None:
            return watched
        # Install before any product click so the known viewer is hidden immediately.
        self.install_product_image_viewer_guard(page)
        agreement = page.get_by_text(USER_AGREEMENT_TITLE_RE)
        try:
            agreement_count = min(agreement.count(), 8)
        except Exception:
            agreement_count = 0
        for index in range(agreement_count):
            try:
                if agreement.nth(index).is_visible(timeout=120):
                    self.save_diagnostics(page, "user-agreement-required")
                    raise UserAgreementRequired(
                        "直播间商品面板要求账号本人确认“使用须知”。"
                        "请在打开的抖音页面中阅读并手动点击一次“同意”，然后重新启动监控。"
                    )
            except UserAgreementRequired:
                raise
            except Exception:
                continue
        verification = page.get_by_text(REAL_NAME_REQUIRED_RE)
        try:
            verification_count = min(verification.count(), 12)
        except Exception:
            verification_count = 0
        for index in range(verification_count):
            try:
                if verification.nth(index).is_visible(timeout=120):
                    self.save_diagnostics(page, "real-name-verification-required")
                    raise AccountVerificationRequired(
                        "抖音当前要求此账号先完成实名认证，自动下单无法继续。"
                        "请在官方抖音 App 内完成实名认证后，再重新登录并测试。"
                    )
            except AccountVerificationRequired:
                raise
            except Exception:
                continue
        self.select_product_options(page)
        if self.submit_payment_then_abandon(page):
            return True
        try:
            checkout_body_text = page.locator("body").inner_text(timeout=300)
        except Exception:
            checkout_body_text = ""
        if is_checkout_context_text(checkout_body_text):
            if not self.state["unsafe_checkout"]:
                self.state["unsafe_checkout"] = True
                self.log(
                    "warn",
                    "已进入订单结算页，但未识别到安全的支付按钮；"
                    "为防止误点商品图片，已停止所有其他点击",
                )
                self.save_diagnostics(page, "checkout-payment-button-not-safe")
            return False
        if self.state["unsafe_checkout"]:
            return False
        self.ensure_all_products_panel(page)

        tokens = keyword_tokens(self.config)
        product_nodes = page.locator(
            "li, [role='dialog'] *, [class*='product' i], [class*='goods' i], "
            "[class*='sku' i], [data-e2e*='product' i], [data-e2e*='goods' i]"
        )
        if tokens:
            token_re = re.compile("|".join(escape_regex(token) for token in tokens), re.I)
            product_nodes = product_nodes.filter(has_text=token_re)

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
            if is_hidden_price_target_card_text(text, self.config):
                for container in self.find_hidden_price_containers(node):
                    actions = [
                        container["locator"].get_by_role(
                            "button", name=REVEAL_PRICE_TEXT_RE
                        ),
                        container["locator"].get_by_role(
                            "link", name=REVEAL_PRICE_TEXT_RE
                        ),
                        container["locator"].locator(
                            "button, a, [role=button]"
                        ).filter(has_text=REVEAL_PRICE_TEXT_RE),
                    ]
                    self.log(
                        "info",
                        "目标商品价格当前被隐藏，正在点击“查看价格”后继续校验："
                        f"{compact_text(container['text'], 220)}",
                    )
                    if self.click_first_visible(
                        actions,
                        "reveal target product price",
                        allow_dry_run_click=True,
                    ):
                        self.wait_ms(max(500, self.config.post_buy_delay_ms))
                        return False
                continue
            if not is_likely_list_product_card_text(text, self.config):
                continue

            self.state["matched_products"] += 1
            self.state["last_match_text"] = compact_text(text, 500)
            action_state = product_action_state(text)
            if action_state == "waiting":
                if self.state["scans"] % 10 == 1:
                    self.log("info", f"目标商品仍在等待开售：{compact_text(text, 180)}")
                if self.lock_waiting_product(page, node):
                    return False
                continue

            if action_state != "ready" or product_unavailable(text) or (
                not self.config.multi_option_enabled and not price_matches(text, self.config)
            ):
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
                    self.purchase_started = not self.config.dry_run
                    self.purchase_started_at = time.monotonic()
                    self.state["options_verified"] = False
                    return self.advance_order_flow(page)
                # A button can already say "立即购买" while still being disabled.
                if self.lock_waiting_product(page, node):
                    return False

        self.scroll_likely_product_lists(page)
        self.log("info", "未在目标商品卡片内找到可点击购买按钮，跳过页面级兜底点击")
        return False

    def active_window_end_local_ms(self, clock_offset_ms: int) -> Optional[float]:
        points = self.config.schedule_windows()
        if not points:
            return None
        now_server = datetime.fromtimestamp(
            (time.time() * 1000 + clock_offset_ms) / 1000,
            tz=AUTOMATION_TIMEZONE,
        )
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
        now_server = datetime.fromtimestamp(
            (time.time() * 1000 + clock_offset_ms) / 1000,
            tz=AUTOMATION_TIMEZONE,
        )
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
        try:
            page.wait_for_timeout(1200)
        except Exception:
            pass
        cookies = self.context.cookies() if self.context else []
        body_text = ""
        try:
            body_text = page.locator("body").inner_text(timeout=2000)
        except Exception:
            pass
        if not has_authenticated_douyin_cookie(cookies) and re.search(
            r"(需先登录|登录后抖音更懂你|扫码登录|验证码登录)", body_text
        ):
            self.save_diagnostics(page, "login-required")
            raise RuntimeError(
                "当前浏览器资料目录没有有效的抖音登录态。"
                "请停止任务，点击“登录抖音”，完成登录后再点击“完成登录并保存”。"
            )
        if has_authenticated_douyin_cookie(cookies):
            self.log("info", "已确认抖音登录态")
        else:
            self.log("warn", "未发现标准登录 Cookie，将继续通过页面状态尝试监控")
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
                except AccountVerificationRequired:
                    raise
                except UserAgreementRequired:
                    raise
                except Exception as exc:
                    self.log("warn", f"扫描异常：{exc}")
                if not ordered and self.locked_product is None:
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
            if self.config.auto_pay and not self.config.dry_run:
                if not self.state["order_submitted"]:
                    raise GracefulStop("尚未提交支付，已停止；请核对当前页面，不能视为购买成功")
                self.wait_for_payment_success(page)
                self.log("info", "平台页面已显示支付成功")
            else:
                self.log("info", "已到达下单流程节点（不代表支付成功）")

    def run_batch_buy(self, page) -> None:
        if not self.config.product_url:
            raise RuntimeError("批量购买模式需要填写商品链接")
        for batch in range(1, self.config.buy_times + 1):
            self.assert_running()
            self.log("info", f"批次 {batch}/{self.config.buy_times}：打开商品链接")
            self.state["options_verified"] = False
            self.state["order_submitted"] = False
            self.state["payment_confirmed"] = False
            self.purchase_started = False
            page.goto(self.config.product_url, wait_until="domcontentloaded", timeout=60000)
            self.install_product_image_viewer_guard(page)
            self.wait_ms(150)
            self.dismiss_blocking_overlays(page)
            if self.config.product_name and not product_matches(
                page.locator("body").inner_text(timeout=1000), self.config
            ):
                raise GracefulStop("商品链接页面未匹配填写的商品关键词，已停止批量下单")
            # Some product URLs open directly into the SKU/checkout panel.
            ready = bool(self.safe_exact_action_buttons(page, PAYMENT_ACTION_TEXT_RE))
            if not ready:
                clicked = self.find_and_click_buy_action(page, page.locator("body"))
                if not clicked:
                    raise GracefulStop(f"批次 {batch} 未找到购买按钮，已停止")
            finished = self.advance_order_flow(page)
            if self.config.dry_run:
                self.log("info", "批量 DRY_RUN 已完成模拟，不创建订单")
            elif not finished:
                raise GracefulStop(f"批次 {batch} 未完成结算，已停止；请核对订单状态")
            elif not self.config.auto_pay:
                self.log("info", "批量任务已停在支付前；请完成当前订单后再启动下一次任务")
                return
            else:
                self.wait_for_payment_success(page)
                self.state["batch_completed"] += 1
                self.log("info", f"平台已确认成功订单：{self.state['batch_completed']}/{self.config.buy_times}")
            if batch < self.config.buy_times:
                self.wait_ms(self.config.batch_interval_ms)
        self.log("info", f"批量购买流程结束，平台确认成功 {self.state['batch_completed']} 单")

    def wait_for_payment_success(self, page) -> None:
        deadline = time.monotonic() + 15
        success = re.compile(r"^\s*(?:支付成功|订单支付成功)[！!。\s]*$")
        while time.monotonic() < deadline:
            self.assert_running()
            entries = page.get_by_text(success)
            for i in range(min(entries.count(), 8)):
                if entries.nth(i).is_visible():
                    self.state["pending_payment_review"] = False
                    self.state["payment_confirmed"] = True
                    return
            self.wait_ms(100)
        self.state["pending_payment_review"] = True
        self.save_diagnostics(page, "payment-unconfirmed")
        raise GracefulStop("当前单未收到平台支付成功提示，已停止后续操作；请核对订单或完成支付验证，勿直接重复下单")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DY 直播间助手")
        self.geometry("1180x860")
        self.minsize(980, 720)
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.runner: Optional[AutomationRunner] = None
        self.login_session: Optional[LoginSession] = None
        self.login_finish_event = threading.Event()
        self.closing = False
        self.log_line_count = 0
        self.vars: dict[str, tk.Variable] = {}
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self.drain_logs)

    def add_field(self, parent, row: int, label: str, name: str, default: str, width: int = 48) -> None:
        hints = {
            "live_url": "粘贴直播间地址，例如 https://live.douyin.com/…",
            "product_name": "填写商品名称中的关键文字；至少填写名称或编号一项。",
            "product_id": "直播间商品列表中的编号，不是规格名称。不确定可以留空。",
            "target_price": "填写想购买的金额（元）；多规格商品填写选中规格的价格。",
            "schedule_windows": "留空立即监控。定时示例：12:00:00，多个时间用逗号分隔，最多 4 个。",
            "product_url": "填写商品详情链接；每一单都会重新打开此商品。",
            "batch_product_name": "用于核对链接打开的商品，可留空。",
            "batch_target_price": "每件商品的价格（元）。支付总额按单价 × 每单数量核对。",
            "buy_times": "需要创建多少笔订单，例如 3 表示连续下 3 单。",
            "buy_quantity": "每笔订单购买几件，与订单数是两个不同的设置。",
            "batch_interval_ms": "两单之间的等待时间；800 毫秒 = 0.8 秒。",
            "poll_ms": "未锁定商品时的扫描间隔，默认 35 毫秒。锁定后改为监听按钮变化。",
            "monitor_duration_ms": "每个监控窗口的时长；1800000 毫秒 = 30 分钟。",
            "prewarm_ms": "比设置的监控时间提前开始检查；1000 毫秒 = 提前 1 秒。",
            "click_timeout_ms": "等待单次点击完成的最长时间，默认 450 毫秒。",
            "open_panel_interval_ms": "尝试打开商品列表的最短间隔，默认 180 毫秒。",
            "profile_dir": "保存登录状态的浏览器资料目录；通常无需修改。两个模块共用。",
            "option_names": "例如 补价=12；不同规格组用 | 分隔：颜色=粉色 | 尺寸=大号。",
        }
        ttk.Label(parent, text=label).grid(row=row*2, column=0, sticky="w", padx=(0,12), pady=(10,2))
        var = tk.StringVar(value=default)
        self.vars[name] = var
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(
            row=row*2, column=1, sticky="ew", pady=(10,2)
        )
        if name in hints:
            hint = ttk.Label(parent, text=hints[name], style="Hint.TLabel", wraplength=260)
            hint.grid(
                row=row*2+1, column=1, sticky="w", pady=(0,4)
            )
            entry.bind("<Configure>", lambda event: hint.configure(wraplength=max(140,event.width)))

    def scroll_panel(self, parent):
        shell = ttk.Frame(parent)
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, background="#f4f6fa", highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=bar.set)
        body = ttk.Frame(canvas, padding=16)
        window = canvas.create_window((0,0), window=body, anchor="nw")
        body.columnconfigure(1, weight=1)
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        def wheel(event):
            canvas.yview_scroll(-int(event.delta / 120), "units")
            return "break"
        # Bind only descendants of this panel, leaving the log's scrolling independent.
        body.bind("<MouseWheel>", wheel)
        self.after_idle(lambda: self.bind_panel_wheel(body, wheel))
        return body

    def bind_panel_wheel(self, widget, callback):
        widget.bind("<MouseWheel>", callback)
        for child in widget.winfo_children():
            self.bind_panel_wheel(child, callback)

    def add_check(self, parent, row: int, col: int, label: str, name: str, default: bool) -> None:
        var = tk.BooleanVar(value=default)
        self.vars[name] = var
        ttk.Checkbutton(parent, text=label, variable=var).grid(
            row=row, column=col, sticky="w", padx=8, pady=4
        )

    def build_ui(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        self.configure(background="#f4f6fa")
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#f4f6fa", foreground="#263449")
        style.configure("TEntry", fieldbackground="#ffffff", padding=5)
        style.configure("TButton", padding=(12,8))
        style.configure("TCheckbutton", padding=(0,4))
        style.configure("TNotebook", tabmargins=(0,4,0,0))
        style.configure("TNotebook.Tab", padding=(18,10))
        style.map("TNotebook.Tab", background=[("selected","#ffffff")],
                  foreground=[("selected","#245fd6")])
        style.configure("Hint.TLabel", foreground="#65758b", font=("Microsoft YaHei UI",9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI",20,"bold"), foreground="#142c50")
        style.configure("Section.TLabel", font=("Microsoft YaHei UI",11,"bold"))
        style.configure("Primary.TButton", background="#2865da", foreground="white")
        style.map("Primary.TButton", background=[("active","#1d50b8"),("disabled","#a5bce7")])
        root = ttk.Frame(self, padding=20)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=6)
        root.columnconfigure(1, weight=4)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0,column=0,columnspan=2,sticky="ew",pady=(0,16))
        ttk.Label(header,text="DY 直播间助手",style="Title.TLabel").pack(anchor="w")
        ttk.Label(header,text="① 登录并保存账号    →    ② 选择任务、填写商品    →    ③ 先测试，再开始下单",
                  style="Hint.TLabel").pack(anchor="w",pady=(6,0))

        left = ttk.Frame(root)
        left.grid(row=1,column=0,sticky="nsew",padx=(0,16))
        panel = self.scroll_panel(left)
        panel.columnconfigure(0,weight=1)
        self.task_tabs = ttk.Notebook(panel)
        self.task_tabs.grid(row=0,column=0,columnspan=2,sticky="ew")
        form = ttk.Frame(self.task_tabs,padding=16)
        batch = ttk.Frame(self.task_tabs,padding=16)
        self.task_tabs.add(form,text="日常秒杀监控")
        self.task_tabs.add(batch,text="批量下单")
        form.columnconfigure(1,weight=1)
        batch.columnconfigure(1,weight=1)
        self.add_field(form,0,"直播间链接","live_url",DEFAULT_LIVE_URL,32)
        self.add_field(form,1,"商品关键词","product_name",DEFAULT_PRODUCT_NAME,32)
        self.add_field(form,2,"商品编号","product_id","2",16)
        self.add_field(form,3,"目标金额（元）","target_price","6",16)
        self.add_field(form,4,"监控时间点","schedule_windows","",24)
        ttk.Label(form,text="开售前即可启动：提前锁定商品，按钮开放后进入购买流程。",
                  style="Hint.TLabel",wraplength=450).grid(row=10,column=0,columnspan=2,sticky="w",pady=(10,0))
        self.add_field(batch,0,"商品详情链接","product_url","",32)
        self.add_field(batch,1,"商品关键词","batch_product_name","",32)
        self.add_field(batch,2,"目标单价（元）","batch_target_price","6",16)
        self.add_field(batch,3,"订单数（笔）","buy_times","1",16)
        self.add_field(batch,4,"每单数量（件）","buy_quantity","1",16)
        self.add_field(batch,5,"两单间隔（毫秒）","batch_interval_ms","800",16)
        ttk.Label(batch,text="确认当前单支付成功后继续下一单；未开启自动支付时停在第一单支付前。",
                  style="Hint.TLabel",wraplength=450).grid(row=12,column=0,columnspan=2,sticky="w",pady=(10,0))

        rules = ttk.LabelFrame(panel,text="下单方式 · 两个模块共用",padding=14)
        rules.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(16,0))
        rules.columnconfigure(0,weight=1)
        self.add_check(rules,0,0,"只测试，不下单（DRY_RUN）","dry_run",True)
        ttk.Label(rules,text="先检查商品匹配；关闭后才会执行真实购买。",
                  style="Hint.TLabel").grid(row=1,column=0,sticky="w",padx=8)
        self.add_check(rules,2,0,"严格核对目标价格","strict_price_match",True)
        self.add_check(rules,3,0,"进入结算页后自动点击支付","auto_pay",False)
        ttk.Label(rules,text="开启后可能直接扣款；关闭时停在支付前。",
                  style="Hint.TLabel").grid(row=4,column=0,sticky="w",padx=8)

        sku = ttk.LabelFrame(panel,text="商品规格 · 需要选款式时开启",padding=14)
        sku.grid(row=2,column=0,columnspan=2,sticky="ew",pady=(16,0))
        sku.columnconfigure(1,weight=1)
        self.add_check(sku,0,0,"启用商品多选项","multi_option_enabled",False)
        # A separate field frame prevents the checkbox row from colliding with helper text.
        names = ttk.Frame(sku)
        names.grid(row=1,column=0,columnspan=2,sticky="ew")
        names.columnconfigure(1,weight=1)
        self.add_field(names,0,"选项名称","option_names","",28)
        self.option_entry = next(w for w in names.winfo_children() if isinstance(w,ttk.Entry))

        self.side_tabs = ttk.Notebook(root)
        self.side_tabs.grid(row=1,column=1,sticky="nsew")
        logs = ttk.Frame(self.side_tabs,padding=12)
        guide = ttk.Frame(self.side_tabs)
        advanced = ttk.Frame(self.side_tabs)
        self.side_tabs.add(logs,text="运行日志")
        self.side_tabs.add(guide,text="使用指南")
        self.side_tabs.add(advanced,text="高级设置")
        logs.columnconfigure(0,weight=1)
        logs.rowconfigure(1,weight=1)
        ttk.Label(logs,text="任务进度与需要处理的提示",style="Section.TLabel").grid(row=0,column=0,sticky="w",pady=(0,12))
        self.log_text = tk.Text(logs,wrap="word",height=20,width=36,font=("Microsoft YaHei UI",9),
                                background="#ffffff",foreground="#334155",relief="flat",padx=12,pady=12)
        self.log_text.grid(row=1,column=0,sticky="nsew")
        bar = ttk.Scrollbar(logs,orient="vertical",command=self.log_text.yview)
        bar.grid(row=1,column=1,sticky="ns")
        self.log_text.configure(yscrollcommand=bar.set)
        self.log_text.tag_configure("warn",foreground="#996500")
        self.log_text.tag_configure("error",foreground="#c33232")
        tools = ttk.Frame(logs)
        tools.grid(row=2,column=0,columnspan=2,sticky="ew",pady=(10,0))
        ttk.Button(tools,text="清空日志",command=self.clear_logs).pack(side="left")
        ttk.Button(tools,text="诊断文件",command=self.open_diagnostics).pack(side="left",padx=(8,0))
        help_body = self.scroll_panel(guide)
        for i,(title,detail) in enumerate([
            ("先登录账号","点击底部“登录抖音”，在浏览器完成登录，再点击“完成登录并保存”。"),
            ("日常秒杀监控","填写直播间、商品和金额，开售前启动。监控时间留空立即等待开售；定时任务会提前预热。"),
            ("批量下单","订单数是下几单；每单数量是每笔买几件。默认每单支付成功后继续。"),
            ("先运行测试","默认勾选“只测试，不下单”。测试匹配正确后关闭此项，再启动真实下单。"),
            ("商品多选项","每个规格组填一个选项。示例：补价=12，或 颜色=粉色 | 尺寸=大号。"),
            ("价格怎么填写","秒杀填写目标金额；批量填写单价。多选项在选中后核对金额，批量按单价乘数量核对。"),
            ("等待开售与支付","锁定商品后监听按钮变化。支付按钮点击不等于支付成功，验证码等提示需要在浏览器处理。"),
            ("高级设置与单位","默认参数通常无需调整。1000 毫秒等于 1 秒；调低轮询无法改变平台更新速度。"),
        ]):
            ttk.Label(help_body,text=title,style="Section.TLabel").grid(row=i*2,column=0,columnspan=2,sticky="w",pady=(12,4))
            ttk.Label(help_body,text=detail,style="Hint.TLabel",wraplength=260).grid(row=i*2+1,column=0,columnspan=2,sticky="w")
        adv = self.scroll_panel(advanced)
        for row,(label,name,default) in enumerate([
            ("扫描间隔（毫秒）","poll_ms","35"),
            ("监控时长（毫秒）","monitor_duration_ms","1800000"),
            ("提前预热（毫秒）","prewarm_ms","1000"),
            ("点击超时（毫秒）","click_timeout_ms","450"),
            ("面板间隔（毫秒）","open_panel_interval_ms","180"),
            ("登录资料目录","profile_dir",str(DEFAULT_PROFILE_DIR)),
        ]):
            self.add_field(adv,row,label,name,default,18)
        switches = ttk.LabelFrame(adv,text="浏览器与诊断",padding=8)
        switches.grid(row=12,column=0,columnspan=2,sticky="ew",pady=(16,0))
        self.add_check(switches,0,0,"不显示浏览器窗口（无头模式）","headless",False)
        self.add_check(switches,1,0,"任务成功后关闭浏览器","close_browser_on_finish",False)
        self.add_check(switches,2,0,"允许点击预约 / 开售提醒","allow_reservation_click",False)
        self.add_check(switches,3,0,"保存截图和页面，便于排查问题","save_diagnostics",True)

        footer = ttk.Frame(root)
        footer.grid(row=2,column=0,columnspan=2,sticky="ew",pady=(18,0))
        self.login_button = ttk.Button(footer,text="1. 登录抖音",command=self.start_login)
        self.login_button.pack(side="left")
        self.finish_login_button = ttk.Button(footer,text="完成登录并保存",command=self.finish_login,state="disabled")
        self.finish_login_button.pack(side="left",padx=8)
        self.start_button = ttk.Button(footer,text="开始秒杀监控",style="Primary.TButton",command=self.start_task)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(footer,text="停止任务",command=self.stop_task,state="disabled")
        self.stop_button.pack(side="right",padx=8)
        self.mode_caption = tk.StringVar()
        ttk.Label(footer,textvariable=self.mode_caption,style="Hint.TLabel").pack(side="right",padx=12)
        def refresh(*_):
            batch_selected = self.task_tabs.index(self.task_tabs.select()) == 1
            selected_form = batch if batch_selected else form
            self.task_tabs.configure(height=selected_form.winfo_reqheight())
            def fit_tab():
                if self.task_tabs.select() == str(selected_form):
                    self.task_tabs.configure(height=max(
                        w.winfo_y()+w.winfo_height() for w in selected_form.winfo_children()
                    ) + 16)
            self.after(100, fit_tab)
            self.start_button.configure(text="开始批量下单" if batch_selected else "开始秒杀监控")
            mode = "测试模式" if self.vars["dry_run"].get() else "真实下单"
            pay = "自动支付" if self.vars["auto_pay"].get() else "停在支付前"
            self.mode_caption.set(mode + " · " + pay)
            self.option_entry.configure(state="normal" if self.vars["multi_option_enabled"].get() else "disabled")
        self.task_tabs.bind("<<NotebookTabChanged>>",refresh)
        for name in ("dry_run","auto_pay","multi_option_enabled"):
            self.vars[name].trace_add("write",refresh)
        refresh()

    def config_from_form(self) -> AutomationConfig:
        get = lambda name: self.vars[name].get()
        batch = self.task_tabs.index(self.task_tabs.select()) == 1
        return AutomationConfig(
            mode="batch" if batch else "flash",
            product_url=str(get("product_url")).strip(),
            buy_times=as_int(get("buy_times"), 1, 1),
            buy_quantity=as_int(get("buy_quantity"), 1, 1),
            batch_interval_ms=as_int(get("batch_interval_ms"), 800, 100),
            live_url=str(get("live_url")).strip() or DEFAULT_LIVE_URL,
            product_name=str(get("batch_product_name" if batch else "product_name")).strip(),
            product_id="" if batch else str(get("product_id")).strip(),
            target_price=as_float(get("batch_target_price" if batch else "target_price"), 0, 0),
            poll_ms=as_int(get("poll_ms"), 35, 20),
            monitor_duration_ms=as_int(get("monitor_duration_ms"), 1800000, 1000),
            prewarm_ms=as_int(get("prewarm_ms"), 1000, 0),
            click_timeout_ms=as_int(get("click_timeout_ms"), 450, 100),
            open_panel_interval_ms=as_int(get("open_panel_interval_ms"), 180, 50),
            schedule_windows_raw="" if batch else str(get("schedule_windows")).strip(),
            profile_dir=str(get("profile_dir")).strip() or str(DEFAULT_PROFILE_DIR),
            strict_price_match=bool(get("strict_price_match")),
            dry_run=bool(get("dry_run")),
            headless=bool(get("headless")),
            close_browser_on_finish=bool(get("close_browser_on_finish")),
            auto_pay=bool(get("auto_pay")),
            multi_option_enabled=bool(get("multi_option_enabled")),
            option_names=str(get("option_names")).strip(),
            submit_payment_and_abandon=False,
            allow_reservation_click=bool(get("allow_reservation_click")),
            save_diagnostics=bool(get("save_diagnostics")),
        )

    def log(self, level: str, message: str) -> None:
        self.log_queue.put((level, message))

    def drain_logs(self) -> None:
        try:
            while True:
                callback = self.ui_queue.get_nowait()
                callback()
        except queue.Empty:
            pass
        try:
            while True:
                level, message = self.log_queue.get_nowait()
                self.log_text.insert("end", f"[{now_text()}] {level.upper()} {message}\n", level)
                self.log_line_count += str(message).count("\n") + 1
                if self.log_line_count > 5000:
                    self.log_text.delete("1.0", "1001.0")
                    self.log_line_count -= 1000
                self.log_text.see("end")
        except queue.Empty:
            pass
        if not self.closing:
            self.after(120, self.drain_logs)

    def reset_controls(self) -> None:
        if self.closing:
            return
        self.start_button.configure(state="normal")
        self.login_button.configure(state="normal")
        self.finish_login_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")

    def start_task(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("任务运行中", "已有任务正在运行")
            return
        config = self.config_from_form()
        if config.mode == "batch":
            for field_name, label in (("buy_times", "订单数"), ("buy_quantity", "每单商品数量")):
                raw = str(self.vars[field_name].get()).strip()
                if not raw.isdigit() or int(raw) < 1:
                    messagebox.showerror("配置错误", f"{label}必须是正整数")
                    return
        if not is_allowed_douyin_url(config.product_url if config.mode == "batch" else config.live_url):
            messagebox.showerror("配置错误", "请填写有效的 https://*.douyin.com 直播间或商品链接")
            return
        if config.mode == "flash" and not config.product_name and not config.product_id:
            messagebox.showerror("配置错误", "请至少填写商品关键词或商品编号")
            return
        if config.strict_price_match and config.target_price <= 0:
            messagebox.showerror("配置错误", "严格价格匹配时必须填写目标价格")
            return
        if config.multi_option_enabled:
            try:
                if not parse_product_options(config.option_names):
                    raise ValueError("请填写选项名称，例如 补价=12")
            except ValueError as exc:
                messagebox.showerror("配置错误", str(exc))
                return
        invalid_times = invalid_schedule_entries(config.schedule_windows_raw)
        if invalid_times:
            messagebox.showerror(
                "配置错误",
                "监控时间格式无效或超过四个：" + "，".join(invalid_times),
            )
            return
        if not config.dry_run:
            warning = "即将启用真实下单操作，脚本会点击购买和提交订单按钮。"
            if config.auto_pay:
                warning += (
                    "\n\n当前已启用“自动点击立即支付”，可能直接产生真实扣款。"
                    "程序不会绕过支付密码、验证码、扫码或其他安全验证。"
                )
            warning += "\n\n请确认商品、价格、收货地址和账号均正确。是否继续？"
            if not messagebox.askyesno("确认真实下单", warning, icon="warning"):
                return
        self.stop_event = threading.Event()
        self.runner = AutomationRunner(config, self.log, self.stop_event)
        self.worker = threading.Thread(target=self.worker_main, daemon=True)
        self.start_button.configure(state="disabled")
        self.login_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.worker.start()

    def worker_main(self) -> None:
        try:
            status = self.runner.run() if self.runner else "stopped"
            self.log("info", f"任务结束：{status}")
        except Exception:
            self.log("error", "任务异常退出")
        finally:
            self.ui_queue.put(self.reset_controls)

    def start_login(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("任务运行中", "请先停止当前任务")
            return
        config = self.config_from_form()
        if not is_allowed_douyin_url(config.live_url):
            messagebox.showerror("配置错误", "直播间链接必须是 https://*.douyin.com 地址")
            return
        self.stop_event = threading.Event()
        self.login_finish_event = threading.Event()
        self.login_session = LoginSession(
            config, self.log, self.stop_event, self.login_finish_event
        )
        self.worker = threading.Thread(target=self.login_worker_main, daemon=True)
        self.start_button.configure(state="disabled")
        self.login_button.configure(state="disabled")
        self.finish_login_button.configure(state="normal")
        self.stop_button.configure(state="normal")
        self.worker.start()

    def login_worker_main(self) -> None:
        try:
            status = self.login_session.run() if self.login_session else "cancelled"
            self.log("info", f"登录流程结束：{status}")
        except Exception:
            self.log("error", "登录流程异常：\n" + traceback.format_exc())
        finally:
            self.ui_queue.put(self.reset_controls)

    def finish_login(self) -> None:
        self.login_finish_event.set()
        self.finish_login_button.configure(state="disabled")
        self.log("info", "正在保存登录态并关闭登录浏览器")

    def stop_task(self) -> None:
        self.stop_event.set()
        self.login_finish_event.set()
        self.log("warn", "收到停止请求，正在中止任务")

    def clear_logs(self) -> None:
        self.log_text.delete("1.0", "end")
        self.log_line_count = 0

    def open_diagnostics(self) -> None:
        path = DEFAULT_DIAGNOSTICS_DIR
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def on_close(self) -> None:
        if self.closing:
            return
        if self.worker and self.worker.is_alive():
            self.closing = True
            self.stop_event.set()
            self.login_finish_event.set()
            self.start_button.configure(state="disabled")
            self.login_button.configure(state="disabled")
            self.finish_login_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.log("warn", "正在安全停止任务并关闭浏览器……")
            self.after(100, self.wait_for_worker_before_close)
            return
        self.destroy()

    def wait_for_worker_before_close(self) -> None:
        if self.worker and self.worker.is_alive():
            self.after(100, self.wait_for_worker_before_close)
            return
        self.destroy()


def main() -> None:
    activation = ActivationDialog(LocalLicenseClient())
    activation.mainloop()
    if not activation.authorized:
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
