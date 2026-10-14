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
from browser_settings import BROWSER_CHOICES, launch_browser as launch_persistent_browser

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
BUY_ACTION_TEXT_RE = re.compile(
    r"^\s*(?:立即购买|去抢购|立即抢|马上抢|抢购|去购买|购买|下单)\s*$"
)
REVEAL_PRICE_TEXT_RE = re.compile(r"^\s*查看价格\s*$")
REAL_NAME_REQUIRED_RE = re.compile(
    r"(未实名|尚未实名|请先实名认证|请完成实名认证|实名认证后)"
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
    if(matches.length!==1)return {matches:matches.length,panels:panels.length,identityReason:matches.length>1?'multiple_detail_panels':'detail_title_unmatched',detailTitles:panels.map(x=>x.titles.map(el=>(el.innerText||'').trim())),manualVerification,offline:!navigator.onLine};
    const {root,index}=matches[0];
    const cards=[...document.querySelectorAll('li')];
    const sameCards=cards.filter(el=>el.matches('li.sLOeOa5R') &&
        normalize(el.querySelector('[data-e2e="promotion-title"]')?.innerText)===normalize(args.name) &&
        (!args.number || (el.querySelectorAll('.hH83LEu1 > .s5ICtQCA').length===1 &&
        (el.querySelector('.s5ICtQCA').innerText||'').trim()===String(Number(args.number)))));
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
        options:[...root.querySelectorAll('.YTFcT_zp')].map(el=>el.outerHTML),
        specControlCount:[...root.querySelectorAll('.YTFcT_zp, .ufz0AqTE, [role=radio], [role=listbox], [data-sku], [class*=sku i], [class*=spec i]')].filter(visible).length,
        quantity:Number.isFinite(quantity)?quantity:null,qr:/请打开抖音APP扫描二维码|扫码.*购买此商品/.test(text),
        listReady,listUnavailable,listIndex:card?cards.indexOf(card):-1,
        manualVerification,verification:/请完成验证|安全验证|实名认证|验证码|使用须知/.test(text),offline:!navigator.onLine};
    const selectedOptions=[...root.querySelectorAll('.ufz0AqTE')].filter(el=>visible(el)&&
        (el.classList.contains('vZSOutR4')||el.getAttribute('aria-selected')==='true')).map(el=>({
        group:(el.closest('.YTFcT_zp')?.querySelector('.R_G6ohly')?.innerText||'').trim(),
        text:(el.innerText||'').trim(),
        unavailable:!!el.closest('[aria-disabled="true"],[disabled],[inert]')||
            /disabled|soldout|unavailable/i.test(el.className)||/缺货|售罄|已抢完|已抢光/.test(el.innerText||'')
    }));
    // SKU amounts update asynchronously; validate the action and safety, not its price.
    const submitPayment=payment?{index:payment.index,visible:payment.visible,safe:payment.safe,
        stockError:payment.stockError,text:normalize(payment.text).replace(/(?:[¥￥]?\d+(?:\.\d{1,2})?元?)$/,'')}:null;
    data.submitSignature=JSON.stringify({matches:data.matches,panels:data.panels,index,
        payment:submitPayment,quantity:data.quantity,selectedOptions,specControlCount:data.specControlCount,manualVerification,
        verification:data.verification,offline:data.offline});
    data.signature=JSON.stringify(data);
    return data;
}"""
DISMISS_TEXT_RE = re.compile(
    r"(我知道了|知道了|允许|稍后再说|以后再说|关闭|继续看播|继续观看|继续看直播)"
)
USER_AGREEMENT_TITLE_RE = re.compile(r"^\s*使用须知\s*$")


DEFAULT_LIVE_URL = "https://live.douyin.com/712159628601"
DEFAULT_PRODUCT_NAME = "【麦序】手作球解压玩具"


def application_data_dir() -> Path:
    """Return a writable data directory in source and packaged builds."""
    from mac_platform import application_data_dir as mac_data_dir
    return mac_data_dir()


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
    """Create an app-specific hash without sending the raw Mac hardware UUID."""
    from mac_platform import machine_fingerprint as mac_fingerprint
    return mac_fingerprint()


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
        ttk.Label(frame, text="软件激活", font=("PingFang SC", 16, "bold")).pack(
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
        from announcement_ui import AnnouncementController
        self.announcements = AnnouncementController(self, client.state_path.parent)
        self.activation_timers.add(self.after(1000,self.announcements.check_startup))
        ttk.Button(actions,text="公告",command=self.announcements.open_history).pack(side="left",padx=8)
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
        if hasattr(self,'announcements'): self.announcements.close()
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
class AutomationConfig:
    mode: str = "flash"
    live_url: str = DEFAULT_LIVE_URL
    product_url: str = ""
    product_name: str = DEFAULT_PRODUCT_NAME
    product_id: str = "1"
    headless: bool = False
    browser_choice: str = "auto"
    allow_reservation_click: bool = False
    open_panel_interval_ms: int = 180
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
    detail_refresh_ms: int = 10000
    profile_dir: str = field(
        default_factory=lambda: str(DEFAULT_PROFILE_DIR)
    )
    network_log_path: str = field(
        default_factory=lambda: str(DEFAULT_NETWORK_LOG)
    )
    diagnostics_dir: str = field(default_factory=lambda: str(DEFAULT_DIAGNOSTICS_DIR))
    close_browser_on_finish: bool = False



def effective_flash_lock_mode(config: AutomationConfig) -> str:
    return 'detail' if config.mode == 'flash' else 'none'


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




def product_matches(text: str, config: AutomationConfig) -> bool:
    name = normalize_text(config.product_name)
    return bool(name) and name in normalize_text(text)


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









def product_match_reason(text: str, config: AutomationConfig) -> str:
    if str(config.product_id or "").strip() and product_list_index_matches(text, config):
        return "编号命中"
    if product_matches(text, config):
        return "完整商品名称命中"
    return "未知"


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
                playwright, self.config.profile_dir, False, self.log, self.config.browser_choice
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
        self.stock_state = None
        self.sale_seen = False
        self.monitor_started_at = 0.0
        self.monitor_reported_at = float('-inf')
        self.monitor_last_error = None
        self.monitor_scan_error = None
        self.monitor_last_success = None
        self.phase_times = {}
        self.detail_entered = False
        self.detail_open_started_at = 0.0
        self.detail_refreshed_at = 0.0
        self.detail_refresh_blocked = False
        self.pending_order_timeout_ms = 5000
        self.preexisting_pending_order_ids = set()
        self.detail_last_list_ready = False
        self.detail_observation = None
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
            ('list_opened', 'payment_available', '卡片开放→详情支付可用'),
            ('payment_available', 'payment_submitted', '支付可用→点击完成'),
            ('specs_confirmed', 'payment_submitted', '规格确认→点击完成'),
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
            '运行模式：', '商品名称：', '商品编号：', '目标价格：',
            '打开直播间：', '已确认抖音登录态', '记录相关响应：', '扫描商品候选节点：',
            '未在目标商品卡片内', '已点击：',
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
        target = '正在查找目标商品'
        if (self.config.mode == 'flash') and self.detail_entered:
            target = '详情页已锁定 · 等待可提交按钮'
        state = '失败 · 页面异常：' + compact_text(error, 100) if error else '正常 · ' + target
        checked = self.monitor_last_success or '尚未确认'
        prefix = '持续监控中（实时监控，仅日志每5秒刷新）'
        if self.state['manual_verification_waiting']:
            prefix = '监控暂停（等待人工验证，已暂停刷新和点击）'
            state = '等待人工验证 · 验证结束且目标详情恢复后继续'
        elif self.state.get('service_error_waiting'):
            prefix = '监控暂停（服务异常，已暂停刷新和点击）'
            state = '请人工恢复目标商品详情后继续'
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
            else:
                self.log("info", "目标商品暂无库存，继续等待可购买状态")
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
        self.log("info", f"商品名称：{self.config.product_name or '(未填写)'}")
        if self.config.multi_option_enabled:
            parse_product_options(self.config.option_names)
            self.log("info", f"商品规格：{self.config.option_names or '每组自动选择首个可用项'}；提交前确认实际规格")
        if self.config.product_id:
            self.log("info", f"商品编号：{self.config.product_id}")

        try:
            Path(self.config.profile_dir).mkdir(parents=True, exist_ok=True)
            Path(self.config.diagnostics_dir).mkdir(parents=True, exist_ok=True)

            self.playwright = sync_playwright().start()
            self.context = launch_persistent_browser(
                self.playwright,
                self.config.profile_dir,
                self.config.headless,
                self.log,
                self.config.browser_choice,
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(2000)
            self.capture_network_evidence(self.page)

            clock_offset_ms = 0
            if self.config.mode == "flash":
                clock_offset_ms = self.calibrate_clock()
                self.resolve_number_target(self.page)
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
            if self.page and self.state.get("waiting_evidence") and not self.state["order_submitted"]:
                self.save_diagnostics(self.page, "number-wait-ended")
            self.network_stop.set()
            if self.network_worker:
                self.network_worker.join(timeout=1)
            if self.queue_lifecycle and self.config.mode == 'batch':
                outcome = self.batch_outcome()
                queue_success = outcome.kind == 'completed' and outcome.confirmed_orders == self.config.buy_times
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
        def record(request, event, status=None) -> None:
            try:
                parsed = urlparse(request.url)
                url = parsed._replace(netloc=parsed.netloc.rsplit('@', 1)[-1],
                                      query='', fragment='').geturl()
                if not re.search(r"product|goods|sku|shop|cart|order|live|commerce|ecom", url, re.I):
                    return
                # Copy only local metadata; never read a response body or pass
                # Playwright objects to the file-writing thread.
                entry = {
                    "time": now_text(),
                    "event": event,
                    "status": status,
                    "url": url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "stage": ('after_submit' if self.state['pending_payment_review']
                              or self.state['order_submitted'] else 'pre_submit'),
                    "detail_opened": 'detail_opened' in self.phase_times,
                    "last_click_label": self.state['last_click_label'],
                    "detail_refreshes": self.state['detail_refreshes'],
                }
                if event == 'requestfailed':
                    failure = request.failure or ''
                    entry['failure'] = failure
                    # An aborted navigation/fetch is a cancellation, not proof
                    # of a server failure or a platform restriction.
                    entry['failure_kind'] = ('cancelled' if failure == 'net::ERR_ABORTED'
                                             else 'request_failed')
                self.network_queue.put_nowait(entry)
            except queue.Full:
                pass  # Diagnostics must never block ordering.
            except Exception:
                pass

        page.on("response", lambda response: record(response.request, 'response', response.status))
        page.on("requestfailed", lambda request: record(request, 'requestfailed'))

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

    def safe_click(self, locator, label: str) -> bool:
        self.assert_purchase_authorized()
        self.state["click_attempts"] += 1

        def do_click() -> bool:
            if self.config.mode == 'flash' and label == 'open target product detail':
                action_page = getattr(locator, "page", None)
                if action_page is None:
                    frame = locator.owner_frame()
                    action_page = frame.page if frame else None
                if action_page is None:
                    raise GracefulStop("点击控件页面归属不明确，停止")
                cards = self.numbered_cards(action_page)
                identity = locator.evaluate("""(el,args)=>{const card=el.closest('li.sLOeOa5R');return !!card&&(!args.number||(card.querySelectorAll('.hH83LEu1 > .s5ICtQCA').length===1&&(card.querySelector('.s5ICtQCA').innerText||'').trim()===args.number))&&card.querySelectorAll('[data-e2e="promotion-title"]').length===1&&(card.querySelector('[data-e2e="promotion-title"]').innerText||'').trim()===args.name;}""", {'number':str(int(self.config.product_id)) if self.config.product_id else '', 'name':self.config.product_name})
                if len(cards) != 1 or not identity:
                    raise GracefulStop('点击前编号卡片归属变化或不唯一，停止')
            option_page = (getattr(locator, 'page', None) if self.config.mode == 'flash'
                           and effective_flash_lock_mode(self.config)!='none' and label.startswith('select product option:') else None)
            if option_page is not None and option_page.evaluate(MANUAL_VERIFICATION_JS):
                return self.pause_for_manual_verification()
            # Native click already scrolls and checks visibility, stability,
            # enablement and hit targeting. A separate scroll repeats the wait.
            timeout = self.config.click_timeout_ms
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
            if (self.config.mode == 'flash') and action_page is not None and action_page.evaluate(MANUAL_VERIFICATION_JS):
                return self.pause_for_manual_verification()
            try:
                do_click()
            except Exception as exc:
                if (self.config.mode == 'flash') and action_page is not None and action_page.evaluate(MANUAL_VERIFICATION_JS):
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
                self.state['payment_click_error'] = {
                    'stage': 'native_submit', 'type': type(exc).__name__, 'message': str(exc),
                }
                self.log('warn', '支付原生点击失败：'+compact_text(exc, 800))
                raise GracefulStop("支付点击结果不明确，已停止重试；请在保留的页面核对订单") from exc
        else:
            if self.retry_action(label, do_click) is False:
                return False
        self.log("info", f"已点击：{label}")
        if label == 'submit payment / create order':
            self.mark_phase('payment_submitted')
        self.state["successful_clicks"] += 1
        self.state["last_click_label"] = label
        return True



    def click_first_visible(
        self, candidates: list, label: str
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
                    return self.safe_click(action, label )
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
    ) -> bool:
        for candidate in self.visible_text_element_candidates(page, pattern):
            self.assert_running()
            try:
                text = candidate["text"]
                box = candidate["box"]
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
        self.click_first_visible(candidates, "dismiss overlay")

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
            page, ALL_PRODUCTS_TEXT_RE, "open all products tab"
        ):
            self.wait_ms(self.config.order_step_delay_ms)
            return True

        if self.click_visible_text_element(
            page,
            COMMERCE_PANEL_TEXT_RE,
            "open live commerce panel",
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
            candidates, "open live commerce panel"
        )
        if opened:
            self.wait_ms(self.config.order_step_delay_ms)
        return opened

    def ensure_all_products_panel(self, page) -> bool:
        if self.config.mode == 'flash' and (self.config.mode == 'flash') and page.evaluate(MANUAL_VERIFICATION_JS):
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







    def set_purchase_quantity(self, page, scope=None) -> None:
        if self.config.mode != "batch":
            return
        inputs = (scope if scope is not None else page).locator('input[type="number"], input[role="spinbutton"]').filter(visible=True)
        if inputs.count() > 1:
            raise GracefulStop("目标详情数量输入框不唯一，未设置数量或提交")
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



    def close_payment_layer(self, page) -> bool:
        raise GracefulStop("禁止关闭待付款页面")

    def confirm_abandon_payment(self, page) -> bool:
        raise GracefulStop("禁止放弃支付")

    def select_product_options(self, page, required: bool = False, scope=None, wait_for_availability=False) -> bool:
        requested = parse_product_options(self.config.option_names)
        wait_for_availability = wait_for_availability and bool(requested)
        def wait_for_option(reason):
            self.state['options_verified'] = False
            self.state['waiting_for_specs'] = True
            self.report_detail_submit_wait(page, reason+'；等待规格上架或恢复可选')
            return False
        if scope is None:
            snapshot = self.detail_snapshot(page)
            if snapshot.get('matches') != 1:
                raise GracefulStop('规格选择缺少唯一目标详情，停止')
            scope = page.locator(DETAIL_ROOT_SELECTOR).nth(snapshot['index'])
        controls = scope.locator(PRODUCT_OPTION_SELECTOR)
        actions = controls.element_handles()
        def details(action):
            return action.evaluate("""el => ({
                connected: el.isConnected,
                text: (el.innerText || '').trim(),
                group: (el.closest('.YTFcT_zp')?.querySelector('.R_G6ohly')?.innerText || '').trim(),
                visible: el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0 && getComputedStyle(el).visibility !== 'hidden',
                selected: el.classList.contains('vZSOutR4') || el.getAttribute('aria-selected') === 'true',
                unavailable: !!el.closest('[aria-disabled="true"],[disabled],[inert]') || /disabled|soldout|unavailable/i.test(el.className) || /缺货|售罄|已抢完|已抢光/.test(el.innerText || ''),
                clickable: getComputedStyle(el).pointerEvents !== 'none'
            })""")
        records = [(action, details(action)) for action in actions]
        records = [(action, data) for action, data in records if data['visible']]
        if not records:
            if requested and wait_for_availability:
                return wait_for_option('指定规格尚未出现')
            if requested or scope.locator('.YTFcT_zp, [role=radio], [role=listbox], [data-sku], [class*=sku i], [class*=spec i]').filter(visible=True).count():
                raise GracefulStop('未找到可确认的规格选项，停止')
            self.state['selected_options'] = []
            self.state['options_verified'] = True
            return True
        if any(not data['group'] or not data['text'] for _, data in records):
            raise GracefulStop('规格组或选项名称不明确，停止')
        labels = scope.locator('.YTFcT_zp').evaluate_all("els => els.filter(el=>el.getBoundingClientRect().width>0 && el.getBoundingClientRect().height>0).map(el=>(el.querySelector('.R_G6ohly')?.innerText || '').trim())")
        if len(labels) != len(set(labels)):
            raise GracefulStop('规格组重名，无法确认选项归属，停止')
        groups = list(dict.fromkeys(data['group'] for _, data in records))
        if set(labels) != set(groups):
            raise GracefulStop('存在未识别选项的规格组，停止')
        chosen = []
        used = set()
        if requested:
            for group, name in requested:
                matches = [(action, data) for action, data in records if data['text'] == name and (not group or data['group'] == group)]
                if not matches and wait_for_availability:
                    return wait_for_option(f'指定规格 {group}={name} 尚未出现')
                if len(matches) != 1 or matches[0][1]['group'] in used:
                    raise GracefulStop(f'指定规格 {group}={name} 未唯一匹配，停止')
                action, data = matches[0]
                used.add(data['group']); chosen.append((action, data))
            for group in groups:
                if group in used: continue
                defaults = [(action, data) for action, data in records if data['group'] == group and not data['unavailable'] and (data['clickable'] or data['selected'])]
                if len(defaults) != 1 or not defaults[0][1]['selected'] or defaults[0][1]['unavailable']:
                    raise GracefulStop(f'已填写规格，但规格组 {group} 未明确指定，停止')
                chosen.append(defaults[0])
        else:
            for group in groups:
                available = [(action, data) for action, data in records if data['group'] == group and not data['unavailable'] and (data['clickable'] or data['selected'])]
                if not available:
                    raise GracefulStop(f'规格组 {group} 没有可用规格，停止')
                chosen.append(available[0])
        if wait_for_availability and any(data['unavailable'] or (not data['selected'] and not data['clickable']) for _, data in chosen):
            return wait_for_option('指定规格尚不可选择')
        for action, original in chosen:
            self.assert_running()
            if page.evaluate(MANUAL_VERIFICATION_JS):
                self.pause_for_manual_verification()
                raise GracefulStop('规格选择期间需要人工验证，停止且未提交')
            current = details(action)
            if wait_for_availability and current['connected'] and current['text'] == original['text'] and current['group'] == original['group'] and current['unavailable']:
                return wait_for_option('指定规格暂时不可选择')
            if not current['connected'] or current['text'] != original['text'] or current['group'] != original['group'] or current['unavailable']:
                raise GracefulStop('选择过程中规格变化或不可用，停止')
            if not current['selected']:
                if not current['clickable'] or not self.safe_click(action, 'select product option: '+current['text']):
                    raise GracefulStop('规格无法选择，停止')
                if self.config.mode != 'flash':
                    self.wait_ms(max(150, self.config.order_step_delay_ms))
        deadline = time.monotonic()+2
        while True:
            verified = [details(action) for action, _ in chosen]
            if all(data['connected'] and data['visible'] and data['selected'] and not data['unavailable'] and data['group'] == original['group'] and data['text'] == original['text'] for data, (_, original) in zip(verified, chosen)):
                break
            if time.monotonic() >= deadline:
                self.save_diagnostics(page, 'product-option-unconfirmed')
                raise GracefulStop('规格选择结果无法确认，停止且未提交')
            self.assert_running(); self.wait_ms(25 if self.config.mode == 'flash' else 80)
        selected = [(data['group'], data['text']) for data in verified]
        if selected != self.state.get('selected_options'):
            self.log('info', ('按填写内容选择' if requested else '自动选择首个可用规格')+'，已确认：'+' | '.join(group+'='+name for group,name in selected))
        self.state['selected_options'] = selected
        self.state['options_verified'] = True
        return True

    def verify_selected_specs(self, root):
        actual = root.locator(PRODUCT_OPTION_SELECTOR).evaluate_all("""els => els.filter(el => {
            const rect=el.getBoundingClientRect();
            return rect.width>0 && rect.height>0 && (el.classList.contains('vZSOutR4') || el.getAttribute('aria-selected')==='true');
        }).map(el => ({
            group:(el.closest('.YTFcT_zp')?.querySelector('.R_G6ohly')?.innerText || '').trim(),
            text:(el.innerText || '').trim(),
            unavailable:!!el.closest('[aria-disabled="true"],[disabled],[inert]') || /disabled|soldout|unavailable/i.test(el.className) || /缺货|售罄|已抢完|已抢光/.test(el.innerText || '')
        }))""")
        expected = self.state.get('selected_options', [])
        if any(data['unavailable'] for data in actual) or sorted((data['group'],data['text']) for data in actual) != sorted(expected):
            raise GracefulStop('提交前已选规格变化或无法确认，停止')

    def capture_ready_specs(self, page, root, payment_text):
        self.verify_selected_specs(root)
        selected = self.state.get('selected_options', [])
        key = (tuple(selected), self.state.get('batch_completed', 0))
        if self.state.get('ready_specs_snapshot') == key:
            return
        stamp = datetime.now(AUTOMATION_TIMEZONE).strftime('%Y-%m-%dT%H-%M-%S-%f')
        base = Path(self.config.diagnostics_dir) / (stamp+'_ready-specs')
        if self.config.mode == 'flash':
            # Keep pre-submit evidence in memory. Browser screenshots wait for
            # rendering and must not consume the short purchase window.
            self.state['ready_specs_snapshot'] = key
            self.state['deferred_specs'] = {
                'product_id': self.config.product_id, 'product_name': self.config.product_name,
                'selected_options': list(selected), 'payment_text': payment_text,
                'verified_at': stamp, 'base': str(base),
            }
            return
        try:
            base.parent.mkdir(parents=True, exist_ok=True)
            root.screenshot(path=str(base)+'.png', timeout=5000)
            Path(str(base)+'.json').write_text(json.dumps({'product_id':self.config.product_id, 'product_name':getattr(self,'batch_target_name',self.config.product_name), 'selected_options':selected, 'payment_text':payment_text, 'captured_at':stamp}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as exc:
            raise GracefulStop(f'可抢购详情规格快照保存失败，未提交：{exc}') from exc
        self.state['ready_specs_snapshot'] = key
        self.log('info', '可抢购详情规格截图：'+str(base)+'.png；JSON：'+str(base)+'.json；当前规格：'+(' | '.join(group+'='+name for group,name in selected) or '无规格'))

    def flush_submit_diagnostics(self, page):
        evidence = self.state.pop('deferred_specs', None)
        if evidence is None:
            return
        observations = self.state.get('detail_observations', [])
        evidence['detail_observations'] = observations
        if observations:
            self.log('info', '页面观察：本轮未主动刷新期间发现规格或支付按钮变化；已保存观察记录，不能仅据此判定平台推送库存')
        base = Path(evidence.pop('base'))
        evidence['captured_at'] = datetime.now(AUTOMATION_TIMEZONE).isoformat()
        evidence['screenshot_stage'] = 'after_submit_attempt'
        evidence['order_confirmed'] = self.state['order_lock_confirmed']
        try:
            base.parent.mkdir(parents=True, exist_ok=True)
            Path(str(base)+'.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
            page.screenshot(path=str(base)+'-after-submit.png', timeout=5000)
            self.log('info', f'提交前规格记录：{base}.json；提交尝试后截图：{base}-after-submit.png')
        except Exception as exc:
            # Failure to save diagnostics cannot reverse an order result.
            self.log('warn', '提交后诊断保存失败：'+str(exc))

    def record_submit_evidence(self, page, reason):
        self.state['submit_evidence'] = {
            'verified_at': datetime.now(AUTOMATION_TIMEZONE).isoformat(),
            'product_name': self.config.product_name, 'product_id': self.config.product_id,
            'selected_options': list(self.state.get('selected_options', [])), 'quantity': 1,
        }

    def prepare_sku_payment_click(self, action):
        if not self.state.get('selected_options'):
            return True
        from playwright.sync_api import Error as PlaywrightError
        self.assert_running()
        try:
            # Native actionability checks only: trial never dispatches a click.
            # A selected SKU can still be followed by checkout animation/repaint.
            action.click(trial=True, timeout=self.config.click_timeout_ms)
        except PlaywrightError as exc:
            evidence = {'stage': 'pre_submit_trial', 'type': type(exc).__name__, 'message': str(exc)}
            if self.state.get('payment_ready_error') != evidence:
                self.log('info', '规格已选，支付按钮尚未就绪；等待页面更新，不提交：'+compact_text(exc, 500))
            self.state['payment_ready_error'] = evidence
            self.assert_running()
            return False
        self.assert_running()
        self.state.pop('payment_ready_error', None)
        self.mark_phase('payment_actionable')
        return True



    def submit_payment_then_abandon(self, page) -> bool:
        # Legacy callers must use the same verified, single-submit path.
        if effective_flash_lock_mode(self.config) == 'none':
            return False
        return self.submit_flash_lock(page)

    def advance_order_flow(self, page) -> bool:
        return self.submit_flash_lock(page)

    def detail_snapshot(self, page):
        data = page.evaluate(DETAIL_SNAPSHOT_JS, {
            'roots': DETAIL_ROOT_SELECTOR, 'name': self.config.product_name, 'requireNumber': self.config.mode == 'flash',
            'number': self.config.product_id if str(self.config.product_id).isdigit() else '',
            'payment': PAYMENT_PRIMARY_SELECTOR})
        self.state['detail_status'] = {
            key: data.get(key) for key in ('matches', 'panels', 'identityReason', 'detailTitles', 'numberedCardCount', 'quantity', 'qr', 'paymentCount', 'offline', 'manualVerification', 'verification')}
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
        self.wait_ms(500)
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
        self.log('info', '人工验证提示已消失且目标详情已恢复，继续监听页面变化')

    def numbered_cards(self, page):
        """Union of exact title and card badge matches; ambiguity always stops."""
        matches = []
        for card in page.locator('li.sLOeOa5R').filter(visible=True).all():
            title = card.locator('[data-e2e="promotion-title"]')
            badge = card.locator('.hH83LEu1 > .s5ICtQCA')
            if title.count() != 1:
                continue
            name = title.inner_text(timeout=300).strip()
            number = badge.inner_text(timeout=300).strip() if badge.count() == 1 else ''
            if hasattr(self, 'resolved_product'):
                expected_name, expected_number = self.resolved_product
                match = normalize_text(name) == normalize_text(expected_name) and (not expected_number or number == expected_number)
            else:
                match = (bool(self.config.product_name) and normalize_text(name) == normalize_text(self.config.product_name)) or (bool(self.config.product_id) and number == str(int(self.config.product_id)))
            if match:
                matches.append(card)
        if len(matches) > 1:
            raise GracefulStop('商品名称或编号对应多个商品，结果不明确，停止')
        return matches

    def bind_target_card(self, card):
        name = card.locator('[data-e2e="promotion-title"]').inner_text(timeout=300).strip()
        badge = card.locator('.hH83LEu1 > .s5ICtQCA')
        number = badge.inner_text(timeout=300).strip() if badge.count() == 1 else ''
        if not name:
            raise GracefulStop('编号对应商品缺少完整名称，无法核对详情身份，停止')
        self.resolved_product = (name, number)
        self.config = replace(self.config, product_name=name, product_id=number)
        self.log('info', f'目标唯一匹配：商品名称={name}；编号={number or "未显示"}')

    def resolve_number_target(self, page):
        if not self.config.product_name and not self.config.product_id:
            raise GracefulStop('商品名称和商品编号至少填写一个')
        if self.config.product_id and (not self.config.product_id.isdigit() or int(self.config.product_id) < 1):
            raise GracefulStop('商品编号必须为正整数')
        self.state['retain_browser']=True
        self.log('info', '阶段：进入直播间，按完整名称或编号定位')
        page.goto(self.config.live_url, wait_until='domcontentloaded', timeout=60000)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            self.assert_running()
            if page.evaluate(MANUAL_VERIFICATION_JS):
                self.save_diagnostics(page,'number-manual-verification')
                self.state['retain_browser']=True
                raise GracefulStop('需要登录或人工验证，已停止，请人工处理')
            self.ensure_all_products_panel(page)
            cards=self.numbered_cards(page)
            if cards:
                self.bind_target_card(cards[0])
                self.save_diagnostics(page,'number-matched')
                return
            self.scroll_likely_product_lists(page); self.wait_ms(150)
        self.save_diagnostics(page,'number-not-found')
        raise GracefulStop('全部商品中未找到目标商品；请检查登录状态或服务异常')

    def open_target_detail(self, page) -> bool:
        """Only open the verified list title; never treat the gray buy label as stock."""
        matches = self.numbered_cards(page)
        if matches:
            name=matches[0].locator('[data-e2e="promotion-title"]').inner_text(timeout=300).strip()
            if name != self.config.product_name:
                raise GracefulStop('目标编号对应商品发生变化，停止')
            self.log('info', f'阶段：进入编号 {self.config.product_id} 的详情')
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
        if not self.safe_click(title, 'open target product detail'):
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
        })""", {'roots': DETAIL_ROOT_SELECTOR, 'name': self.config.product_name, 'requireNumber': self.config.mode == 'flash',
                 'payment': PAYMENT_PRIMARY_SELECTOR, 'index': snapshot.get('index', -1),
                 'number': self.config.product_id if str(self.config.product_id).isdigit() else '',
                 'listIndex': snapshot.get('listIndex', -1),
                 'signature': snapshot.get('signature')})
        self.assert_running()

    def reopen_waiting_detail(self, page, snapshot) -> None:
        # Reopening a detail may refresh server data, but the real platform's
        # caching/update behavior is not assumed. Recheck after each navigation.
        self.assert_running()
        if self.state['order_submitted'] or self.state['pending_payment_review']:
            raise GracefulStop('订单结果需要核对，不再刷新或重新购买')
        if self.pause_if_detail_service_error(page):
            return
        latest = self.detail_snapshot(page)
        if latest.get('manualVerification') or latest.get('verification'):
            self.pause_for_manual_verification()
            return
        if snapshot.get('signature') and latest.get('matches') == 1 and latest.get('signature') != snapshot['signature']:
            # A SKU/button may have changed after the refresh decision. Let the
            # next scan verify it before discarding a potentially ready detail.
            self.state['refresh_skipped_for_change'] = self.state.get('refresh_skipped_for_change', 0)+1
            return
        latest_payment = latest.get('payment') or {}
        if latest.get('matches') != 1 or (not self.state.get('waiting_for_specs') and latest_payment.get('safe') and not latest_payment.get('stockError')
                and PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(latest_payment.get('text'), 160))
                ):
            return
        root = page.locator(DETAIL_ROOT_SELECTOR).nth(latest['index'])
        back = root.locator('.xRcNm_F4,button[aria-label="返回"],button[aria-label="关闭"],[role="button"][aria-label="返回"]')
        back = back.filter(visible=True)
        if back.count() > 1:
            raise GracefulStop('详情返回入口不唯一，停止刷新；请核对页面')
        if not back.count():
            self.log('info', '详情页锁单：刷新已核对的目标详情，继续等待支付按钮')
            page.reload(wait_until='domcontentloaded', timeout=60000)
            self.assert_running()
            self.detail_entered = False
            self.state['options_verified'] = False
            self.state['detail_refreshes'] += 1
            if self.pause_if_detail_service_error(page):
                return
            refreshed = self.detail_snapshot(page)
            if refreshed.get('manualVerification') or refreshed.get('verification'):
                self.pause_for_manual_verification()
                return
            if refreshed.get('panels', 0) == 0:
                self.detail_open_started_at = 0
                self.ensure_all_products_panel(page)
                if len(self.numbered_cards(page)) != 1:
                    raise GracefulStop('刷新后未找到唯一目标商品，停止且未提交')
                self.open_target_detail(page)
            elif refreshed.get('matches') != 1:
                raise GracefulStop('刷新后目标详情身份不明确，停止且未提交')
            return
        self.log('info', '详情页锁单：返回并重新打开目标详情，继续等待支付按钮')
        if not self.safe_click(back, 'reopen target detail back'):
            return
        deadline = time.monotonic()+1
        while time.monotonic() < deadline:
            self.assert_running()
            if self.pause_if_detail_service_error(page):
                return
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
        options = self.state.get('selected_options', parse_product_options(self.config.option_names))
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

    def report_detail_submit_wait(self, page, reason):
        key = ('detail-submit-wait', reason)
        if self.state.get('detail_submit_wait_reason') != key:
            self.state['detail_submit_wait_reason'] = key
            self.log('warn', '详情尚未提交：'+reason)
            self.save_diagnostics(page, 'detail-submit-wait')

    def observe_detail_update(self, data, *, record=True) -> None:
        payment = data.get('payment') or {}
        current = {
            'generation': (self.detail_open_started_at, self.state['detail_refreshes']),
            'payment_ready': bool(payment.get('safe') and not payment.get('stockError')),
            'options': tuple(data.get('options', [])),
        }
        previous = self.detail_observation
        self.detail_observation = current
        if not record or previous is None or previous['generation'] != current['generation']:
            return
        changes = []
        if current['payment_ready'] and not previous['payment_ready']:
            changes.append('payment_available')
        if current['options'] != previous['options']:
            changes.append('spec_options_changed')
        if changes:
            # Memory only on the click path. Saved by existing post-submit or
            # stopped-task diagnostics; not proof of server-side stock pushes.
            events = self.state.setdefault('detail_observations', [])
            events.append({'time': now_text(), 'changes': changes,
                           'refreshes': self.state['detail_refreshes'],
                           'since_last_open_ms': round(max(0, time.monotonic()-self.detail_refreshed_at)*1000)})
            del events[:-8]

    def pause_if_detail_service_error(self, page) -> bool:
        # The saved real failure page has these exact visible messages and no
        # checkout control. Do not mistake it for an ordinary stock wait.
        failure = page.get_by_text(re.compile(r'^\s*(?:服务异常|服务出现异常|页面加载失败)\s*$'))
        if any(node.is_visible() for node in failure.all()):
            self.state['retain_browser'] = True
            if not self.state.get('service_error_waiting'):
                self.state['service_error_waiting'] = True
                self.log('warn', '详情服务异常：暂停刷新和点击，保留浏览器；请人工恢复目标详情后继续')
                self.save_diagnostics(page, 'detail-service-error')
            self.report_monitor_status(0, None)
            self.wait_ms(500)
            return True
        return False

    def submit_flash_lock(self, page) -> bool:
        self.assert_running()
        if self.state['order_submitted']:
            if self.state['order_lock_confirmed']:
                return True
            raise GracefulStop('已经提交一次且结果待确认，禁止重复提交')
        if self.state['pending_payment_review']:
            raise GracefulStop('提交结果需要人工核对，禁止重新购买')
        if self.pause_if_detail_service_error(page):
            return False
        self.install_product_image_viewer_guard(page)
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
            self.wait_ms(500)
            return False
        if self.state.get('service_error_waiting'):
            if not data.get('panels'):
                self.wait_ms(500)
                return False
            if data.get('matches') == 1:
                self.state['service_error_waiting'] = False
                self.detail_observation = None
                self.detail_refreshed_at = time.monotonic()
                self.log('info', '服务异常提示已消失且目标详情已核对，恢复监听页面变化')
        self.monitor_last_success = datetime.now(AUTOMATION_TIMEZONE).strftime('%H:%M:%S')
        if data['matches'] > 1:
            self.state['retain_browser'] = True
            self.save_diagnostics(page, 'detail-identity-unconfirmed')
            raise GracefulStop('目标详情面板不唯一，未提交；已保存详情身份诊断')
        if data['matches'] == 0 and data['panels'] > 0:
            if self.detail_open_started_at and time.monotonic()-self.detail_open_started_at <= 3:
                self.wait_ms(25)
                return False
            self.state['retain_browser'] = True
            self.save_diagnostics(page, 'detail-identity-unconfirmed')
            reason = data.get('identityReason', 'unknown')
            raise GracefulStop(f'目标详情身份未确认：{reason}；未提交，已保存诊断')
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
            self.log('info', f'阶段：目标详情编号 {self.config.product_id} 已核对，等待提交控件')
            if not (data.get('payment') or {}).get('safe'):
                self.save_diagnostics(page,'number-detail-confirmed')
        self.observe_detail_update(data)
        root = page.locator(DETAIL_ROOT_SELECTOR).nth(data['index'])
        if (data.get('payment') or {}).get('safe'):
            self.mark_phase('payment_available')
        list_became_ready = data.get('listReady', False) and not self.detail_last_list_ready
        self.detail_last_list_ready = data.get('listReady', False)
        if list_became_ready:
            self.mark_phase('list_opened')
            self.log('info', '目标商品卡片已开放购买；核对详情支付入口')
        cache_key = (data['index'], tuple(data.get('options', [])), data.get('specControlCount'), self.config.option_names)
        cached = self.state['options_verified'] and self.state.get('verified_specs_key') == cache_key
        if not cached and not self.select_product_options(page, required=True, scope=root, wait_for_availability=self.config.mode == 'flash'):
            self.state.pop('verified_specs_key', None)
            if (self.config.mode == 'flash') and not self.detail_refresh_blocked and (list_became_ready or (time.monotonic()-self.detail_refreshed_at)*1000 >= max(1000, self.config.detail_refresh_ms)):
                self.reopen_waiting_detail(page, data)
                self.detail_refreshed_at = time.monotonic()
            else:
                self.wait_for_detail_update(page, data)
            return False
        self.state['waiting_for_specs'] = False
        if not cached:
            self.mark_phase('specs_confirmed')
            self.log('info', '规格已核对：'+(' | '.join(g+'='+n for g,n in self.state.get('selected_options', [])) or '无规格'))
            spec_count = data.get('specControlCount')
            data = self.detail_snapshot(page)
            if data.get('matches') != 1 or data.get('manualVerification') or data.get('verification'):
                raise GracefulStop('选择规格后目标详情状态不明确，停止')
            if data.get('specControlCount') != spec_count:
                self.state['options_verified'] = False
                self.state.pop('verified_specs_key', None)
                return False
            self.observe_detail_update(data, record=False)
            self.state['verified_specs_key'] = (data.get('index'), tuple(data.get('options', [])), data.get('specControlCount'), self.config.option_names)
        if data.get('matches') != 1 or data.get('manualVerification') or data.get('verification'):
            raise GracefulStop('选择规格后目标详情状态不明确，停止')
        if data['paymentCount'] > 1:
            raise GracefulStop('详情页支付控件不唯一，未提交订单')
        payment = data.get('payment')
        if not payment:
            self.report_detail_submit_wait(page, '目标详情没有网页支付按钮；二维码页面不能视为可提交')
        elif not payment.get('safe') and not payment.get('stockError'):
            self.report_detail_submit_wait(page, '目标支付按钮尚未满足可见、可点击及控件安全条件')
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
            self.mark_phase('payment_available')
            if not PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(payment['text'], 160)):
                raise GracefulStop('详情页控件文字不是支付动作，未点击')
            if data['quantity'] != 1:
                raise GracefulStop('详情页锁单只允许一件，未确认数量为1，未提交订单')
            self.capture_ready_specs(page, root, payment['text'])
            self.assert_running()
            self.mark_phase('sale_detected')
            self.pending_order_snapshot(page, capture_existing=True)
            action = page.locator(DETAIL_ROOT_SELECTOR).nth(data['index']).locator(PAYMENT_PRIMARY_SELECTOR).nth(payment['index']).element_handle(timeout=200)
            if action is None:
                return False
            if not self.prepare_sku_payment_click(action):
                return False
            # Pin the actual native-click node, then validate its current context.
            # Recycled indices or a detached/reused panel must not target another item.
            pinned_snapshot_js = """(el,args) => {
                const collect=""" + DETAIL_SNAPSHOT_JS + """;
                const value=collect(args);
                const root=document.querySelectorAll(args.roots)[value.index];
                value.pinned=el.isConnected&&value.matches===1&&
                    root?.querySelectorAll(args.payment)[value.payment?.index]===el;
                return value;
            }"""
            pinned_snapshot_args = {'roots': DETAIL_ROOT_SELECTOR, 'name': self.config.product_name, 'requireNumber': self.config.mode == 'flash',
                     'number': self.config.product_id if str(self.config.product_id).isdigit() else '',
                     'payment': PAYMENT_PRIMARY_SELECTOR}
            current = action.evaluate(pinned_snapshot_js, pinned_snapshot_args)
            checked = current.get('payment')
            if current.get('manualVerification') or current.get('verification'):
                return self.pause_for_manual_verification()
            if not current.get('pinned') or current.get('submitSignature') != data.get('submitSignature'):
                raise GracefulStop('提交前商品详情或控件身份变化，未点击支付')
            if current.get('offline') or not checked or not checked['safe'] or checked['stockError']:
                return False
            if current['quantity'] != 1 or not PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(checked['text'], 160)):
                raise GracefulStop('提交前数量或控件文字变化，未点击支付')
            # Always a native click; synthetic purchase mode and auto-pay are ignored.
            self.log('info', f'阶段：编号 {self.config.product_id} 提交前核对完成，仅提交一次')
            self.record_submit_evidence(page,'number-before-submit')
            self.verify_selected_specs(root)
            final = action.evaluate(pinned_snapshot_js, pinned_snapshot_args)
            if not final.get('pinned') or final.get('submitSignature') != current.get('submitSignature') or final.get('manualVerification') or final.get('verification') or final.get('offline') or not PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text((final.get('payment') or {}).get('text', ''), 160)):
                before = json.loads(current.get('submitSignature') or '{}')
                after = json.loads(final.get('submitSignature') or '{}')
                self.state['submit_change_fields'] = [key for key in set(before)|set(after) if before.get(key) != after.get(key)]
                self.save_diagnostics(page, 'number-submit-state-changed')
                changed = '、'.join(self.state['submit_change_fields']) if final.get('pinned') else '支付按钮节点'
                raise GracefulStop(f'提交前详情状态变化（{changed}），停止')
            self.assert_running()
            try:
                self.safe_click(action, 'submit payment / create order')
                self.state['payment_clicks'] += 1
                self.state['order_submitted'] = True
                self.log('info', '已点击一次提交，正在确认待付款订单；不会继续付款或放弃支付')
                result = self.wait_for_pending_order(page)
                self.log_phase_timing('本次提交阶段耗时')
                return result
            finally:
                self.flush_submit_diagnostics(page)
                self.save_diagnostics(page, 'number-after-submit')
        if list_became_ready and self.config.mode == 'flash' and not self.detail_refresh_blocked:
            self.log('info', '卡片已开放但详情仍未可提交，立即重新核对并打开一次详情')
            self.reopen_waiting_detail(page, data)
            self.detail_refreshed_at = time.monotonic()
            return False
        if ((self.config.mode == 'flash') and not self.detail_refresh_blocked and ((time.monotonic()-self.detail_refreshed_at)*1000 >= max(1000, self.config.detail_refresh_ms))):
            self.reopen_waiting_detail(page, data)
            self.detail_refreshed_at = time.monotonic()
        else:
            self.wait_for_detail_update(page, data)
        return False

    def scan_dom_and_order(self, page) -> bool:
        self.state['scans'] += 1
        return self.submit_flash_lock(page)

    def save_diagnostics(self, page, reason: str) -> None:
        if self.config.mode == 'flash' and reason in ('number-detail-confirmed', 'detail-submit-wait'):
            self.state.setdefault('waiting_evidence', {})[reason] = {
                'observed_at': datetime.now(AUTOMATION_TIMEZONE).isoformat(),
                'detail_status': dict(self.state.get('detail_status', {})),
                'selected_options': list(self.state.get('selected_options', [])),
            }
            return
        critical = reason in ('detail-service-error','detail-submit-wait','detail-identity-unconfirmed') or reason.startswith('number-') or reason == 'product-option-unconfirmed' or reason in ('pending-order-unconfirmed','purchase-click-unconfirmed','product-option-not-unique','product-options-missing')
        if not self.config.save_diagnostics and not critical:
            return
        now = time.time() * 1000
        if not critical and now - self.state["last_diagnostic_at"] < 5000:
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
        if not hasattr(self, "resolved_product"):
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
        self.open_target_detail(page)
        self.monitor_started_at = time.monotonic()
        self.monitor_reported_at = float('-inf')
        self.log('info', f'开始持续监控：{self.config.product_name} · 编号 {self.config.product_id}；监听页面变化，低频刷新补充更新')
        ordered = False
        while not ordered:
            self.assert_running()
            started = time.monotonic()
            self.monitor_scan_error = None
            if page.is_closed():
                raise GracefulStop('浏览器页面已关闭，任务停止')
            try:
                ordered = self.scan_dom_and_order(page)
            except (GracefulStop, AccountVerificationRequired, UserAgreementRequired):
                raise
            except Exception as exc:
                self.state['retain_browser'] = True
                self.save_diagnostics(page, 'detail-scan-error')
                raise GracefulStop('详情检测异常，未重复提交；请人工核对页面：'+str(exc)) from exc
            if not ordered:
                self.report_monitor_status((time.monotonic()-started)*1000, self.monitor_scan_error)
        if not self.state['order_lock_confirmed']:
            raise GracefulStop('未确认待付款订单，不能视为锁单成功')
        self.log('info', '锁单成功：已确认待付款订单，任务停止；请自行完成付款')
        self.log_phase_timing('本次锁单耗时')

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
                matches = []
                for card in self.numbered_cards(page):
                    title = card.locator('[data-e2e="promotion-title"]')
                    self.bind_target_card(card)
                    matches.append((title, title.inner_text(timeout=300).strip()))
                if len(matches) > 1:
                    raise GracefulStop('商品匹配不唯一，未提交订单')
                if len(matches) == 1:
                    title, name = matches[0]
                    if getattr(self, 'batch_target_name', name) != name:
                        raise GracefulStop('重新匹配的商品身份变化，停止')
                    self.batch_target_name = name
                    self.log('info', f'商品匹配唯一：{name}；编号={self.config.product_id or "未填写"}')
                    if not self.safe_click(title, 'open target product detail'):
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
            raise GracefulStop('批量下单需要直播间链接及商品名称或编号')
        try:
            self.open_batch_target(page)
            for batch in range(1, self.config.buy_times + 1):
                self.assert_running()
                if (self.state['pending_payment_review'] or self.state['order_submitted']) and not self.state['order_lock_confirmed']:
                    raise GracefulStop('上一笔提交结果未确认，禁止继续批量下单')
                self.state['pending_payment_review'] = False
                self.log('info', f'批次 {batch}/{self.config.buy_times}：商品名称={self.config.product_name or "未填写"}；编号={self.config.product_id or "未填写"}')
                self.state['options_verified'] = False
                self.state['order_submitted'] = False
                self.state['order_lock_confirmed'] = False
                self.state.pop('ready_specs_snapshot', None)
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
                self.set_purchase_quantity(page, scope=root)
                data = self.batch_detail_snapshot(page)
                payment = data.get('payment')
                if (data.get('matches') != 1 or data.get('quantity') != self.config.buy_quantity or
                        not payment or payment['stockError'] or
                        not PAYMENT_ACTION_TEXT_RE.fullmatch(compact_text(payment['text'], 160))):
                    raise GracefulStop('提交前商品、数量或按钮状态不明确，停止')
                self.capture_ready_specs(page, root, payment['text'])
                self.batch_pending_order_snapshot(page, capture_existing=True)
                action = self.prepare_batch_submit(page, data)
                self.state['batch_verified_submission'] = {'quantity':self.config.buy_quantity, 'product_name':self.batch_target_name, 'selected_options':list(self.state.get('selected_options', [])), 'click_confirmed':False}
                self.assert_running()
                self.state['pending_payment_review'] = True
                self.state['order_submitted'] = True
                if not self.safe_click(action, 'submit payment / create order'):
                    raise GracefulStop('提交点击结果不明确，禁止重试')
                self.state['batch_verified_submission']['click_confirmed'] = True
                self.wait_for_batch_pending_order(page)
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
            self._batch_outcome = BatchOutcome('completed', self.state['batch_completed'], '全部待付款订单已确认，后续支付由用户处理')
        except Exception as exc:
            self.state['retain_browser'] = True
            self.save_diagnostics(page, 'number-batch-stopped')
            self.log('warn', f'批量停止原因：{exc}；已确认锁单 {self.state["batch_completed"]} 笔')
            raise

    def batch_pending_order_snapshot(self, page, capture_existing=False):
        from batch_receipt import NATIVE_RECEIPTS_JS
        records=page.evaluate(NATIVE_RECEIPTS_JS)
        if capture_existing:
            self.preexisting_pending_order_ids.update(x['id'] for x in records)
            return None
        fresh=[x for x in records if x['id'] not in self.preexisting_pending_order_ids]
        if len(fresh)>1:
            raise GracefulStop('新的待付款订单不唯一，停止且不重复提交')
        if not fresh:
            return None
        record=fresh[0]
        if not record['native']:
            text=record.get('text','')
            names=re.findall(r'(?:商品名称|商品名)\s*[:：]\s*([^\n]+)',text)
            quantities=re.findall(r'(?:购买数量|商品数量)\s*[:：]?\s*(\d+)',text)
            options=self.state.get('selected_options',parse_product_options(self.config.option_names))
            valid=(len(names)==1 and normalize_text(names[0])==normalize_text(self.batch_target_name)
                   and quantities==[str(self.config.buy_quantity)])
            for group,value in options:
                values=re.findall(re.escape(group)+r'\s*[:：=]\s*([^\n|]+)',text)
                valid=valid and len(values)==1 and normalize_text(values[0])==normalize_text(value)
            if not options:
                valid=valid and bool(re.search(r'规格\s*[:：]\s*(?:默认单一规格|无规格)\s*(?:$|\n)',text))
            if not valid:
                raise GracefulStop('待付款证据不足或商品、规格、数量不符，停止且不重复提交')
            self.state['batch_pending_quantity_source']='order_receipt'
            return record
        if record['item_count']!=1 or len(record['names'])!=1 or normalize_text(record['names'][0])!=normalize_text(self.batch_target_name):
            raise GracefulStop('待付款商品身份不符或不唯一，停止')
        options=self.state.get('selected_options',parse_product_options(self.config.option_names))
        values=[value for group,value in options]
        expected_specs=[normalize_text(sep.join(values)) for sep in (' ', '；', '，', ' / ')] if values else [normalize_text('默认'),normalize_text('默认单一规格'),normalize_text('无规格')]
        if len(record['specs'])!=1 or normalize_text(record['specs'][0]) not in expected_specs:
            raise GracefulStop('待付款规格不明确或不符，停止')
        if record['quantities']:
            if record['quantities']!=[self.config.buy_quantity]:
                raise GracefulStop('待付款数量与本笔不符，停止')
            self.state['batch_pending_quantity_source']='order_receipt'
        else:
            verified=self.state.get('batch_verified_submission',{})
            if (not self.state['order_submitted'] or not verified.get('click_confirmed') or
                    verified.get('quantity')!=self.config.buy_quantity or
                    verified.get('product_name')!=self.batch_target_name or
                    verified.get('selected_options')!=list(options)):
                raise GracefulStop('待付款未展示数量且本笔提交证据不足，停止')
            self.state['batch_pending_quantity_source']='pre_submit_verified'
            self.log('info', f'待付款弹窗未展示件数；本笔提交前已核对数量 {self.config.buy_quantity}，新订单商品及规格已匹配')
        return record

    def wait_for_batch_pending_order(self, page):
        deadline=time.monotonic()+self.pending_order_timeout_ms/1000
        while True:
            self.assert_running()
            result=self.batch_pending_order_snapshot(page)
            if result:
                self.state['order_lock_confirmed']=True
                self.state['pending_order_id']=result['id']
                self.state['pending_payment_review']=True
                self.state['manual_verification_waiting']=False
                self.mark_phase('order_locked')
                return True
            if page.evaluate(MANUAL_VERIFICATION_JS):
                self.pause_for_manual_verification(after_submit=True)
                deadline=time.monotonic()+self.pending_order_timeout_ms/1000
                continue
            if self.state['manual_verification_waiting']:
                self.resume_after_manual_verification(after_submit=True)
            if time.monotonic()>=deadline:
                self.save_diagnostics(page,'pending-order-unconfirmed')
                raise GracefulStop('批量已提交但未匹配唯一新待付款订单，停止且不重复提交')
            self.wait_ms(35)

    def prepare_batch_submit(self, page, expected):
        from playwright.sync_api import Error as PlaywrightError
        expected_state = json.loads(expected.get('submitSignature') or '{}')
        if expected_state.get('payment'):
            expected_state['payment']['safe'] = True
            expected_state['payment']['visible'] = True
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            self.assert_running()
            if self.state['pending_payment_review'] or self.state['order_submitted']:
                raise GracefulStop('已有提交结果待确认，禁止再次提交')
            current = self.batch_detail_snapshot(page)
            if (current.get('matches') != 1 or current.get('offline') or current.get('manualVerification')
                    or current.get('verification') or current.get('quantity') != self.config.buy_quantity
                    or current.get('paymentCount') != 1):
                raise GracefulStop('批量提交前商品、数量、验证或按钮唯一性未确认')
            root = page.locator(DETAIL_ROOT_SELECTOR).nth(current['index'])
            self.verify_selected_specs(root)
            payment = current.get('payment') or {}
            if payment.get('stockError'):
                raise GracefulStop('批量提交前商品库存不足')
            if not payment.get('safe'):
                self.wait_ms(50)
                continue
            after=json.loads(current.get('submitSignature') or '{}')
            if after != expected_state:
                before=expected_state
                changed=sorted(key for key in set(before)|set(after) if before.get(key)!=after.get(key))
                self.state['batch_submit_change_fields']=changed
                raise GracefulStop('批量提交前关键状态变化：'+'、'.join(changed))
            action = root.locator(PAYMENT_PRIMARY_SELECTOR).nth(payment['index']).element_handle(timeout=300)
            try:
                if action is None:
                    self.wait_ms(50)
                    continue
                action.click(trial=True, timeout=self.config.click_timeout_ms)
                final = self.batch_detail_snapshot(page)
                pinned = action.evaluate("""(el,args)=>el.isConnected &&
                    document.querySelectorAll(args.roots)[args.index]?.querySelectorAll(args.payment)[args.paymentIndex]===el""",
                    {'roots':DETAIL_ROOT_SELECTOR,'index':final.get('index'),
                     'payment':PAYMENT_PRIMARY_SELECTOR,'paymentIndex':payment['index']})
                if pinned and final.get('submitSignature') == current.get('submitSignature'):
                    self.verify_selected_specs(root)
                    return action
            except PlaywrightError as exc:
                self.state['batch_payment_ready_error']=str(exc)
            self.wait_ms(50)
        raise GracefulStop('批量支付按钮两秒内未稳定就绪，未提交；请核对页面')


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
                                    font=('PingFang SC', 10, 'bold' if active else 'normal'),
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


from ui_scrollbar import SmoothScrollbar


class App(tk.Tk):
    def __init__(self, license_controller=None) -> None:
        super().__init__()
        self.title("DY直播间自助工具")
        from mac_platform import set_window_icon
        set_window_icon(self, Path(__file__).resolve().parent / 'assets')
        self.geometry(f"{min(1180,self.winfo_screenwidth()-80)}x{min(860,self.winfo_screenheight()-100)}")
        self.minsize(980, 640)
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
        from mac_update_ui import UpdateController
        self.updater = UpdateController(self)
        try:
            self.accounts = self.account_store.load()
        except AccountStoreError as exc:
            self.accounts = ()
            self.account_store_error = str(exc)
            self.log('warn', self.account_store_error + '；多账号功能不可用，原单账号不受影响')
        self.build_ui()
        self.restore_preferences()
        from announcement_ui import AnnouncementController
        self.announcements = AnnouncementController(self, APP_DATA_DIR)
        self.after(1000, self.announcements.check_startup)
        for var in self.vars.values():
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
        for name in ('start_button','login_button','logout_button','browser_button','network_start_button','account_manager_button','update_button'):
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
                if name not in values:
                    continue
                value=values[name]
                if isinstance(var,tk.BooleanVar):
                    if isinstance(value,bool):
                        var.set(value)
                elif isinstance(value,str):
                    var.set(str(as_int(value,10000,1000)) if name=='detail_refresh_ms' else value)
            if self.vars['browser_choice'].get() not in BROWSER_CHOICES.values():
                self.vars['browser_choice'].set('auto')
        except (OSError,ValueError,tk.TclError):
            pass
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
        values={name:var.get() for name,var in self.vars.items()}
        values['profile_dir']=str(resolve_profile_directory(values.get('profile_dir','')))
        try:
            self.preferences_path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.preferences_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temporary,self.preferences_path)
        except OSError as exc:
            self.log('warn','配置保存失败：'+str(exc))

    def destroy(self) -> None:
        if hasattr(self, 'announcements'):
            self.announcements.close()
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
            "product_name": "请复制完整的商品名称；名称或编号至少填写一项。",
            "product_id": "填写“全部商品”卡片左上角的数字，不是规格编号。",
            "batch_live_url": "填写直播间链接，从全部商品定位目标。",
            "batch_product_id": "直播间商品卡片左上角编号；名称或编号任一匹配且结果唯一即可。",
            "batch_product_name": "请复制完整的商品名称；名称和编号至少填写一项，任一匹配且结果唯一即可。",
            "buy_times": "需要创建多少笔订单，例如 3 表示连续下 3 单。",
            "buy_quantity": "每笔订单购买几件，与订单数是两个不同的设置。",
            "batch_interval_ms": "两单之间的等待时间；800 毫秒 = 0.8 秒。",
            "click_timeout_ms": "等待单次点击完成的最长时间，默认 450 毫秒。",
            "open_panel_interval_ms": "尝试打开商品列表的最短间隔，默认 180 毫秒。",
            "detail_refresh_ms": "等待支付按钮时定期重新打开或刷新目标详情。默认10000毫秒（10秒），最低1000毫秒（1秒）；页面变化会立即检测，服务异常或验证时暂停刷新。确认待付款后停止。",
            "profile_dir": "保存登录状态的浏览器资料目录；通常无需修改。两个模块共用。",
            "option_names": "用中文分号；分隔规格组；填写即生效，空白时自动选择首个可用项。旧 | 格式仍兼容。",
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
        style.configure(".", font=("PingFang SC", 10), background="#f4f6fa", foreground="#263449")
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
        style.configure("Hint.TLabel", foreground="#65758b", font=("PingFang SC",9))
        style.configure("Section.TLabel", font=("PingFang SC",11,"bold"))
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
        self.update_button=ttk.Button(updates,text='检查更新',style='Primary.TButton',command=lambda:self.updater.check(manual=True))
        self.update_button.pack(side='left')
        from announcement_ui import show_announcements
        ttk.Button(updates,text='导出更新诊断',style='Quiet.TButton',command=self.updater.export_diagnostics).pack(side='left',padx=6)
        self.announcement_button=ttk.Button(updates,text='公告',style='Primary.TButton',command=lambda:self.announcements.open_history())
        self.announcement_button.pack(side='left',padx=6)
        from feedback_ui import show_feedback
        self.feedback_button=ttk.Button(updates,text='向我反馈',style='Primary.TButton',command=lambda:show_feedback(self,{'version':APP_VERSION,'module':'batch' if self.task_tabs.index(self.task_tabs.select())==1 else 'flash','browser':self.vars['browser_choice'].get()}))
        self.feedback_button.pack(side='left',padx=6)
        ttk.Label(header,text="① 登录并保存账号    →    ② 选择任务、填写商品    →    ③ 核对信息后开始下单",
                  style="Hint.TLabel").pack(anchor="w",pady=(6,0))

        left = ttk.Frame(root)
        left.grid(row=1,column=0,sticky="nsew",padx=(0,16))
        panel = self.scroll_panel(left)
        panel.configure(padding=(16, 0, 16, 16))
        panel.columnconfigure(0,weight=1)
        self.task_tabs = SmoothNotebook(panel)
        self.task_tabs.grid(row=0,column=0,columnspan=2,sticky="ew")
        form = ttk.Frame(self.task_tabs,padding=(16,10,16,8))
        batch = ttk.Frame(self.task_tabs,padding=16)
        self.task_tabs.add(form,text="DY直播间商品秒杀")
        self.task_tabs.add(batch,text="DY直播间批量下单")
        form.columnconfigure(1,weight=1)
        batch.columnconfigure(1,weight=1)
        self.add_field(form,0,"直播间链接","live_url",DEFAULT_LIVE_URL,32)
        self.add_field(form,1,"商品名称","product_name",DEFAULT_PRODUCT_NAME,32)
        self.add_field(form,2,"商品编号","product_id","1",16)
        detail_hint=ttk.Label(form,text="启动建议：在商品开始抢购的一分钟内启动秒杀监控。",style="Hint.TLabel",wraplength=450)
        detail_hint.grid(row=6,column=0,columnspan=2,sticky="ew",pady=(14,4))
        detail_hint.bind("<Configure>",lambda event:detail_hint.configure(wraplength=max(140,event.width)))
        ttk.Label(form,text="启动后会真实提交订单；确认待付款后，请自行完成付款。",
                  style="Hint.TLabel",wraplength=450).grid(row=7,column=0,columnspan=2,sticky="w",pady=(0,4))
        self.add_field(batch,0,"直播间链接","batch_live_url","",32)
        self.add_field(batch,1,"商品名称","batch_product_name","",32)
        self.add_field(batch,2,"商品编号","batch_product_id","",16)
        self.add_field(batch,3,"订单数（笔）","buy_times","1",16)
        self.add_field(batch,4,"每单数量（件）","buy_quantity","1",16)
        self.add_field(batch,5,"两单间隔（毫秒）","batch_interval_ms","800",16)
        accounts = ttk.LabelFrame(batch,text='多账号 · 按顺序执行',padding=12)
        accounts.grid(row=12,column=0,columnspan=2,sticky='ew',pady=(14,0))
        accounts.columnconfigure(0,weight=1)
        self.add_check(accounts,0,0,'启用多账号依次下单','batch_multi_account',False)
        self.account_manager_button = ttk.Button(accounts,text='管理账号',style='Quiet.TButton',command=self.open_account_manager)
        self.account_manager_button.grid(row=0,column=1,sticky='e',padx=(8,0))
        account_hint = ttk.Label(accounts,text='默认关闭。管理账号 → 登录并保存 → 设置数量和顺序。\n开启后按各账号设置执行；异常停止整队，先核对订单。',style='Hint.TLabel',wraplength=450)
        account_hint.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(6,0))
        accounts.bind('<Configure>',lambda event: account_hint.configure(wraplength=max(140,event.width-28)))
        ttk.Label(batch,text="每笔确认待付款即锁单成功；订单数是独立订单笔数，每单数量是每笔购买件数。",
                  style="Hint.TLabel",wraplength=450).grid(row=13,column=0,columnspan=2,sticky="w",pady=(10,0))

        rules = ttk.LabelFrame(panel,text="商品规格 · 两个模块共享",padding=14)
        rules.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(10,0))
        rules.columnconfigure(0,weight=1)
        sku = ttk.Frame(rules)
        sku.grid(row=0,column=0,columnspan=2,sticky="ew")
        sku.columnconfigure(1,weight=1)
        ttk.Label(sku,text="填写规格信息即生效；两个模块共享。",style="Hint.TLabel").grid(row=0,column=0,columnspan=2,sticky="w")
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
        ttk.Button(examples,text='复制多规格示例',command=lambda: (self.clipboard_clear(), self.clipboard_append('规格1=规格名称1；规格2=规格名称2；规格3=规格名称3'))).grid(row=0,column=1,padx=8)
        example_labels = []
        for row, text in enumerate((
            '以下只为示例，名称以商品页面为准；不同规格组用“；”分隔。（注意：这是中文分号）',
            '未填写规格信息时，如页面要求选择规格，每组自动选择第一个可用选项并确认。',
            '手机：机身颜色=曜石黑；存储容量=12GB+512GB',
            '电脑：套餐类型=套餐一；硬盘容量=1TB',
            '捏捏：款式=麦序',
            '日常用品：规格=规格名称',
            '日常用品（多个规格）：规格1=规格名称1；规格2=规格名称2；规格3=规格名称3',
        ),1):
            label = ttk.Label(examples,text=text,style='Hint.TLabel',wraplength=450)
            label.grid(row=row,column=0,columnspan=2,sticky='ew',pady=2)
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
        self.log_text = tk.Text(logs,wrap="word",height=20,width=36,font=("PingFang SC",9),
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
        from usage_guide import FLASH_GUIDE, BATCH_GUIDE
        self.guide_mode = None
        def refresh_guide(batch_selected):
            mode = 'batch' if batch_selected else 'flash'
            if self.guide_mode == mode:
                return
            self.guide_mode = mode
            for child in help_body.winfo_children():
                child.destroy()
            for i,(title,detail) in enumerate(BATCH_GUIDE if batch_selected else FLASH_GUIDE):
                ttk.Label(help_body,text=title,style="Section.TLabel").grid(row=i*2,column=0,columnspan=2,sticky="w",pady=(12,4))
                label=ttk.Label(help_body,text=detail,style="Hint.TLabel",wraplength=300)
                label.grid(row=i*2+1,column=0,columnspan=2,sticky="ew")
                label.bind('<Configure>',lambda e,w=label:w.configure(wraplength=max(140,e.width)))
        adv = self.scroll_panel(advanced)
        for row,(label,name,default) in enumerate([
            ("点击超时（毫秒）","click_timeout_ms","450"),
            ("面板间隔（毫秒）","open_panel_interval_ms","180"),
            ("详情刷新间隔（毫秒，最低1000）","detail_refresh_ms","10000"),
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
        self.vars['browser_choice'] = tk.StringVar(value='auto')
        self.browser_button = ttk.Button(footer,text='选择浏览器',command=self.open_browser_choice)
        self.browser_button.pack(side='left',padx=(8,0))
        self.start_button = ttk.Button(footer,text="开始秒杀监控",style="Primary.TButton",command=self.start_task)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(footer,text="停止任务",command=self.stop_task,state="disabled")
        self.stop_button.pack(side="right",padx=8)
        self.mode_caption = tk.StringVar()
        ttk.Label(footer,textvariable=self.mode_caption,style="Hint.TLabel").pack(side="right",padx=12)
        def refresh(*_):
            batch_selected = self.task_tabs.index(self.task_tabs.select()) == 1
            refresh_guide(batch_selected)
            if _ and isinstance(_[0], tk.Event) and _[0].type == tk.EventType.VirtualEvent:
                self.cancel_scroll(panel.master)
                panel.master.yview_moveto(0)
            self.start_button.configure(text="开始批量下单" if batch_selected else "开始秒杀监控")
            self.mode_caption.set('真实下单 · '+('确认待付款后继续' if batch_selected else '待付款后停止'))
            self.option_entry.configure(state="normal")
            page = self.task_tabs.pages[1 if batch_selected else 0]
            height = page.winfo_reqheight()+self.task_tabs.header_height
            if self.task_tabs.winfo_reqheight() != height:
                self.task_tabs.configure(height=height)
        self.task_tabs.bind("<<NotebookTabChanged>>",refresh)
        for page in self.task_tabs.pages:
            page.bind('<Configure>',refresh)
        refresh()
        # Each task fits its content; the footer stays outside the scrolling form.
        self.update_idletasks()
        self.task_tabs.fit_pages()
        self.side_tabs.fit_pages()
        refresh()

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
        self.network_result = tk.Text(parent,wrap='word',height=18,width=32,font=('PingFang SC',9),
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
        self.browser_button.configure(state='disabled')
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
            click_timeout_ms=as_int(get("click_timeout_ms"), 450, 100),
            open_panel_interval_ms=as_int(get("open_panel_interval_ms"), 180, 50),
            detail_refresh_ms=as_int(get('detail_refresh_ms'), 10000, 1000),
            profile_dir=str(resolve_profile_directory(get("profile_dir"))),
            headless=bool(get("headless")),
            browser_choice=str(get("browser_choice")),
            close_browser_on_finish=bool(get("close_browser_on_finish")),
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
        self.browser_button.configure(state='disabled' if self.updater.busy() else 'normal')
        self.finish_login_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.network_start_button.configure(state='normal')
        self.set_account_busy(False)

    def account_busy(self) -> bool:
        return bool(self.worker and self.worker.is_alive()) or self.network_busy() or self.closing or self.updater.busy()

    def set_account_busy(self, busy):
        if busy:
            self.logout_button.configure(state='disabled')
            self.browser_button.configure(state='disabled')
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
            messagebox.showerror('配置错误', '商品名称和商品编号至少填写一个'); return
        if config.product_id and (not config.product_id.isdigit() or int(config.product_id) < 1):
            messagebox.showerror('配置错误', '商品编号必须为正整数'); return
        if config.option_names.strip():
            try:
                parse_product_options(config.option_names)
            except ValueError as exc:
                messagebox.showerror("配置错误", str(exc))
                return
        if jobs:
            summary='\n'.join(f'{i+1}. {j.name} · {j.account_id[:8]}：{j.buy_times} 单，每单 {j.buy_quantity} 件' for i,j in enumerate(jobs))
            summary += '\n\n'+('真实下单 · '+'每笔待付款即完成，后续支付由用户处理')
            if not messagebox.askyesno('确认账号执行顺序',summary+'\n异常停止整队，先核对订单再决定是否重跑。继续？'): return
        warning = "即将启用真实下单操作，脚本会点击购买和提交订单按钮。"
        lock_mode=effective_flash_lock_mode(config)
        if lock_mode != 'none':
            warning += ('\n\n启动建议：在商品开始抢购的一分钟内启动秒杀监控。'
                        '\n这是启动建议，不限制你提前或稍后启动。'
                        '\n程序会点击一次“支付”提交，确认新待付款订单后停止，不继续付款。'
                        '\n按钮是否会直接扣款取决于平台和账号设置，程序不能保证只锁库存。')
        warning += "\n\n请确认商品、规格、数量、收货地址和账号均正确。是否继续？"
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

    def set_browser_choice(self, choice):
        if self.update_blocked():
            messagebox.showwarning('暂不能切换浏览器', '请先结束任务、登录、网络检测或更新。')
            return False
        if choice not in BROWSER_CHOICES.values():
            raise ValueError('请选择自动选择、Microsoft Edge 或 Google Chrome')
        self.vars['browser_choice'].set(choice)
        self.save_preferences()
        label = next(label for label, value in BROWSER_CHOICES.items() if value == choice)
        self.log('info', f'浏览器已设置为：{label}；下次登录和任务使用此设置，原登录资料保留')
        return True

    def open_browser_choice(self):
        if self.update_blocked():
            messagebox.showwarning('暂不能切换浏览器', '请先结束任务、登录、网络检测或更新。')
            return
        dialog = tk.Toplevel(self)
        dialog.title('选择软件使用的浏览器')
        dialog.transient(self)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='登录和下单使用的浏览器', style='Section.TLabel').pack(anchor='w')
        label = next(label for label, value in BROWSER_CHOICES.items() if value == self.vars['browser_choice'].get())
        selected = tk.StringVar(value=label)
        ttk.Combobox(body, textvariable=selected, values=tuple(BROWSER_CHOICES), state='readonly', width=28).pack(fill='x', pady=12)
        ttk.Label(body, text='自动选择：优先 Edge，再尝试 Chrome。\n明确选择后，启动失败会提示，不自动换浏览器。\n首次切换可能需要重新登录；原登录资料保留。\n此设置不会修改 Windows 默认浏览器。',
                  style='Hint.TLabel', wraplength=360).pack(anchor='w')
        def save():
            if self.set_browser_choice(BROWSER_CHOICES[selected.get()]):
                dialog.destroy()
        ttk.Button(body, text='保存选择', style='Primary.TButton', command=save).pack(anchor='e', pady=(16,0))

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
        config=replace(session.config, browser_choice=self.vars['browser_choice'].get()) if session and account_id else self.config_from_form()
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
        from mac_platform import open_directory
        open_directory(path)

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
    if sys.platform != 'darwin':
        raise RuntimeError('此独立版本只能在macOS运行，请使用Windows正式版')
    online = OnlineLicenseClient(APP_DATA_DIR, machine_fingerprint(), LICENSE_PUBLIC_KEYS, LicenseTransport(LICENSE_ORIGIN))
    activation = ActivationDialog(LocalLicenseClient(), online)
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
