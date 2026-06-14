#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JLC browser-only lottery automation.

This script drives the mobile page with Playwright and does not call lottery
APIs through requests/httpx. Prize results are read from the visible result
popup so they can be written into the same JSON shape used by h3.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "h4" / "results"
_PUBLIC_IP_CACHE = {"loaded": False, "value": ""}

MINIPROGRAM_APPID = "wx6c7b851c877dba42"
DEFAULT_REFERER = f"https://servicewechat.com/{MINIPROGRAM_APPID}/140/page-frame.html"
DEFAULT_MP_SECRET_KEY_VALUE = "62333335373634382d613039362d346439642d383935652d626666396162323664656136"
INVOICE_INFO_PATH = "/api/integrated/vatInvoiceInfo/selectInvoiceInfoDetails"
DEFAULT_ACTIVITY_URL = (
    "https://m.jlc.com/pages-promo/brand-campaign/index"
    "?_embed=1&source=jlc_mobile_app&clientType=MP-WEIXIN"
)
MINIPROGRAM_UA = (
    "Mozilla/5.0 (Linux; Android 15; 23078RKD5C Build/AQ3A.240912.001; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/132.0.6834.122 Mobile Safari/537.36 "
    "MicroMessenger/8.0.56.2820(0x28003859) WeChat/arm64 Weixin "
    "NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android"
)

MINIPROGRAM_PROFILES = [
    {
        "name": "K60U",
        "ua": (
            "Mozilla/5.0 (Linux; Android 13; 23078RKD5C Build/TP1A.220624.014; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/146.0.7680.178 Mobile Safari/537.36 XWEB/1460205 "
            "MMWEBSDK/20260202 MMWEBID/5956 MicroMessenger/8.0.71.3080(0x28004750) "
            "WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android"
        ),
        "width": 393,
        "height": 873,
        "scale": 2.75,
    },
    {
        "name": "Android-15",
        "ua": MINIPROGRAM_UA,
        "width": 393,
        "height": 873,
        "scale": 2.75,
    },
    {
        "name": "Xiaomi",
        "ua": (
            "Mozilla/5.0 (Linux; Android 14; 23127PN0CC Build/UKQ1.230804.001; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/141.0.7390.122 Mobile Safari/537.36 XWEB/1410133 "
            "MMWEBSDK/20251201 MicroMessenger/8.0.63.2860(0x28003f5c) "
            "WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android"
        ),
        "width": 412,
        "height": 915,
        "scale": 2.625,
    },
    {
        "name": "OnePlus",
        "ua": (
            "Mozilla/5.0 (Linux; Android 14; PJD110 Build/UKQ1.230924.001; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/140.0.7339.210 Mobile Safari/537.36 XWEB/1400091 "
            "MMWEBSDK/20251115 MicroMessenger/8.0.61.2840(0x28003d5b) "
            "WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android"
        ),
        "width": 384,
        "height": 854,
        "scale": 2.75,
    },
]


BAD_PRIZE_RE = re.compile(r"(抽奖机会|我的抽奖机会|立即抽奖|开始抽奖|去抽奖|再抽一次|报名|兑换|活动规则|订单统计)")
RESULT_TITLE_RE = re.compile(r"(恭喜[您你]?|抽中)")


def now_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(message: str) -> None:
    print(f"[{now_text()}] {message}", flush=True)


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def get_public_ip() -> str:
    configured = env_first("SIGN_IP", "PUBLIC_IP")
    if configured:
        return configured
    if _PUBLIC_IP_CACHE["loaded"]:
        return _PUBLIC_IP_CACHE["value"]

    ip_value = ""
    for url, response_type in (
        ("https://api.ipify.org?format=json", "json"),
        ("https://ifconfig.me/ip", "text"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=6) as response:
                text = response.read().decode("utf-8", errors="ignore").strip()
            if response_type == "json":
                payload = json.loads(text or "{}")
                ip_value = str(payload.get("ip") or "").strip()
            else:
                ip_value = text
            if ip_value:
                break
        except Exception:
            continue

    _PUBLIC_IP_CACHE["loaded"] = True
    _PUBLIC_IP_CACHE["value"] = ip_value
    return ip_value


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def mask_account(account: str) -> str:
    if not account:
        return ""
    if len(account) <= 4:
        return account[0] + "***"
    return account[:3] + "****" + account[-4:]


def random_sleep(min_seconds: float, max_seconds: float, reason: str = "") -> None:
    delay = random.uniform(min_seconds, max_seconds)
    if reason:
        log(f"{reason}，等待 {delay:.1f}s")
    time.sleep(delay)


def choose_miniprogram_profile() -> dict[str, Any]:
    configured_ua = env_first("USER_AGENT", "JLC_USER_AGENT")
    if configured_ua and truthy(os.getenv("H4_FORCE_ENV_UA"), default=False):
        profile = random.choice(MINIPROGRAM_PROFILES).copy()
        profile["name"] = "env-ua"
        profile["ua"] = configured_ua
        return profile
    return random.choice(MINIPROGRAM_PROFILES).copy()


def safe_money(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(str(value).strip()), 2)
    except Exception:
        return default


def load_account_from_args() -> tuple[str, str, int]:
    if len(sys.argv) >= 2 and sys.argv[1] in {"-h", "--help"}:
        print(
            "用法:\n"
            "  python h4/script.py <账号> <密码> [账号序号]\n"
            "或通过环境变量 ACCOUNT_USERNAME / ACCOUNT_PASSWORD / ACCOUNT_INDEX 提供。\n"
            "脚本只操作浏览器页面，不使用 requests/httpx 主动发抽奖接口。"
        )
        sys.exit(0)

    username = sys.argv[1].strip() if len(sys.argv) >= 2 else env_first("ACCOUNT_USERNAME", "JLC_USERNAME")
    password = sys.argv[2].strip() if len(sys.argv) >= 3 else env_first("ACCOUNT_PASSWORD", "JLC_PASSWORD")
    account_index = int(sys.argv[3]) if len(sys.argv) >= 4 and sys.argv[3].isdigit() else env_int("ACCOUNT_INDEX", 1)

    if username and password:
        return username, password, account_index

    accounts = env_first("ACCOUNTS", "JLC_ACCOUNTS")
    if accounts:
        first = accounts.splitlines()[0].strip()
        if "----" in first:
            parts = first.split("----")
        elif "," in first:
            parts = first.split(",")
        else:
            parts = first.split()
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip(), account_index

    raise SystemExit("缺少账号密码：请传入 python h4/script.py <账号> <密码>，或设置 ACCOUNT_USERNAME/ACCOUNT_PASSWORD。")


def find_dict_values(data: Any, wanted_keys: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key) in wanted_keys and value not in (None, ""):
                found.append(value)
            found.extend(find_dict_values(value, wanted_keys))
    elif isinstance(data, list):
        for item in data:
            found.extend(find_dict_values(item, wanted_keys))
    return found


def first_text(data: Any, keys: set[str]) -> str:
    for value in find_dict_values(data, keys):
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text:
                return text
    return ""


def unwrap_response_data(payload: Any) -> Any:
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        return payload["data"]
    return payload


def is_valid_prize_title(title: str) -> bool:
    text = str(title or "").strip()
    if not text:
        return False
    if text.lower() in {"true", "false", "none", "null"}:
        return False
    if len(text) > 60:
        return False
    compacted = re.sub(r"\s+", "", text)
    return not BAD_PRIZE_RE.search(compacted)


def prize_title_from_dict(item: dict[str, Any]) -> str:
    title_keys = [
        "prizeTitle",
        "goodsName",
        "skuTitle",
        "prizeName",
        "awardName",
        "couponName",
        "couponTitle",
        "giftName",
        "lotteryName",
        "title",
        "name",
    ]
    for key in title_keys:
        value = item.get(key)
        if isinstance(value, (str, int, float)):
            title = str(value).strip()
            if is_valid_prize_title(title):
                return title
    return ""


def direct_prize_nodes(payload: Any) -> list[dict[str, Any]]:
    data = unwrap_response_data(payload)
    if isinstance(data, dict):
        prize_list = data.get("prizeList")
        if isinstance(prize_list, list):
            return [item for item in prize_list if isinstance(item, dict)]
        for key in ("prize", "award", "coupon", "goods", "winRecord", "winningRecord"):
            value = data.get(key)
            if isinstance(value, dict):
                return [value]
        if prize_title_from_dict(data):
            return [data]
    return []


def collect_prizes_from_json(payload: Any) -> list[dict[str, str]]:
    code_keys = {"winCode", "prizeCode", "awardCode", "couponCode", "code", "id"}
    amount_keys = {"amount", "num", "count", "beanNum", "point", "points"}

    prizes: list[dict[str, str]] = []

    for item in direct_prize_nodes(payload):
        title = prize_title_from_dict(item)
        if title:
            prizes.append(
                {
                    "prize_title": title,
                    "prize_code": first_text(item, code_keys),
                    "amount": first_text(item, amount_keys),
                }
            )

    if prizes:
        cleaned: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for prize in prizes:
            title = prize.get("prize_title", "").strip()
            code = prize.get("prize_code", "").strip()
            key = (title, code)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(prize)
        return cleaned

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            title = prize_title_from_dict(node)
            has_prize_signal = any(
                key in node
                for key in (
                    "prizeTitle",
                    "goodsName",
                    "skuTitle",
                    "prizeCode",
                    "winCode",
                    "turnCode",
                    "couponName",
                    "awardName",
                )
            )
            if title and has_prize_signal:
                prizes.append(
                    {
                        "prize_title": title,
                        "prize_code": first_text(node, code_keys),
                        "amount": first_text(node, amount_keys),
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)

    cleaned: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for prize in prizes:
        title = prize.get("prize_title", "").strip()
        code = prize.get("prize_code", "").strip()
        if not is_valid_prize_title(title):
            continue
        key = (title, code)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(prize)
    return cleaned


def find_invoice_money_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "invoiceMoney":
                return safe_money(item)
        for item in value.values():
            found = find_invoice_money_value(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        if not value:
            return 0.0
        for item in value:
            found = find_invoice_money_value(item)
            if found is not None:
                return found
    return None


@dataclass
class LotteryRecord:
    prize_title: str
    prize_code: str = ""
    amount: str = ""
    won_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    expire_time: str = field(default_factory=lambda: (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"))
    source_url: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "activity": "lottery",
            "prize_title": self.prize_title,
            "prize_name": self.prize_title,
            "title": self.prize_title,
            "prize_code": self.prize_code,
            "win_code": self.prize_code,
            "amount": self.amount,
            "won_at": self.won_at,
            "draw_time": self.won_at,
            "expiry_date": self.expire_time,
            "status_text": self.expire_time,
            "expire_time": self.expire_time,
            "expiry_time": self.expire_time,
            "source_url": self.source_url,
        }


class LotteryMonitor:
    def __init__(self) -> None:
        self.records: list[LotteryRecord] = []


@dataclass
class Config:
    passport_url: str = ""
    activity_url: str = ""
    referer: str = ""
    slider_id: str = ""
    wrapper_id: str = ""
    headless: bool = False
    signup_target: int = 4
    exchange_target: int = 3
    draw_target: int = 3
    slow_mo: int = 0
    generate_report: bool = False
    cleanup_local_files: bool = False
    default_timeout_ms: int = 30_000
    navigation_timeout_ms: int = 90_000
    networkidle_timeout_ms: int = 45_000
    selector_timeout_ms: int = 30_000
    result_popup_timeout_seconds: float = 25.0


def load_config() -> Config:
    in_github_actions = truthy(os.getenv("GITHUB_ACTIONS"), default=False)
    default_timeout = 60_000 if in_github_actions else 30_000
    navigation_timeout = 120_000 if in_github_actions else 90_000
    networkidle_timeout = 60_000 if in_github_actions else 45_000
    selector_timeout = 45_000 if in_github_actions else 30_000
    result_popup_timeout = 35 if in_github_actions else 25
    cleanup_requested = truthy(
        os.getenv("H4_CLEANUP_LOCAL_FILES"),
        default=not in_github_actions,
    )
    cleanup_local_files = cleanup_requested and not truthy(os.getenv("H4_KEEP_LOCAL_FILES"), default=False)
    if in_github_actions and not truthy(os.getenv("H4_FORCE_CLEANUP_LOCAL_FILES"), default=False):
        cleanup_local_files = False

    return Config(
        passport_url=env_first(
            "PASSPORT_URL",
            default="https://passport.jlc.com/mobile/login?redirect=https%3A%2F%2Fm.jlc.com%2F",
        ),
        activity_url=env_first("ACTIVITY_URL", "LOTTERY_ACTIVITY_URL", default=DEFAULT_ACTIVITY_URL),
        referer=env_first("REFERER", "JLC_REFERER", default=DEFAULT_REFERER),
        slider_id=env_first("SLIDER_ID", default="nc_1_n1z"),
        wrapper_id=env_first("WRAPPER_ID", default="nc_1__scale_text"),
        headless=truthy(env_first("H4_HEADLESS", "HEADLESS"), default=False),
        signup_target=env_int("H4_SIGNUP_TARGET", 4),
        exchange_target=env_int("H4_EXCHANGE_TARGET", 3),
        draw_target=env_int("H4_DRAW_TARGET", 3),
        slow_mo=env_int("H4_SLOW_MO", 0),
        generate_report=truthy(os.getenv("GENERATE_XLSX"), default=False),
        cleanup_local_files=cleanup_local_files,
        default_timeout_ms=env_int("H4_DEFAULT_TIMEOUT_MS", default_timeout),
        navigation_timeout_ms=env_int("H4_NAVIGATION_TIMEOUT_MS", navigation_timeout),
        networkidle_timeout_ms=env_int("H4_NETWORKIDLE_TIMEOUT_MS", networkidle_timeout),
        selector_timeout_ms=env_int("H4_SELECTOR_TIMEOUT_MS", selector_timeout),
        result_popup_timeout_seconds=safe_money(env_first("H4_RESULT_POPUP_TIMEOUT_SECONDS"), result_popup_timeout),
    )


def add_miniprogram_fingerprint(context: BrowserContext) -> None:
    context.add_init_script(
        """
        (() => {
          const define = (target, key, value) => {
            try { Object.defineProperty(target, key, { get: () => value, configurable: true }); } catch (_) {}
          };
          define(Navigator.prototype, 'webdriver', undefined);
          define(Navigator.prototype, 'platform', 'Linux armv8l');
          define(Navigator.prototype, 'maxTouchPoints', 5);
          define(Navigator.prototype, 'languages', ['zh-CN', 'zh']);
          window.__wxjs_environment = 'miniprogram';
          window.__wxConfig = window.__wxConfig || {};
          window.WeixinJSBridge = window.WeixinJSBridge || {
            invoke: function(_, __, cb) { if (typeof cb === 'function') cb({ err_msg: 'ok' }); },
            on: function() {},
            call: function() {}
          };
          window.wx = window.wx || {
            miniProgram: {
              getEnv: function(cb) { if (typeof cb === 'function') cb({ miniprogram: true }); },
              navigateTo: function() {},
              redirectTo: function() {},
              switchTab: function() {},
              postMessage: function() {}
            }
          };
          document.addEventListener('WeixinJSBridgeReady', function() {}, false);
        })();
        """
    )


def new_browser(p: Any, config: Config) -> tuple[Browser, BrowserContext, Page]:
    profile = choose_miniprogram_profile()
    if truthy(os.getenv("H4_FORCE_VIEWPORT"), default=False):
        viewport_width = env_int("BROWSER_VIEWPORT_WIDTH", int(profile["width"]))
        viewport_height = env_int("BROWSER_VIEWPORT_HEIGHT", int(profile["height"]))
        device_scale_factor = safe_money(env_first("BROWSER_DEVICE_SCALE_FACTOR"), float(profile["scale"]))
    else:
        viewport_width = int(profile["width"])
        viewport_height = int(profile["height"])
        device_scale_factor = float(profile["scale"])
    log(
        f"使用移动端 profile: {profile.get('name')} "
        f"{viewport_width}x{viewport_height}@{device_scale_factor}"
    )
    browser = p.chromium.launch(
        headless=config.headless,
        slow_mo=config.slow_mo,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    )
    context = browser.new_context(
        user_agent=str(profile["ua"]),
        viewport={"width": viewport_width, "height": viewport_height},
        screen={"width": viewport_width, "height": viewport_height},
        device_scale_factor=device_scale_factor,
        is_mobile=True,
        has_touch=True,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        extra_http_headers={
            "Referer": config.referer,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "x-jlc-clienttype": "MP-WEIXIN",
        },
    )
    add_miniprogram_fingerprint(context)
    page = context.new_page()
    page.set_default_timeout(config.default_timeout_ms)
    page.set_default_navigation_timeout(config.navigation_timeout_ms)
    return browser, context, page


def solve_slider_with_bezier(page: Page, config: Config, account_label: str = "") -> bool:
    slider_id = config.slider_id
    wrapper_id = config.wrapper_id
    if not slider_id or not wrapper_id:
        log(f"{account_label}未配置滑块 ID，跳过滑块检测")
        return True

    slider_selector = f"#{slider_id}"
    wrapper_selector = f"#{wrapper_id}"
    try:
        page.wait_for_selector(slider_selector, state="visible", timeout=min(10_000, config.selector_timeout_ms))
    except TimeoutError:
        log(f"{account_label}未检测到滑块")
        return True

    log(f"{account_label}检测到滑块，开始拖动")
    for attempt in range(1, 4):
        try:
            ok = page.evaluate(
                """
                async ({ sliderSelector, wrapperSelector }) => {
                  const slider = document.querySelector(sliderSelector);
                  const wrapper = document.querySelector(wrapperSelector) || slider?.parentElement;
                  if (!slider || !wrapper) return false;
                  const s = slider.getBoundingClientRect();
                  const w = wrapper.getBoundingClientRect();
                  const startX = s.left + s.width / 2;
                  const startY = s.top + s.height / 2;
                  const distance = Math.max(220, w.width - s.width - 8);
                  const steps = 38 + Math.floor(Math.random() * 16);
                  const overshoot = 8 + Math.random() * 18;

                  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
                  function dispatch(type, x, y) {
                    const init = {
                      bubbles: true,
                      cancelable: true,
                      composed: true,
                      clientX: x,
                      clientY: y,
                      screenX: x,
                      screenY: y,
                      pageX: x,
                      pageY: y,
                      button: 0,
                      buttons: type === 'mouseup' || type === 'pointerup' ? 0 : 1,
                      pointerId: 1,
                      pointerType: 'mouse',
                      isPrimary: true,
                    };
                    slider.dispatchEvent(new PointerEvent(type.replace('mouse', 'pointer'), init));
                    slider.dispatchEvent(new MouseEvent(type, init));
                  }

                  dispatch('mousedown', startX, startY);
                  await sleep(180 + Math.random() * 140);
                  for (let i = 1; i <= steps; i++) {
                    const t = i / steps;
                    const ease = 1 - Math.pow(1 - t, 2.6);
                    const jitter = Math.sin(t * Math.PI * 5) * (1.5 + Math.random() * 2.5);
                    let x = startX + (distance + overshoot) * ease + jitter;
                    let y = startY + Math.sin(t * Math.PI * 2) * (2 + Math.random() * 3);
                    dispatch('mousemove', x, y);
                    await sleep(8 + Math.random() * 18);
                  }
                  await sleep(120 + Math.random() * 220);
                  dispatch('mousemove', startX + distance, startY + Math.random() * 3);
                  await sleep(80 + Math.random() * 120);
                  dispatch('mouseup', startX + distance, startY);
                  return true;
                }
                """,
                {"sliderSelector": slider_selector, "wrapperSelector": wrapper_selector},
            )
            if not ok:
                continue
            time.sleep(2.0)
            if page.locator(slider_selector).count() == 0 or not page.locator(slider_selector).first.is_visible(timeout=2_000):
                log(f"{account_label}滑块通过")
                return True
        except Exception as exc:
            log(f"{account_label}第 {attempt} 次拖动滑块失败: {exc}")
        random_sleep(1.0, 2.0)
    return False


def click_if_visible(page: Page, selector: str, timeout: int = 1_500) -> bool:
    try:
        locator = page.locator(selector).first
        if locator.count() > 0 and locator.is_visible(timeout=timeout):
            locator.click(timeout=timeout)
            return True
    except Exception:
        return False
    return False


def login(page: Page, config: Config, username: str, password: str, account_index: int) -> None:
    label = f"账号{account_index} - "
    log(f"{label}打开移动登录页")
    page.goto(config.passport_url, wait_until="domcontentloaded", timeout=config.navigation_timeout_ms)
    page.wait_for_load_state("networkidle", timeout=config.networkidle_timeout_ms)

    user_selectors = [
        'input[placeholder*="手机"]',
        'input[placeholder*="邮箱"]',
        'input[type="text"]',
        'input[type="tel"]',
    ]
    user_filled = False
    for selector in user_selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=min(8_000, config.selector_timeout_ms)):
                locator.fill(username)
                user_filled = True
                break
        except Exception:
            continue
    if not user_filled:
        raise RuntimeError("找不到账号输入框")

    agreement_selectors = [
        "#__layout > div > div > div > div > div:nth-child(3) > form > div.mt-30.mb-32 > div.consent-agreement > div > img:nth-child(2)",
        ".consent-agreement img",
        ".consent-agreement [role='checkbox']",
        "input[type='checkbox']",
    ]
    if any(click_if_visible(page, selector) for selector in agreement_selectors):
        log(f"{label}已点击同意协议控件")
    else:
        log(f"{label}未找到同意协议控件，可能页面已默认同意")

    first_login_btn = "#__layout > div > div > div > div > div:nth-child(3) > form > button"
    click_if_visible(page, first_login_btn) or click_if_visible(page, 'button:has-text("下一步")') or click_if_visible(
        page, 'button:has-text("登录")'
    )
    time.sleep(1.0)

    password_xpath = "/html/body/div[1]/div/div/div/div/div/div[2]/div[2]/form/div[2]/div/div[1]/div[1]/input"
    pwd_selectors = [f"xpath={password_xpath}", 'input[type="password"]', 'input[placeholder*="密码"]']
    pwd_filled = False
    for selector in pwd_selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=min(12_000, config.selector_timeout_ms)):
                locator.fill(password)
                pwd_filled = True
                break
        except Exception:
            continue
    if not pwd_filled:
        raise RuntimeError("找不到密码输入框")

    second_login_btn = "#__layout > div > div > div > div > div:nth-child(2) > div:nth-child(2) > form > button"
    click_if_visible(page, second_login_btn) or click_if_visible(page, 'button:has-text("登录")') or click_if_visible(
        page, 'form button[type="submit"]'
    )
    solve_slider_with_bezier(page, config, label)

    try:
        page.wait_for_load_state("networkidle", timeout=config.networkidle_timeout_ms)
    except TimeoutError:
        pass
    log(f"{label}登录步骤完成")


def goto_activity(page: Page, config: Config) -> None:
    log("打开抽奖活动页")
    page.goto(config.activity_url, wait_until="domcontentloaded", referer=config.referer, timeout=config.navigation_timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=config.networkidle_timeout_ms)
    except TimeoutError:
        pass
    random_sleep(1.8, 3.2, "活动页加载后模拟停留")


def normalize_click_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def locator_is_disabled(locator: Any) -> bool:
    try:
        return bool(
            locator.evaluate(
                """
                (node) => {
                  const disabled = node.disabled || node.getAttribute('disabled') !== null;
                  const cls = String(node.className || '').toLowerCase();
                  const aria = node.getAttribute('aria-disabled');
                  return disabled || aria === 'true' || /disabled|disable|plain/.test(cls);
                }
                """
            )
        )
    except Exception:
        return False


def click_exact_locator(
    locator: Any,
    description: str,
    timeout: int = 5_000,
    mark_clicked: bool = False,
    fallback_text: str = "",
) -> bool:
    try:
        count = min(locator.count(), 20)
    except Exception:
        count = 0

    for index in range(count):
        item = locator.nth(index)
        try:
            if not item.is_visible(timeout=1_000):
                continue
            if locator_is_disabled(item):
                continue
            text = ""
            try:
                text = normalize_click_text(item.inner_text(timeout=500))
            except Exception:
                pass
            item.scroll_into_view_if_needed(timeout=timeout)
            item.click(timeout=timeout)
            if mark_clicked:
                try:
                    item.evaluate("(node) => node.setAttribute('data-h4-clicked', '1')")
                except Exception:
                    pass
            if text or fallback_text:
                log(f"点击{description}: {text or fallback_text}")
            else:
                log(f"点击{description}")
            return True
        except Exception:
            continue
    return False


def read_lucky_count_from_page(page: Page) -> int | None:
    try:
        text = page.locator(".lucky .count").first.inner_text(timeout=1_500)
    except Exception:
        return None
    match = re.search(r"(\d+)", text or "")
    return int(match.group(1)) if match else None


def read_exchange_usage_from_page(page: Page) -> tuple[int, int] | None:
    try:
        box_text = page.locator(".lottery-chance .box").filter(has_text="金豆兑换抽奖机会").first.inner_text(timeout=2_000)
    except Exception:
        return None
    match = re.search(r"\((\d+)\s*/\s*(\d+)\)", box_text or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def read_registered_signup_count(page: Page) -> int:
    try:
        return page.locator(".registered").filter(has_text=re.compile(r"^\s*报名成功\s*$")).count()
    except Exception:
        return 0


def click_signup_button(page: Page) -> bool:
    locator = page.locator('.submit-btn:not([data-h4-clicked="1"])').filter(has_text=re.compile(r"^\s*立即报名\s*$"))
    return click_exact_locator(locator, "报名按钮", mark_clicked=True)


def click_exchange_button(page: Page) -> bool:
    locator = page.locator(".lottery-chance .box").filter(has_text="金豆兑换抽奖机会").locator(".submit-btn")
    return click_exact_locator(locator, "5金豆兑换按钮")


def click_exchange_confirm_button(page: Page) -> bool:
    selectors = [
        ".base-modal .base-modal__confirm",
        ".base-modal__confirm",
        ".uni-modal__btn_primary",
        "uni-button:has-text('确定')",
        "button:has-text('确定')",
        "uni-button:has-text('确认')",
        "button:has-text('确认')",
        "[role='button']:has-text('确定')",
        "[role='button']:has-text('确认')",
    ]
    try:
        page.wait_for_selector(".base-modal__confirm, .uni-modal__btn_primary", state="visible", timeout=8_000)
    except TimeoutError:
        try:
            page.wait_for_selector("text=/确认|确定/", state="visible", timeout=4_000)
        except TimeoutError:
            log("兑换后未出现确认弹窗，可能页面已直接兑换或弹窗结构变化")
            return False

    for selector in selectors:
        try:
            locator = page.locator(selector)
            if click_exact_locator(locator, "兑换确认按钮", timeout=6_000):
                return True
        except Exception:
            continue
    log("检测到兑换确认弹窗，但没有点到确认按钮")
    return False


def click_draw_button(page: Page) -> bool:
    locator = page.locator(".lucky .lottery-grid .start-btn, .lottery-grid .start-btn")
    return click_exact_locator(locator, "九宫格开始抽奖按钮(.start-btn)", fallback_text=".start-btn")


def click_continue_draw_button(page: Page) -> bool:
    selectors = [
        ".lottery-result .redraw",
        ".lottery-result uni-button.redraw",
        ".base-popup .redraw",
        "uni-button:has-text('继续抽奖')",
        "button:has-text('继续抽奖')",
        "uni-button:has-text('再抽一次')",
        "button:has-text('再抽一次')",
        "[role='button']:has-text('继续抽奖')",
        "[role='button']:has-text('再抽一次')",
    ]
    for selector in selectors:
        try:
            if click_exact_locator(page.locator(selector), "继续抽奖按钮", timeout=6_000):
                return True
        except Exception:
            continue
    return False


def signup_activities(page: Page, config: Config) -> int:
    log(f"开始按源码结构报名，目标 {config.signup_target} 个活动")
    clicked = 0
    time.sleep(0.8)

    registered_count = read_registered_signup_count(page)
    if registered_count >= config.signup_target:
        log(f"页面显示报名成功 {registered_count}/{config.signup_target}，跳过报名阶段")
        return 0

    for _ in range(config.signup_target):
        registered_count = read_registered_signup_count(page)
        if registered_count + clicked >= config.signup_target:
            break
        if click_signup_button(page):
            clicked += 1
            random_sleep(3.0, 5.0, "报名后随机停顿")
            try:
                page.wait_for_load_state("networkidle", timeout=min(20_000, config.networkidle_timeout_ms))
            except TimeoutError:
                pass
            continue
        break

    registered_count = read_registered_signup_count(page)
    if clicked == 0:
        if registered_count:
            log(f"未找到可点击的立即报名按钮，页面显示报名成功 {registered_count}/{config.signup_target}")
        else:
            log("未找到可点击的立即报名按钮，可能这些活动已报名或当前页面不展示报名入口")
    log(f"报名阶段完成，本轮实际点击 {clicked} 次，页面显示报名成功 {registered_count}/{config.signup_target}")
    return clicked


def exchange_chances(page: Page, config: Config, monitor: LotteryMonitor) -> int:
    log(f"开始按源码结构兑换抽奖机会，目标最多 {config.exchange_target} 次")
    clicked = 0
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.8)

    for _ in range(config.exchange_target):
        usage = read_exchange_usage_from_page(page)
        if usage:
            used, maximum = usage
            log(f"页面兑换次数: {used}/{maximum}")
            if maximum > 0 and used >= maximum:
                log("页面显示兑换次数已满，停止兑换")
                break

        lucky_count = read_lucky_count_from_page(page)
        if lucky_count is not None and lucky_count >= config.draw_target:
            log(f"页面显示抽奖机会已到 {lucky_count} 次，停止兑换")
            break

        random_sleep(3.0, 5.0, "兑换前随机停顿")
        if not click_exchange_button(page):
            log("未找到可点击的 5金豆兑换按钮，停止兑换")
            break
        time.sleep(random.uniform(0.4, 0.9))
        if not click_exchange_confirm_button(page):
            log("未完成兑换确认，停止兑换")
            break
        clicked += 1
        try:
            page.wait_for_load_state("networkidle", timeout=min(25_000, config.networkidle_timeout_ms))
        except TimeoutError:
            pass
        time.sleep(random.uniform(1.2, 2.0))

    log(f"兑换阶段完成，本轮实际点击 {clicked} 次")
    return clicked


def read_prize_from_result_popup(page: Page, timeout_seconds: float = 15.0) -> str:
    deadline = time.time() + timeout_seconds
    title_locator = page.locator(".title")
    prize_locator = page.locator(".lottery-result .prize__name, .base-popup .prize__name, .prize__name")

    while time.time() < deadline:
        try:
            scoped_title = page.evaluate(
                """
                () => {
                  const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                      && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
                  };
                  const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const bad = /(抽奖机会|我的抽奖机会|立即抽奖|开始抽奖|去抽奖|再抽一次|报名|兑换|活动规则|订单统计)/;
                  const valid = (value) => {
                    const text = clean(value);
                    return text && text.length <= 60 && !bad.test(text.replace(/\\s+/g, ''));
                  };
                  const titles = Array.from(document.querySelectorAll('.title'))
                    .filter((el) => visible(el) && /(恭喜[您你]?|抽中)/.test(clean(el.innerText || el.textContent)));
                  for (const title of titles) {
                    let node = title;
                    for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
                      for (const prize of Array.from(node.querySelectorAll('.prize__name'))) {
                        const text = clean(prize.innerText || prize.textContent);
                        if (visible(prize) && valid(text)) return text;
                      }
                    }
                  }
                  return '';
                }
                """
            )
            if is_valid_prize_title(scoped_title):
                return scoped_title
        except Exception:
            pass

        title_ok = False
        try:
            title_count = min(title_locator.count(), 10)
        except Exception:
            title_count = 0

        for index in range(title_count):
            item = title_locator.nth(index)
            try:
                if not item.is_visible(timeout=250):
                    continue
                title_text = normalize_click_text(item.inner_text(timeout=250))
            except Exception:
                continue
            if RESULT_TITLE_RE.search(title_text):
                title_ok = True
                break

        if title_ok:
            try:
                prize_count = min(prize_locator.count(), 10)
            except Exception:
                prize_count = 0
            for index in range(prize_count):
                item = prize_locator.nth(index)
                try:
                    if not item.is_visible(timeout=250):
                        continue
                    prize_title = normalize_click_text(item.inner_text(timeout=250))
                except Exception:
                    continue
                if is_valid_prize_title(prize_title):
                    return prize_title

        time.sleep(0.25)
    return ""


def draw_lottery(page: Page, config: Config, monitor: LotteryMonitor) -> int:
    log(f"开始抽奖，目标最多 {config.draw_target} 次，每次间隔 7-10s")
    draw_count = 0
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(0.8)

    while draw_count < config.draw_target:
        page_lucky_count = read_lucky_count_from_page(page)
        if page_lucky_count == 0:
            log("页面显示抽奖机会为 0，停止抽奖")
            break
        if page_lucky_count is not None:
            log(f"抽奖前页面剩余机会: {page_lucky_count}")

        random_sleep(7.0, 10.0, "抽奖前随机停顿")
        if not click_draw_button(page):
            log("没有找到源码里的九宫格开始按钮 .start-btn，停止抽奖")
            break

        draw_count += 1
        title = read_prize_from_result_popup(page, timeout_seconds=config.result_popup_timeout_seconds)
        if not title:
            log(f"{config.result_popup_timeout_seconds:.0f}s 内未从页面弹窗读取到 .prize__name，停止后续抽奖以避免漏记")
            break

        monitor.records.append(LotteryRecord(prize_title=title, source_url="page_dom"))
        log(f"页面弹窗识别中奖结果: {title}")

        after_count = read_lucky_count_from_page(page)
        if after_count is not None:
            log(f"抽奖后页面剩余机会: {after_count}")
        if after_count == 0:
            break
        if draw_count < config.draw_target:
            if not click_continue_draw_button(page):
                log("未找到继续抽奖按钮，停止抽奖")
                break
            time.sleep(random.uniform(0.8, 1.4))

    click_if_visible(page, 'text=/知道了|确定|关闭|开心收下|收下|我知道了/')
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    log(f"抽奖阶段完成，本轮实际点击 {draw_count} 次")
    return draw_count


def fetch_invoice_money(page: Page) -> float:
    token_keys = [
        env_first("TOKEN_KEY"),
        env_first("HEADER_ACCESS_TOKEN"),
        "X-JLC-AccessToken",
        "x-jlc-accesstoken",
        "accessToken",
        "token",
        "jlc-token",
    ]
    token_keys.extend([item.strip() for item in env_first("TOKEN_ALTERNATIVE_KEYS").split(",") if item.strip()])
    payload = {
        "path": INVOICE_INFO_PATH,
        "tokenKeys": [item for item in token_keys if item],
        "clientType": env_first("JLC_CLIENT_TYPE", "CLIENT_TYPE", default="MP-WEIXIN"),
        "mpVersion": env_first("JLC_MP_VERSION", "MP_VERSION", default="1.112.0"),
        "mpEnv": env_first("JLC_MP_ENV", "MP_ENV", default="release"),
        "mpAppid": env_first("JLC_MP_APPID", "MP_APPID", default=MINIPROGRAM_APPID),
        "secretKey": env_first("JLC_SECRET_KEY_VALUE", "SECRET_KEY_VALUE", "HEADER_SECRET_KEY_VALUE", default=DEFAULT_MP_SECRET_KEY_VALUE),
        "clientTypeHeader": env_first("HEADER_CLIENT_TYPE", default="x-jlc-clienttype"),
        "tokenHeader": env_first("HEADER_ACCESS_TOKEN", default="x-jlc-accesstoken"),
        "secretKeyHeader": env_first("HEADER_SECRET_KEY", default="secretkey"),
    }
    try:
        result = page.evaluate(
            """
            async (cfg) => {
              const cleanKey = (key) => String(key || '').trim();
              const getStorage = (key) => {
                key = cleanKey(key);
                if (!key) return "";
                try {
                  return window.localStorage.getItem(key)
                    || window.localStorage.getItem(String(key).toLowerCase())
                    || window.sessionStorage.getItem(key)
                    || window.sessionStorage.getItem(String(key).toLowerCase());
                } catch (_) {
                  return "";
                }
              };
              const getCookie = (key) => {
                key = cleanKey(key);
                if (!key) return "";
                try {
                  const rows = String(document.cookie || '').split(';');
                  for (const row of rows) {
                    const index = row.indexOf('=');
                    const name = row.slice(0, index).trim();
                    if (name === key || name.toLowerCase() === key.toLowerCase()) {
                      return decodeURIComponent(row.slice(index + 1));
                    }
                  }
                } catch (_) {}
                return "";
              };
              let token = "";
              for (const key of cfg.tokenKeys || []) {
                token = getStorage(key) || getCookie(key);
                if (token) break;
              }
              const hasInvoiceMoney = (value) => {
                if (Array.isArray(value)) return value.some(hasInvoiceMoney);
                if (value && typeof value === 'object') {
                  if (Object.prototype.hasOwnProperty.call(value, 'invoiceMoney')) return true;
                  return Object.values(value).some(hasInvoiceMoney);
                }
                return false;
              };
              const isEmptyData = (value) => value && typeof value === 'object'
                && Array.isArray(value.data) && value.data.length === 0;
              const buildHeaders = (withJson) => {
                const headers = {
                  "accept": "application/json, text/plain, */*",
                  "x-jlc-mp-version": cfg.mpVersion || "1.112.0",
                  "x-jlc-mp-env": cfg.mpEnv || "release",
                  "x-jlc-mp-appid": cfg.mpAppid || "wx6c7b851c877dba42",
                };
                if (withJson) headers["content-type"] = "application/json";
                headers[cfg.clientTypeHeader || "x-jlc-clienttype"] = cfg.clientType || "MP-WEIXIN";
                headers["x-jlc-clienttype"] = cfg.clientType || "MP-WEIXIN";
                if (cfg.secretKey) {
                  headers[cfg.secretKeyHeader || "secretkey"] = cfg.secretKey;
                  headers["secretkey"] = cfg.secretKey;
                }
                if (token) {
                  headers[cfg.tokenHeader || "x-jlc-accesstoken"] = token;
                  headers["x-jlc-accesstoken"] = token;
                }
                return headers;
              };
              const request = async (method) => {
                const response = await fetch(cfg.path, {
                  method,
                  credentials: "include",
                  headers: buildHeaders(method === "POST"),
                  body: method === "POST" ? "{}" : undefined,
                });
                const text = await response.text();
                let data = null;
                try { data = JSON.parse(text); } catch (_) { data = text; }
                return { ok: response.ok, status: response.status, method, data };
              };
              const postResult = await request("POST");
              if (hasInvoiceMoney(postResult.data) || isEmptyData(postResult.data)) return postResult;
              return await request("GET");
            }
            """,
            payload,
        )
    except Exception as exc:
        log(f"消费金额获取异常，按 0 写入: {exc}")
        return 0.0

    data = result.get("data") if isinstance(result, dict) else result
    money = find_invoice_money_value(data)
    if money is None:
        log(f"消费金额接口未找到 invoiceMoney，按 0 写入")
        return 0.0
    log(f"消费金额: {money}")
    return money


def build_result(
    username: str,
    account_index: int,
    monitor: LotteryMonitor,
    signup_count: int,
    exchange_count: int,
    draw_count: int,
    invoice_money: float = 0.0,
    error: str = "",
) -> dict[str, Any]:
    records = [item.to_json() for item in monitor.records]
    success = not error and bool(records)
    detail_reason = (
        f"浏览器自动流程完成：报名点击 {signup_count} 次，兑换点击 {exchange_count} 次，抽奖点击 {draw_count} 次，中奖记录 {len(records)} 条"
        if not error
        else error
    )

    group_number = env_int("GROUP_NUMBER", 1)
    group_name = env_first("GROUP_NAME", default="h4-browser-lottery")
    retry_count = env_int("RETRY_COUNT", 0)

    return {
        "account_index": account_index,
        "execution_order": env_int("EXECUTION_ORDER", account_index),
        "group_name": group_name,
        "group_number": group_number,
        "group_position": f"{group_name}账号{account_index}" if group_name else f"账号{account_index}",
        "username": username,
        "masked_username": mask_account(username),
        "sign_success": success,
        "sign_status": "success" if success else "failed",
        "detail_reason": detail_reason,
        "initial_points": 0,
        "final_points": 0,
        "points_reward": 0,
        "invoice_money": invoice_money,
        "consumption_amount": invoice_money,
        "has_reward": bool(records),
        "password_error": False,
        "risk_controlled": False,
        "retry_count": retry_count,
        "is_final_retry": truthy(os.getenv("IS_FINAL_RETRY"), default=False),
        "sign_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sign_ip": get_public_ip(),
        "activity_records": {
            "lottery": records,
            "signup_count": signup_count,
            "exchange_count": exchange_count,
            "draw_count": draw_count,
            "browser_only": True,
        },
    }


def resolve_runtime_path(path: Path) -> Path:
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def default_result_path(config: Config) -> Path:
    configured = env_first("RESULT_JSON_PATH")
    if configured:
        return resolve_runtime_path(Path(configured))
    if config.cleanup_local_files:
        return Path(tempfile.mkdtemp(prefix="h4-lottery-")) / "result.json"
    return RESULT_DIR / "browser-lottery-result.json"


def write_result(result: dict[str, Any], config: Config) -> Path:
    path = default_result_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "browser-only-lottery",
        "batch_name": env_first("GROUP_NAME", default="h4-browser-lottery"),
        "group_name": env_first("GROUP_NAME", default="h4-browser-lottery"),
        "group_number": env_int("GROUP_NUMBER", 1),
        "total_accounts": 1,
        "results": [result],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入结果 JSON: {path}")
    return path


def maybe_generate_report(config: Config, result_path: Path) -> list[Path]:
    if not config.generate_report:
        return []
    report_py = ROOT / "h3" / "report.py"
    if not report_py.exists():
        log("未找到 h3/report.py，跳过 xlsx 汇总")
        return []

    env = os.environ.copy()
    generated_paths: list[Path] = []
    if config.cleanup_local_files:
        output_path = Path(tempfile.mkdtemp(prefix="h4-report-")) / "h4-browser-lottery-summary.xlsx"
        env["OUTPUT_XLSX_PATH"] = str(output_path)
        generated_paths.append(output_path)
    elif env_first("OUTPUT_XLSX_PATH"):
        output_path = Path(env_first("OUTPUT_XLSX_PATH"))
        generated_paths.append(output_path if output_path.is_absolute() else ROOT / output_path)

    log("开始调用 h3/report.py 生成 xlsx 汇总")
    subprocess.run([sys.executable, str(report_py), str(result_path.parent)], cwd=str(ROOT), env=env, check=False)
    return generated_paths


def cleanup_local_outputs(config: Config, paths: list[Path]) -> None:
    if not config.cleanup_local_files:
        return
    for raw_path in paths:
        path = raw_path if raw_path.is_absolute() else resolve_runtime_path(raw_path)
        try:
            if path.exists() and path.is_file():
                path.unlink()
                log(f"已清理本地临时文件: {path}")
        except Exception as exc:
            log(f"清理本地临时文件失败: {path} ({exc})")

        parent = path.parent
        try:
            if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            pass


def run() -> int:
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / "h4" / ".env", override=not truthy(os.getenv("GITHUB_ACTIONS"), default=False))

    username, password, account_index = load_account_from_args()
    config = load_config()
    monitor = LotteryMonitor()
    signup_count = 0
    exchange_count = 0
    draw_count = 0
    invoice_money = 0.0
    error = ""

    with sync_playwright() as p:
        browser, context, page = new_browser(p, config)
        try:
            login(page, config, username, password, account_index)
            goto_activity(page, config)
            signup_count = signup_activities(page, config)
            exchange_count = exchange_chances(page, config, monitor)
            draw_count = draw_lottery(page, config, monitor)
            invoice_money = fetch_invoice_money(page)
        except Exception as exc:
            error = f"浏览器抽奖流程异常: {exc}"
            log(error)
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

    result = build_result(username, account_index, monitor, signup_count, exchange_count, draw_count, invoice_money, error)
    result_path = write_result(result, config)
    report_paths = maybe_generate_report(config, result_path)
    cleanup_local_outputs(config, [result_path] + report_paths)
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(run())
