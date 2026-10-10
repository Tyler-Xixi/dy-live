# -*- coding: utf-8 -*-
"""Python GUI for the live-room product monitor.

This is a Python/Tkinter port of the existing Node Playwright runner.  It keeps
the same core flow: open live room, open all products, match the target product,
click buy when ready, submit the order to the payment state, then retain the pending-payment page for manual review. Batch orders confirm pending payment
and recover by reload or by reopening the verified live-room product.
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
from network_probe import NetworkProbe, ProbeCancelled
from license_protocol import LicenseStatus
from online_license import OnlineLicenseClient, LicenseTransport
from license_config import LICENSE_ORIGIN, LICENSE_PUBLIC_KEYS
from batch_queue import BatchOutcome, SequentialBatchQueue
from batch_accounts import AccountStore, AccountStoreError
from batch_accounts_ui import AccountManagerDialog

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
PAYMENT_SNAPSHOT_JS = r"""els => els.map((el,index) => {
    const r=el.getBoundingClientRect(), style=getComputedStyle(el);
    const visible=r.width>0&&r.height>0&&style.visibility!=='hidden';
    let stockError='';
    for(let panel=el.parentElement;panel&&panel!==document.body;panel=panel.parentElement){
        const text=panel.innerText||'';
        if(/购买数量|订单留言|优惠明细/.test(text)){
            stockError=(text.match(/商品已抢光[^\n]*|商品已抢完[^\n]*|商品已售罄[^\n]*|库存不足[^\n]*|库存为0[^\n]*|抢购失败[，,:： ]*(?:商品)?已抢完[^\n]*/)||[''])[0];
            break;
        }
    }
    const data={index,visible,text:el.innerText||'',stockError,
        safe:visible&&!el.matches(':disabled')&&!el.closest('[aria-disabled="true"],[disabled],[inert]')&&
            style.pointerEvents!=='none'&&!/(?:^|[\s_-])disabled(?:$|[\s_-])/i.test(el.className||'')&&
            !el.querySelector('img,picture,video,canvas')&&r.width>48&&r.height>20&&r.height<=160&&
            r.width*r.height<=innerWidth*innerHeight*.22};
    data.signature=JSON.stringify(data);
    return data;
}).filter(data=>data.visible)"""
PRODUCT_IMAGE_VIEWER_SELECTOR = "div.rpiGKCVd"
PRODUCT_IMAGE_VIEWER_GUARD_ID = "dyla-product-image-viewer-guard"
PRODUCT_OPTION_SELECTOR = "div.ufz0AqTE"
# The title and full-screen back control are verified in the user's saved HTML.
# Unknown layouts fail closed instead of clicking a QR/image or unrelated button.
DETAIL_ROOT_SELECTOR = '.yGW2Oo6H, .zwBai1Fs, [role="dialog"], section'
MANUAL_VERIFICATION_JS = r"""() => {
    const visible=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';};
    const prompt=/请完成(?:安全)?验证|安全验证|人机验证|滑块验证|拖动.*(?:滑块|拼图)|请按.*(?:顺序|文字).*点击|访问过于频繁|操作过于频繁/;
    for(const el of document.querySelectorAll('iframe,[role="dialog"],[role="alert"],[class*="captcha" i],[id*="captcha" i],[class*="verify" i],[id*="verify" i]')){
        if(!visible(el))continue;
        if(el.tagName==='IFRAME'&&/captcha|verifycenter|verify_center|verification|安全验证|人机验证/i.test(
            (el.getAttribute('src')||'')+' '+(el.getAttribute('title')||'')))return true;
        if(prompt.test(el.innerText||''))return true;
        if(/captcha/i.test((el.id||'')+' '+(el.className||''))&&el.querySelector('canvas,img,[role="slider"]'))return true;
    }
    return false;
}"""
DETAIL_SNAPSHOT_JS = r"""args => {
    const visible=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';};
    const normalize=t=>(t||'').replace(/\s+/g,'').toLowerCase();
    const manualVerification=(""" + MANUAL_VERIFICATION_JS + r""")();
    const roots=[...document.querySelectorAll(args.roots)];
    const candidates=roots.map((root,index)=>({root,index,titles:[...root.querySelectorAll('span.vs9hmvGz,h1,h2,h3')]
        .filter(el=>visible(el)&&!el.closest('li'))})).filter(x=>visible(x.root)&&!x.root.closest('li')&&x.titles.length&&
        (x.root.querySelector(args.payment)||/购买数量|订单留言|扫描二维码|购买此商品/.test(x.root.innerText||'')));
    const panels=candidates.filter(x=>!candidates.some(y=>y!==x&&x.root.contains(y.root)));
    const matches=panels.filter(x=>x.titles.some(el=>normalize(el.innerText)===normalize(args.name)));
    if(matches.length!==1)return {matches:matches.length,panels:panels.length,manualVerification,offline:!navigator.onLine};
    const {root,index}=matches[0];
    const cards=[...document.querySelectorAll('li')];
    const sameCards=cards.filter(el=>normalize(el.innerText).includes(normalize(args.name))&&
        (!args.number||new RegExp('^\\s*'+args.number+'(?:\\s|【|\\[)').test(el.innerText||'')));
    const card=sameCards.length===1?sameCards[0]:null;
    const buy=card?.querySelector('[data-e2e="shop-buyBtn"]');
    const listUnavailable=!!card&&/售罄|抢光|抢完|缺货|已下架|已结束/.test(card.innerText||'');
    const listReady=!!buy&&!/售罄|抢光|抢完|缺货|已下架|已结束/.test(card.innerText||'')&&
        !buy.disabled&&!buy.closest('[disabled],[aria-disabled="true"],[inert]')&&
        !/disabled|unavailable|Yys40cl5/i.test(buy.className||'')&&
        /^(去抢购|立即购买|去购买|购买|下单|抢购|立即抢|马上抢)$/.test((buy.innerText||'').trim());
    const collect=""" + PAYMENT_SNAPSHOT_JS + r""";
    const payments=collect([...root.querySelectorAll(args.payment)]);
    const text=root.innerText||'';
    const quantities=[...root.querySelectorAll('input[type="number"],input[role="spinbutton"]')].filter(visible);
    const quantity=quantities.length===1?Number(quantities[0].value):Number((text.match(/购买数量\s*[-－−]?\s*(\d+)/)||[])[1]);
    const payment=payments.length===1?payments[0]:null;
    const data={matches:1,panels:panels.length,index,payment,paymentCount:payments.length,
        options:[...root.querySelectorAll('.YTFcT_zp')].map(el=>el.innerHTML),
        quantity:Number.isFinite(quantity)?quantity:null,qr:/请打开抖音APP扫描二维码|扫码.*购买此商品/.test(text),
        listReady,listUnavailable,listIndex:card?cards.indexOf(card):-1,
        manualVerification,verification:/请完成验证|安全验证|实名认证|验证码|使用须知/.test(text),offline:!navigator.onLine};
    data.signature=JSON.stringify(data);
    return data;
}"""
DISMISS_TEXT_RE = re.compile(
    r"(我知道了|知道了|允许|稍后再说|以后再说|关闭|继续看播|继续观看|继续看直播)"
)
USER_AGREEMENT_TITLE_RE = re.compile(r"^\s*使用须知\s*$")
UNAVAILABLE_TEXT_RE = re.compile(
    r"(售罄|已售罄|抢光|已抢光|抢完|已抢完|缺货|补货中|已结束|已下架|不可购买|卖光)"
)


DEFAULT_LIVE_URL = "https://live.douyin.com/712159628601"
DEFAULT_PRODUCT_NAME = "【麦序】手作球解压玩具"


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


def resolve_profile_directory(value: str) -> Path:
    """Use the same default and trimming for login and update protection."""
    return Path(str(value).strip() or str(DEFAULT_PROFILE_DIR)).absolute()


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
    def __init__(self, client: LocalLicenseClient, online_client=None) -> None:
        super().__init__()
        self.client = client
        self.online_client = online_client
        self.authorized = False
        self.authorized_mode = "offline"
        self.activation_queue = queue.Queue()
        self.activation_busy = False
        self.activation_generation = 0
        self.activation_timers = set()
        self.mode_path = client.state_path.parent / "activation_mode.json"
        default_mode = "offline" if client.verify_saved() else "online" if online_client else "offline"
        try:
            saved_mode = json.loads(self.mode_path.read_text(encoding="utf-8")).get("mode")
            if saved_mode in ("online", "offline"):
                default_mode = saved_mode
        except (ValueError, OSError):
            pass
        self.mode_var = tk.StringVar(value=default_mode)
        self.title("DY 直播间助手 - 软件激活")
        self.geometry("560x340")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="软件激活", font=("Microsoft YaHei UI", 16, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            frame,
            text="联网卡密首次激活绑定电脑；旧版离线卡密保留原逻辑，不受联网后台控制。",
            wraplength=470,
        ).pack(anchor="w", pady=(8, 14))
        modes = ttk.Frame(frame)
        modes.pack(fill="x", pady=(0, 12))
        ttk.Radiobutton(modes, text="联网激活", value="online", variable=self.mode_var, command=self.change_activation_mode).pack(side="left")
        ttk.Radiobutton(modes, text="旧版离线激活", value="offline", variable=self.mode_var, command=self.change_activation_mode).pack(side="left", padx=18)
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
        ttk.Button(actions, text="重新联网校验", command=self.refresh_saved_online).pack(side="left", padx=8)
        ttk.Button(actions, text="退出", command=self.destroy).pack(side="right")
        self.activation_timers.add(self.after(150, self.verify_existing))
        self.activation_timers.add(self.after(80, self.poll_activation_result))

    def change_activation_mode(self):
        self.activation_generation += 1
        self.activation_busy = False
        self.card_var.set("")
        self.verify_existing()

    def verify_existing(self) -> None:
        if self.activation_busy:
            return
        if self.mode_var.get() == "offline" and self.client.verify_saved():
            self.allow_start()
            return
        if self.mode_var.get() == "online" and self.online_client:
            status = self.online_client.verify_saved()
            if status.allowed:
                self.allow_start()
                return
            if getattr(self.online_client, "credential", None):
                self.refresh_saved_online()
                return
        self.ready_for_key("请输入联网卡密（DYL- 开头）" if self.mode_var.get() == "online" else "请输入旧版离线卡密")

    def refresh_saved_online(self):
        if self.activation_busy: return
        if self.mode_var.get() != "online" or not self.online_client or not getattr(self.online_client, "credential", None):
            self.ready_for_key("请先使用联网卡密完成首次激活")
            return
        self.activation_busy = True
        self.activation_generation += 1
        generation = self.activation_generation
        self.activate_button.configure(state="disabled")
        self.status_var.set("正在校验已保存授权，无需重新输入卡密……")
        def refresh_online():
            try: result = self.online_client.refresh()
            except Exception: result = LicenseStatus(False, "online", "network_unconfirmed")
            self.activation_queue.put((generation, result))
        threading.Thread(target=refresh_online, daemon=True).start()

    def ready_for_key(self, message: str) -> None:
        self.status_var.set(message)
        self.activate_button.configure(state="normal")
        self.card_entry.focus_set()

    def activate(self) -> None:
        if self.activation_busy:
            return
        card_key = self.card_var.get().strip()
        if not card_key:
            self.status_var.set("请输入卡密")
            return
        self.activate_button.configure(state="disabled")
        self.status_var.set("正在验证卡密……")
        if self.mode_var.get() == "online":
            if not self.online_client:
                self.ready_for_key("联网激活未配置")
                return
            if isinstance(self.online_client, OnlineLicenseClient) and not self.online_client.public_keys:
                self.ready_for_key("管理员尚未配置服务器公钥，联网激活暂不可用")
                return
            self.activation_busy = True
            self.activation_generation += 1
            generation = self.activation_generation
            def activate_online():
                try:
                    result = self.online_client.activate(card_key)
                except Exception:
                    result = LicenseStatus(False, "online", "network_unconfirmed")
                self.activation_queue.put((generation, result))
            threading.Thread(target=activate_online, daemon=True).start()
            return
        try:
            self.client.activate(card_key)
            self.allow_start()
        except Exception as exc:
            self.ready_for_key(str(exc))

    def poll_activation_result(self):
        try:
            while True:
                generation, result = self.activation_queue.get_nowait()
                if generation != self.activation_generation or self.mode_var.get() != "online":
                    continue
                self.activation_busy = False
                if result.allowed:
                    self.allow_start()
                    return
                self.ready_for_key(license_status_text(result))
        except queue.Empty:
            pass
        self.activation_timers.add(self.after(80, self.poll_activation_result))

    def allow_start(self) -> None:
        self.authorized_mode = self.mode_var.get()
        try:
            self.mode_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.mode_path.with_suffix(".tmp")
            temp.write_text(json.dumps({"mode": self.authorized_mode}), encoding="utf-8")
            os.replace(temp, self.mode_path)
        except OSError:
            self.ready_for_key("无法保存激活模式，请检查软件数据目录权限")
            return
        self.authorized = True
        self.destroy()

    def destroy(self):
        for timer in getattr(self, "activation_timers", ()):
            try: self.after_cancel(timer)
            except tk.TclError: pass
        super().destroy()


def license_status_text(status):
    return {"activation_required": "需要联网激活", "invalid_card": "联网卡密格式不正确", "network_unconfirmed": "网络或授权响应未确认，请稍后重试",
            "disabled": "卡密已停用", "archived": "卡密已归档", "expired": "卡密已过期，请续期后联网验证",
            "binding_mismatch": "卡密已绑定其他电脑或绑定已变更", "clock_confirmation_required": "系统时间发生回拨，请联网确认",
            "storage_error": "授权保存失败，请检查数据目录权限", "valid": "联网授权有效"}.get(status.reason, "授权状态未确认")


class GracefulStop(Exception):
    pass


class AuthorizationExpired(GracefulStop):
    """Stop new purchases without treating licensing loss as an explicit browser close."""


class CheckoutSoldOut(GracefulStop):
    """Explicit stock loss before submitting an order, not a price mismatch."""
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
    duration_ms: int = 0


@dataclass
class AutomationConfig:
    mode: str = "flash"
    live_url: str = DEFAULT_LIVE_URL
    product_url: str = ""
    product_name: str = DEFAULT_PRODUCT_NAME
    product_id: str = "1"
    target_price: float = 20.0
    dry_run: bool = False
    headless: bool = False
    poll_ms: int = 35
    jitter_ms: int = 0
    monitor_duration_ms: int = 30 * 60 * 1000
    prewarm_ms: int = 1000
    strict_price_match: bool = False
    strict_product_match: bool = False
    allow_reservation_click: bool = False
    open_panel_interval_ms: int = 180
    max_order_steps: int = 8
    post_buy_delay_ms: int = 500
    order_step_delay_ms: int = 300
    click_timeout_ms: int = 450
    multi_option_enabled: bool = False
    option_names: str = ""
    submit_payment_and_abandon: bool = False
    save_diagnostics: bool = False
    max_retries: int = 5
    retry_base_ms: int = 80
    circuit_breaker_429: int = 5
    buy_quantity: int = 1
    buy_times: int = 1
    batch_interval_ms: int = 800
    schedule_windows_raw: str = ""
    continue_after_sold_out: bool = True
    purchase_speed_priority: bool = False
    experimental_purchase_click: bool = False
    detail_lock_mode: bool = True
    detail_refresh_ms: int = 2000
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


def effective_flash_lock_mode(config: AutomationConfig) -> str:
    if config.mode!='flash': return 'none'
    if config.detail_lock_mode: return 'detail'
    if config.experimental_purchase_click: return 'script'
    if config.purchase_speed_priority: return 'normal'
    return 'none'


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
    first_text = re.sub(r"^[-—\s]*(?:已抢光|已抢完|已售罄|售罄|补货中)[-—\s]*", "", first_text)
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

    if config.strict_product_match:
        if config.product_name and normalize_text(config.product_name) not in normalize_text(value):
            return False
        if not product_list_index_matches(value, config):
            return False

    index_matched = product_list_index_matches(value, config)
    keyword_price_fallback = (not index_matched) and exact_target_price_matches(value, config)
    if not index_matched and not keyword_price_fallback:
        return False

    return product_action_state(value) != "unknown" and (
        product_action_state(value) in ("waiting", "unavailable") or config.multi_option_enabled or price_matches(value, config)
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
        try:
            parse_schedule_range(raw)
        except ValueError:
            invalid.append(raw)
    return invalid


def parse_schedule_range(raw: str) -> SchedulePoint:
    parts = re.split(r"\s*(?:-|–|—|~|～|到|至)\s*", raw.strip())
    if len(parts) != 2:
        raise ValueError("请填写开始时间和结束时间")
    seconds = []
    for part in parts:
        match = re.fullmatch(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", part)
        if not match:
            raise ValueError("时间格式错误")
        h, m, s = int(match[1]), int(match[2]), int(match[3] or 0)
        if h > 23 or m > 59 or s > 59:
            raise ValueError("时间超出范围")
        seconds.append(h*3600 + m*60 + s)
    duration = (seconds[1] - seconds[0]) % 86400
    if not 0 < duration <= 3600:
        raise ValueError("每段时长必须大于零且不超过一小时")
    labels = [f"{n//3600:02d}:{n//60%60:02d}:{n%60:02d}" for n in seconds]
    return SchedulePoint('–'.join(labels), seconds[0]//3600, seconds[0]//60%60, seconds[0]%60, duration*1000)


def parse_schedule_windows(value: str) -> list[SchedulePoint]:
    result: list[SchedulePoint] = []
    for raw in schedule_entries(value)[:4]:
        result.append(parse_schedule_range(raw))
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


def clear_douyin_login(context) -> None:
    """Clear local Douyin auth only; never remove profiles or other site data."""
    def douyin_host(host):
        host=str(host or '').lower().lstrip('.')
        return host=='douyin.com' or host.endswith('.douyin.com')
    state=context.storage_state(indexed_db=True)
    origins={'https://douyin.com','https://www.douyin.com','https://live.douyin.com'}
    for entry in state.get('origins',()):
        parsed=urlparse(entry['origin'])
        if parsed.scheme in ('http','https') and douyin_host(parsed.hostname): origins.add(entry['origin'])
    for cookie in state.get('cookies',()):
        if douyin_host(cookie.get('domain')):
            host=cookie['domain'].lstrip('.')
            origins.update(('https://'+host,'http://'+host))
    # Stop live auth scripts and discard sessionStorage before clearing disk data.
    for page in list(context.pages):
        if douyin_host(urlparse(page.url).hostname):
            page.close()
        else:
            # Preserve foreign top-level state; stop only embedded Douyin frames.
            for frame in list(page.frames):
                if douyin_host(urlparse(frame.url).hostname):
                    frame.goto('about:blank',wait_until='commit')
    page=context.new_page(); cdp=context.new_cdp_session(page)
    try:
        for origin in sorted(origins):
            cdp.send('Storage.clearDataForOrigin',{'origin':origin,'storageTypes':'all'})
        context.clear_cookies(domain=re.compile(r'^\.?([^.]+\.)*douyin\.com$',re.IGNORECASE))
        remaining=[cookie for cookie in context.cookies() if douyin_host(cookie.get('domain'))]
        if has_authenticated_douyin_cookie(remaining): raise RuntimeError('当前抖音登录状态未清除，请重试')
    finally:
        cdp.detach(); page.close()


class LoginSession:
    """Open the official site and persist a user-completed login session."""

    def __init__(self, config, log, stop_event, finish_event, *, reset_login=False, on_logout=None, on_ready=None) -> None:
        self.config = config
        self.log = log
        self.stop_event = stop_event
        self.finish_event = finish_event
        self.reset_login=reset_login
        self.logout_event=threading.Event()
        self.on_logout=on_logout or (lambda:None)
        self.on_ready=on_ready or (lambda:None)

    def run(self) -> str:
        from playwright.sync_api import sync_playwright

        Path(self.config.profile_dir).mkdir(parents=True, exist_ok=True)
        playwright = sync_playwright().start()
        context = None
        try:
            context = launch_persistent_browser(
                playwright, self.config.profile_dir, False, self.log
            )
            if self.stop_event.is_set(): return 'cancelled'
            if self.reset_login:
                clear_douyin_login(context)
                self.on_logout()
                self.log('info','当前账号的本地抖音登录状态已清除，请重新登录')
            page = context.new_page() if self.reset_login else (context.pages[0] if context.pages else context.new_page())
            page.goto(
                self.config.live_url or "https://www.douyin.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            self.log("info", "登录浏览器已打开，请在抖音官方页面完成扫码或验证码登录")
            self.log("info", "确认页面已登录后，回到本程序点击“完成登录并保存”")
            self.on_ready()

            while not self.finish_event.wait(0.2):
                if self.stop_event.is_set():
                    return "cancelled"
                if self.logout_event.is_set():
                    clear_douyin_login(context)
                    self.on_logout()
                    page=context.new_page()
                    page.goto(self.config.live_url or 'https://www.douyin.com/',wait_until='domcontentloaded',timeout=60000)
                    self.logout_event.clear()
                    self.log('info','已退出当前账号，请重新扫码登录，再点击“完成登录并保存”')
                    self.on_ready()
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
        license_status=None,
        queue_lifecycle: bool = False,
    ) -> None:
        self.config = config
        self.log_callback = log
        self.log = self.emit_log
        self.stop_event = stop_event
        self.license_status = license_status
        self.queue_lifecycle = queue_lifecycle
        self._batch_outcome = BatchOutcome('stopped', 0, '尚未完成')
        self.playwright = None
        self.context = None
        self.page = None
        self.locked_product = None
        self.prelocked_buy_action = None
        self.purchase_started = False
        self.purchase_started_at = 0.0
        self.stock_state = None
        self.sale_seen = False
        self.monitor_deadline_ms = float('inf')
        self.monitor_started_at = 0.0
        self.monitor_reported_at = float('-inf')
        self.monitor_last_error = None
        self.monitor_scan_error = None
        self.monitor_last_success = None
        self.last_identity_check_at = 0.0
        self.last_unique_check_at = float('-inf')
        self.phase_times = {}
        self.payment_primary_seen = False
        self.last_payment_snapshot = None
        self.detail_entered = False
        self.detail_open_started_at = 0.0
        self.detail_refreshed_at = 0.0
        self.detail_refresh_blocked = False
        self.pending_order_timeout_ms = 5000
        self.preexisting_pending_order_ids = set()
        self.detail_last_list_ready = False
        self.network_queue = queue.Queue(maxsize=128)
        self.network_worker = None
        self.network_stop = threading.Event()
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
            "order_lock_confirmed": False,
            "detail_refreshes": 0,
            "manual_verification_waiting": False,
            "retain_browser": False,
            "last_match_text": "",
            "last_click_label": "",
            "last_panel_open_at": 0.0,
            "last_diagnostic_at": 0.0,
        }

    def batch_outcome(self) -> BatchOutcome:
        return self._batch_outcome

    def assert_running(self) -> None:
        if self.stop_event.is_set():
            raise GracefulStop("任务已由用户停止")
        if not self.state.get('order_submitted') and not self.state.get('payment_clicks'):
            self.assert_purchase_authorized()

    def assert_purchase_authorized(self) -> None:
        if self.license_status is not None:
            status = self.license_status()
            if not status.allowed:
                self.state['retain_browser'] = True
                raise AuthorizationExpired(license_status_text(status) + "；已停止新增购买，浏览器保留供核对")

    def mark_phase(self, name: str) -> None:
        self.phase_times.setdefault(name, time.monotonic())

    def log_phase_timing(self, outcome: str) -> None:
        pairs = (
            ('link_opened', 'confirmed', '打开链接→成功确认'),
            ('sale_detected', 'buy_clicked', '检测开售→购买点击'),
            ('buy_clicked', 'payment_submitted', '购买点击→提交'),
            ('sale_detected', 'confirmed', '检测开售→成功确认'),
            ('sale_detected', 'payment_submitted', '检测可提交→提交点击'),
            ('payment_submitted', 'order_locked', '提交点击→待付款确认'),
            ('link_opened', 'order_locked', '打开链接→待付款确认（含等待开售）'),
            ('sale_detected', 'order_locked', '检测可提交→待付款确认'),
        )
        values = [f'{label} {(self.phase_times[end]-self.phase_times[start])*1000:.0f}ms'
                  for start, end, label in pairs if start in self.phase_times and end in self.phase_times]
        if values:
            self.log('info', outcome + ' | ' + ' | '.join(values))

    def emit_log(self, level: str, message: str) -> None:
        if self.config.mode == 'flash' and level == 'info' and message.startswith((
            '运行模式：', 'DRY_RUN：', '商品关键词：', '商品编号：', '目标价格：',
            '打开直播间：', '已确认抖音登录态', '记录相关响应：', '扫描商品候选节点：',
            '未在目标商品卡片内', '已点击：', '当前窗口结束，未完成目标动作。',
        )):
            return
        self.log_callback(level, message)

    def report_monitor_status(self, cycle_ms: float, error: Optional[str]) -> None:
        now = time.monotonic()
        if now - self.monitor_reported_at < 5 and error == self.monitor_last_error:
            return
        self.monitor_reported_at = now
        self.monitor_last_error = error
        seconds = max(0, int(now - self.monitor_started_at))
        elapsed = f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'
        target = '已锁定 · 等待开售/补库存' if self.locked_product else '正在查找目标商品'
        if self.config.detail_lock_mode and self.detail_entered:
            target = '详情页已锁定 · 等待可提交按钮'
        state = '失败 · 页面异常：' + compact_text(error, 100) if error else '正常 · ' + target
        checked = self.monitor_last_success or '尚未确认'
        prefix = '持续监控中（实时监控，仅日志每5秒刷新）'
        if self.state['manual_verification_waiting']:
            prefix = '监控暂停（等待人工验证，已暂停刷新和点击）'
            state = '等待人工验证 · 验证结束且目标详情恢复后继续'
        self.log('monitor', f'{prefix} | 监控时间 {elapsed} | 状态 {state} | 最近成功检测 {checked} | 检测周期 {cycle_ms:.0f} ms')

    def record_stock_state(self, status: str) -> None:
        if status == self.stock_state:
            return
        self.stock_state = status
        if status == "ready":
            self.sale_seen = True
            self.mark_phase('sale_detected')
            self.log("info", "目标商品已开放购买，立即尝试抢购")
        elif status == "unavailable":
            if not self.sale_seen:
                self.log("info", "目标商品当前显示已抢完/无库存，持续监控等待商家上架库存")
            elif self.config.continue_after_sold_out:
                self.log("warn", "本轮未抢到：目标商品已抢完；继续监控等待补库存")
            else:
                raise GracefulStop("本轮未抢到：目标商品已抢完；未开启售罄后继续监控，任务停止")
        elif status == "waiting":
            self.log("info", "目标商品尚未开放购买，持续监控中")

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
            if self.config.mode == 'batch':
                self._batch_outcome = BatchOutcome('stopped', self.state['batch_completed'], str(exc))
                if self.queue_lifecycle:
                    self.state['retain_browser'] = True
            self.log("warn", str(exc))
            self.log_phase_timing('任务停止（尚未确认成功）')
            return "stopped"
        except Exception as exc:
            if self.config.mode == 'batch':
                self._batch_outcome = BatchOutcome('failed', self.state['batch_completed'], str(exc))
                if self.queue_lifecycle:
                    self.state['retain_browser'] = True
            self.log("error", "任务失败：\n" + traceback.format_exc())
            raise
        finally:
            self.network_stop.set()
            if self.network_worker:
                self.network_worker.join(timeout=1)
            if self.queue_lifecycle and self.config.mode == 'batch':
                outcome = self.batch_outcome()
                queue_success = outcome.kind == 'tested' and outcome.confirmed_orders == 0 or outcome.kind == 'completed' and outcome.confirmed_orders == self.config.buy_times
                self.state['retain_browser'] = not queue_success
            else:
                queue_success = False
            keep_open = bool(
                self.context
                and not queue_success
                and (completed_normally or self.state["pending_payment_review"] or self.state['retain_browser'])
                and (self.state["pending_payment_review"] or self.state['retain_browser'] or not self.config.close_browser_on_finish)
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
            release_failed = False
            if self.context:
                try:
                    self.context.close()
                except Exception:
                    release_failed = True
                if self.config.close_browser_on_finish or self.stop_event.is_set():
                    self.log("info", "浏览器上下文已关闭")
            if self.playwright:
                try:
                    self.playwright.stop()
                except Exception:
                    release_failed = True
            if release_failed and self.queue_lifecycle and self.config.mode == 'batch':
                outcome = self.batch_outcome()
                self._batch_outcome = BatchOutcome('failed', outcome.confirmed_orders, '浏览器释放未确认，停止账号交接；请核对订单并手动关闭仍在运行的浏览器')
                self.log('warn', self._batch_outcome.reason)

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
        if not self.config.save_diagnostics:
            return
        if not self.network_worker:
            self.network_worker=threading.Thread(target=self.write_network_evidence,daemon=True)
            self.network_worker.start()
        def on_response(response) -> None:
            try:
                url = response.url
                if not re.search(r"product|goods|sku|shop|cart|order|live|commerce|ecom", url, re.I):
                    return
                # Copy only local metadata; never read a response body or pass
                # Playwright objects to the file-writing thread.
                entry = {
                    "time": now_text(),
                    "status": response.status,
                    "url": url,
                }
                self.network_queue.put_nowait(entry)
            except queue.Full:
                pass  # Diagnostics must never block ordering.
            except Exception:
                pass

        page.on("response", on_response)

    def write_network_evidence(self) -> None:
        while not self.network_stop.is_set() or not self.network_queue.empty():
            try:
                entry=self.network_queue.get(timeout=.1)
            except queue.Empty:
                continue
            try:
                path=Path(self.config.network_log_path)
                path.parent.mkdir(parents=True,exist_ok=True)
                rotate_file_if_needed(str(path))
                with path.open('a',encoding='utf-8') as handle:
                    handle.write(json.dumps(entry,ensure_ascii=False)+'\n')
            except OSError:
                pass
            finally:
                self.network_queue.task_done()

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

    def safe_click(self, locator, label: str, allow_dry_run_click: bool = False,
                   experimental_purchase: bool = False, experimental_card=None) -> bool:
        self.assert_purchase_authorized()
        self.state["click_attempts"] += 1
        if self.config.dry_run and not allow_dry_run_click:
            self.log("info", f"DRY_RUN 命中动作：{label}")
            self.state["last_click_label"] = label
            return True

        def do_click() -> bool:
            option_page = (getattr(locator, 'page', None) if self.config.mode == 'flash'
                           and effective_flash_lock_mode(self.config)!='none' and label.startswith('select product option:') else None)
            if option_page is not None and option_page.evaluate(MANUAL_VERIFICATION_JS):
                return self.pause_for_manual_verification()
            if label in ('locked product buy button', 'target product buy button', 'buy/order button'):
                self.mark_phase('sale_detected')
            if experimental_purchase and label in ('locked product buy button','target product buy button') and effective_flash_lock_mode(self.config)=='script':
                self.assert_running()
                self.state['purchase_click_mode'] = 'experimental'
                deadline = self.monitor_deadline_ms
                if not math.isfinite(deadline):
                    deadline = time.time()*1000 + 3600000
                clicked = locator.evaluate("""(el, {deadline,card}) => {
                    let watcher = window.__dylaStockWatcher;
                    if(card){
                        const check=()=>{
                            const available=[...card.querySelectorAll('button,a,[role=button],[data-e2e="shop-buyBtn"]')].filter(btn=>{
                                const r=btn.getBoundingClientRect(),s=getComputedStyle(btn);
                                return /^(立即购买|去抢购|立即抢|马上抢|抢购|去购买|购买|下单)$/.test((btn.innerText||'').trim()) &&
                                    !btn.closest('[aria-disabled="true"],[disabled],[inert]') &&
                                    !/disabled|unavailable|Yys40cl5/i.test(btn.className) && r.width>0 && r.height>0 &&
                                    s.visibility!=='hidden' && s.pointerEvents!=='none';
                            });
                            watcher.fastReady=card.isConnected && !/售罄|抢光|抢完|缺货|补货中|已下架|已结束|不可购买|卖光/.test(card.innerText||'') && available.length===1 && available[0]===el;
                        };
                        watcher={el:card,experimentalDispatched:!!el.__dylaScriptDispatched,check};
                    }
                    if (!navigator.onLine || Date.now() >= deadline || !watcher ||
                        !el.isConnected || !watcher.el.isConnected || !watcher.el.contains(el) ||
                        watcher.experimentalDispatched || el.closest('[aria-disabled="true"],[disabled],[inert]')) return false;
                    watcher.check();
                    if (!watcher.fastReady) return false;
                    // Mark before dispatch: an exception or ignored synthetic event
                    // must not trigger an automatic second/native purchase click.
                    watcher.experimentalDispatched = true;
                    el.__dylaScriptDispatched = true;
                    el.click();
                    return true;
                }""", {'deadline':deadline,'card':experimental_card})
                return bool(clicked)
            # Native click already scrolls and checks visibility, stability,
            # enablement and hit targeting. A separate scroll repeats the wait.
            if label in ('locked product buy button', 'target product buy button', 'buy/order button'):
                self.state['purchase_click_mode'] = 'native'
            timeout = self.config.click_timeout_ms
            if label == 'submit payment / create order' and effective_flash_lock_mode(self.config)!='none':
                remaining = self.monitor_deadline_ms-time.time()*1000
                if remaining <= 0:
                    raise GracefulStop('监控窗口已结束，未提交订单')
                if math.isfinite(remaining):
                    timeout = min(timeout, max(1, int(remaining)))
            try:
                locator.click(timeout=timeout)
            except Exception:
                if option_page is not None and option_page.evaluate(MANUAL_VERIFICATION_JS):
                    return self.pause_for_manual_verification()
                raise
            return True

        if label in ('open target product detail', 'reopen target detail back'):
            self.assert_running()
            action_page = getattr(locator, 'page', None)
            if self.config.detail_lock_mode and action_page is not None and action_page.evaluate(MANUAL_VERIFICATION_JS):
                return self.pause_for_manual_verification()
            try:
                do_click()
            except Exception as exc:
                if self.config.detail_lock_mode and action_page is not None and action_page.evaluate(MANUAL_VERIFICATION_JS):
                    # No navigation retry: wait for the human and require the
                    # verified target detail to return before resuming scans.
                    return self.pause_for_manual_verification()
                raise GracefulStop('商品详情导航结果不明确，未重复点击；请核对当前页面') from exc
        elif label == "submit payment / create order":
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
        elif label in ('locked product buy button','target product buy button','buy/order button'):
            # Never assume a timed-out purchase click had no effect.
            try:
                if not do_click():
                    return False
            except GracefulStop:
                raise
            except Exception as exc:
                # Fast-path controls are ElementHandles; resolve their page only
                # on the exceptional path, without adding latency to each click.
                action_page = getattr(locator, 'page', None)
                if action_page is None:
                    try:
                        frame = locator.owner_frame()
                        action_page = frame.page if frame else None
                    except Exception:
                        pass
                deadline=time.monotonic()+1
                while time.monotonic()<deadline:
                    self.assert_running()
                    if action_page is not None and self.purchase_flow_visible(action_page):
                        self.log('info','购买点击返回超时，但已确认进入规格/结算页，继续当前下单流程')
                        break
                    self.wait_ms(50)
                else:
                    self.state['pending_payment_review']=True
                    self.state['purchase_click_error'] = str(exc)
                    self.log('warn', f'购买点击失败原因：{compact_text(exc, 800)}')
                    if action_page is not None:
                        self.save_diagnostics(action_page, 'purchase-click-unconfirmed')
                    raise GracefulStop('购买点击结果不明确，已停止重复点击；请核对当前商品页面或订单') from exc
        else:
            if self.retry_action(label, do_click) is False:
                return False
        self.log("info", f"已点击：{label}")
        if label in ('locked product buy button', 'target product buy button', 'buy/order button'):
            self.mark_phase('buy_clicked')
        elif label == 'submit payment / create order':
            self.mark_phase('payment_submitted')
        self.state["successful_clicks"] += 1
        self.state["last_click_label"] = label
        return True

    def purchase_flow_visible(self, page) -> bool:
        try:
            return bool(page.evaluate(r"""() => {
                const visible=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';};
                if ([...document.querySelectorAll('div.ufz0AqTE, div.iHAKgO8B')].some(visible)) return true;
                const text=document.body.innerText||'';
                return /优惠明细/.test(text)&&/订单留言|购买数量/.test(text);
            }"""))
        except Exception:
            return False

    def assert_unique_product(self, page, force: bool = False) -> None:
        if not self.config.strict_product_match:
            return
        now=time.monotonic()
        if not force and now-self.last_unique_check_at<1:
            return
        texts=page.evaluate("""() => {
            let nodes=[...document.querySelectorAll('li')];
            if (!nodes.length) nodes=[...document.querySelectorAll('[class*="product" i],[class*="goods" i]')];
            nodes=nodes.filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden'&&el.querySelector('button,a,[role=button],[data-e2e="shop-buyBtn"]');});
            nodes=nodes.filter(el=>!nodes.some(other=>other!==el&&el.contains(other)));
            return nodes.map(el=>(el.innerText||'').slice(0,900));
        }""")
        self.last_unique_check_at=now
        if sum(is_likely_list_product_card_text(text,self.config) for text in texts)>1:
            raise GracefulStop('检测到多个目标候选商品，已暂停抢购；请填写更准确的商品名称和编号')

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
        if self.state["pending_payment_review"] or self.state["order_submitted"]:
            return False
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
        if self.state["pending_payment_review"] or self.state["order_submitted"]:
            return
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
        if self.config.mode == 'flash' and self.config.detail_lock_mode and page.evaluate(MANUAL_VERIFICATION_JS):
            return self.pause_for_manual_verification()
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
        try:
            if product_unavailable(container.inner_text(timeout=200)):
                return False
        except Exception:
            return False
        if self.config.mode == 'flash':
            # Pin the actual primary DOM node in one round trip. Native click
            # still performs its normal actionability checks; no forced click.
            candidate = container.evaluate_handle(r"""(el, patterns) => {
                if(new RegExp(patterns.unavailable).test(el.innerText||''))return null;
                const actions=[...el.querySelectorAll('[data-e2e="shop-buyBtn"]')].filter(btn=>{
                    const r=btn.getBoundingClientRect(),s=getComputedStyle(btn);
                    return new RegExp(patterns.buy).test(btn.innerText||'')&&
                        !btn.matches(':disabled')&&!btn.closest('[aria-disabled="true"],[disabled],[inert]')&&
                        !/disabled|unavailable|Yys40cl5/i.test(btn.className)&&r.width>0&&r.height>0&&
                        s.visibility!=='hidden'&&s.pointerEvents!=='none';
                });
                return actions.length>1?{ambiguous:true}:(actions[0]||null);
            }""", {'buy': BUY_ACTION_TEXT_RE.pattern, 'unavailable': UNAVAILABLE_TEXT_RE.pattern})
            try:
                action = candidate.as_element()
                if action is not None:
                    script=effective_flash_lock_mode(self.config)=='script'
                    return self.safe_click(action,label,experimental_purchase=script,
                                           experimental_card=container.element_handle(timeout=200) if script else None)
                if candidate.json_value():
                    raise GracefulStop('目标商品的购买控件不唯一，已停止；请核对当前商品')
            finally:
                candidate.dispose()
        actions = [
            container.locator('[data-e2e="shop-buyBtn"]').filter(has_text=BUY_ACTION_TEXT_RE),
            container.get_by_role("button", name=BUY_ACTION_TEXT_RE).first,
            container.get_by_role("link", name=BUY_ACTION_TEXT_RE).first,
            container.locator("button, a, [role=button]").filter(
                has_text=BUY_ACTION_TEXT_RE
            ).first,
        ]
        for group in actions:
            for index in range(min(group.count(), 8)):
                action = group.nth(index)
                if action.evaluate("el => !el.disabled && el.getAttribute('aria-disabled') !== 'true' && !/disabled|unavailable|Yys40cl5/i.test(el.className) && getComputedStyle(el).pointerEvents !== 'none'"):
                    if effective_flash_lock_mode(self.config)=='script' and label in ('locked product buy button','target product buy button'):
                        if not action.is_visible(timeout=120): continue
                        return self.safe_click(action,label,experimental_purchase=True,
                                               experimental_card=container.element_handle(timeout=200))
                    if self.click_first_visible([action], label):
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
        self.assert_unique_product(page)
        containers = self.find_tight_product_containers(node)
        card = node.locator('xpath=ancestor-or-self::li[1]')
        if card.count():
            text = card.inner_text(timeout=200)
            if is_likely_list_product_card_text(text, self.config):
                containers.insert(0, {'locator': card, 'text': text})
        for container in containers:
            handle = container["locator"].element_handle(timeout=200)
            if handle:
                self.locked_product = (container["locator"], handle)
                self.prepare_locked_buy_action(container["locator"])
                status = product_action_state(container['text'])
                self.record_stock_state('waiting' if status == 'ready' else status)
                return True
        return False

    def prepare_locked_buy_action(self, container) -> None:
        """Resolve a unique control while waiting, not on the sale critical path."""
        self.prelocked_buy_action = None
        if not (self.config.purchase_speed_priority or self.config.experimental_purchase_click):
            return
        primary = container.locator('[data-e2e="shop-buyBtn"]')
        if primary.count() == 1:
            self.prelocked_buy_action = primary.element_handle(timeout=200)
        elif primary.count() == 0:
            actions = container.locator('button,a,[role=button]').filter(
                has_text=re.compile(r'^(立即购买|去抢购|立即抢|马上抢|抢购|去购买|购买|下单|已抢完|已售罄|抢完了)$'))
            if actions.count() == 1:
                self.prelocked_buy_action = actions.element_handle(timeout=200)

    def watch_locked_product(self, page) -> Optional[bool]:
        """Wait on the already matched card, avoiding full-page rescans while waiting."""
        if not self.locked_product:
            return None
        locator, handle = self.locked_product
        try:
            self.assert_running()
            if time.time()*1000 >= self.monitor_deadline_ms:
                return False
            if not handle.evaluate("el => el.isConnected && el.getBoundingClientRect().height > 0"):
                self.clear_stock_watcher(page)
                self.locked_product = None
                if self.relock_replaced_product(page, locator):
                    return False
                return None
            ready = page.evaluate("""({el, action}) => {
                let watcher = window.__dylaStockWatcher;
                if (!watcher || watcher.el !== el) {
                    watcher?.dispose();
                    watcher = {el, status: null, reported: null, listeners: new Set(), sawReady: false};
                    const check = () => {
                    let status = 'waiting';
                    watcher.fastReady = false;
                    if (!el.isConnected) status = 'detached';
                    else if (/售罄|抢光|抢完|缺货|补货中|已下架|已结束|不可购买|卖光/.test(el.innerText || '')) status = 'unavailable';
                    else {
                    const available = Array.from(el.querySelectorAll('button,a,[role=button],[data-e2e="shop-buyBtn"]')).filter(btn => {
                        const r = btn.getBoundingClientRect();
                        return /^(立即购买|去抢购|立即抢|马上抢|抢购|去购买|购买|下单)$/.test((btn.innerText||'').trim()) &&
                            !btn.disabled && btn.getAttribute('aria-disabled') !== 'true' &&
                            !/disabled|unavailable|Yys40cl5/i.test(btn.className) && r.width > 0 && r.height > 0 &&
                            getComputedStyle(btn).visibility !== 'hidden' &&
                            getComputedStyle(btn).pointerEvents !== 'none';
                    });
                    if (available.length) {
                        status = 'ready'; watcher.sawReady = true;
                        watcher.fastReady = available.length === 1 && available[0] === action;
                    }
                    }
                    watcher.status = status;
                    watcher.checkedAt = Date.now();
                    for (const notify of [...watcher.listeners]) notify();
                    };
                    watcher.observer = new MutationObserver(check);
                    watcher.observer.observe(el, {subtree:true,childList:true,characterData:true,attributes:true});
                    // A removed card receives no mutation when its parent replaces
                    // it. Observe structure separately; ignore unrelated chat edits.
                    watcher.structureObserver = new MutationObserver(()=>{if(!el.isConnected)check();});
                    watcher.structureObserver.observe(document.documentElement,{subtree:true,childList:true});
                    watcher.fallback = setInterval(check, 50);
                    watcher.dispose = () => {watcher.observer.disconnect();watcher.structureObserver.disconnect();clearInterval(watcher.fallback);};
                    watcher.check = check;
                    window.__dylaStockWatcher = watcher;
                }
                watcher.check();
                return new Promise(resolve => {
                    let timer;
                    const finish = value => {clearTimeout(timer);watcher.listeners.delete(notify);resolve({...value,offline:!navigator.onLine,idle:!value});};
                    const notify = () => {
                        const status=watcher.status;
                        if (status === 'waiting') {watcher.reported=status;return;}
                        if (status === 'ready' || status === 'detached' || status !== watcher.reported || (watcher.sawReady && !watcher.readyReported)) {
                            watcher.reported = status;
                            if (watcher.sawReady) watcher.readyReported=true;
                            finish({ready:status==='ready',fastReady:watcher.fastReady,detached:status==='detached',stock:status==='unavailable'?'unavailable':null,sawReady:watcher.sawReady});
                        }
                    };
                    watcher.listeners.add(notify);
                    timer=setTimeout(()=>finish(null),250);
                    notify();
                });
            }""", {'el':handle, 'action':self.prelocked_buy_action})
            if ready and ready.get('offline'):
                self.monitor_scan_error='浏览器处于离线状态'
                return False
            self.monitor_last_success = datetime.now(AUTOMATION_TIMEZONE).strftime('%H:%M:%S')
            if ready and ready.get('idle'):
                if time.monotonic()-self.last_identity_check_at>=1:
                    self.last_identity_check_at=time.monotonic()
                    if not is_likely_list_product_card_text(locator.inner_text(timeout=200),self.config):
                        self.clear_stock_watcher(page)
                        self.locked_product=None
                        self.stock_state=None
                        self.sale_seen=False
                        self.log('info','目标商品卡片已变化，重新定位商品')
                        return None
                    self.assert_unique_product(page)
                return False
            if not ready:
                return False
            if ready.get("detached"):
                self.clear_stock_watcher(page)
                self.locked_product = None
                if self.relock_replaced_product(page, locator):
                    return False
                return None
            if ready.get('fastReady') and (self.config.purchase_speed_priority or self.config.experimental_purchase_click) and self.prelocked_buy_action is not None:
                # Identity and unique control were confirmed while waiting.
                # Do not reread text, rescan actions or log before dispatching.
                self.assert_running()
                if time.time()*1000 >= self.monitor_deadline_ms:
                    return False
                action = self.prelocked_buy_action
                if self.safe_click(action, 'locked product buy button', experimental_purchase=True):
                    self.purchase_started = not self.config.dry_run
                    self.purchase_started_at = time.monotonic()
                    self.record_stock_state('ready')
                    self.log('info', '实验极速（仅测试）：识别到购买控件，未触发购买事件' if self.config.experimental_purchase_click and self.config.dry_run
                             else '实验极速：已触发页面购买事件（尚未确认下单），等待结算结果' if self.config.experimental_purchase_click
                             else '极速购买：已直接点击预先绑定的商品控件，等待结算结果')
                    self.clear_stock_watcher(page)
                    self.locked_product = None
                    self.state['options_verified'] = False
                    return self.advance_order_flow(page)
                return False
            text = locator.inner_text(timeout=200)
            if not is_likely_list_product_card_text(text, self.config):
                self.clear_stock_watcher(page)
                self.locked_product = None
                return None
            if ready.get('sawReady') and not self.sale_seen:
                self.record_stock_state('ready')
            if ready.get('stock'):
                self.record_stock_state(ready['stock'])
                return False
            if product_unavailable(text):
                self.record_stock_state('unavailable')
                return False
            if time.time()*1000 >= self.monitor_deadline_ms:
                return False
            if not ready.get('ready'):
                return False
            self.record_stock_state('ready')
            self.log("info", "锁定商品已开放购买，立即执行购买")
            if self.find_and_click_buy_action(page, locator, "locked product buy button"):
                self.clear_stock_watcher(page)
                self.locked_product = None
                self.purchase_started = not self.config.dry_run
                self.purchase_started_at = time.monotonic()
                self.state["options_verified"] = False
                return self.advance_order_flow(page)
            return False
        except GracefulStop:
            raise
        except Exception as exc:
            if self.purchase_started:
                raise GracefulStop("购买动作后页面状态异常，已停止重复点击；请核对订单状态")
            self.monitor_scan_error = str(exc)
            self.clear_stock_watcher(page)
            self.locked_product = None
            return None

    def relock_replaced_product(self, page, locator) -> bool:
        """Reuse the locator only after revalidating identity and uniqueness."""
        if locator.count() != 1:
            return False
        text = locator.inner_text(timeout=200)
        if not is_likely_list_product_card_text(text, self.config):
            return False
        self.assert_unique_product(page, force=True)
        handle = locator.element_handle(timeout=200)
        if handle and handle.evaluate("el=>el.isConnected&&el.getBoundingClientRect().height>0"):
            self.locked_product = (locator, handle)
            self.prepare_locked_buy_action(locator)
            return True
        return False

    def clear_stock_watcher(self, page) -> None:
        self.prelocked_buy_action = None
        try:
            page.evaluate("() => {window.__dylaStockWatcher?.dispose();delete window.__dylaStockWatcher;}")
        except Exception:
            pass

    def set_purchase_quantity(self, page, scope=None) -> None:
        if self.config.mode != "batch":
            return
        inputs = (scope if scope is not None else page).locator('input[type="number"], input[role="spinbutton"]')
        for i in range(min(inputs.count(), 8)):
            control = inputs.nth(i)
            if not control.is_visible():
                continue
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
                if not candidate.count():
                    continue
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
                if not candidate.count():
                    continue
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
        raise GracefulStop("禁止关闭待付款页面")

    def confirm_abandon_payment(self, page) -> bool:
        raise GracefulStop("禁止放弃支付")

    def select_product_options(self, page, required: bool = False, scope=None) -> bool:
        if not self.config.multi_option_enabled:
            return True
        requested = parse_product_options(self.config.option_names)
        if not requested:
            raise GracefulStop("已停止：多选项功能已开启，但没有填写选项名称")
        controls = (scope if scope is not None else page).locator(PRODUCT_OPTION_SELECTOR)
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
                unavailable: el.getAttribute('aria-disabled') === 'true' || el.hasAttribute('disabled') ||
                    /disabled|soldout|unavailable/i.test(el.className) || /缺货|售罄|已抢完|已抢光/.test(el.innerText || ''),
                groupCount: [...el.closest('.YTFcT_zp')?.querySelectorAll('div.ufz0AqTE') || []].length,
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
                if not self.safe_click(action, f"select product option: {name}"):
                    return False
                self.wait_ms(max(150, self.config.order_step_delay_ms))
            chosen.append((action, name))

        defaults = []
        for group in available_groups - used_groups:
            candidates = [data for data in records if data['group'] == group]
            if len(candidates) != 1:
                raise GracefulStop("已停止：请为有多个选项的规格组填写一个选项名称，未点击支付")
            action = controls.nth(candidates[0]['index'])
            data = details(action)
            # Fixed selected defaults may have pointer-events:none. Stock-disabled
            # controls are different and must never be accepted as defaults.
            if (data['group'] != group or data['groupCount'] != 1 or
                    not data['selected'] or data['unavailable']):
                raise GracefulStop(f"已停止：规格组“{group}”未确认可用的唯一默认选项，请填写或检查规格，未点击支付")
            defaults.append((action, data['text'], group))
            chosen.append((action, data['text']))

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
        for action, name, group in defaults:
            data = details(action)
            if (data['text'] != name or data['group'] != group or data['groupCount'] != 1 or
                    not data['selected'] or data['unavailable']):
                raise GracefulStop("已停止：默认规格发生变化或不可用，未点击支付")
        if not self.state["options_verified"]:
            self.log("info", "指定商品规格已全部确认选中：" + " | ".join(name for _, name in chosen))
        self.state["options_verified"] = True
        return True

    def flash_payment_snapshot(self, page):
        """One browser round trip for the verified primary checkout control."""
        records = page.locator(PAYMENT_PRIMARY_SELECTOR).evaluate_all(PAYMENT_SNAPSHOT_JS)
        self.last_payment_snapshot = records[0] if len(records) == 1 else None
        if not records:
            return None
        if len(records) != 1:
            raise GracefulStop('结算支付控件不唯一，已停止；请核对当前页面')
        data = records[0]
        if data['stockError'] and not self.state['order_submitted']:
            raise CheckoutSoldOut('本轮未抢到：结算页显示商品已抢完/库存不足，未点击支付')
        data['text'] = compact_text(data['text'], 160)
        data['safe'] = data['safe'] and bool(PAYMENT_ACTION_TEXT_RE.fullmatch(data['text']))
        data['action'] = page.locator(PAYMENT_PRIMARY_SELECTOR).nth(data['index'])
        return data

    def wait_for_payment_update(self, page, snapshot, milliseconds: int = 100) -> bool:
        """Wake on checkout changes, including changes since the last snapshot."""
        self.assert_running()
        changed = page.evaluate("""args => new Promise(resolve => {
            const collect = """ + PAYMENT_SNAPSHOT_JS + """;
            let observer, timeout, fallback, finished=false;
            const finish=value=>{
                if(finished)return;
                finished=true;observer?.disconnect();clearTimeout(timeout);clearInterval(fallback);resolve(value);
            };
            const check=()=>{
                const records=collect([...document.querySelectorAll(args.selector)]);
                const signature=records.length===1?records[0].signature:null;
                if(records.length>1||signature!==args.signature)finish(true);
            };
            observer=new MutationObserver(check);
            observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true});
            fallback=setInterval(check,25);
            timeout=setTimeout(()=>finish(false),args.timeout);
            check();
        })""", {'selector': PAYMENT_PRIMARY_SELECTOR,
                 'signature': snapshot.get('signature') if snapshot else None,
                 'timeout': max(1, min(100, milliseconds))})
        self.assert_running()
        return bool(changed)

    def submit_payment_then_abandon(self, page) -> bool:
        # Legacy callers must use the same verified, single-submit path.
        if effective_flash_lock_mode(self.config) == 'none':
            return False
        return self.submit_flash_lock(page)

    def advance_order_flow(self, page) -> bool:
        if effective_flash_lock_mode(self.config)!='none' and not self.config.dry_run:
            if self.purchase_started and not self.detail_open_started_at:
                self.detail_open_started_at=self.purchase_started_at or time.monotonic()
            return self.submit_flash_lock(page)
        if self.config.dry_run or self.config.max_order_steps <= 0:
            return True

        step = 1
        deadline = time.monotonic() + 5
        while step <= self.config.max_order_steps and time.monotonic() < deadline:
            self.assert_running()
            self.select_product_options(page)
            if self.submit_payment_then_abandon(page):
                return True
            if self.config.mode == 'flash' and not self.config.multi_option_enabled and self.payment_primary_seen:
                self.wait_for_payment_update(page, self.last_payment_snapshot)
                continue
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

    def detail_snapshot(self, page):
        data = page.evaluate(DETAIL_SNAPSHOT_JS, {
            'roots': DETAIL_ROOT_SELECTOR, 'name': self.config.product_name,
            'number': self.config.product_id if str(self.config.product_id).isdigit() else '',
            'payment': PAYMENT_PRIMARY_SELECTOR})
        self.state['detail_status'] = {
            key: data.get(key) for key in ('matches', 'panels', 'quantity', 'qr', 'paymentCount', 'offline', 'manualVerification', 'verification')}
        return data

    def pause_for_manual_verification(self, *, after_submit: bool = False) -> bool:
        """Read-only waiting: the human completes the platform challenge."""
        self.assert_running()
        if not self.state['manual_verification_waiting']:
            self.state['manual_verification_waiting'] = True
            self.state['retain_browser'] = True
            self.monitor_reported_at = float('-inf')
            self.log('warn', '等待人工验证：已暂停刷新和点击，浏览器保留；请在浏览器完成验证后保持目标详情页')
        self.report_monitor_status(0, None)
        remaining = self.monitor_deadline_ms-time.time()*1000
        self.wait_ms(500 if after_submit else max(0, min(500, remaining)))
        return False

    def resume_after_manual_verification(self, *, after_submit: bool = False) -> None:
        self.state['manual_verification_waiting'] = False
        self.monitor_reported_at = float('-inf')
        if after_submit:
            self.log('info', '验证提示已消失，仅核对本次提交结果，不重新下单')
            return
        self.detail_open_started_at = time.monotonic()
        self.detail_refreshed_at = time.monotonic()
        self.detail_refresh_blocked = False
        self.detail_last_list_ready = False
        self.state['options_verified'] = False
        self.stock_state = None
        self.sale_seen = False
        self.log('info', '人工验证提示已消失且目标详情已恢复，继续实时监控；监控区间不延长')

    def open_target_detail(self, page) -> bool:
        """Only open the verified list title; never treat the gray buy label as stock."""
        if not self.config.product_name or self.config.target_price <= 0:
            raise GracefulStop('详情页锁单需要完整商品名称和大于0的目标金额')
        cards = page.locator('li').filter(has_text=self.config.product_name).filter(visible=True)
        matches = []
        for card in cards.all():
            text = card.inner_text(timeout=300)
            # This mode never uses the legacy same-price/non-strict fallback.
            strict = replace(self.config, strict_product_match=True, strict_price_match=True)
            if is_likely_list_product_card_text(text, strict) and exact_target_price_matches(text, strict):
                matches.append(card)
        if len(matches) > 1:
            raise GracefulStop('详情页锁单：目标商品不唯一，请核对名称和编号')
        if not matches:
            self.ensure_all_products_panel(page)
            return False
        title = matches[0].locator('[data-e2e="promotion-title"]')
        if title.count() != 1:
            title = matches[0].get_by_text(self.config.product_name, exact=True)
        if title.count() != 1:
            raise GracefulStop('详情入口不唯一，未点击图片或其他商品')
        if page.evaluate(MANUAL_VERIFICATION_JS):
            return self.pause_for_manual_verification()
        if not self.safe_click(title, 'open target product detail', allow_dry_run_click=True):
            return False
        self.detail_open_started_at = time.monotonic()
        self.detail_refreshed_at = self.detail_open_started_at
        self.state['options_verified'] = False
        self.mark_phase('detail_opened')
        return False

    def wait_for_detail_update(self, page, snapshot) -> None:
        self.assert_running()
        page.evaluate("""args => new Promise(resolve => {
            const collect=""" + DETAIL_SNAPSHOT_JS + """;
            let observer,timer,fallback,done=false;
            const finish=()=>{if(done)return;done=true;observer?.disconnect();clearTimeout(timer);clearInterval(fallback);resolve();};
            const check=()=>{const value=collect(args);if(value.signature!==args.signature||value.offline)finish();};
            const root=document.querySelectorAll(args.roots)[args.index];
            observer=new MutationObserver(check);
            observer.observe(root||document.documentElement,{subtree:true,childList:true,attributes:true,characterData:true});
            const card=document.querySelectorAll('li')[args.listIndex];
            if(card)observer.observe(card,{subtree:true,childList:true,attributes:true,characterData:true});
            fallback=setInterval(check,25);
            timer=setTimeout(finish,100);
            check();
        })""", {'roots': DETAIL_ROOT_SELECTOR, 'name': self.config.product_name,
                 'payment': PAYMENT_PRIMARY_SELECTOR, 'index': snapshot.get('index', -1),
                 'number': self.config.product_id if str(self.config.product_id).isdigit() else '',
                 'listIndex': snapshot.get('listIndex', -1),
                 'signature': snapshot.get('signature')})
        self.assert_running()

    def reopen_waiting_detail(self, page, snapshot) -> None:
        # Reopening a detail may refresh server data, but the real platform's
        # caching/update behavior is not assumed. Never reload the whole room.
        self.assert_running()
        if self.state['order_submitted'] or self.state['pending_payment_review']:
            raise GracefulStop('订单结果需要核对，不再刷新或重新购买')
        latest = self.detail_snapshot(page)
        if latest.get('manualVerification') or latest.get('verification'):
            self.pause_for_manual_verification()
            return
        latest_payment = latest.get('payment') or {}
        if latest.get('matches') != 1 or (latest_payment.get('safe') and not latest_payment.get('stockError')
                and PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(latest_payment.get('text'), 160))
                and exact_target_price_matches(latest_payment.get('text', ''), self.config)):
            return
        root = page.locator(DETAIL_ROOT_SELECTOR).nth(latest['index'])
        back = root.locator('.xRcNm_F4,button[aria-label="返回"],button[aria-label="关闭"],[role="button"][aria-label="返回"]')
        back = back.filter(visible=True)
        if back.count() != 1:
            self.detail_refresh_blocked = True
            self.log('warn', '未识别到唯一安全返回入口：继续实时监控详情变化，但无法自动重新打开；可手动返回并打开目标商品')
            return
        if not self.safe_click(back, 'reopen target detail back', allow_dry_run_click=True):
            return
        deadline = time.monotonic()+1
        while time.monotonic() < deadline:
            self.assert_running()
            current = self.detail_snapshot(page)
            if current.get('manualVerification') or current.get('verification'):
                self.pause_for_manual_verification()
                return
            if current.get('panels', 0) == 0:
                self.detail_entered = False
                self.detail_open_started_at = 0
                self.state['detail_refreshes'] += 1
                self.open_target_detail(page)
                return
            self.wait_ms(25)
        raise GracefulStop('返回后详情页仍未关闭，未重复返回或刷新；请核对页面')

    def pending_order_snapshot(self, page, capture_existing: bool = False):
        records = page.evaluate(r"""() => {
            const visible=el=>{const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(el).visibility!=='hidden';};
            const found=new Map();
            for(const label of document.querySelectorAll('h1,h2,h3,p,span,div')){
                if(label.children.length||!visible(label)||!/^\s*(订单已提交[，,、\s]*待付款|待付款|订单待支付|等待付款|待支付)\s*$/.test(label.innerText||''))continue;
                for(let box=label.parentElement;box&&box!==document.body;box=box.parentElement){
                    const id=(box.innerText||'').match(/订单(?:号|编号)\s*[:：]?\s*([A-Za-z0-9_-]{6,})/);
                    if(id){found.set(id[1],{id:id[1],status:(label.innerText||'').trim(),text:box.innerText||''});break;}
                }
            }
            return [...found.values()];
        }""")
        if capture_existing:
            self.preexisting_pending_order_ids.update(record['id'] for record in records)
            return None
        fresh = [record for record in records if record['id'] not in self.preexisting_pending_order_ids]
        if len(fresh) > 1:
            raise GracefulStop('新待付款订单不唯一，停止且不重复提交')
        if not fresh:
            return None
        record = fresh[0]
        text = record.get('text', '')
        name = getattr(self, 'batch_target_name', self.config.product_name)
        # Require labelled order evidence; surrounding product cards are insufficient.
        names = re.findall(r'(?:商品名称|商品名)\s*[:：]\s*([^\n]+)', text)
        quantities = re.findall(r'(?:购买数量|商品数量)\s*[:：]?\s*(\d+)', text)
        expected_quantity = self.config.buy_quantity if self.config.mode == 'batch' else 1
        options = parse_product_options(self.config.option_names) if self.config.multi_option_enabled else []
        valid = (len(names) == 1 and normalize_text(names[0]) == normalize_text(name)
                 and quantities == [str(expected_quantity)])
        for group, value in options:
            values = re.findall(re.escape(group) + r'\s*[:：=]\s*([^\n|]+)', text)
            valid = valid and len(values) == 1 and normalize_text(values[0]) == normalize_text(value)
        if not options:
            valid = valid and bool(re.search(r'规格\s*[:：]\s*(?:默认单一规格|无规格)\s*(?:$|\n)', text))
        if not valid:
            raise GracefulStop('待付款证据不足或商品、规格、数量不符，停止且不重复提交')
        return record

    def wait_for_pending_order(self, page) -> bool:
        deadline = time.monotonic()+self.pending_order_timeout_ms/1000
        while True:
            self.assert_running()
            result = self.pending_order_snapshot(page)
            if result:
                self.state['order_lock_confirmed'] = True
                self.state['pending_order_id'] = result['id']
                # Retain the page even if close-on-finish is set; payment is manual.
                self.state['pending_payment_review'] = True
                self.mark_phase('order_locked')
                self.state['manual_verification_waiting'] = False
                return True
            if page.evaluate(MANUAL_VERIFICATION_JS):
                self.pause_for_manual_verification(after_submit=True)
                # Manual verification may take longer than the normal ack wait.
                # There is never another submit, even after the window expires.
                deadline = time.monotonic()+self.pending_order_timeout_ms/1000
                continue
            if self.state['manual_verification_waiting']:
                self.resume_after_manual_verification(after_submit=True)
            if time.monotonic() >= deadline:
                break
            self.wait_ms(35)
        self.state['pending_payment_review'] = True
        self.save_diagnostics(page, 'pending-order-unconfirmed')
        raise GracefulStop('已点击一次提交，但未确认待付款订单，不能视为锁单成功；未重复提交，请核对保留页面或订单列表')

    def scan_detail_lock_and_order(self, page) -> bool:
        return self.submit_flash_lock(page)

    def submit_flash_lock(self, page) -> bool:
        self.assert_running()
        if self.state['order_submitted']:
            if self.state['order_lock_confirmed']:
                return True
            raise GracefulStop('已经提交一次且结果待确认，禁止重复提交')
        if self.state['pending_payment_review']:
            raise GracefulStop('提交结果需要人工核对，禁止重新购买')
        if time.time()*1000 >= self.monitor_deadline_ms:
            return False
        data = self.detail_snapshot(page)
        if data.get('manualVerification') or data.get('verification'):
            return self.pause_for_manual_verification()
        if self.state['manual_verification_waiting']:
            # Disappearance alone is not proof: the target must be back online.
            if data.get('matches') != 1 or data.get('offline'):
                return self.pause_for_manual_verification()
            self.resume_after_manual_verification()
        if data.get('offline'):
            self.monitor_scan_error = '浏览器处于离线状态'
            return False
        self.monitor_last_success = datetime.now(AUTOMATION_TIMEZONE).strftime('%H:%M:%S')
        if data['matches'] > 1 or (data['matches'] == 0 and data['panels'] > 0):
            raise GracefulStop('详情页不是唯一目标商品，未提交订单；请核对商品')
        if not data['matches']:
            if self.detail_open_started_at:
                if time.monotonic()-self.detail_open_started_at > 3:
                    self.save_diagnostics(page, 'detail-entry-unconfirmed')
                    raise GracefulStop('打开商品后未确认目标详情，未重复点击；请核对页面')
                self.wait_ms(25)
                return False
            return self.open_target_detail(page)
        if not self.detail_entered:
            self.pending_order_snapshot(page, capture_existing=True)
            self.detail_entered = True
            self.detail_refreshed_at = time.monotonic()
            self.log('info', '目标详情已确认：等待网页提交按钮；仅二维码展示不算可下单')
        if data['paymentCount'] > 1:
            raise GracefulStop('详情页支付控件不唯一，未提交订单')
        payment = data.get('payment')
        list_became_ready = data.get('listReady', False) and not self.detail_last_list_ready
        self.detail_last_list_ready = data.get('listReady', False)
        if list_became_ready:
            self.sale_seen = True
            self.stock_state = 'ready'
        elif data.get('listUnavailable'):
            self.record_stock_state('unavailable')
        if payment and payment.get('stockError'):
            # A freshly ready list can disagree with a cached sold-out detail.
            # Let the safe reopen path refresh it; only explicit unavailable list evidence or
            # unavailable detail without a ready list establishes stock loss.
            if not data.get('listReady'):
                self.record_stock_state('unavailable')
        elif payment and payment['safe']:
            if not PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(payment['text'], 160)):
                raise GracefulStop('详情页控件文字不是支付动作，未点击')
            if data['quantity'] != 1:
                raise GracefulStop('详情页锁单只允许一件，未确认数量为1，未提交订单')
            if not exact_target_price_matches(payment['text'], self.config):
                # A transient zero while price hydrates is not an available order.
                if re.search(r'[¥￥]\s*0(?:\.0+)?\s*$', payment['text']):
                    self.wait_for_detail_update(page, data)
                    return False
                self.save_diagnostics(page, 'detail-price-mismatch')
                raise GracefulStop('详情页提交金额与目标金额不符，未点击支付')
            if self.config.multi_option_enabled:
                root = page.locator(DETAIL_ROOT_SELECTOR).nth(data['index'])
                if not self.select_product_options(page, required=True, scope=root):
                    return False
                data = self.detail_snapshot(page)
                payment = data.get('payment')
                if data['matches'] != 1 or not payment or not payment['safe'] or payment['stockError']:
                    return False
                if data['quantity'] != 1 or not exact_target_price_matches(payment['text'], self.config):
                    raise GracefulStop('选择规格后金额或数量变化，未提交订单')
            if self.config.dry_run:
                self.log('info', '详情页测试命中：金额和数量符合配置，未提交订单、未锁单')
                return True
            self.assert_running()
            if time.time()*1000 >= self.monitor_deadline_ms:
                return False
            self.mark_phase('sale_detected')
            self.pending_order_snapshot(page, capture_existing=True)
            action = page.locator(DETAIL_ROOT_SELECTOR).nth(data['index']).locator(PAYMENT_PRIMARY_SELECTOR).nth(payment['index']).element_handle(timeout=200)
            if action is None:
                return False
            # Pin the actual native-click node, then validate its current context.
            # Recycled indices or a detached/reused panel must not target another item.
            current = action.evaluate("""(el,args) => {
                const collect=""" + DETAIL_SNAPSHOT_JS + """;
                const value=collect(args);
                const root=document.querySelectorAll(args.roots)[value.index];
                value.pinned=el.isConnected&&value.matches===1&&
                    root?.querySelectorAll(args.payment)[value.payment?.index]===el;
                return value;
            }""", {'roots': DETAIL_ROOT_SELECTOR, 'name': self.config.product_name,
                     'number': self.config.product_id if str(self.config.product_id).isdigit() else '',
                     'payment': PAYMENT_PRIMARY_SELECTOR})
            checked = current.get('payment')
            if current.get('manualVerification') or current.get('verification'):
                return self.pause_for_manual_verification()
            if not current.get('pinned'):
                raise GracefulStop('提交前商品详情或控件身份变化，未点击支付')
            if current.get('offline') or not checked or not checked['safe'] or checked['stockError']:
                return False
            if (current['quantity'] != 1 or not PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(checked['text'], 160))
                    or not exact_target_price_matches(checked['text'], self.config)):
                raise GracefulStop('提交前金额、数量或控件文字变化，未点击支付')
            # Always a native click; synthetic purchase mode and auto-pay are ignored.
            self.safe_click(action, 'submit payment / create order')
            self.state['payment_clicks'] += 1
            self.state['order_submitted'] = True
            self.log('info', '已点击一次提交，正在确认待付款订单；不会继续付款或放弃支付')
            return self.wait_for_pending_order(page)
        if (self.config.detail_lock_mode and not self.detail_refresh_blocked and (list_became_ready and
                (data.get('qr') or (payment and payment.get('stockError'))) or
                (time.monotonic()-self.detail_refreshed_at)*1000 >= max(500, self.config.detail_refresh_ms))):
            self.reopen_waiting_detail(page, data)
            self.detail_refreshed_at = time.monotonic()
        else:
            self.wait_for_detail_update(page, data)
        return False

    def scan_dom_and_order(self, page) -> bool:
        self.state["scans"] += 1
        if self.config.mode == 'flash' and self.config.detail_lock_mode:
            return self.scan_detail_lock_and_order(page)
        if self.purchase_started:
            if time.monotonic() - self.purchase_started_at > 15:
                if self.state.get('purchase_click_mode') == 'experimental':
                    self.state['pending_payment_review'] = True
                    self.save_diagnostics(page, 'experimental-purchase-unconfirmed')
                    raise GracefulStop('实验极速购买未确认进入结算页，平台可能未接受页面事件；未改用原生点击重试，请核对订单或关闭实验模式')
                raise GracefulStop("购买后结算页超时，已停止重复购买；请核对订单状态")
            return self.advance_order_flow(page)
        watched = self.watch_locked_product(page)
        if watched is not None:
            return watched
        if not page.evaluate('() => navigator.onLine'):
            self.monitor_scan_error='浏览器处于离线状态'
            return False
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
        self.assert_unique_product(page)

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
            self.monitor_last_success = datetime.now(AUTOMATION_TIMEZONE).strftime('%H:%M:%S')
        except Exception as exc:
            self.monitor_scan_error = str(exc)
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
            if action_state in ("waiting", "unavailable"):
                self.record_stock_state(action_state)
                if self.lock_waiting_product(page, node):
                    return False
                continue

            if action_state != "ready" or product_unavailable(text) or (
                not self.config.multi_option_enabled and not price_matches(text, self.config)
            ):
                self.state["unavailable_matches"] += 1
                self.monitor_scan_error = '目标商品状态或价格不符合配置'
                continue

            containers = self.find_tight_product_containers(node)
            for container in containers:
                if not is_likely_list_product_card_text(container["text"], self.config):
                    continue
                self.state["last_match_text"] = container["text"]
                if self.find_and_click_buy_action(page, container["locator"], "target product buy button"):
                    self.record_stock_state('ready')
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
            for day in (0, -1):
                start_ms = (start + timedelta(days=day)).timestamp() * 1000
                end_ms = start_ms + point.duration_ms
                now_server_ms = now_server.timestamp() * 1000
                if start_ms <= now_server_ms < end_ms:
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
            self.log("info", "未配置监控区间，立即监控（最长一小时）")
            return time.time() * 1000 + min(3600000, self.config.monitor_duration_ms)

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
            self.log("info", "已进入预热阶段，等待区间开始")
            while time.time()*1000 < next_open:
                self.wait_ms(min(50, max(1, int(next_open-time.time()*1000))))
            # Re-evaluate the range to retain its explicit end, including midnight.

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
        self.mark_phase('link_opened')
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
        if not self.config.detail_lock_mode:
            self.dismiss_blocking_overlays(page)
            self.open_commerce_panel(page, True)

        ordered = False
        while not ordered:
            self.assert_running()
            window_ends_at = self.wait_for_schedule_window(clock_offset_ms)
            self.monitor_deadline_ms = window_ends_at
            self.monitor_started_at = time.monotonic()
            self.monitor_reported_at = float('-inf')
            self.log("info", f"开始持续监控：{self.config.product_name} · 编号 {self.config.product_id} · ¥{self.config.target_price:g}")
            self.report_monitor_status(0,None)
            while time.time() * 1000 < window_ends_at and not ordered:
                self.assert_running()
                scan_started_at = time.monotonic()
                self.monitor_scan_error = None
                try:
                    if hasattr(page, 'is_closed') and page.is_closed():
                        raise GracefulStop('监控失败：浏览器页面已关闭，任务停止')
                    if self.purchase_started:
                        # Scope stock failures to the authoritative checkout, never
                        # the live list's unrelated sold-out goods.
                        self.flash_payment_snapshot(page)
                    ordered = self.scan_dom_and_order(page)
                except CheckoutSoldOut:
                    # Restart only after an explicit pre-submission stock failure.
                    if self.state['order_submitted'] or self.state['payment_clicks'] or self.state['pending_payment_review']:
                        raise GracefulStop('订单/支付结果需要核对，已停止重新购买')
                    self.log_phase_timing('本轮未抢到')
                    if not self.config.continue_after_sold_out:
                        raise GracefulStop('本轮未抢到：结算页显示商品已抢完/库存不足；未开启“售罄后继续监控”，任务停止')
                    self.record_stock_state('unavailable')
                    self.clear_stock_watcher(page)
                    self.purchase_started = False
                    self.locked_product = None
                    self.state['unsafe_checkout'] = False
                    self.state['options_verified'] = False
                    self.payment_primary_seen = False
                    self.last_payment_snapshot = None
                    self.phase_times = {key:value for key,value in self.phase_times.items() if key == 'link_opened'}
                    page.goto(self.config.live_url, wait_until='domcontentloaded', timeout=60000)
                    continue
                except GracefulStop:
                    raise
                except AccountVerificationRequired:
                    raise
                except UserAgreementRequired:
                    raise
                except Exception as exc:
                    self.monitor_scan_error = str(exc)
                if not ordered and self.locked_product is None and not (self.config.detail_lock_mode and self.detail_entered):
                    self.wait_ms(randomized_delay_ms(self.config))
                if not ordered and not self.purchase_started:
                    self.report_monitor_status((time.monotonic()-scan_started_at)*1000, self.monitor_scan_error)

            if not ordered:
                self.clear_stock_watcher(page)
                self.locked_product = None
                self.stock_state = None
                self.sale_seen = False
                self.log("warn", "本监控区间未抢到商品，已结束扫描")
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
            if effective_flash_lock_mode(self.config)!='none':
                if self.config.dry_run:
                    self.log('info', '详情页测试完成，未创建订单、未锁单')
                elif self.state['order_lock_confirmed']:
                    self.log('info', '锁单成功：已确认待付款订单，任务停止；请自行完成付款')
                    self.log_phase_timing('本次锁单耗时')
                else:
                    raise GracefulStop('未确认待付款订单，不能视为锁单成功')
            else:
                self.log("info", "测试命中成功，未创建真实订单" if self.config.dry_run else "抢购已进入下单流程，等待用户支付（尚未确认购买成功）")

    def open_batch_target(self, page) -> None:
        page.goto(self.config.live_url, wait_until="domcontentloaded", timeout=60000)
        self.install_product_image_viewer_guard(page)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.assert_running()
            if page.evaluate(MANUAL_VERIFICATION_JS):
                raise GracefulStop('需要人工验证，停止商品定位')
            self.open_commerce_panel(page, True)
            if self.ensure_all_products_panel(page):
                cards = page.locator('li').filter(visible=True)
                matches = []
                for card in cards.all():
                    text = card.inner_text(timeout=300)
                    if (product_matches(text, self.config) and
                            product_list_index_matches(text, self.config)):
                        title = card.locator('[data-e2e="promotion-title"]')
                        if title.count() == 1:
                            matches.append((title, title.inner_text(timeout=300).strip()))
                if len(matches) > 1:
                    raise GracefulStop('商品匹配不唯一，未提交订单')
                if len(matches) == 1:
                    title, name = matches[0]
                    if getattr(self, 'batch_target_name', name) != name:
                        raise GracefulStop('重新匹配的商品身份变化，停止')
                    self.batch_target_name = name
                    self.log('info', f'商品匹配唯一：{name}；编号={self.config.product_id or "未填写"}')
                    if not self.safe_click(title, 'open target product detail', allow_dry_run_click=True):
                        raise GracefulStop('目标详情入口点击未确认')
                    return
            self.scroll_likely_product_lists(page)
            self.wait_ms(150)
        raise GracefulStop('直播间“全部商品”未唯一匹配目标商品，停止')

    def batch_detail_snapshot(self, page):
        return page.evaluate(DETAIL_SNAPSHOT_JS, {
            'roots': DETAIL_ROOT_SELECTOR, 'name': self.batch_target_name,
            'number': self.config.product_id, 'payment': PAYMENT_PRIMARY_SELECTOR})

    def run_batch_buy(self, page) -> None:
        if not self.config.live_url or not (self.config.product_name or self.config.product_id):
            raise GracefulStop('批量下单需要直播间链接及商品关键词或编号')
        try:
            self.open_batch_target(page)
            for batch in range(1, self.config.buy_times + 1):
                self.assert_running()
                self.log('info', f'批次 {batch}/{self.config.buy_times}：关键词={self.config.product_name or "未填写"}；编号={self.config.product_id or "未填写"}')
                self.state['options_verified'] = False
                self.state['order_submitted'] = False
                self.state['order_lock_confirmed'] = False
                deadline = time.monotonic() + 5
                data = self.batch_detail_snapshot(page)
                while data.get('matches') == 0 and time.monotonic() < deadline:
                    self.wait_ms(100)
                    data = self.batch_detail_snapshot(page)
                if data.get('matches') != 1 or data.get('offline') or data.get('manualVerification') or data.get('verification'):
                    raise GracefulStop('目标详情身份或页面状态不明确，停止')
                root = page.locator(DETAIL_ROOT_SELECTOR).nth(data['index'])
                if not self.select_product_options(page, required=True, scope=root):
                    raise GracefulStop('指定规格未核对成功，停止')
                if not self.config.multi_option_enabled:
                    groups = root.locator('.YTFcT_zp')
                    for group in groups.all():
                        options = group.locator(PRODUCT_OPTION_SELECTOR)
                        if options.count() != 1 or not options.first.evaluate("el => (el.classList.contains('vZSOutR4') || el.getAttribute('aria-selected') === 'true') && el.getAttribute('aria-disabled') !== 'true' && !el.hasAttribute('disabled') && !/disabled|soldout|unavailable/i.test(el.className) && !/缺货|售罄|已抢完|已抢光/.test(el.innerText || '')"):
                            raise GracefulStop('存在未明确指定的商品规格，停止')
                self.log('info', '规格核对完成：' + (self.config.option_names if self.config.multi_option_enabled else '默认单一规格'))
                if self.config.dry_run:
                    self.log('info', f'DRY_RUN：每单计划购买 {self.config.buy_quantity} 件')
                self.set_purchase_quantity(page, scope=root)
                data = self.batch_detail_snapshot(page)
                payment = data.get('payment')
                expected = replace(self.config, target_price=self.config.target_price*self.config.buy_quantity)
                if (data.get('matches') != 1 or data.get('quantity') != self.config.buy_quantity or
                        not payment or not payment['safe'] or payment['stockError'] or
                        not PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(payment['text'], 160)) or
                        (self.config.strict_price_match and not exact_target_price_matches(payment['text'], expected))):
                    raise GracefulStop('提交前商品、数量、金额或按钮状态不明确，停止')
                if self.config.dry_run:
                    self.log('info', 'DRY_RUN：已核对商品、规格及数量，未提交订单')
                else:
                    self.pending_order_snapshot(page, capture_existing=True)
                    action = root.locator(PAYMENT_PRIMARY_SELECTOR).nth(payment['index']).element_handle(timeout=300)
                    if action is None or not action.evaluate("el => el.isConnected"):
                        raise GracefulStop('提交控件消失，停止')
                    current = self.batch_detail_snapshot(page)
                    pinned = action.evaluate("""(el,args) => {
                        const root=document.querySelectorAll(args.roots)[args.index];
                        return el.isConnected && root?.querySelectorAll(args.payment)[args.paymentIndex]===el;
                    }""", {'roots': DETAIL_ROOT_SELECTOR, 'index': current.get('index'),
                             'payment': PAYMENT_PRIMARY_SELECTOR, 'paymentIndex': payment['index']})
                    if not pinned or current.get('signature') != data.get('signature'):
                        raise GracefulStop('提交前详情状态变化，停止')
                    self.state['pending_payment_review'] = True
                    self.state['order_submitted'] = True
                    if not self.safe_click(action, 'submit payment / create order'):
                        raise GracefulStop('提交点击结果不明确，禁止重试')
                    self.wait_for_pending_order(page)
                    order_id = self.state['pending_order_id']
                    self.preexisting_pending_order_ids.add(order_id)
                    self.state['batch_completed'] += 1
                    self.log('info', f'批次 {batch} 待付款订单确认：{order_id}；锁单成功 {self.state["batch_completed"]} 笔')
                if batch < self.config.buy_times:
                    self.wait_ms(self.config.batch_interval_ms)
                    try:
                        page.reload(wait_until='domcontentloaded', timeout=60000)
                        self.wait_ms(200)
                        recovered = self.batch_detail_snapshot(page)
                    except Exception as exc:
                        self.log('warn', f'刷新恢复失败：{exc}；尝试回直播间')
                        recovered = {}
                    if recovered.get('matches') == 1 and not recovered.get('verification') and not recovered.get('manualVerification'):
                        self.log('info', f'批次 {batch} 恢复路径：原详情页继续，下一笔重新核对规格')
                    else:
                        self.log('info', f'批次 {batch} 恢复路径：回直播间“全部商品”重新匹配')
                        self.open_batch_target(page)
            self._batch_outcome = BatchOutcome('tested' if self.config.dry_run else 'completed', self.state['batch_completed'], '模拟完成，未创建订单' if self.config.dry_run else '全部待付款订单已确认，后续支付由用户处理')
        except Exception as exc:
            self.log('warn', f'批量停止原因：{exc}；已确认锁单 {self.state["batch_completed"]} 笔')
            raise


def checkbox_image(master, size: int, selected: bool, disabled: bool):
    """Supersampled, DPI-sized indicator; generated once, never by the worker."""
    image = tk.PhotoImage(master=master, width=size, height=size)
    background = (244, 246, 250)
    fill = ((165, 188, 224) if disabled else (56, 111, 202)) if selected else (255, 255, 255)
    border = (204, 213, 224) if disabled else (173, 188, 208)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            total = [0, 0, 0]
            for sy in range(4):
                for sx in range(4):
                    u = (x + (sx + .5) / 4) * 24 / size
                    v = (y + (sy + .5) / 4) * 24 / size
                    # Signed-distance rounded square, with a consistent one-pixel border.
                    dx, dy = abs(u - 11) - 5.5, abs(v - 12) - 5.5
                    distance = math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0) - 3
                    color = background if distance > 0 else (border if distance > -1.1 and not selected else fill)
                    if selected and distance <= 0:
                        for ax, ay, bx, by in ((6, 12, 9.5, 15.5), (9.5, 15.5, 16, 8.5)):
                            t = max(0, min(1, ((u-ax)*(bx-ax)+(v-ay)*(by-ay))/((bx-ax)**2+(by-ay)**2)))
                            if (u-ax-t*(bx-ax))**2 + (v-ay-t*(by-ay))**2 <= 1.05**2:
                                color = (255, 255, 255)
                    for channel in range(3):
                        total[channel] += color[channel]
            row.append('#' + ''.join(f'{round(channel/16):02x}' for channel in total))
        rows.append('{' + ' '.join(row) + '}')
    image.put(' '.join(rows))
    return image


class SmoothNotebook(ttk.Frame):
    """Stable-height pages and a small, cancellable UI-only transition."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.pack_propagate(False)
        self.pages = []
        self.labels = []
        self.selected_index = -1
        self.animation = None
        self.pill_x = None
        self.header_height = round(44 * max(1, float(self.tk.call('tk', 'scaling')) / 1.3333))
        self.header = tk.Canvas(self, height=self.header_height, background='#e9eef5', highlightthickness=0, takefocus=True)
        self.header.pack(fill='x', side='top')
        self.header.bind('<Configure>', lambda _: self.draw_header())
        self.header.bind('<Button-1>', self.click_tab)
        self.header.bind('<Motion>', lambda _: self.header.configure(cursor='hand2'))
        self.header.bind('<Left>', lambda _: self.cycle(-1))
        self.header.bind('<Right>', lambda _: self.cycle(1))
        self.bind('<Control-Tab>', lambda _: self.cycle(1))
        self.bind('<Control-Shift-Tab>', lambda _: self.cycle(-1))

    def add(self, page, *, text):
        self.pages.append(page)
        self.labels.append(text)
        if self.selected_index < 0:
            self.select(0)

    def tabs(self):
        return tuple(str(page) for page in self.pages)

    def index(self, tab):
        if tab == 'end':
            return len(self.pages)
        if isinstance(tab, int):
            if 0 <= tab < len(self.pages):
                return tab
        else:
            for index, page in enumerate(self.pages):
                if str(page) == str(tab):
                    return index
        raise tk.TclError(f'Unknown tab: {tab}')

    def fit_pages(self):
        if self.pages:
            self.configure(height=max(page.winfo_reqheight() for page in self.pages) + self.header_height,
                           width=max(page.winfo_reqwidth() for page in self.pages))
            self.draw_header()

    def cycle(self, direction):
        if self.pages:
            self.select((self.selected_index + direction) % len(self.pages))
        return 'break'

    def click_tab(self, event):
        if self.pages:
            self.header.focus_set()
            self.select(min(len(self.pages)-1, int(event.x / max(1, self.header.winfo_width()) * len(self.pages))))

    def draw_header(self):
        self.header.delete('all')
        if not self.pages:
            return
        width = self.header.winfo_width() / len(self.pages)
        x = self.selected_index * width if self.pill_x is None else self.pill_x
        h = self.header_height
        # Rounded selection pill; all labels share the same baseline and hit area.
        self.header.create_polygon(x+12, 5, x+width-12, 5, x+width-5, 5,
                                   x+width-5, 12, x+width-5, h-12, x+width-5, h-5,
                                   x+width-12, h-5, x+12, h-5, x+5, h-5, x+5, h-12,
                                   x+5, 12, x+5, 5, smooth=True, fill='#ffffff', outline='')
        for index, text in enumerate(self.labels):
            active = index == self.selected_index
            self.header.create_text((index+.5)*width, h/2, text=text,
                                    font=('Microsoft YaHei UI', 10, 'bold' if active else 'normal'),
                                    fill='#245fd6' if active else '#617086')

    def select(self, tab=None):
        if tab is None:
            return str(self.pages[self.selected_index]) if self.pages else ''
        index = self.index(tab)
        if index == self.selected_index:
            return
        previous = self.selected_index
        if self.animation:
            self.after_cancel(self.animation)
            self.animation = None
        if previous >= 0:
            self.pages[previous].place_forget()
        self.selected_index = index
        page = self.pages[index]
        offset = 10 if index > previous else -10
        page.place(x=0, y=self.header_height, relwidth=1, relheight=1, height=-self.header_height)
        page.lift()
        tk.Misc.lift(self.header)
        self.event_generate('<<NotebookTabChanged>>', when='tail')
        if previous < 0:
            self.draw_header()
            return
        start = time.monotonic()
        width = self.header.winfo_width() / len(self.pages)
        origin = self.pill_x if self.pill_x is not None else previous * width
        def frame():
            progress = min(1, (time.monotonic()-start)/.16)
            ease = 1 - (1-progress)**3
            self.pill_x = origin + (index*width-origin)*ease
            page.place_configure(x=round(offset*(1-ease)))
            self.draw_header()
            if progress < 1:
                self.animation = self.after(16, frame)
            else:
                self.animation = None
                self.pill_x = None
                self.draw_header()
        frame()


class SmoothScrollbar(tk.Canvas):
    """Slim rounded thumb, immediate dragging, stable gutter when unused."""
    def __init__(self, master, command, on_interact=None, **kwargs):
        scale = max(1, float(master.tk.call('tk', 'scaling')) / 1.3333)
        super().__init__(master, width=round(14*scale), highlightthickness=0,
                         background='#f4f6fa', takefocus=True, **kwargs)
        self.command = command
        self.on_interact = on_interact
        self.first, self.last = 0., 1.
        self.hover = False
        self.drag_offset = None
        self.bind('<Configure>', lambda _: self.draw())
        self.bind('<Enter>', lambda _: self.set_hover(True))
        self.bind('<Leave>', lambda _: self.set_hover(False))
        self.bind('<Button-1>', self.press)
        self.bind('<B1-Motion>', self.drag)
        self.bind('<ButtonRelease-1>', self.release)
        self.bind('<Up>', lambda _: self.scroll(-1))
        self.bind('<Down>', lambda _: self.scroll(1))
        self.bind('<Prior>', lambda _: self.scroll(-1, 'pages'))
        self.bind('<Next>', lambda _: self.scroll(1, 'pages'))

    def set(self, first, last):
        self.first = max(0., min(1., float(first)))
        self.last = max(self.first, min(1., float(last)))
        self.draw()

    def thumb_bounds(self):
        track = max(1, self.winfo_height()-8)
        span = self.last-self.first
        length = min(track, max(28, track*span))
        top = 4 + (track-length)*self.first / max(.000001, 1-span)
        return top, top+length, track

    def draw(self):
        self.delete('all')
        if self.last-self.first >= .999:
            return
        top, bottom, _ = self.thumb_bounds()
        width = 8 if self.hover or self.drag_offset is not None else 6
        self.create_line(self.winfo_width()/2, top+width/2,
                         self.winfo_width()/2, bottom-width/2,
                         width=width, capstyle='round',
                         fill='#809bc0' if self.hover or self.drag_offset is not None else '#becada')

    def set_hover(self, hover):
        self.hover = hover
        self.configure(cursor='hand2' if hover else '')
        self.draw()

    def press(self, event):
        if self.last-self.first >= .999:
            return
        self.focus_set()
        if self.on_interact:
            self.on_interact()
        top, bottom, _ = self.thumb_bounds()
        self.drag_offset = event.y-top if top <= event.y <= bottom else (bottom-top)/2
        self.drag(event)

    def drag(self, event):
        if self.drag_offset is None:
            return
        top, bottom, track = self.thumb_bounds()
        position = max(0., min(1., (event.y-4-self.drag_offset)/max(1, track-(bottom-top))))
        self.command('moveto', position*(1-(self.last-self.first)))
        self.draw()

    def release(self, _):
        self.drag_offset = None
        self.draw()

    def scroll(self, direction, units='units'):
        if self.on_interact:
            self.on_interact()
        self.command('scroll', direction, units)
        return 'break'


class App(tk.Tk):
    def __init__(self, license_controller=None) -> None:
        super().__init__()
        self.title("DY直播间自助工具")
        self.iconbitmap(str(Path(__file__).resolve().parent / "assets" / "app.ico"))
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
        self.time_picker = None
        self.scroll_motion = {}
        self.preferences_path=APP_DATA_DIR/'preferences.json'
        self.preferences_timer=None
        self.monitor_received_at=None
        self.monitor_stall_reported=False
        self.network_thread = None
        self.network_probe = None
        self.license_controller = license_controller
        self.account_store = AccountStore(APP_DATA_DIR)
        self.account_store_error = ''
        self.account_manager = None
        self.batch_queue = None
        self.login_account_id = None
        self.login_in_progress = False
        from update_client import UpdateClient, UpdateTransport
        from update_config import UPDATE_ORIGIN, UPDATE_PUBLIC_KEYS
        from update_ui import UpdateController
        self.updater = UpdateController(self,UpdateClient(APP_DATA_DIR,UPDATE_PUBLIC_KEYS,UpdateTransport(UPDATE_ORIGIN)))
        try:
            self.accounts = self.account_store.load()
        except AccountStoreError as exc:
            self.accounts = ()
            self.account_store_error = str(exc)
            self.log('warn', self.account_store_error + '；多账号功能不可用，原单账号不受影响')
        self.build_ui()
        self.restore_preferences()
        for name,var in self.vars.items():
            if name not in ('dry_run',):
                var.trace_add('write',self.schedule_preferences_save)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(120, self.drain_logs)
        if self.license_controller:
            self.license_controller.start_background(lambda status: self.ui_queue.put(lambda: self.on_license_change(status)))
        if getattr(sys,'frozen',False):
            self.after_idle(self.updater.check)

    def update_blocked(self, *, ignore_updater=False) -> bool:
        return bool(self.closing or self.network_busy() or
                    (self.worker and self.worker.is_alive()) or
                    (not ignore_updater and self.updater.busy()))

    def update_protected_paths(self):
        return (APP_DATA_DIR,DEFAULT_PROFILE_DIR,resolve_profile_directory(self.vars['profile_dir'].get()))

    def set_update_busy(self,busy):
        for name in ('start_button','login_button','logout_button','network_start_button','account_manager_button','update_button'):
            button=getattr(self,name,None)
            if button:
                button.configure(state='disabled' if busy or self.closing or self.network_busy() or
                                 (self.worker and self.worker.is_alive()) else 'normal')

    def on_license_change(self, status):
        if not self.closing:
            self.log("info" if status.allowed else "warn", "授权状态：" + license_status_text(status))

    def restore_preferences(self) -> None:
        try:
            values=json.loads(self.preferences_path.read_text(encoding='utf-8'))
            if not isinstance(values,dict):
                return
            for name,var in self.vars.items():
                if name in ('dry_run',) or name not in values:
                    continue
                value=values[name]
                if isinstance(var,tk.BooleanVar):
                    if isinstance(value,bool):
                        var.set(value)
                elif isinstance(value,str):
                    var.set(value)
        except (OSError,ValueError,tk.TclError):
            pass
        finally:
            self.vars['dry_run'].set(False)

    def schedule_preferences_save(self,*_) -> None:
        if self.preferences_timer:
            self.after_cancel(self.preferences_timer)
        def flush():
            self.preferences_timer=None
            self.save_preferences()
        self.preferences_timer=self.after(800,flush)

    def save_preferences(self) -> None:
        if self.preferences_timer:
            self.after_cancel(self.preferences_timer)
        self.preferences_timer=None
        values={name:var.get() for name,var in self.vars.items() if name not in ('dry_run',)}
        values['profile_dir']=str(resolve_profile_directory(values.get('profile_dir','')))
        try:
            self.preferences_path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.preferences_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temporary,self.preferences_path)
        except OSError as exc:
            self.log('warn','配置保存失败：'+str(exc))

    def destroy(self) -> None:
        # Cancel UI animation and refresh callbacks before the Tcl widgets go away.
        for timer in self.tk.call('after', 'info'):
            try:
                # The callback may belong to a child widget. Let that owner delete
                # its Tcl command during destruction, instead of deleting it twice.
                self.tk.call('after', 'cancel', timer)
            except tk.TclError:
                pass
        super().destroy()

    def add_field(self, parent, row: int, label: str, name: str, default: str, width: int = 48) -> None:
        hints = {
            "live_url": "粘贴直播间地址，例如 https://live.douyin.com/…",
            "product_name": "填写商品名称中的关键文字；至少填写名称或编号一项。",
            "product_id": "直播间商品列表中的编号，不是规格名称。不确定可以留空。",
            "target_price": "填写想购买的金额（元）；多规格商品填写选中规格的价格。",
            "schedule_windows": "每行填写开始和结束时间，例如 12:00-13:00；支持 23:30-00:20 跨午夜，每段最长一小时。全部留空立即监控。",
            "batch_live_url": "填写直播间链接，从全部商品定位目标。",
            "batch_product_id": "直播间商品编号；与关键词同时填写时必须匹配同一商品。",
            "batch_product_name": "在直播间全部商品中匹配；关键词和编号至少填写一项，两项必须匹配同一商品。",
            "batch_target_price": "每件商品的价格（元）。支付总额按单价 × 每单数量核对。",
            "buy_times": "需要创建多少笔订单，例如 3 表示连续下 3 单。",
            "buy_quantity": "每笔订单购买几件，与订单数是两个不同的设置。",
            "batch_interval_ms": "两单之间的等待时间；800 毫秒 = 0.8 秒。",
            "poll_ms": "未锁定商品时的扫描间隔，默认 35 毫秒。锁定后改为监听按钮变化。",
            "monitor_duration_ms": "仅用于区间留空的即时监控，最长一小时；定时监控按填写的结束时间停止。",
            "prewarm_ms": "比设置的监控时间提前开始检查；1000 毫秒 = 提前 1 秒。",
            "click_timeout_ms": "等待单次点击完成的最长时间，默认 450 毫秒。",
            "open_panel_interval_ms": "尝试打开商品列表的最短间隔，默认 180 毫秒。",
            "detail_refresh_ms": "详情实时监听；等待时每隔此时间安全返回并重新打开，默认2000毫秒，最低500。没有安全返回入口时只监听，不强制刷新。",
            "profile_dir": "保存登录状态的浏览器资料目录；通常无需修改。两个模块共用。",
            "option_names": "不同规格组用 | 分隔：颜色=粉色 | 尺寸=大号。唯一且已默认选中、未缺货的规格可省略。",
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

    def add_time_range(self, parent, slot: int) -> None:
        name = 'schedule_windows' if slot == 0 else f'schedule_range_{slot+1}'
        row = (4+slot)*2
        ttk.Label(parent, text=f'监控区间 {slot+1}').grid(row=row, column=0, sticky='w', pady=(8,2))
        line = ttk.Frame(parent)
        line.grid(row=row, column=1, sticky='ew', pady=(8,2))
        line.columnconfigure(0, weight=1)
        self.vars[name] = tk.StringVar()
        ttk.Entry(line, textvariable=self.vars[name], width=20).grid(row=0, column=0, sticky='ew')
        ttk.Button(line, text='选择时间', style='Quiet.TButton', command=lambda: self.open_time_picker(name, slot+1)).grid(row=0, column=1, padx=(8,0))
        if slot == 0:
            ttk.Label(parent, text='可输入或选择时间 · 每段≤1小时 · 支持跨午夜\n全部留空立即监控', style='Hint.TLabel').grid(row=row+1, column=1, sticky='w', pady=(4,6))

    def open_time_picker(self, name: str, slot: int) -> None:
        if self.time_picker and self.time_picker.winfo_exists():
            self.time_picker.lift()
            return
        popup = tk.Toplevel(self)
        self.time_picker = popup
        popup.title(f'选择监控区间 {slot}')
        popup.transient(self)
        popup.resizable(False, False)
        popup.configure(background='#f4f6fa')
        body=ttk.Frame(popup, padding=24)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='设置监控时间', style='Section.TLabel').grid(row=0,column=0,columnspan=4,sticky='w',pady=(0,16))
        now=datetime.now(AUTOMATION_TIMEZONE)
        ends=now+timedelta(minutes=30)
        defaults=[(now.hour,now.minute,0),(ends.hour,ends.minute,0)]
        try:
            point=parse_schedule_range(self.vars[name].get())
            start=point.hour*3600+point.minute*60+point.second
            end=(start+point.duration_ms//1000)%86400
            defaults=[(point.hour,point.minute,point.second),(end//3600,end//60%60,end%60)]
        except ValueError:
            pass
        values=[]
        for row,label in enumerate(('开始时间','结束时间'),1):
            ttk.Label(body,text=label).grid(row=row,column=0,padx=(0,12),pady=8)
            parts=[]
            for col,limit in enumerate((24,60,60),1):
                var=tk.StringVar(value=f'{defaults[row-1][col-1]:02d}')
                ttk.Combobox(body,textvariable=var,values=[f'{n:02d}' for n in range(limit)],width=4,state='readonly').grid(row=row,column=col,padx=4,pady=8)
                parts.append(var)
            values.append(parts)
        message=tk.StringVar(value='小时 / 分钟 / 秒 · 支持跨午夜，每段最长一小时')
        ttk.Label(body,textvariable=message,style='Hint.TLabel',wraplength=340).grid(row=3,column=0,columnspan=4,sticky='w',pady=(12,16))
        def apply_time():
            raw='-'.join(':'.join(part.get() for part in group) for group in values)
            try:
                parse_schedule_range(raw)
            except ValueError as exc:
                message.set(str(exc))
                return
            self.vars[name].set(raw)
            popup.destroy()
        actions=ttk.Frame(body)
        actions.grid(row=4,column=0,columnspan=4,sticky='ew')
        ttk.Button(actions,text='清除此区间',command=lambda: (self.vars[name].set(''),popup.destroy())).pack(side='left')
        ttk.Button(actions,text='确定',style='Primary.TButton',command=apply_time).pack(side='right')
        popup.bind('<Return>',lambda _: apply_time())
        popup.bind('<Escape>',lambda _: popup.destroy())
        popup.update_idletasks()
        popup.geometry(f'+{self.winfo_rootx()+max(0,(self.winfo_width()-popup.winfo_reqwidth())//2)}+{self.winfo_rooty()+150}')
        popup.grab_set()

    def cancel_scroll(self, widget):
        state = self.scroll_motion.pop(widget, None)
        if state and state.get('timer'):
            self.after_cancel(state['timer'])

    def smooth_panel_scroll(self, canvas, delta: int) -> None:
        first, last = canvas.yview()
        span = last-first
        if span >= .999 or span <= 0:
            return
        old = self.scroll_motion.get(canvas)
        total_height = canvas.winfo_height()/span
        target = max(0., min(1-span, (old['target'] if old else first)-delta/120*86/total_height))
        self.cancel_scroll(canvas)
        state = {'target': target, 'timer': None}
        self.scroll_motion[canvas] = state
        start = time.monotonic()
        def frame():
            if not canvas.winfo_exists():
                self.scroll_motion.pop(canvas, None)
                return
            progress = min(1., (time.monotonic()-start)/.18)
            ease = 1-(1-progress)**3
            canvas.yview_moveto(first+(target-first)*ease)
            if progress >= 1:
                self.scroll_motion.pop(canvas, None)
            else:
                state['timer'] = self.after(16, frame)
        frame()

    def scroll_panel(self, parent):
        shell = ttk.Frame(parent)
        shell.pack(fill="both", expand=True)
        canvas = tk.Canvas(shell, background="#f4f6fa", highlightthickness=0, borderwidth=0, yscrollincrement=1)
        bar = SmoothScrollbar(shell, command=canvas.yview, on_interact=lambda: self.cancel_scroll(canvas))
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=bar.set)
        body = ttk.Frame(canvas, padding=16)
        window = canvas.create_window((0,0), window=body, anchor="nw")
        body.columnconfigure(1, weight=1)
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        def wheel(event):
            self.smooth_panel_scroll(canvas,event.delta)
            return "break"
        canvas.bind('<MouseWheel>', wheel)
        bar.bind('<MouseWheel>', wheel)
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
        style.configure("TEntry", bordercolor='#dbe2ea', lightcolor='#dbe2ea', darkcolor='#dbe2ea', padding=(9,7))
        style.map('TEntry',bordercolor=[('focus','#6289cf')],lightcolor=[('focus','#6289cf')],darkcolor=[('focus','#6289cf')])
        style.configure("TButton", padding=(12,8), background='#edf1f6', borderwidth=0, foreground='#334155')
        style.map('TButton',background=[('pressed','#d7e1ee'),('active','#e3eaf3')],foreground=[('disabled','#9aa8b8')])
        style.configure('Quiet.TButton',padding=(10,6),foreground='#386bb5')
        style.configure('TCombobox',padding=(7,6),fieldbackground='white',arrowsize=14)
        style.map('TCombobox',fieldbackground=[('readonly','white')],foreground=[('readonly','#334155')])
        style.configure("TCheckbutton", padding=(0,4))
        self.checkbox_images=[]
        scale=max(1,float(self.tk.call('tk','scaling'))/1.3333)
        size=round(24*scale)
        for selected,disabled in ((False,False),(True,False),(False,True),(True,True)):
            self.checkbox_images.append(checkbox_image(self, size, selected, disabled))
        style.element_create('Clean.Check.indicator','image',self.checkbox_images[0],('disabled','selected',self.checkbox_images[3]),('selected',self.checkbox_images[1]),('disabled',self.checkbox_images[2]),width=size,border=0,sticky='')
        style.layout('TCheckbutton',[('Checkbutton.padding',{'sticky':'nswe','children':[('Clean.Check.indicator',{'side':'left','sticky':''}),('Checkbutton.focus',{'side':'left','sticky':'w','children':[('Checkbutton.label',{'sticky':'nswe'})]})]})])
        style.map('TCheckbutton',foreground=[('disabled','#9aa8b8'),('active','#245fd6')])
        style.configure("TNotebook", tabmargins=(0,4,0,0))
        style.configure("TNotebook.Tab", padding=(18,10),borderwidth=0)
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
        from app_version import APP_VERSION
        updates=ttk.Frame(header)
        updates.pack(side='right',anchor='n')
        self.update_status=tk.StringVar(value='版本 '+APP_VERSION)
        ttk.Label(updates,textvariable=self.update_status,style='Hint.TLabel').pack(side='left',padx=8)
        self.update_button=ttk.Button(updates,text='检查更新',style='Quiet.TButton',command=lambda:self.updater.check(manual=True))
        self.update_button.pack(side='left')
        ttk.Button(updates,text='导出更新诊断',style='Quiet.TButton',command=self.updater.export_diagnostics).pack(side='left',padx=6)
        ttk.Label(header,text="DY 直播间助手",style="Title.TLabel").pack(anchor="w")
        ttk.Label(header,text="① 登录并保存账号    →    ② 选择任务、填写商品    →    ③ 先测试，再开始下单",
                  style="Hint.TLabel").pack(anchor="w",pady=(6,0))

        left = ttk.Frame(root)
        left.grid(row=1,column=0,sticky="nsew",padx=(0,16))
        panel = self.scroll_panel(left)
        panel.configure(padding=(16, 0, 16, 16))
        panel.columnconfigure(0,weight=1)
        self.task_tabs = SmoothNotebook(panel)
        self.task_tabs.grid(row=0,column=0,columnspan=2,sticky="ew")
        form = ttk.Frame(self.task_tabs,padding=16)
        batch = ttk.Frame(self.task_tabs,padding=16)
        self.task_tabs.add(form,text="DY直播间商品秒杀")
        self.task_tabs.add(batch,text="DY直播间批量下单")
        form.columnconfigure(1,weight=1)
        batch.columnconfigure(1,weight=1)
        self.add_field(form,0,"直播间链接","live_url",DEFAULT_LIVE_URL,32)
        self.add_field(form,1,"商品关键词","product_name",DEFAULT_PRODUCT_NAME,32)
        self.add_field(form,2,"商品编号","product_id","1",16)
        self.add_field(form,3,"目标金额（元）","target_price","20",16)
        for slot in range(4):
            self.add_time_range(form,slot)
        self.add_check(form,16,1,"售罄后继续监控（等待补库存）","continue_after_sold_out",True)
        self.add_check(form,17,1,"普通锁单（提前绑定，开售后提交一次）","purchase_speed_priority",False)
        self.add_check(form,18,1,"脚本锁单（页面事件，可能不被平台接受）","experimental_purchase_click",False)
        self.add_check(form,19,1,"详情页锁单（推荐：提前打开，待付款后停止）","detail_lock_mode",True)
        ttk.Label(form,text="开售前即可启动：提前锁定商品，按钮开放后进入购买流程。",
                  style="Hint.TLabel",wraplength=450).grid(row=20,column=0,columnspan=2,sticky="w",pady=(10,0))
        self.add_field(batch,0,"直播间链接","batch_live_url","",32)
        self.add_field(batch,1,"商品关键词","batch_product_name","",32)
        self.add_field(batch,2,"目标单价（元）","batch_target_price","6",16)
        self.add_field(batch,3,"订单数（笔）","buy_times","1",16)
        self.add_field(batch,4,"每单数量（件）","buy_quantity","1",16)
        self.add_field(batch,5,"两单间隔（毫秒）","batch_interval_ms","800",16)
        self.add_field(batch,6,"商品编号","batch_product_id","",16)
        accounts = ttk.LabelFrame(batch,text='多账号 · 按顺序执行',padding=12)
        accounts.grid(row=14,column=0,columnspan=2,sticky='ew',pady=(14,0))
        accounts.columnconfigure(0,weight=1)
        self.add_check(accounts,0,0,'启用多账号依次下单','batch_multi_account',False)
        self.account_manager_button = ttk.Button(accounts,text='管理账号',style='Quiet.TButton',command=self.open_account_manager)
        self.account_manager_button.grid(row=0,column=1,sticky='e',padx=(8,0))
        account_hint = ttk.Label(accounts,text='默认关闭。管理账号 → 登录并保存 → 设置数量和顺序。\n开启后按各账号设置执行；异常停止整队，先核对订单。',style='Hint.TLabel',wraplength=450)
        account_hint.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(6,0))
        accounts.bind('<Configure>',lambda event: account_hint.configure(wraplength=max(140,event.width-28)))
        ttk.Label(batch,text="每笔确认待付款即锁单成功；订单数是独立订单笔数，每单数量是每笔购买件数。",
                  style="Hint.TLabel",wraplength=450).grid(row=15,column=0,columnspan=2,sticky="w",pady=(10,0))

        rules = ttk.LabelFrame(panel,text="下单方式 · 两个模块共用",padding=14)
        rules.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(16,0))
        rules.columnconfigure(0,weight=1)
        self.add_check(rules,0,0,"只测试，不下单（DRY_RUN）","dry_run",False)
        ttk.Label(rules,text="先检查商品匹配；关闭后才会执行真实购买。",
                  style="Hint.TLabel").grid(row=1,column=0,sticky="w",padx=8)
        self.add_check(rules,2,0,"严格核对目标价格","strict_price_match",False)
        self.add_check(rules,5,0,"严格匹配商品名称与编号（秒杀）","strict_product_match",False)
        ttk.Label(rules,text="开启时不使用同价商品兜底；多个匹配商品会停止并提示。",style='Hint.TLabel').grid(row=6,column=0,sticky='w',padx=8)

        sku = ttk.LabelFrame(rules,text="商品规格 · 两个模块共享",padding=14)
        sku.grid(row=7,column=0,columnspan=2,sticky="ew",pady=(16,0))
        sku.columnconfigure(1,weight=1)
        self.add_check(sku,0,0,"启用商品多选项","multi_option_enabled",False)
        # A separate field frame prevents the checkbox row from colliding with helper text.
        names = ttk.Frame(sku)
        names.grid(row=1,column=0,columnspan=2,sticky="ew")
        names.columnconfigure(1,weight=1)
        self.add_field(names,0,"选项名称","option_names","",28)
        self.option_entry = next(w for w in names.winfo_children() if isinstance(w,ttk.Entry))
        examples = ttk.Frame(sku)
        examples.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(8,0))
        examples.columnconfigure(0,weight=1)
        ttk.Label(examples,text='多规格填写示例',style='Section.TLabel').grid(row=0,column=0,sticky='w',pady=(0,5))
        ttk.Button(examples,text='复制多规格示例',command=lambda: (self.clipboard_clear(), self.clipboard_append('机身颜色=曜石黑 | 存储容量=12GB+512GB | 网络类型=全网通'))).grid(row=0,column=1,padx=8)
        example_labels = []
        for row, text in enumerate((
            '机身颜色=曜石黑 | 存储容量=12GB+512GB | 网络类型=全网通',
            '电脑：套餐类型=套餐一 | 硬盘容量=1TB',
            '日常用品：规格名=规格名称',
            '名称以商品页面为准；不同规格组用 | 分隔。',
            '只有一个选项且已默认选中、未缺货的规格可不填。',
        ),1):
            label = ttk.Label(examples,text=text,style='Hint.TLabel',wraplength=450)
            label.grid(row=row,column=0,sticky='ew',pady=2)
            example_labels.append(label)
        examples.bind('<Configure>',lambda event: [label.configure(wraplength=max(140,event.width)) for label in example_labels])

        self.side_tabs = SmoothNotebook(root)
        self.side_tabs.grid(row=1,column=1,sticky="nsew")
        logs = ttk.Frame(self.side_tabs,padding=12)
        guide = ttk.Frame(self.side_tabs)
        advanced = ttk.Frame(self.side_tabs)
        network = ttk.Frame(self.side_tabs)
        self.side_tabs.add(logs,text="运行日志")
        self.side_tabs.add(guide,text="使用指南")
        self.side_tabs.add(advanced,text="高级设置")
        self.side_tabs.add(network,text="网络检测")
        self.build_network_panel(network)
        logs.columnconfigure(0,weight=1)
        logs.rowconfigure(1,weight=1)
        ttk.Label(logs,text="实时监控 · 仅日志每 5 秒刷新",style="Section.TLabel").grid(row=0,column=0,sticky="w",pady=(0,12))
        self.log_text = tk.Text(logs,wrap="word",height=20,width=36,font=("Microsoft YaHei UI",9),
                                background="#ffffff",foreground="#334155",relief="flat",padx=14,pady=14,spacing1=3,spacing3=5,insertbackground='#386fca',selectbackground='#dbeafe')
        self.log_text.grid(row=1,column=0,sticky="nsew")
        bar = SmoothScrollbar(logs, command=self.log_text.yview,
                              on_interact=lambda: self.cancel_scroll(self.log_text))
        bar.grid(row=1,column=1,sticky="ns")
        self.log_text.configure(yscrollcommand=bar.set)
        def log_wheel(event):
            self.smooth_panel_scroll(self.log_text, event.delta)
            return 'break'
        self.log_text.bind('<MouseWheel>', log_wheel)
        bar.bind('<MouseWheel>', log_wheel)
        self.log_text.tag_configure("warn",foreground="#996500")
        self.log_text.tag_configure("error",foreground="#c33232")
        tools = ttk.Frame(logs)
        tools.grid(row=2,column=0,columnspan=2,sticky="ew",pady=(10,0))
        ttk.Button(tools,text="清空日志",command=self.clear_logs).pack(side="left")
        ttk.Button(tools,text="诊断文件",command=self.open_diagnostics).pack(side="left",padx=(8,0))
        help_body = self.scroll_panel(guide)
        for i,(title,detail) in enumerate([
            ("先登录账号","点击底部“登录抖音”，在浏览器完成登录，再点击“完成登录并保存”。"),
            ("日常秒杀监控","最多四段每日监控区间，例如12:00-13:00、23:30-00:20，支持跨午夜，每段最长一小时；全部留空立即监控。"),
            ("批量下单","订单数是下几单；每单数量是每笔买几件。每单确认待付款后继续。"),
            ("多账号批量","管理账号→逐个登录保存→勾选加入队列并保存修改→开启多账号。按列表顺序执行；异常停止整队，先核对订单。全部待付款订单确认后切下一账号。"),
            ("先运行测试","“只测试，不下单”默认关闭；测试阶段可主动开启，先检查匹配和规格。"),
            ("商品多选项","机身颜色=曜石黑 | 存储容量=12GB+512GB | 网络类型=全网通\n电脑：套餐类型=套餐一 | 硬盘容量=1TB\n日常用品：规格名=规格名称\n名称以页面为准，用 | 分隔不同组。只有一个选项且已默认选中、未缺货的规格可不填。"),
            ("价格怎么填写","秒杀填写目标金额；批量填写单价。多选项在选中后核对金额，批量按单价乘数量核对。"),
            ("等待开售与支付","初始已抢光时等待上架库存；开售后再次售罄按开关继续或停止。成功及未抢到按状态变化提示。支付按钮点击后需等待平台成功确认。"),
            ("详情页锁单","提前打开目标详情，以待付款状态和新订单号确认锁单。识别到验证码时暂停刷新和点击，保留浏览器等待本人验证；正确详情恢复后继续，不延长监控时间。不会继续付款或放弃支付，实际提交按钮仍可能扣款。"),
            ("高级设置与单位","默认参数通常无需调整。1000 毫秒等于 1 秒；调低轮询无法改变平台更新速度。"),
        ]):
            ttk.Label(help_body,text=title,style="Section.TLabel").grid(row=i*2,column=0,columnspan=2,sticky="w",pady=(12,4))
            ttk.Label(help_body,text=detail,style="Hint.TLabel",wraplength=260).grid(row=i*2+1,column=0,columnspan=2,sticky="w")
        adv = self.scroll_panel(advanced)
        for row,(label,name,default) in enumerate([
            ("扫描间隔（毫秒）","poll_ms","35"),
            ("即时监控时长（毫秒，最长1小时）","monitor_duration_ms","1800000"),
            ("提前预热（毫秒）","prewarm_ms","1000"),
            ("点击超时（毫秒）","click_timeout_ms","450"),
            ("面板间隔（毫秒）","open_panel_interval_ms","180"),
            ("详情重新打开间隔（毫秒，最低500）","detail_refresh_ms","2000"),
            ("登录资料目录","profile_dir",str(DEFAULT_PROFILE_DIR)),
        ]):
            self.add_field(adv,row,label,name,default,18)
        switches = ttk.LabelFrame(adv,text="浏览器与诊断",padding=8)
        switches.grid(row=14,column=0,columnspan=2,sticky="ew",pady=(16,0))
        self.add_check(switches,0,0,"不显示浏览器窗口（无头模式）","headless",False)
        self.add_check(switches,1,0,"任务成功后关闭浏览器","close_browser_on_finish",False)
        self.add_check(switches,2,0,"允许点击预约 / 开售提醒","allow_reservation_click",False)
        self.add_check(switches,3,0,"保存截图和页面，便于排查问题","save_diagnostics",False)

        footer = ttk.Frame(root)
        footer.grid(row=2,column=0,columnspan=2,sticky="ew",pady=(18,0))
        self.login_button = ttk.Button(footer,text="1. 登录抖音",command=self.start_login)
        self.login_button.pack(side="left")
        self.finish_login_button = ttk.Button(footer,text="完成登录并保存",command=self.finish_login,state="disabled")
        self.finish_login_button.pack(side="left",padx=8)
        self.logout_button=ttk.Button(footer,text='退出当前账号',command=self.logout_current_account)
        self.logout_button.pack(side='left')
        self.start_button = ttk.Button(footer,text="开始秒杀监控",style="Primary.TButton",command=self.start_task)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(footer,text="停止任务",command=self.stop_task,state="disabled")
        self.stop_button.pack(side="right",padx=8)
        self.mode_caption = tk.StringVar()
        ttk.Label(footer,textvariable=self.mode_caption,style="Hint.TLabel").pack(side="right",padx=12)
        def refresh(*_):
            batch_selected = self.task_tabs.index(self.task_tabs.select()) == 1
            self.start_button.configure(text="开始批量下单" if batch_selected else "开始秒杀监控")
            mode = "测试模式" if self.vars["dry_run"].get() else "真实下单"
            selected_mode=effective_flash_lock_mode(self.config_from_form())
            pay = ({'detail':'详情页锁单','script':'脚本锁单','normal':'普通锁单'}.get(selected_mode,'停在支付前')
                   if not batch_selected else '确认待付款后继续')
            self.mode_caption.set(mode + " · " + pay)
            self.option_entry.configure(state="normal" if self.vars["multi_option_enabled"].get() else "disabled")
        self.task_tabs.bind("<<NotebookTabChanged>>",refresh)
        for name in ("dry_run","multi_option_enabled","detail_lock_mode","purchase_speed_priority","experimental_purchase_click"):
            self.vars[name].trace_add("write",refresh)
        refresh()
        # Settle child requests once before measuring; switching never resizes pages.
        self.update_idletasks()
        self.task_tabs.fit_pages()
        self.side_tabs.fit_pages()

    def build_network_panel(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        ttk.Label(parent, text='网络检测', style='Section.TLabel').grid(row=0,column=0,sticky='w',padx=16,pady=(16,8))
        ttk.Label(parent, text='检测站点响应、波动和下载吞吐。建议在开售前检测；测速期间暂停启动任务。',
                  style='Hint.TLabel',wraplength=300).grid(row=1,column=0,sticky='ew',padx=16,pady=(0,12))
        actions = ttk.Frame(parent)
        actions.grid(row=2,column=0,sticky='ew',padx=16)
        self.network_start_button = ttk.Button(actions,text='开始检测',style='Primary.TButton',command=self.start_network_test)
        self.network_start_button.pack(side='left')
        self.network_stop_button = ttk.Button(actions,text='停止检测',command=self.stop_network_test,state='disabled')
        self.network_stop_button.pack(side='left',padx=8)
        self.network_result = tk.Text(parent,wrap='word',height=18,width=32,font=('Microsoft YaHei UI',9),
                                      background='#ffffff',foreground='#334155',relief='flat',padx=14,pady=14,spacing3=6)
        self.network_result.grid(row=3,column=0,sticky='nsew',padx=16,pady=12)
        bar = SmoothScrollbar(parent,command=self.network_result.yview,
                              on_interact=lambda: self.cancel_scroll(self.network_result))
        bar.grid(row=3,column=1,sticky='ns',pady=12)
        self.network_result.configure(yscrollcommand=bar.set)
        def wheel(event):
            self.smooth_panel_scroll(self.network_result,event.delta)
            return 'break'
        self.network_result.bind('<MouseWheel>',wheel)
        bar.bind('<MouseWheel>',wheel)
        self.network_result.insert('1.0','点击“开始检测”，获取当前网络参考结果。\n\n下载检测最多使用约 8 MiB 流量。抖音站点每轮 6 个样本，失败率是请求失败比例，不是丢包率。\n\n检测使用直连 HTTPS；浏览器代理线路可能不同。网速不能单独推算抢购成功率。')
        self.network_result.configure(state='disabled')
        self.network_status = tk.StringVar(value='尚未检测')
        ttk.Label(parent,textvariable=self.network_status,style='Hint.TLabel',wraplength=300).grid(row=4,column=0,sticky='ew',padx=16)
        self.network_progress = ttk.Progressbar(parent,mode='indeterminate')
        self.network_progress.grid(row=5,column=0,sticky='ew',padx=16,pady=(8,16))

    def network_busy(self) -> bool:
        return bool(self.network_thread and self.network_thread.is_alive())

    def start_network_test(self) -> None:
        if self.updater.busy():
            messagebox.showwarning('更新进行中','请先完成或取消更新。'); return
        if self.closing or self.network_busy():
            return
        if self.worker and self.worker.is_alive():
            messagebox.showwarning('任务运行中','请先停止监控或登录任务，避免测速占用抢购网络。')
            return
        self.network_start_button.configure(state='disabled')
        self.network_stop_button.configure(state='normal')
        self.start_button.configure(state='disabled')
        self.login_button.configure(state='disabled')
        self.network_status.set('正在检测…')
        self.network_progress.start(12)
        def progress(text):
            self.ui_queue.put(lambda text=text: self.network_status.set(text) if not self.closing else None)
        self.network_probe = NetworkProbe(progress)
        self.network_thread = threading.Thread(target=self.network_test_worker,daemon=True)
        self.network_thread.start()

    def network_test_worker(self) -> None:
        try:
            result = self.network_probe.run()
            self.ui_queue.put(lambda: self.show_network_result(result) if not self.closing else None)
        except ProbeCancelled:
            self.ui_queue.put(lambda: self.network_status.set('检测已停止，原有结果保留') if not self.closing else None)
        except Exception as exc:
            message = '检测已停止，原有结果保留' if self.network_probe.stop.is_set() else '检测失败：'+str(exc)[:200]
            self.ui_queue.put(lambda message=message: self.network_status.set(message) if not self.closing else None)
        finally:
            self.ui_queue.put(self.finish_network_test)

    def finish_network_test(self) -> None:
        if self.closing:
            return
        self.network_progress.stop()
        self.network_thread = None
        self.network_start_button.configure(state='normal')
        self.network_stop_button.configure(state='disabled')
        self.reset_controls()

    def stop_network_test(self) -> None:
        if self.network_probe:
            self.network_probe.cancel()
        self.network_status.set('正在停止检测…')
        self.network_stop_button.configure(state='disabled')

    def show_network_result(self, result) -> None:
        def ms(value):
            return '不可用' if value is None else f'{value:.2f} ms'
        lines = ['检测时间：'+datetime.now(AUTOMATION_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')]
        for title, key in (('抖音直播站点','site'),('通用网络（Cloudflare）','general')):
            sample = result[key]
            lines.extend(['',title, f"HTTPS 首包中位延迟：{ms(sample['median_ms'])}",
                          f"响应抖动：{ms(sample['jitter_ms'])}",
                          f"最快 / 最慢：{ms(sample['min_ms'])} / {ms(sample['max_ms'])}",
                          f"请求失败率：{sample['request_failure_pct']}%（{sample['responses']}/{sample['samples']} 返回响应）",
                          f"HTTP 异常：{sample['http_errors']} 次"])
            if sample.get('errors'):
                lines.append('最近失败：'+sample['errors'][-1])
        speed = result['download']
        lines.extend(['', '单连接下载吞吐：'+(f"{speed['mbps']:.2f} Mbps（{speed['mbps']/8:.2f} MB/s）" if speed['mbps'] is not None else '不可用'),
                      f"下载流量：{speed['bytes']/1048576:.2f} MiB"])
        if speed.get('error'):
            lines.append('下载检测：'+speed['error'])
        if speed.get('partial'):
            lines.append('达到时间上限，以上吞吐按已收到的数据估算。')
        assessment = result['assessment']
        lines.extend(['', '抢购网络状况：'+assessment['level'],assessment['detail'],
                      '', '成功率：无法单凭网速计算。还取决于库存、竞争人数、平台处理、登录及支付验证。',
                      '经验阈值：延迟≤100ms 且抖动≤20ms 评为较好；延迟>300ms、抖动>80ms 或存在连接失败评为较差。',
                      'HTTPS 首包不是 ICMP ping，包含首次 DNS/TLS、服务器处理。站点检测不代表下单接口延迟；样本少，仅供参考。',
                      '直连 HTTPS 与浏览器代理线路可能不同；Cloudflare 吞吐不是抖音带宽或运营商宽带的峰值。'])
        self.network_result.configure(state='normal')
        self.network_result.delete('1.0','end')
        self.network_result.insert('1.0','\n'.join(lines))
        self.network_result.configure(state='disabled')
        self.network_status.set(f"检测完成 · {result.get('elapsed_s',0):g} 秒")

    def config_from_form(self) -> AutomationConfig:
        get = lambda name: self.vars[name].get()
        batch = self.task_tabs.index(self.task_tabs.select()) == 1
        return AutomationConfig(
            mode="batch" if batch else "flash",
            product_url="",
            buy_times=as_int(get("buy_times"), 1, 1),
            buy_quantity=as_int(get("buy_quantity"), 1, 1),
            batch_interval_ms=as_int(get("batch_interval_ms"), 800, 100),
            live_url=str(get("batch_live_url" if batch else "live_url")).strip(),
            product_name=str(get("batch_product_name" if batch else "product_name")).strip(),
            product_id=str(get("batch_product_id" if batch else "product_id")).strip(),
            target_price=as_float(get("batch_target_price" if batch else "target_price"), 0, 0),
            poll_ms=as_int(get("poll_ms"), 35, 20),
            monitor_duration_ms=as_int(get("monitor_duration_ms"), 1800000, 1000),
            prewarm_ms=as_int(get("prewarm_ms"), 1000, 0),
            click_timeout_ms=as_int(get("click_timeout_ms"), 450, 100),
            open_panel_interval_ms=as_int(get("open_panel_interval_ms"), 180, 50),
            schedule_windows_raw="" if batch else ','.join(str(get(key)).strip() for key in ('schedule_windows', 'schedule_range_2', 'schedule_range_3', 'schedule_range_4') if str(get(key)).strip()),
            continue_after_sold_out=bool(get("continue_after_sold_out")),
            purchase_speed_priority=bool(get("purchase_speed_priority")),
            experimental_purchase_click=bool(get("experimental_purchase_click")),
            detail_lock_mode=not batch and bool(get('detail_lock_mode')),
            detail_refresh_ms=as_int(get('detail_refresh_ms'), 2000, 500),
            profile_dir=str(resolve_profile_directory(get("profile_dir"))),
            strict_price_match=bool(get("strict_price_match")),
            strict_product_match=bool(get("strict_product_match")),
            dry_run=bool(get("dry_run")),
            headless=bool(get("headless")),
            close_browser_on_finish=bool(get("close_browser_on_finish")),
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
                if level == 'monitor':
                    self.monitor_received_at=time.monotonic()
                    self.monitor_stall_reported=False
                    stamp = f'[{now_text()}] {message}'
                    if 'monitor_line' in self.log_text.mark_names():
                        self.log_text.delete('monitor_line', 'monitor_line lineend')
                        self.log_text.insert('monitor_line', stamp, 'error' if '状态 失败' in message else 'info')
                    else:
                        self.log_text.mark_set('monitor_line', 'end-1c')
                        self.log_text.mark_gravity('monitor_line', 'left')
                        self.log_text.insert('end', stamp+'\n', 'error' if '状态 失败' in message else 'info')
                        self.log_line_count += 1
                    self.log_text.see('end')
                    continue
                if 'monitor_line' in self.log_text.mark_names():
                    self.log_text.mark_unset('monitor_line')
                self.log_text.insert("end", f"[{now_text()}] {level.upper()} {message}\n", level)
                self.log_line_count += str(message).count("\n") + 1
                if self.log_line_count > 5000:
                    self.log_text.delete("1.0", "1001.0")
                    self.log_line_count -= 1000
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.check_monitor_stall()
        if not self.closing:
            self.after(120, self.drain_logs)

    def check_monitor_stall(self) -> None:
        if self.monitor_received_at is None or self.monitor_stall_reported or 'monitor_line' not in self.log_text.mark_names():
            return
        elapsed=time.monotonic()-self.monitor_received_at
        if elapsed<=12:
            return
        self.monitor_stall_reported=True
        self.log_text.delete('monitor_line','monitor_line lineend')
        self.log_text.insert('monitor_line',f'[{now_text()}] 持续监控中 | 状态 异常：检测反馈超时（{elapsed:.0f}秒） | 等待检测恢复，请核对浏览器','error')

    def reset_controls(self) -> None:
        if self.closing:
            return
        if self.network_busy():
            return
        self.login_in_progress=False
        self.start_button.configure(state="normal")
        self.login_button.configure(state="normal")
        self.logout_button.configure(state='disabled' if self.updater.busy() else 'normal')
        self.finish_login_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.network_start_button.configure(state='normal')
        self.set_account_busy(False)

    def account_busy(self) -> bool:
        return bool(self.worker and self.worker.is_alive()) or self.network_busy() or self.closing or self.updater.busy()

    def set_account_busy(self, busy):
        if busy: self.logout_button.configure(state='disabled')
        self.account_manager_button.configure(state='disabled' if busy or self.account_store_error else 'normal')
        if self.account_manager and self.account_manager.winfo_exists():
            self.account_manager.set_busy(busy)

    def save_accounts(self, accounts):
        if self.account_store_error:
            raise AccountStoreError(self.account_store_error)
        if self.account_busy():
            raise AccountStoreError('任务运行中，不能修改账号')
        self.account_store.save(tuple(accounts))
        self.accounts = tuple(accounts)

    def open_account_manager(self):
        if self.account_busy():
            messagebox.showwarning('任务运行中','请先结束当前任务或登录'); return
        if self.account_store_error:
            messagebox.showerror('账号文件不可用',self.account_store_error); return
        if self.account_manager and self.account_manager.winfo_exists():
            self.account_manager.lift(); return
        self.account_manager = AccountManagerDialog(self,self.accounts,self.save_accounts,self.start_account_login,self.account_busy)

    def prepare_batch_jobs(self, config):
        if config.mode != 'batch' or not self.vars['batch_multi_account'].get():
            return ()
        if self.account_store_error:
            raise AccountStoreError(self.account_store_error)
        jobs = self.account_store.jobs(self.accounts,Path(config.profile_dir))
        if not jobs:
            raise AccountStoreError('请在“管理账号”中勾选至少一个账号并保存修改')
        return jobs

    def finish_account_login(self, account_id, status, *, session=None):
        if session is not None and self.login_session is not session: return
        if status != 'saved': return
        if self.closing: return
        if self.worker and self.worker.is_alive():
            self.after(50, lambda: self.finish_account_login(account_id, status,session=session))
            return
        try:
            values=tuple(replace(a,login_saved=True) if a.account_id==account_id else a for a in self.accounts)
            self.save_accounts(values)
            if self.account_manager and self.account_manager.winfo_exists():
                self.account_manager.accounts=values
                self.account_manager.refresh(account_id)
        except (ValueError,OSError) as exc:
            self.log('error','登录资料已保存，但账号标记保存失败：'+str(exc))

    def start_account_login(self, account_id):
        if self.account_busy():
            messagebox.showwarning('任务运行中','请先结束当前任务或登录'); return
        if self.account_store_error:
            messagebox.showerror('账号文件不可用',self.account_store_error); return
        try:
            config=self.config_from_form()
            accounts=tuple(replace(a,selected=True) for a in self.accounts)
            jobs=self.account_store.jobs(accounts,Path(config.profile_dir))
            job=next(j for j in jobs if j.account_id==account_id)
            if not is_allowed_douyin_url(config.live_url): raise ValueError('直播间链接必须是 https://*.douyin.com 地址')
            self.launch_login(replace(config,profile_dir=job.profile_dir),account_id)
        except (ValueError,StopIteration) as exc:
            messagebox.showerror('账号登录失败',str(exc) or '账号记录不存在')

    def start_task(self) -> None:
        if self.updater.busy() or self.closing:
            messagebox.showwarning('更新进行中','请先完成或取消更新。'); return
        if self.license_controller:
            status = self.license_controller.snapshot()
            if not status.allowed:
                messagebox.showwarning("授权不可用", license_status_text(status))
                self.license_controller.start_background(lambda result: self.ui_queue.put(lambda: self.on_license_change(result)))
                return
        if self.network_busy():
            messagebox.showwarning('网络检测中','请先停止网络检测，再启动任务。')
            return
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("任务运行中", "已有任务正在运行")
            return
        config = self.config_from_form()
        try:
            jobs = self.prepare_batch_jobs(config)
        except AccountStoreError as exc:
            messagebox.showerror('多账号配置错误',str(exc)); return
        if config.mode == "batch" and not jobs:
            for field_name, label in (("buy_times", "订单数"), ("buy_quantity", "每单商品数量")):
                raw = str(self.vars[field_name].get()).strip()
                if not raw.isdigit() or int(raw) < 1:
                    messagebox.showerror("配置错误", f"{label}必须是正整数")
                    return
        if not is_allowed_douyin_url(config.live_url):
            messagebox.showerror("配置错误", "请填写有效的 https://*.douyin.com 直播间或商品链接")
            return
        if not config.product_name and not config.product_id:
            messagebox.showerror("配置错误", "请至少填写商品关键词或商品编号")
            return
        if config.strict_price_match and config.target_price <= 0:
            messagebox.showerror("配置错误", "严格价格匹配时必须填写目标价格")
            return
        if effective_flash_lock_mode(config) != 'none' and (not config.product_name or config.target_price <= 0):
            messagebox.showerror('配置错误', '秒杀锁单需要填写完整商品名称和大于0的目标金额')
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
                "监控区间无效：每段格式为 12:00-13:00，支持跨午夜；时长必须大于0且不超过1小时，最多4段。\n" + "，".join(invalid_times),
            )
            return
        if jobs:
            summary='\n'.join(f'{i+1}. {j.name} · {j.account_id[:8]}：{j.buy_times} 单，每单 {j.buy_quantity} 件' for i,j in enumerate(jobs))
            summary += '\n\n'+('测试模式，不创建真实订单' if config.dry_run else '真实下单 · '+'每笔待付款即完成，后续支付由用户处理')
            if not messagebox.askyesno('确认账号执行顺序',summary+'\n异常停止整队，先核对订单再决定是否重跑。继续？'): return
        if not config.dry_run:
            warning = "即将启用真实下单操作，脚本会点击购买和提交订单按钮。"
            lock_mode=effective_flash_lock_mode(config)
            if lock_mode != 'none':
                mode_name={'detail':'详情页锁单','script':'脚本锁单','normal':'普通锁单'}[lock_mode]
                warning += (f'\n\n{mode_name}会点击一次“支付”提交，确认新待付款订单后停止，不继续付款。'
                            '\n按钮是否会直接扣款取决于平台和账号设置，程序不能保证只锁库存。')
            warning += "\n\n请确认商品、价格、收货地址和账号均正确。是否继续？"
            if not messagebox.askyesno("确认真实下单", warning, icon="warning"):
                return
        # Confirmation dialogs run a nested Tk loop: recheck ownership before
        # creating any ordering state, even when the entry check was idle.
        if self.update_blocked():
            messagebox.showwarning('暂不能启动','任务状态已变化，请结束当前操作后重试。'); return
        self.stop_event = threading.Event()
        self.batch_queue = None
        if jobs:
            base_config = replace(config)
            stop_event = self.stop_event
            license_status = self.license_controller.snapshot if self.license_controller else None
            def factory(job):
                prefix=f'[{job.name} {jobs.index(job)+1}/{len(jobs)}] '
                account_config=replace(base_config,profile_dir=job.profile_dir,buy_times=job.buy_times,buy_quantity=job.buy_quantity,
                    diagnostics_dir=str(Path(base_config.diagnostics_dir)/'batch-accounts'/job.account_id),
                    network_log_path=str(Path(base_config.network_log_path).parent/'batch-accounts'/job.account_id/'network.jsonl'))
                self.runner=AutomationRunner(account_config,lambda level,msg:self.log(level,prefix+msg),stop_event,license_status=license_status,queue_lifecycle=True)
                return self.runner
            self.batch_queue = SequentialBatchQueue(factory,self.log,stop_event,license_status)
            self.batch_jobs = jobs
            self.runner = None
        else:
            self.runner = AutomationRunner(config, self.log, self.stop_event,
                license_status=self.license_controller.snapshot if self.license_controller else None)
        if self.license_controller:
            self.license_controller.start_background(lambda result: self.ui_queue.put(lambda: self.on_license_change(result)))
        self.save_preferences()
        self.worker = threading.Thread(target=self.worker_main, daemon=True)
        self.start_button.configure(state="disabled")
        self.login_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.network_start_button.configure(state='disabled')
        self.set_account_busy(True)
        self.worker.start()

    def worker_main(self) -> None:
        try:
            if self.batch_queue:
                self.queue_report = self.batch_queue.run(self.batch_jobs)
            else:
                status = self.runner.run() if self.runner else "stopped"
                self.log("info", f"任务结束：{status}")
        except Exception:
            self.log("error", "任务异常退出")
        finally:
            self.ui_queue.put(self.reset_controls)

    def start_login(self) -> None:
        if self.network_busy():
            messagebox.showwarning('网络检测中','请先停止网络检测，再登录。')
            return
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("任务运行中", "请先停止当前任务")
            return
        config = self.config_from_form()
        if not is_allowed_douyin_url(config.live_url):
            messagebox.showerror("配置错误", "直播间链接必须是 https://*.douyin.com 地址")
            return
        self.launch_login(config)

    def launch_login(self, config, account_id=None, *, reset_login=False):
        if self.updater.busy() or self.closing:
            messagebox.showwarning('更新进行中','请先完成或取消更新。'); return
        self.login_account_id = account_id
        self.login_in_progress=True
        self.stop_event = threading.Event()
        self.login_finish_event = threading.Event()
        session = LoginSession(
            config, self.log, self.stop_event, self.login_finish_event,reset_login=reset_login,
            on_logout=lambda:self.ui_queue.put(lambda:self.mark_account_logged_out(account_id)),
            on_ready=lambda:self.ui_queue.put(lambda:self.login_ready(session))
        )
        self.login_session=session
        self.worker = threading.Thread(target=self.login_worker_main, daemon=True)
        self.start_button.configure(state="disabled")
        self.login_button.configure(state="disabled")
        self.finish_login_button.configure(state="disabled")
        self.logout_button.configure(state='disabled')
        self.stop_button.configure(state="normal")
        self.network_start_button.configure(state='disabled')
        self.set_account_busy(True)
        self.worker.start()

    def login_ready(self, session):
        if self.login_session is not session or self.closing or not self.login_in_progress or self.login_finish_event.is_set() or self.stop_event.is_set(): return
        self.finish_login_button.configure(state='normal')
        self.logout_button.configure(state='normal')

    def mark_account_logged_out(self, account_id):
        if self.closing: return
        if self.account_store_error:
            self.log('warn','账号文件不可用，保留原文件；已清除的本地登录状态不受影响。'); return
        identity=account_id or 'legacy'
        values=tuple(replace(a,login_saved=False) if a.account_id==identity else a for a in self.accounts)
        try:
            self.account_store.save(values); self.accounts=values
            if self.account_manager and self.account_manager.winfo_exists():
                self.account_manager.accounts=values; self.account_manager.refresh(identity)
        except (ValueError,OSError) as exc:
            self.log('warn','已清除抖音登录状态，但账号记录未更新：'+str(exc))

    def logout_current_account(self):
        def blocked():
            return (self.closing or self.updater.busy() or self.network_busy() or
                    bool(self.worker and self.worker.is_alive() and not self.login_in_progress))
        if blocked():
            messagebox.showwarning('暂不能退出','请先结束监控、下单、测速或更新。'); return
        session=self.login_session
        active=bool(self.worker and self.worker.is_alive() and self.login_in_progress)
        account_id=self.login_account_id if session else None
        if active and (self.login_finish_event.is_set() or self.stop_event.is_set() or session.logout_event.is_set()):
            messagebox.showwarning('登录处理中','请等待当前登录操作结束。'); return
        if account_id and not any(a.account_id==account_id for a in self.accounts):
            messagebox.showwarning('账号记录已变化','请重新选择账号并打开登录浏览器。'); return
        config=replace(session.config) if session and account_id else self.config_from_form()
        if active: config=replace(session.config)
        if not is_allowed_douyin_url(config.live_url):
            messagebox.showerror('配置错误','直播间链接必须是 https://*.douyin.com 地址'); return
        name=next((a.name for a in self.accounts if a.account_id==(account_id or 'legacy')),'默认账号')
        if not messagebox.askyesno('退出当前账号',f'退出「{name}」在本软件中的抖音登录状态并重新登录？\n资料目录：{config.profile_dir}\n其他账号、商品设置和软件激活不会删除。'): return
        if blocked() or self.login_session is not session or bool(self.worker and self.worker.is_alive())!=active:
            messagebox.showwarning('状态已变化','请等待当前操作结束后重试。'); return
        if active:
            if self.login_finish_event.is_set() or self.stop_event.is_set() or session.logout_event.is_set(): return
            self.finish_login_button.configure(state='disabled'); self.logout_button.configure(state='disabled')
            session.logout_event.set()
        else: self.launch_login(config,account_id,reset_login=True)

    def login_worker_main(self) -> None:
        account_id = self.login_account_id
        session=self.login_session
        try:
            status = session.run() if session else "cancelled"
            self.log("info", f"登录流程结束：{status}")
            if account_id:
                self.ui_queue.put(lambda: self.finish_account_login(account_id,status,session=session))
        except Exception:
            self.log("error", "登录流程异常：\n" + traceback.format_exc())
        finally:
            self.ui_queue.put(lambda:self.finish_login_controls(session))

    def finish_login_controls(self, session):
        if self.login_session is session:
            self.reset_controls()

    def finish_login(self) -> None:
        if self.login_session and self.login_session.logout_event.is_set(): return
        self.login_finish_event.set()
        self.finish_login_button.configure(state="disabled")
        self.logout_button.configure(state='disabled')
        self.log("info", "正在保存登录态并关闭登录浏览器")

    def stop_task(self) -> None:
        self.stop_event.set()
        self.login_finish_event.set()
        self.log("warn", "收到停止请求，正在中止任务")

    def clear_logs(self) -> None:
        if 'monitor_line' in self.log_text.mark_names():
            self.log_text.mark_unset('monitor_line')
        self.log_text.delete("1.0", "end")
        self.log_line_count = 0

    def open_diagnostics(self) -> None:
        path = DEFAULT_DIAGNOSTICS_DIR
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def on_close(self) -> None:
        if self.closing:
            return
        self.updater.close()
        if self.license_controller:
            self.license_controller.close()
        self.save_preferences()
        if self.network_probe:
            self.network_probe.cancel()
        if self.network_busy() or (self.worker and self.worker.is_alive()):
            self.closing = True
            self.stop_event.set()
            self.login_finish_event.set()
            self.start_button.configure(state="disabled")
            self.login_button.configure(state="disabled")
            self.finish_login_button.configure(state="disabled")
            self.logout_button.configure(state='disabled')
            self.stop_button.configure(state="disabled")
            self.log("warn", "正在安全停止任务并关闭浏览器……")
            self.after(100, self.wait_for_worker_before_close)
            return
        self.destroy()

    def wait_for_worker_before_close(self) -> None:
        if self.network_busy() or (self.worker and self.worker.is_alive()):
            self.after(100, self.wait_for_worker_before_close)
            return
        self.destroy()


def main() -> None:
    from update_process import acknowledge_startup
    online = OnlineLicenseClient(APP_DATA_DIR, machine_fingerprint(), LICENSE_PUBLIC_KEYS, LicenseTransport(LICENSE_ORIGIN))
    activation = ActivationDialog(LocalLicenseClient(), online)
    activation.after_idle(acknowledge_startup)
    activation.mainloop()
    if not activation.authorized:
        online.close()
        return
    if activation.authorized_mode != "online":
        online.close()
    app = App(online if activation.authorized_mode == "online" else None)
    app.mainloop()


if __name__ == "__main__":
    main()
