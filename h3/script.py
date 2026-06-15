import os
import sys
import time
import random
import json
import requests
try:
    import httpx
except Exception:
    httpx = None
import smtplib
import threading
import re
from email.message import EmailMessage
from datetime import datetime, timedelta
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError
from fake_useragent import UserAgent
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

def env_first(*names, default=""):
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default

# 统一东八区时间
os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    time.tzset()
except Exception:
    pass

# ==============================================================================
# 从环境变量读取所有配置（必须设置）
# ==============================================================================
BASE_URL = os.getenv('BASE_URL')
PASSPORT_URL = os.getenv('PASSPORT_URL')
JLC_CLIENT_TYPE = env_first("JLC_CLIENT_TYPE", "CLIENT_TYPE", default="MP-WEIXIN")
JLC_MP_APPID = env_first("JLC_MP_APPID", "MP_APPID", default="wx6c7b851c877dba42")
JLC_MP_PAGE_VERSION = env_first("JLC_MP_PAGE_VERSION", "MP_PAGE_VERSION", default="140")
DEFAULT_MP_REFERER = f"https://servicewechat.com/{JLC_MP_APPID}/{JLC_MP_PAGE_VERSION}/page-frame.html"
DEFAULT_MP_SECRET_KEY_VALUE = "62333335373634382d613039362d346439642d383935652d626666396162323664656136"
_JLC_REFERER_OVERRIDE = env_first("JLC_REFERER")
_RAW_REFERER = env_first("REFERER")
if _JLC_REFERER_OVERRIDE:
    REFERER = _JLC_REFERER_OVERRIDE
elif JLC_CLIENT_TYPE.upper() == "MP-WEIXIN" and (not _RAW_REFERER or "pages-promo/brand-campaign" in _RAW_REFERER):
    REFERER = DEFAULT_MP_REFERER
else:
    REFERER = _RAW_REFERER or DEFAULT_MP_REFERER
HEADER_ACCESS_TOKEN_FALLBACKS = [
    k.strip().lower()
    for k in os.getenv('HEADER_ACCESS_TOKEN_FALLBACKS', '').split(',')
    if k.strip()
]
SLIDER_ID = os.getenv('SLIDER_ID')
WRAPPER_ID = os.getenv('WRAPPER_ID')

HEADER_CLIENT_TYPE = os.getenv('HEADER_CLIENT_TYPE')
HEADER_ACCESS_TOKEN = os.getenv('HEADER_ACCESS_TOKEN')
HEADER_SECRET_KEY = os.getenv('HEADER_SECRET_KEY', 'secretkey')

TOKEN_KEY = os.getenv('TOKEN_KEY')
TOKEN_ALTERNATIVE_KEYS = [k.strip() for k in os.getenv('TOKEN_ALTERNATIVE_KEYS', '').split(',') if k.strip()]

JLC_SECRET_KEY_VALUE = env_first(
    "JLC_SECRET_KEY_VALUE",
    "SECRET_KEY_VALUE",
    "HEADER_SECRET_KEY_VALUE",
    default=DEFAULT_MP_SECRET_KEY_VALUE if JLC_CLIENT_TYPE.upper() == "MP-WEIXIN" else "",
)
JLC_MP_VERSION = env_first("JLC_MP_VERSION", "MP_VERSION", default="1.112.0")
JLC_MP_ENV = env_first("JLC_MP_ENV", "MP_ENV", default="release")
JLC_USER_AGENT = env_first("JLC_USER_AGENT", "USER_AGENT")
JLC_ACCEPT_LANGUAGE = env_first("JLC_ACCEPT_LANGUAGE", "ACCEPT_LANGUAGE", default="zh-CN,zh;q=0.9")
JLC_USE_HTTP2 = env_first("JLC_USE_HTTP2", "USE_HTTP2", default="true")
MINI_PROGRAM_UA = (
    "Mozilla/5.0 (Linux; Android 13; 23078RKD5C Build/TP1A.220624.014; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/146.0.7680.178 Mobile Safari/537.36 XWEB/1460205 "
    "MMWEBSDK/20260202 MMWEBID/5956 MicroMessenger/8.0.71.3080(0x28004750) "
    "WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android"
)

ACTIVE_STATUS_PATH = "/api/sms/front/internal-message/active-status"
LOGIN_API_PATH = "/api/cas/login/mobile/with-password"
PASSWORD_ERROR_HINTS = ["账号或密码不正确", "请重新输入", "密码错误"]

# 首页元素（用于判断是否进入首页）
HOME_SELECTOR = 'div.uni-tabbar__label:has-text("首页")'

# 抽奖相关接口
CUSTOMER_INTEGRAL_PATH = "/api/activity/front/getCustomerIntegral"
LOTTERY_WINS_PATH = "/api/cgi/operationService/front/lottery/queryWins"
VOUCHER_CHANGE_RECORD_PATH = "/api/activity/front/selectIntegralVoucherChangeRecord"
BRAND_ACTIVITY_CONFIG_PATH = "/api/activity/brand/activity/ns/selectActivityConfig"
ACTIVITY_SIGNUP_PATH = "/api/activity/brand/activity/activitySignUp"
ACTIVITY_SIGNUP_INFO_PATH = "/api/activity/integral/activity/selectCustomerActivitySignUpInfo"
VOUCHER_LOTTERY_DETAIL_PATH = "/api/activity/brand/activity/ns/getVoucherLotteryDetail"
EXCHANGE_LOTTERY_CHANCE_PATH = "/api/activity/brand/activity/exchangeLotteryChance"
LOTTERY_KEY_COUNT_PATH = "/api/cgi/operationService/front/lottery/getLuckyKeyCount"
LOTTERY_TURN_PATH = "/api/cgi/operationService/front/lottery/turn"
INVOICE_INFO_PATH = "/api/integrated/vatInvoiceInfo/selectInvoiceInfoDetails"
DEFAULT_INVOICE_SECRET_KEY_VALUE = "64656661756c744b65794964"
DEFAULT_INVOICE_REFERER_PATH = "/pages-common/invoice/index"
DEFAULT_LOTTERY_ACTIVITY_CODE = "LAKU"
LOTTERY_SIGNUP_BATCHES = ([6], [7, 8], [9], [10])

# 检查必要变量
required_vars = {
    "BASE_URL": BASE_URL,
    "PASSPORT_URL": PASSPORT_URL,
    "REFERER": REFERER,
    "SLIDER_ID": SLIDER_ID,
    "WRAPPER_ID": WRAPPER_ID,
    "HEADER_CLIENT_TYPE": HEADER_CLIENT_TYPE,
    "HEADER_ACCESS_TOKEN": HEADER_ACCESS_TOKEN,
    "TOKEN_KEY": TOKEN_KEY,
}
missing_vars = [name for name, value in required_vars.items() if not str(value or "").strip()]
if missing_vars:
    print("❌ 缺少必要环境变量 / GitHub Secret：")
    for name in missing_vars:
        print(f"  - {name}")
    print("请在 GitHub Settings → Secrets and variables → Actions 中检查同名 Secret，或在本地 .env 中填写。")
    sys.exit(1)

parsed_base = urlparse(BASE_URL)
HOST = parsed_base.netloc
URL_PATTERN = f"**/{HOST}/**"

# ==============================================================================
# 小工具函数
# ==============================================================================
_UUID_RE = re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')
_PUBLIC_IP_CACHE = {"loaded": False, "value": ""}

def truthy(v) -> bool:
    if v is True:
        return True
    if v is False or v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on")

def is_mp_weixin_client() -> bool:
    return str(JLC_CLIENT_TYPE or "").strip().upper() == "MP-WEIXIN"

def safe_int(v, default=0) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default

def safe_float(v, default=0.0) -> float:
    try:
        return float(str(v).strip())
    except Exception:
        return default

def find_invoice_money_value(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) == "invoiceMoney":
                return safe_float(item, 0.0)
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


def pick_money_value(*values) -> float:
    fallback = None
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text == "":
            continue
        money = safe_float(text, 0.0)
        if money != 0:
            return money
        if fallback is None:
            fallback = 0.0
    return fallback if fallback is not None else 0.0


def record_invoice_money(record) -> float:
    if not isinstance(record, dict):
        return 0.0
    return pick_money_value(
        record.get("invoice_money"),
        record.get("consumption_amount"),
        record.get("invoiceMoney"),
    )

def truncate_text(s: str, limit: int = 1200) -> str:
    if s is None:
        return ""
    s = str(s)
    if len(s) <= limit:
        return s
    return s[:limit] + f"...(truncated, len={len(s)})"

def redact_sensitive(s: str) -> str:
    if not s:
        return ""
    return _UUID_RE.sub(lambda m: m.group(0)[:8] + "-****-****-****-" + m.group(0)[-12:], s)

def extract_message(value):
    if isinstance(value, dict):
        for key in ("message", "msg", "errorMessage", "error", "detail"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for nested in value.values():
            candidate = extract_message(nested)
            if candidate:
                return candidate
        return ""
    if isinstance(value, list):
        for item in value:
            candidate = extract_message(item)
            if candidate:
                return candidate
        return ""
    if isinstance(value, str):
        return value.strip()
    return ""

def build_detail_reason(value, default=""):
    msg = extract_message(value)
    if msg:
        return redact_sensitive(truncate_text(msg, 800))
    if value is None:
        return default
    try:
        dumped = json.dumps(value, ensure_ascii=False)
    except Exception:
        dumped = str(value)
    dumped = redact_sensitive(truncate_text(dumped, 800))
    return dumped or default

def is_risk_control_response(data) -> bool:
    reason = build_detail_reason(data)
    return (
        "抽奖失败，疑似触发活动限制" in reason
        or "风险" in reason
        or "风控" in reason
    )

def current_date_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def normalize_task_start_date(value="") -> str:
    raw = str(value or os.getenv("SIGN_TASK_START_DATE") or "").strip()
    if raw:
        match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
        if match:
            return match.group(0)
    return current_date_text()

def date_part(value="") -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else ""

def has_next_day_success(task_start_date: str, sign_time: str) -> bool:
    start = date_part(task_start_date)
    signed = date_part(sign_time)
    return bool(start and signed and signed > start)

def extract_data_list(payload) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "list", "records", "rows", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_data_list(value)
            if nested:
                return nested
    return []

def parse_datetime_value(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    return None

def build_expiry_lookup(change_records: list[dict]) -> dict[str, str]:
    lookup = {}
    for item in change_records:
        if not isinstance(item, dict):
            continue
        goods_name = str(item.get("goodsName") or item.get("skuTitle") or item.get("prizeTitle") or "").strip()
        created_at = parse_datetime_value(item.get("createTime") or item.get("createdAt") or item.get("createDate"))
        if not goods_name or not created_at:
            continue
        lookup[goods_name] = (created_at + timedelta(days=7)).strftime("%Y-%m-%d")
    return lookup

def find_expiry_date(title: str, expiry_lookup: dict[str, str]) -> str:
    title = str(title or "").strip()
    if not title:
        return ""
    for goods_name, expiry_date in expiry_lookup.items():
        if goods_name and (goods_name == title or goods_name in title or title in goods_name):
            return expiry_date
    return ""

def apply_expiry_dates(records: list[dict], expiry_lookup: dict[str, str]):
    for item in records:
        if truthy(item.get("claimed")):
            continue
        expiry_date = find_expiry_date(item.get("title"), expiry_lookup)
        if not expiry_date:
            continue
        item["expiry_date"] = expiry_date
        item["status_text"] = f"未领取 {expiry_date}"

def make_empty_extra_records() -> dict:
    return {"lottery": []}

# ==============================================================================
# 移动端 UA 池（至少数千条）
# ==============================================================================
MOBILE_DEVICES = [
    "SM-G970F", "SM-G973F", "SM-G975F", "SM-G980F", "SM-G985F",
    "SM-G991B", "SM-G996B", "SM-S901B", "SM-S906B", "SM-S911B",
    "SM-S916B", "SM-S918B", "SM-A505F", "SM-A515F", "SM-A525F",
    "SM-A535F", "SM-A546B", "SM-A715F", "SM-A725F", "SM-A736B",
    "SM-F711B", "SM-F721B", "SM-F936B", "SM-F946B",
    "Pixel 4", "Pixel 4a", "Pixel 5", "Pixel 5a", "Pixel 6",
    "Pixel 6a", "Pixel 6 Pro", "Pixel 7", "Pixel 7a", "Pixel 7 Pro",
    "Pixel 8", "Pixel 8 Pro",
    "MI 9", "MI 10", "MI 11", "MI 12", "Mi 11T",
    "Redmi Note 10", "Redmi Note 11", "Redmi Note 12",
    "POCO F3", "POCO F4",
    "ONEPLUS A6013", "ONEPLUS A5000", "ONEPLUS A6003", "ONEPLUS A3003"
]

ANDROID_VERSIONS = ["8.0", "8.1", "9", "10", "11", "12", "13", "14"]

CHROME_VERSIONS = [
    "118.0.5993.80",
    "119.0.6045.134",
    "120.0.6099.224",
    "121.0.6167.164",
    "122.0.6261.105",
    "123.0.6312.120",
    "124.0.6367.207",
    "125.0.6422.147",
    "126.0.6478.122",
    "127.0.6533.103"
]

_FAKE_UA = None
try:
    _FAKE_UA = UserAgent(use_cache_server=False, verify_ssl=False)
except Exception:
    _FAKE_UA = None

def build_mobile_ua_pool():
    pool = []
    for device in MOBILE_DEVICES:
        for av in ANDROID_VERSIONS:
            for cv in CHROME_VERSIONS:
                ua = f"Mozilla/5.0 (Linux; Android {av}; {device}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{cv} Mobile Safari/537.36"
                pool.append(ua)

    if _FAKE_UA:
        seen = set(pool)
        for _ in range(200):
            try:
                candidate = _FAKE_UA.random
                if ("Mobile" in candidate or "Android" in candidate or "iPhone" in candidate) and candidate not in seen:
                    pool.append(candidate)
                    seen.add(candidate)
            except Exception:
                break

    random.shuffle(pool)
    return pool

MOBILE_UA_POOL = build_mobile_ua_pool()

def get_random_mobile_ua():
    if MOBILE_UA_POOL:
        return random.choice(MOBILE_UA_POOL)
    if _FAKE_UA:
        try:
            return _FAKE_UA.random
        except Exception:
            pass
    return "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.224 Mobile Safari/537.36"

def get_runtime_user_agent():
    if JLC_USER_AGENT:
        return JLC_USER_AGENT
    if is_mp_weixin_client():
        return MINI_PROGRAM_UA
    return get_random_mobile_ua()

# --- 全局日志变量 ---
in_summary = False
summary_logs = []

def log(msg):
    full_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(full_msg, flush=True)
    if in_summary:
        summary_logs.append(msg)

def mask_account(account):
    if account is None:
        return ""
    s = str(account)
    if len(s) <= 4:
        return "*" * len(s)
    return s[:-4] + "****"

def current_time_text() -> str:
    return datetime.now().strftime("%H:%M:%S")

def random_delay(min_seconds: float, max_seconds: float, label: str = ""):
    low = max(0.0, float(min_seconds))
    high = max(low, float(max_seconds))
    seconds = random.uniform(low, high)
    if label:
        log(f"{label}，等待 {seconds:.1f}s")
    time.sleep(seconds)

def env_delay_range(prefix: str, default_min: float, default_max: float) -> tuple[float, float]:
    low = safe_float(os.getenv(f"{prefix}_MIN"), default_min)
    high = safe_float(os.getenv(f"{prefix}_MAX"), default_max)
    if high < low:
        high = low
    return low, high

def get_public_ip() -> str:
    if _PUBLIC_IP_CACHE["loaded"]:
        return _PUBLIC_IP_CACHE["value"]

    candidates = (
        ("https://api.ipify.org?format=json", "json"),
        ("https://ifconfig.me/ip", "text"),
    )
    ip_value = ""
    for url, response_type in candidates:
        try:
            response = requests.get(url, timeout=8)
            if response.status_code != 200:
                continue
            if response_type == "json":
                ip_value = str((response.json() or {}).get("ip") or "").strip()
            else:
                ip_value = response.text.strip()
            if ip_value:
                break
        except Exception:
            continue

    _PUBLIC_IP_CACHE["loaded"] = True
    _PUBLIC_IP_CACHE["value"] = ip_value
    return ip_value

def finalize_result_metadata(result: dict):
    result["sign_time"] = str(result.get("sign_time") or current_time_text()).strip()
    result["sign_ip"] = str(result.get("sign_ip") or get_public_ip()).strip()
    result["next_day_success"] = False

def masked_label(result):
    if result.get('masked_username'):
        return result['masked_username']
    if result.get('username'):
        return mask_account(result['username'])
    return f"账号序号{result.get('account_index')}"

def with_retry(func, max_retries=5, delay=1):
    def wrapper(*args, **kwargs):
        for _ in range(max_retries):
            try:
                result = func(*args, **kwargs)
                if result is not None:
                    return result
                time.sleep(delay + random.uniform(0, 1))
            except Exception:
                time.sleep(delay + random.uniform(0, 1))
        return None
    return wrapper

def wait_token_from_requests(token_holder, timeout=8):
    start = time.time()
    while time.time() - start < timeout:
        token = token_holder.get('value')
        if token:
            return token
        time.sleep(0.2)
    return None

# ==============================================================================
# 滑块破解脚本（注入式，ID 从环境变量读取）
# ==============================================================================
def solve_slider_with_bezier(page: Page) -> bool:
    try:
        page.locator(f"#{SLIDER_ID}").wait_for(state="visible", timeout=10000)
        log("✅ 检测到滑块，准备注入破解脚本...")
    except Exception:
        log("🟢 未检测到滑块，跳过。")
        return True

    script = f"""
    (async function() {{
        const slider = document.getElementById('{SLIDER_ID}');
        const wrapper = document.getElementById('{WRAPPER_ID}');
        if (!slider || !wrapper) return false;

        wrapper.scrollIntoView({{behavior: 'instant', block: 'center'}});
        await new Promise(r => setTimeout(r, 300));

        function generateHumanPath(x1, y1, x2, y2) {{
            const points = [];
            const cx1 = x1 + (x2 - x1) * 0.3 + (Math.random() - 0.5) * 20;
            const cy1 = y1 + (Math.random() - 0.5) * 50;
            const cx2 = x1 + (x2 - x1) * 0.7 + (Math.random() - 0.5) * 20;
            const cy2 = y1 + (Math.random() - 0.5) * 50;
            const totalDuration = 800 + Math.random() * 700;
            const steps = 60 + Math.floor(Math.random() * 40);
            for (let i = 0; i <= steps; i++) {{
                const t = i / steps;
                const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
                const x = Math.pow(1 - ease, 3) * x1 +
                          3 * Math.pow(1 - ease, 2) * ease * cx1 +
                          3 * (1 - ease) * ease * ease * cx2 +
                          Math.pow(ease, 3) * x2;
                const y = Math.pow(1 - ease, 3) * y1 +
                          3 * Math.pow(1 - ease, 2) * ease * cy1 +
                          3 * (1 - ease) * ease * ease * cy2 +
                          Math.pow(ease, 3) * y2;
                points.push({{
                    x: x + (Math.random() - 0.5) * 2,
                    y: y + (Math.random() - 0.5) * 2,
                    t: Math.floor(totalDuration * t)
                }});
            }}
            return points;
        }}

        function triggerEvent(el, type, x, y) {{
            const mouseEvent = new MouseEvent(type, {{
                bubbles: true, cancelable: true, view: window,
                clientX: x, clientY: y, screenX: x, screenY: y,
                button: 0, buttons: 1
            }});
            el.dispatchEvent(mouseEvent);
            if (type.startsWith('mouse')) {{
                const pointerType = type.replace('mouse', 'pointer');
                const pointerEvent = new PointerEvent(pointerType, {{
                    bubbles: true, cancelable: true, view: window,
                    clientX: x, clientY: y, screenX: x, screenY: y,
                    button: 0, buttons: 1, pointerId: 1,
                    width: 1, height: 1, pressure: 0.5,
                    tiltX: 0, tiltY: 0, pointerType: 'mouse'
                }});
                el.dispatchEvent(pointerEvent);
            }}
        }}

        const sliderRect = slider.getBoundingClientRect();
        const wrapperRect = wrapper.getBoundingClientRect();
        const startX = sliderRect.left + sliderRect.width / 2;
        const startY = sliderRect.top + sliderRect.height / 2;
        const extraDistance = 15;
        const endX = wrapperRect.left + wrapperRect.width - (sliderRect.width / 2) + extraDistance;
        const endY = startY + (Math.random() - 0.5) * 5;

        const path = generateHumanPath(startX, startY, endX, endY);
        triggerEvent(slider, 'mousedown', startX, startY);
        let previousTime = 0;
        for (let point of path) {{
            const waitTime = point.t - previousTime;
            if (waitTime > 0) await new Promise(r => setTimeout(r, waitTime));
            triggerEvent(slider, 'mousemove', point.x, point.y);
            triggerEvent(document, 'mousemove', point.x, point.y);
            previousTime = point.t;
        }}
        await new Promise(r => setTimeout(r, 200 + Math.random() * 100));
        const last = path[path.length - 1];
        triggerEvent(slider, 'mouseup', last.x, last.y);
        triggerEvent(document, 'mouseup', last.x, last.y);
        return true;
    }})();
    """

    try:
        page.evaluate(script)
        log("✅ 滑块脚本执行完成")
    except Exception as e:
        log(f"❌ 滑块脚本异常: {e}")
        return False

    time.sleep(5)
    if page.locator(f"#{SLIDER_ID}").is_visible(timeout=2000):
        log("⚠️ 滑块仍然存在（5s检测）")
        time.sleep(5)
        if page.locator(f"#{SLIDER_ID}").is_visible(timeout=2000):
            log("❌ 滑块10秒后仍存在，进入重试阶段")
            return False
        log("✅ 10秒后滑块已消失，破解成功")
        return True

    log("✅ 滑块已消失，破解成功")
    return True

# ==============================================================================
# 提取 localStorage 中的 AccessToken（键名从环境变量读取）
# ==============================================================================
@with_retry
def extract_token_from_local_storage(page: Page):
    try:
        token = page.evaluate(f"() => window.localStorage.getItem('{TOKEN_KEY}')")
        if token:
            log("✅ 已提取到 token")
            return token
        for key in TOKEN_ALTERNATIVE_KEYS:
            token = page.evaluate(f"() => window.localStorage.getItem('{key}')")
            if token:
                log("✅ 已提取到 token")
                return token
    except Exception as e:
        log(f"❌ 提取 token 失败: {e}")
    return None

# ==============================================================================
# API 客户端（只用 GET；失败重试一次 GET）
# ==============================================================================
class ApiClient:
    def __init__(self, access_token, secretkey, account_index, page: Page, user_agent=None):
        self.base_url = BASE_URL
        self.user_agent = user_agent or get_runtime_user_agent()
        self.client_type = JLC_CLIENT_TYPE
        effective_secretkey = JLC_SECRET_KEY_VALUE or secretkey
        self.headers = {
            'user-agent': self.user_agent,
            HEADER_CLIENT_TYPE: self.client_type,
            'accept': 'application/json, text/plain, */*',
            'accept-encoding': 'gzip, deflate, br',
            'accept-language': JLC_ACCEPT_LANGUAGE,
            'content-type': 'application/json',
            HEADER_ACCESS_TOKEN: access_token,
            'referer': REFERER,
        }
        if effective_secretkey:
            self.headers[HEADER_SECRET_KEY] = effective_secretkey
        if is_mp_weixin_client():
            self.headers.update({
                'charset': 'utf-8',
                'x-jlc-mp-version': JLC_MP_VERSION,
                'x-jlc-mp-env': JLC_MP_ENV,
                'x-jlc-mp-appid': JLC_MP_APPID,
            })
        self.http = None
        if truthy(JLC_USE_HTTP2) and httpx is not None:
            self.http = httpx.Client(http2=True, timeout=12)

        self.account_index = account_index
        self.page = page

        self.initial_points = 0
        self.final_points = 0
        self.points_reward = 0
        self.invoice_money = 0.0
        self.invoice_money_loaded = False

        self.sign_status = "未知"
        self.has_reward = False

        self.today_day = 0
        self.detail_reason = ""
        self.risk_controlled = False
        self.sign_completed_at = ""
        self.activity_records = make_empty_extra_records()
        self.lottery_activity_code = DEFAULT_LOTTERY_ACTIVITY_CODE
        self.draw_results = []

    def close(self):
        if self.http:
            self.http.close()

    def _mark_failure(self, status, raw=None, detail=""):
        reason = detail or build_detail_reason(raw, default=status)
        if is_risk_control_response(raw) or ("风控" in reason) or ("风险" in reason):
            self.sign_status = "抽奖风控"
            self.detail_reason = reason or "抽奖失败，疑似触发活动限制"
            self.risk_controlled = True
            return
        self.sign_status = status
        self.detail_reason = reason or status

    def _refresh_token(self) -> bool:
        try:
            self.page.goto(BASE_URL, wait_until="networkidle")
            self.page.reload(wait_until="networkidle")
            new_token = extract_token_from_local_storage(self.page)
            if new_token:
                self.headers[HEADER_ACCESS_TOKEN] = new_token
                log(f"账号{self.account_index} - 🔄 token 已刷新")
                return True
        except Exception as e:
            log(f"账号{self.account_index} - 🔄 token 刷新失败: {e}")
        return False

    def _browser_cookie_header(self) -> str:
        try:
            cookies = self.page.context.cookies(self.base_url)
        except Exception:
            return ""
        pairs = []
        for item in cookies:
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if name:
                pairs.append(f"{name}={value}")
        return "; ".join(pairs)

    def _browser_xsrf_token(self) -> str:
        try:
            return str(
                self.page.evaluate(
                    """
                    () => {
                      const names = ['XSRF-TOKEN', 'xsrf-token', 'x-xsrf-token'];
                      for (const key of names) {
                        const local = window.localStorage.getItem(key) || window.sessionStorage.getItem(key);
                        if (local) return local;
                      }
                      const cookies = String(document.cookie || '').split(';');
                      for (const row of cookies) {
                        const index = row.indexOf('=');
                        const name = row.slice(0, index).trim();
                        if (names.includes(name)) return decodeURIComponent(row.slice(index + 1));
                      }
                      return '';
                    }
                    """
                )
                or ""
            ).strip()
        except Exception:
            return ""

    def _invoice_headers(self) -> dict:
        headers = dict(self.headers)
        invoice_client_type = env_first("INVOICE_CLIENT_TYPE", default="WEB")
        invoice_secret_key = env_first("INVOICE_SECRET_KEY_VALUE", default=DEFAULT_INVOICE_SECRET_KEY_VALUE)
        invoice_referer = env_first("INVOICE_REFERER", default=f"{self.base_url}{DEFAULT_INVOICE_REFERER_PATH}")
        headers[HEADER_CLIENT_TYPE] = invoice_client_type
        headers["x-jlc-clienttype"] = invoice_client_type
        headers[HEADER_SECRET_KEY] = invoice_secret_key
        headers["secretkey"] = invoice_secret_key
        headers["referer"] = invoice_referer
        headers["origin"] = self.base_url
        headers["content-type"] = "application/json"
        cookie = self._browser_cookie_header()
        if cookie:
            headers["cookie"] = cookie
        xsrf = self._browser_xsrf_token()
        if xsrf:
            headers["x-xsrf-token"] = xsrf
        return headers

    def _get_json_once(self, url, tag="API", dump_body_on_error=False, dump_json_on_success_false=True, headers_override=None):
        try:
            headers = headers_override or self.headers
            if self.http:
                resp = self.http.get(url, headers=headers)
            else:
                resp = requests.get(url, headers=headers, timeout=12)

            if resp.status_code != 200:
                allow = resp.headers.get("Allow") or resp.headers.get("allow") or ""
                msg = f"账号{self.account_index} - {tag}请求失败 {resp.status_code} (GET {url})"
                if allow:
                    msg += f" Allow={allow}"
                log(msg)
                if dump_body_on_error:
                    body = redact_sensitive(truncate_text(resp.text, 2000))
                    log(f"账号{self.account_index} - {tag}响应内容: {body}")
                return None

            try:
                data = resp.json()
            except Exception:
                log(f"账号{self.account_index} - {tag}响应JSON解析失败 (200 GET {url})")
                if dump_body_on_error:
                    body = redact_sensitive(truncate_text(resp.text, 2000))
                    log(f"账号{self.account_index} - {tag}响应内容: {body}")
                return None

            if dump_json_on_success_false and isinstance(data, dict) and data.get("success") is False:
                log(f"账号{self.account_index} - ⚠️ {tag}返回success=false: {redact_sensitive(truncate_text(json.dumps(data, ensure_ascii=False), 2000))}")

            if is_risk_control_response(data):
                self.risk_controlled = True
                self.detail_reason = build_detail_reason(data, "抽奖失败，疑似触发活动限制")
            return data

        except Exception as e:
            log(f"账号{self.account_index} - {tag}异常: {e}")
            return None

    def _post_json_once(self, url, payload=None, tag="API", dump_body_on_error=False, dump_json_on_success_false=True, headers_override=None):
        try:
            headers = dict(headers_override or self.headers)
            headers.setdefault("content-type", "application/json;charset=UTF-8")
            if payload is None:
                if self.http:
                    resp = self.http.post(url, headers=headers)
                else:
                    resp = requests.post(url, headers=headers, timeout=12)
            else:
                if self.http:
                    resp = self.http.post(url, headers=headers, json=payload)
                else:
                    resp = requests.post(url, headers=headers, json=payload, timeout=12)

            if resp.status_code != 200:
                allow = resp.headers.get("Allow") or resp.headers.get("allow") or ""
                msg = f"账号{self.account_index} - {tag}请求失败 {resp.status_code} (POST {url})"
                if allow:
                    msg += f" Allow={allow}"
                log(msg)
                if dump_body_on_error:
                    body = redact_sensitive(truncate_text(resp.text, 2000))
                    log(f"账号{self.account_index} - {tag}响应内容: {body}")
                return None

            try:
                data = resp.json()
            except Exception:
                log(f"账号{self.account_index} - {tag}响应JSON解析失败 (200 POST {url})")
                if dump_body_on_error:
                    body = redact_sensitive(truncate_text(resp.text, 2000))
                    log(f"账号{self.account_index} - {tag}响应内容: {body}")
                return None

            if dump_json_on_success_false and isinstance(data, dict) and data.get("success") is False:
                log(f"账号{self.account_index} - ⚠️ {tag}返回success=false: {redact_sensitive(truncate_text(json.dumps(data, ensure_ascii=False), 2000))}")

            if is_risk_control_response(data):
                self.risk_controlled = True
                self.detail_reason = build_detail_reason(data, "抽奖失败，疑似触发活动限制")
            return data

        except Exception as e:
            log(f"账号{self.account_index} - {tag}异常: {e}")
            return None

    def _post_raw_json_once(self, url, payload, tag="API", dump_body_on_error=False, dump_json_on_success_false=True, headers_override=None):
        try:
            headers = dict(headers_override or self.headers)
            for key in list(headers.keys()):
                if str(key).lower() in {"content-type", "content-length"}:
                    headers.pop(key, None)
            headers["Content-Type"] = "application/json;charset=UTF-8"
            body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if self.http:
                resp = self.http.post(url, headers=headers, content=body)
            else:
                resp = requests.post(url, headers=headers, data=body, timeout=12)

            if resp.status_code != 200:
                allow = resp.headers.get("Allow") or resp.headers.get("allow") or ""
                msg = f"账号{self.account_index} - {tag}请求失败 {resp.status_code} (POST {url})"
                if allow:
                    msg += f" Allow={allow}"
                log(msg)
                if dump_body_on_error:
                    body_text = redact_sensitive(truncate_text(resp.text, 2000))
                    log(f"账号{self.account_index} - {tag}响应内容: {body_text}")
                return None

            try:
                data = resp.json()
            except Exception:
                log(f"账号{self.account_index} - {tag}响应JSON解析失败 (200 POST {url})")
                if dump_body_on_error:
                    body_text = redact_sensitive(truncate_text(resp.text, 2000))
                    log(f"账号{self.account_index} - {tag}响应内容: {body_text}")
                return None

            if dump_json_on_success_false and isinstance(data, dict) and data.get("success") is False:
                log(f"账号{self.account_index} - ⚠️ {tag}返回success=false: {redact_sensitive(truncate_text(json.dumps(data, ensure_ascii=False), 2000))}")

            if is_risk_control_response(data):
                self.risk_controlled = True
                self.detail_reason = build_detail_reason(data, "抽奖失败，疑似触发活动限制")
            return data

        except Exception as e:
            log(f"账号{self.account_index} - {tag}异常: {e}")
            return None

    def get_json_retry1(self, url, tag="API", dump_body_on_error=False, dump_json_on_success_false=True, headers_override=None):
        data = self._get_json_once(
            url,
            tag=tag,
            dump_body_on_error=dump_body_on_error,
            dump_json_on_success_false=dump_json_on_success_false,
            headers_override=headers_override,
        )
        if isinstance(data, dict) and data.get("success") is True:
            return data

        time.sleep(random.uniform(0.6, 1.2))
        log(f"账号{self.account_index} - 🔁 {tag}GET失败，重试一次GET...")
        data2 = self._get_json_once(
            url,
            tag=tag,
            dump_body_on_error=dump_body_on_error,
            dump_json_on_success_false=dump_json_on_success_false,
            headers_override=headers_override,
        )
        return data2 if data2 is not None else data

    def post_json_retry1(self, url, payload=None, tag="API", dump_body_on_error=False, dump_json_on_success_false=True, headers_override=None):
        data = self._post_json_once(
            url,
            payload=payload,
            tag=tag,
            dump_body_on_error=dump_body_on_error,
            dump_json_on_success_false=dump_json_on_success_false,
            headers_override=headers_override,
        )
        if isinstance(data, dict) and data.get("success") is True:
            return data

        time.sleep(random.uniform(0.6, 1.2))
        log(f"账号{self.account_index} - 🔁 {tag}POST失败，重试一次POST...")
        data2 = self._post_json_once(
            url,
            payload=payload,
            tag=tag,
            dump_body_on_error=dump_body_on_error,
            dump_json_on_success_false=dump_json_on_success_false,
            headers_override=headers_override,
        )
        return data2 if data2 is not None else data

    def post_raw_json_retry1(self, url, payload=None, tag="API", dump_body_on_error=False, dump_json_on_success_false=True, headers_override=None):
        data = self._post_raw_json_once(
            url,
            payload=payload,
            tag=tag,
            dump_body_on_error=dump_body_on_error,
            dump_json_on_success_false=dump_json_on_success_false,
            headers_override=headers_override,
        )
        if isinstance(data, dict) and data.get("success") is True:
            return data

        time.sleep(random.uniform(0.6, 1.2))
        log(f"账号{self.account_index} - 🔁 {tag}POST失败，重试一次POST...")
        data2 = self._post_raw_json_once(
            url,
            payload=payload,
            tag=tag,
            dump_body_on_error=dump_body_on_error,
            dump_json_on_success_false=dump_json_on_success_false,
            headers_override=headers_override,
        )
        return data2 if data2 is not None else data

    @with_retry
    def get_points(self):
        data = self.get_json_retry1(
            f"{self.base_url}/api/activity/front/getCustomerIntegral",
            tag="金豆",
            dump_body_on_error=True,
            dump_json_on_success_false=True
        )
        if data and data.get('success'):
            return data.get('data', {}).get('integralVoucher', 0)

        self._refresh_token()
        return None

    def fetch_invoice_money(self) -> float:
        url = f"{self.base_url}{INVOICE_INFO_PATH}"
        headers = self._invoice_headers()
        payload = {"invoiceType": 1, "vatCompanyName": ""}
        data = self.post_raw_json_retry1(
            url,
            payload=payload,
            tag="消费金额",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
            headers_override=headers,
        )
        money = find_invoice_money_value(data)
        if money is None:
            log(f"账号{self.account_index} - 消费金额接口未找到 invoiceMoney，按 0 写入")
            self.invoice_money = 0.0
            self.invoice_money_loaded = True
            return self.invoice_money
        self.invoice_money = money
        self.invoice_money_loaded = True
        log(f"账号{self.account_index} - 消费金额: {self.invoice_money}")
        return self.invoice_money

    def fetch_voucher_change_records(self) -> list[dict]:
        data = self.get_json_retry1(
            f"{self.base_url}{VOUCHER_CHANGE_RECORD_PATH}",
            tag="金豆变更记录",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        records = [item for item in extract_data_list(data) if isinstance(item, dict)]
        log(f"账号{self.account_index} - 金豆变更记录 {len(records)} 条")
        return records

    def _build_lottery_record(self, prize: dict, draw_time: datetime | None = None, draw_index: int | None = None) -> dict:
        draw_time = draw_time or datetime.now()
        title = str(
            prize.get("prizeTitle")
            or prize.get("goodsName")
            or prize.get("skuTitle")
            or prize.get("title")
            or "未知奖品"
        ).strip()
        expiry_date = (draw_time + timedelta(days=7)).strftime("%Y-%m-%d")
        return {
            "draw_index": draw_index,
            "title": title,
            "prize_code": str(prize.get("prizeCode") or "").strip(),
            "win_code": str(prize.get("winCode") or "").strip(),
            "turn_code": str(prize.get("turnCode") or "").strip(),
            "won_at": draw_time.strftime("%Y-%m-%d %H:%M:%S"),
            "expiry_date": expiry_date,
            "status_text": expiry_date,
            "claimed": False,
        }

    def _parse_turn_prizes(self, data: dict, draw_index: int) -> list[dict]:
        if not isinstance(data, dict):
            return []
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        prizes = payload.get("prizeList")
        if not isinstance(prizes, list):
            prizes = [payload] if payload else []
        draw_time = datetime.now()
        rows = []
        for prize in prizes:
            if not isinstance(prize, dict):
                continue
            row = self._build_lottery_record(prize, draw_time=draw_time, draw_index=draw_index)
            if payload.get("turnCode") and not row["turn_code"]:
                row["turn_code"] = str(payload.get("turnCode") or "").strip()
            rows.append(row)
        return rows

    def fetch_lottery_wins(self) -> list[dict]:
        data = self.post_json_retry1(
            f"{self.base_url}{LOTTERY_WINS_PATH}",
            payload={},
            tag="我的中奖记录",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        rows = []
        for index, item in enumerate([row for row in extract_data_list(data) if isinstance(row, dict)][:3], start=1):
            created = parse_datetime_value(
                item.get("createTime")
                or item.get("createdAt")
                or item.get("winTime")
                or item.get("winningTime")
            ) or datetime.now()
            rows.append(self._build_lottery_record(item, draw_time=created, draw_index=index))
        log(f"账号{self.account_index} - 我的中奖记录解析 {len(rows)} 条")
        return rows

    def fetch_activity_records(self) -> dict:
        try:
            lottery_records = self.draw_results[:3]
            if not lottery_records:
                lottery_records = self.fetch_lottery_wins()
            self.activity_records = {"lottery": lottery_records[:3]}
        except Exception as e:
            log(f"账号{self.account_index} - 活动记录抓取异常: {e}")
            self.activity_records = make_empty_extra_records()
        return self.activity_records

    def load_brand_activity_config(self) -> str:
        data = self.post_json_retry1(
            f"{self.base_url}{BRAND_ACTIVITY_CONFIG_PATH}",
            payload={},
            tag="活动配置",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        payload = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else {}
        common = payload.get("brandActivityCommonConfigVO") if isinstance(payload.get("brandActivityCommonConfigVO"), dict) else {}
        activity_code = str(common.get("lotteryActivityId") or DEFAULT_LOTTERY_ACTIVITY_CODE).strip()
        self.lottery_activity_code = activity_code or DEFAULT_LOTTERY_ACTIVITY_CODE
        log(f"账号{self.account_index} - 抽奖活动ID: {self.lottery_activity_code}")
        return self.lottery_activity_code

    def signup_activity_batch(self, sub_activity_types: list[int]) -> bool:
        payload = {"activityType": 2, "subActivityTypes": sub_activity_types}
        label = ",".join(str(item) for item in sub_activity_types)
        data = self.post_json_retry1(
            f"{self.base_url}{ACTIVITY_SIGNUP_PATH}",
            payload=payload,
            tag=f"报名活动[{label}]",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        if isinstance(data, dict) and data.get("success") is True:
            log(f"账号{self.account_index} - ✅ 报名活动[{label}]完成")
            return True
        message = build_detail_reason(data, default=f"报名活动[{label}]失败")
        if "已报名" in message or "重复" in message:
            log(f"账号{self.account_index} - ℹ️ 活动[{label}]已报名，继续后续流程")
            return True
        self._mark_failure(f"报名活动[{label}]失败", raw=data)
        return False

    def fetch_signup_info(self, sub_activity_types: list[int]) -> list[dict]:
        data = self.post_json_retry1(
            f"{self.base_url}{ACTIVITY_SIGNUP_INFO_PATH}",
            payload={"activityType": 2, "subActivityTypes": sub_activity_types},
            tag="报名状态",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        return [item for item in extract_data_list(data) if isinstance(item, dict)]

    def signup_all_lottery_activities(self) -> bool:
        delay_min, delay_max = env_delay_range("LOTTERY_SIGNUP_DELAY", 3, 5)
        for index, batch in enumerate(LOTTERY_SIGNUP_BATCHES, start=1):
            if not self.signup_activity_batch(list(batch)):
                return False
            self.fetch_signup_info(list(batch))
            if index < len(LOTTERY_SIGNUP_BATCHES):
                random_delay(delay_min, delay_max, f"账号{self.account_index} - 报名间隔")
        return True

    def get_voucher_lottery_detail(self) -> dict:
        data = self.post_json_retry1(
            f"{self.base_url}{VOUCHER_LOTTERY_DETAIL_PATH}",
            payload=None,
            tag="兑换状态",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        detail = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else {}
        log(
            f"账号{self.account_index} - 兑换状态: "
            f"status={detail.get('status')} exchangeNum={detail.get('exchangeNum')} exchangeMaxNum={detail.get('exchangeMaxNum')}"
        )
        return detail

    def can_exchange_lottery_chance(self, detail: dict) -> bool:
        exchange_num = safe_int(detail.get("exchangeNum"), 0)
        exchange_max = safe_int(detail.get("exchangeMaxNum"), 3)
        status = safe_int(detail.get("status"), 0)
        if exchange_max > 0 and exchange_num >= exchange_max:
            return False
        return status == 5

    def exchange_lottery_chance_once(self) -> bool:
        data = self.post_json_retry1(
            f"{self.base_url}{EXCHANGE_LOTTERY_CHANCE_PATH}",
            payload={},
            tag="兑换抽奖次数",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        if isinstance(data, dict) and data.get("success") is True:
            log(f"账号{self.account_index} - ✅ 兑换抽奖次数成功")
            return True
        self._mark_failure("兑换抽奖次数失败", raw=data)
        return False

    def exchange_lottery_chances(self) -> int:
        delay_min, delay_max = env_delay_range("LOTTERY_EXCHANGE_DELAY", 3, 5)
        exchanged = 0
        for _ in range(safe_int(os.getenv("LOTTERY_EXCHANGE_MAX_ATTEMPTS"), 3)):
            detail = self.get_voucher_lottery_detail()
            if not self.can_exchange_lottery_chance(detail):
                log(f"账号{self.account_index} - 已无法继续兑换抽奖次数，停止兑换")
                break
            random_delay(delay_min, delay_max, f"账号{self.account_index} - 兑换前随机延迟")
            if not self.exchange_lottery_chance_once():
                break
            exchanged += 1
        return exchanged

    def get_lucky_key_count(self) -> int:
        data = self.post_json_retry1(
            f"{self.base_url}{LOTTERY_KEY_COUNT_PATH}",
            payload={"activityCode": self.lottery_activity_code},
            tag="抽奖次数",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        payload = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else {}
        count = safe_int(payload.get("count"), 0)
        log(f"账号{self.account_index} - 当前可用抽奖次数: {count}")
        return count

    def turn_lottery_once(self, draw_index: int) -> list[dict]:
        data = self.post_json_retry1(
            f"{self.base_url}{LOTTERY_TURN_PATH}",
            payload={"clientType": self.client_type, "activityCode": self.lottery_activity_code},
            tag=f"抽奖{draw_index}",
            dump_body_on_error=True,
            dump_json_on_success_false=True,
        )
        if not (isinstance(data, dict) and data.get("success") is True):
            self._mark_failure(f"抽奖{draw_index}失败", raw=data)
            return []
        prizes = self._parse_turn_prizes(data, draw_index)
        if prizes:
            names = "、".join(item["title"] for item in prizes)
            log(f"账号{self.account_index} - 🎉 抽奖{draw_index}获得: {names}")
        else:
            log(f"账号{self.account_index} - ⚠️ 抽奖{draw_index}成功但未解析到奖品")
        return prizes

    def draw_lottery_chances(self) -> list[dict]:
        delay_min, delay_max = env_delay_range("LOTTERY_DRAW_DELAY", 7, 10)
        max_draws = safe_int(os.getenv("LOTTERY_MAX_DRAWS"), 3)
        count = min(self.get_lucky_key_count(), max_draws)
        results = []
        for draw_index in range(1, count + 1):
            random_delay(delay_min, delay_max, f"账号{self.account_index} - 抽奖{draw_index}前随机延迟")
            prizes = self.turn_lottery_once(draw_index)
            results.extend(prizes)
            self.draw_results = results[:3]
            if draw_index < count:
                self.get_lucky_key_count()
        return results[:3]

    def execute_lottery_process(self):
        time.sleep(random.uniform(1, 2))
        self.initial_points = self.get_points() or 0
        self.load_brand_activity_config()

        if not self.signup_all_lottery_activities():
            return False

        exchanged = self.exchange_lottery_chances()
        draw_records = self.draw_lottery_chances()
        self.draw_results = draw_records[:3]

        time.sleep(random.uniform(1, 2))
        self.final_points = self.get_points() or self.initial_points
        self.points_reward = self.final_points - self.initial_points
        self.fetch_invoice_money()
        self.sign_completed_at = current_time_text()
        self.has_reward = bool(draw_records)

        if draw_records:
            self.sign_status = f"抽奖完成，获得{len(draw_records)}个奖品"
            self.detail_reason = ""
            return True

        remaining = self.get_lucky_key_count()
        if exchanged == 0 and remaining == 0:
            self.sign_status = "已无可兑换/抽奖次数"
            self.detail_reason = "兑换次数已达上限或没有可用抽奖次数"
            return True

        self.sign_status = "抽奖完成但未解析到奖品"
        self.detail_reason = "抽奖接口未返回可解析的 prizeList"
        return False

    def execute_full_process(self):
        return self.execute_lottery_process()

# ==============================================================================
# 单个账号登录与抽奖主流程
# ==============================================================================
def sign_in_account(username, password, account_index, total_accounts, retry_count=0, is_final_retry=False):
    label = f" (重试{retry_count})" if retry_count > 0 else (" (最终重试)" if is_final_retry else "")
    log(f"开始处理账号 {account_index}/{total_accounts}{label}")
    task_start_date = normalize_task_start_date()

    result = {
        'account_index': account_index,
        'username': username,
        'masked_username': mask_account(username),
        'sign_status': '未知',
        'sign_success': False,
        'initial_points': 0,
        'final_points': 0,
        'points_reward': 0,
        'invoice_money': 0.0,
        'consumption_amount': 0.0,
        'has_reward': False,
        'token_extracted': False,
        'secretkey_extracted': False,
        'retry_count': retry_count,
        'is_final_retry': is_final_retry,
        'password_error': False,
        'risk_controlled': False,
        'detail_reason': '',
        'sign_time': '',
        'sign_ip': '',
        'next_day_success': False,
        'task_start_date': task_start_date,
        'sign_completed_at': '',
        'activity_records': make_empty_extra_records(),
    }

    ua_string = get_runtime_user_agent()
    default_width = 393 if is_mp_weixin_client() else 375
    default_height = 873 if is_mp_weixin_client() else 812
    default_scale = 2.75 if is_mp_weixin_client() else 2
    viewport_width = safe_int(os.getenv("BROWSER_VIEWPORT_WIDTH"), default_width)
    viewport_height = safe_int(os.getenv("BROWSER_VIEWPORT_HEIGHT"), default_height)
    device_scale_factor = safe_float(os.getenv("BROWSER_DEVICE_SCALE_FACTOR"), default_scale)

    with sync_playwright() as p:
        browser = None
        context = None
        page = None
        try:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--disable-web-security',
                ]
            )
            context = browser.new_context(
                user_agent=ua_string,
                viewport={'width': viewport_width, 'height': viewport_height},
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
                device_scale_factor=device_scale_factor,
                has_touch=True,
                is_mobile=True,
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                window.chrome = {runtime: {}};
                window.__wxjs_environment = 'miniprogram';
                window.WeixinJSBridge = window.WeixinJSBridge || {};
            """)

            page = context.new_page()

            secretkey_holder = {'value': None}
            token_holder = {'value': None}

            def handle_route(route):
                headers = {k.lower(): v for k, v in route.request.headers.items()}
                key = headers.get(HEADER_SECRET_KEY.lower())
                if key:
                    secretkey_holder['value'] = key
                token = headers.get(HEADER_ACCESS_TOKEN.lower())
                if not token:
                    for hk in HEADER_ACCESS_TOKEN_FALLBACKS:
                        token = headers.get(hk)
                        if token:
                            break
                if token:
                    token_holder['value'] = token
                route.continue_()

            context.route(f"**{LOGIN_API_PATH}*", handle_route)

            # ---------- 登录流程 ----------
            log(f"账号{account_index} - 打开移动登录页...")
            page.goto(PASSPORT_URL, timeout=60000)
            page.wait_for_selector('input[placeholder*="手机号码"], input[placeholder*="邮箱"]', timeout=30000)
            log("✅ 登录页加载完成")

            page.locator('input[placeholder*="手机号码"], input[placeholder*="邮箱"]').first.fill(username)
            log("✅ 已填写账号")

            agree_selector = "#__layout > div > div > div > div > div:nth-child(3) > form > div.mt-30.mb-32 > div.consent-agreement > div > img:nth-child(2)"
            try:
                page.locator(agree_selector).click(timeout=5000)
                log("✅ 已点击同意协议")
            except Exception as e:
                log(f"⚠️ 点击同意协议失败（可能已默认同意）: {e}")

            first_login_btn = "#__layout > div > div > div > div > div:nth-child(3) > form > button"
            try:
                page.locator(first_login_btn).click(timeout=5000)
                log("✅ 已点击第一步登录按钮")
            except Exception as e:
                log(f"⚠️ 点击第一步登录按钮失败: {e}")

            time.sleep(1)

            password_xpath = "/html/body/div[1]/div/div/div/div/div/div[2]/div[2]/form/div[2]/div/div[1]/div[1]/input"
            page.wait_for_selector(f"xpath={password_xpath}", timeout=10000)
            log("✅ 密码框已出现")
            page.locator(f"xpath={password_xpath}").fill(password)
            log("✅ 已填写密码")

            second_login_btn = "#__layout > div > div > div > div > div:nth-child(2) > div:nth-child(2) > form > button"
            try:
                page.locator(second_login_btn).click(timeout=5000)
                log("✅ 已点击最终登录按钮")
            except Exception as e:
                log(f"⚠️ 点击最终登录按钮失败: {e}")
                page.locator('form button[type="submit"]').click()

            # ===== 执行滑块破解 =====
            slider_ok = solve_slider_with_bezier(page)
            if not slider_ok:
                result['sign_status'] = '滑块未通过'
                result['detail_reason'] = f"登录滑块未通过：未能在页面中完成 {SLIDER_ID}"
                return result

            # ===== 滑块完成后，监控密码错误7秒，同时等待首页 =====
            monitor_start = time.time()
            home_found = False

            while time.time() - monitor_start < 7:
                if page.locator("text=/账号或密码不正确|用户名或密码错误|密码错误|登录失败/").is_visible(timeout=500):
                    log(f"账号{account_index} - ❌ 密码错误（滑块后检测）")
                    result['password_error'] = True
                    result['sign_status'] = '密码错误'
                    result['detail_reason'] = '登录页提示账号或密码错误'
                    return result

                try:
                    page.wait_for_selector(HOME_SELECTOR, timeout=500)
                    home_found = True
                    break
                except PlaywrightTimeoutError:
                    continue

            if not home_found:
                page.wait_for_selector(HOME_SELECTOR, timeout=30000 - 7000)
                log(f"账号{account_index} - ✅ 已进入首页")
            else:
                log(f"账号{account_index} - ✅ 已进入首页")

            # 提取 token
            access_token = extract_token_from_local_storage(page)
            if not access_token:
                access_token = wait_token_from_requests(token_holder, timeout=8)

            if not access_token:
                page.reload(wait_until="networkidle")
                access_token = extract_token_from_local_storage(page)
                if not access_token:
                    access_token = wait_token_from_requests(token_holder, timeout=8)

            secretkey = secretkey_holder['value']
            result['token_extracted'] = bool(access_token)
            result['secretkey_extracted'] = bool(secretkey or JLC_SECRET_KEY_VALUE)

            if access_token:
                client = ApiClient(access_token, secretkey, account_index, page, user_agent=ua_string)
                log(f"账号{account_index} - API clientType={client.client_type}, referer={REFERER}")
                log(f"账号{account_index} - 使用 token 执行抽奖流程（报名、兑换、抽奖）")
                success = client.execute_full_process()
                if client.final_points == 0:
                    latest_points = client.get_points()
                    if latest_points is not None:
                        client.final_points = latest_points
                        client.points_reward = client.final_points - client.initial_points
                if not client.invoice_money_loaded:
                    client.fetch_invoice_money()

                client.fetch_activity_records()
                result.update({
                    'sign_success': success,
                    'sign_status': '抽奖风控' if client.risk_controlled and not success else client.sign_status,
                    'initial_points': client.initial_points,
                    'final_points': client.final_points,
                    'points_reward': client.points_reward,
                    'invoice_money': client.invoice_money,
                    'consumption_amount': client.invoice_money,
                    'has_reward': client.has_reward,
                    'risk_controlled': client.risk_controlled,
                    'detail_reason': client.detail_reason,
                    'sign_completed_at': client.sign_completed_at,
                    'activity_records': client.activity_records,
                })
                client.close()
            else:
                log(f"账号{account_index} - ❌ 未提取到 token")
                result['sign_status'] = 'Token提取失败'
                result['detail_reason'] = (
                    f"登录成功后未从 localStorage({TOKEN_KEY}) 或请求头"
                    f"({HEADER_ACCESS_TOKEN}) 中提取到 token"
                )

        except Exception as e:
            log(f"账号{account_index} - ❌ 执行异常: {e}")
            result['sign_status'] = '执行异常'
            result['detail_reason'] = f"{type(e).__name__}: {truncate_text(str(e), 500)}"
        finally:
            if context:
                context.close()
            if browser:
                browser.close()
            finalize_result_metadata(result)
            time.sleep(1)

    return result

# ==============================================================================
# 重试逻辑与结果合并（保持不变）
# ==============================================================================
def should_retry(res):
    if res.get('password_error'):
        return False
    return not res['sign_success']

def process_single_account(username, password, account_index, total_accounts):
    merged = {
        'account_index': account_index,
        'username': username,
        'masked_username': mask_account(username),
        'sign_status': '未知',
        'sign_success': False,
        'initial_points': 0,
        'final_points': 0,
        'points_reward': 0,
        'invoice_money': 0.0,
        'consumption_amount': 0.0,
        'has_reward': False,
        'token_extracted': False,
        'secretkey_extracted': False,
        'retry_count': 0,
        'is_final_retry': False,
        'password_error': False,
        'risk_controlled': False,
        'detail_reason': '',
        'sign_time': '',
        'sign_ip': '',
        'next_day_success': False,
        'task_start_date': normalize_task_start_date(),
        'sign_completed_at': '',
        'activity_records': make_empty_extra_records(),
    }
    max_retries = 3
    for attempt in range(max_retries + 1):
        res = sign_in_account(username, password, account_index, total_accounts, retry_count=attempt)

        if res.get('password_error'):
            merged['password_error'] = True
            merged['sign_status'] = '密码错误'
            merged['username'] = username
            merged['masked_username'] = mask_account(username)
            merged['detail_reason'] = res.get('detail_reason') or '密码错误'
            merged['sign_time'] = res.get('sign_time', '')
            merged['sign_ip'] = res.get('sign_ip', '')
            merged['activity_records'] = res.get('activity_records') or make_empty_extra_records()
            break

        if res['sign_success'] and not merged['sign_success']:
            for k in ['sign_success', 'sign_status', 'initial_points', 'final_points', 'points_reward', 'invoice_money', 'consumption_amount', 'has_reward', 'risk_controlled', 'detail_reason', 'sign_time', 'sign_ip', 'next_day_success', 'task_start_date', 'sign_completed_at', 'activity_records']:
                merged[k] = res[k]
        elif not merged['sign_success']:
            for k in ['sign_status', 'risk_controlled', 'detail_reason', 'sign_time', 'sign_ip', 'next_day_success', 'task_start_date', 'sign_completed_at', 'activity_records', 'initial_points', 'final_points', 'points_reward', 'invoice_money', 'consumption_amount']:
                merged[k] = res.get(k)

        merged['retry_count'] = res['retry_count']

        if not should_retry(merged) or attempt >= max_retries:
            break
        log(f"账号{account_index} - 🔄 准备第 {attempt+1} 次重试...")
        time.sleep(random.uniform(3, 7))
    return merged

def final_retry(all_results, usernames, passwords, total_accounts):
    log("=" * 70)
    log("🔄 执行最终重试（针对之前失败的账号）")
    log("=" * 70)
    failed = []
    for i, r in enumerate(all_results):
        if should_retry(r):
            failed.append({
                'index': i,
                'account_index': r['account_index'],
                'username': r.get('username') or usernames[i],
                'password': passwords[i],
                'prev_retry': r['retry_count']
            })
    if not failed:
        log("✅ 没有需要最终重试的账号")
        return all_results

    log(f"📋 需重试账号序号: {', '.join(str(f['account_index']) for f in failed)}")
    time.sleep(random.uniform(3, 5))

    for f in failed:
        log(f"🔄 最终重试账号 {f['account_index']}")
        final = sign_in_account(f['username'], f['password'], f['account_index'], total_accounts,
                                retry_count=f['prev_retry'] + 1, is_final_retry=True)
        orig = all_results[f['index']]

        if final.get('password_error'):
            orig.update({
                'password_error': True,
                'sign_status': '密码错误',
                'username': f['username'],
                'masked_username': mask_account(f['username']),
                'detail_reason': final.get('detail_reason') or '密码错误',
                'sign_time': final.get('sign_time', ''),
                'sign_ip': final.get('sign_ip', ''),
                'activity_records': final.get('activity_records') or orig.get('activity_records') or make_empty_extra_records(),
                'is_final_retry': True
            })
            continue

        if final['sign_success'] and not orig['sign_success']:
            for k in ['sign_success', 'sign_status', 'initial_points', 'final_points', 'points_reward', 'invoice_money', 'consumption_amount', 'has_reward', 'risk_controlled', 'detail_reason', 'sign_time', 'sign_ip', 'next_day_success', 'task_start_date', 'sign_completed_at', 'activity_records']:
                orig[k] = final[k]
        elif not orig['sign_success']:
            for k in ['sign_status', 'risk_controlled', 'detail_reason', 'sign_time', 'sign_ip', 'next_day_success', 'task_start_date', 'sign_completed_at', 'activity_records', 'initial_points', 'final_points', 'points_reward', 'invoice_money', 'consumption_amount']:
                orig[k] = final.get(k)

        orig.update({
            'is_final_retry': True,
            'retry_count': f['prev_retry'] + 1,
            'username': f['username'],
            'masked_username': mask_account(f['username'])
        })

        if f != failed[-1]:
            time.sleep(random.uniform(4, 8))
    log("✅ 最终重试完成")
    return all_results

def summarize_results(all_results):
    success_count = 0
    reward_count = 0
    total_lottery_results = 0
    prize_distribution = {}
    password_error = []
    other_failed = []

    for r in all_results:
        if r.get('sign_success'):
            success_count += 1
        else:
            if r.get('password_error'):
                password_error.append(r)
            else:
                other_failed.append(r)

        lottery_rows = (r.get("activity_records") or {}).get("lottery") or []
        parsed_titles = []
        for item in lottery_rows:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("prizeTitle") or "").strip()
            if not title:
                continue
            parsed_titles.append(title)
            prize_distribution[title] = prize_distribution.get(title, 0) + 1
        total_lottery_results += len(parsed_titles)
        if parsed_titles and r.get('sign_success'):
            reward_count += 1

    return {
        "success_count": success_count,
        "reward_count": reward_count,
        "total_lottery_results": total_lottery_results,
        "prize_distribution": dict(sorted(prize_distribution.items(), key=lambda item: (-item[1], item[0]))),
        "password_error": password_error,
        "other_failed": other_failed,
    }

def print_summary(all_results, total_accounts):
    global in_summary
    in_summary = True
    log("=" * 70)
    log("📊 抽奖任务总结")
    log("=" * 70)

    summary = summarize_results(all_results)
    success_count = summary["success_count"]
    reward_count = summary["reward_count"]
    total_lottery_results = summary["total_lottery_results"]
    prize_distribution = summary["prize_distribution"]
    password_error = summary["password_error"]
    other_failed = summary["other_failed"]

    log("📈 总体统计:")
    log(f"  ├── 总账号数: {total_accounts}")
    log(f"  ├── 抽奖成功: {success_count}/{total_accounts}")

    success_rate = (success_count / total_accounts) * 100 if total_accounts > 0 else 0
    log(f"  └── 抽奖成功率: {success_rate:.1f}%")

    if reward_count > 0:
        log(f"  ✅ 有中奖记录账号数: {reward_count}")
    log(f"  🎁 抽奖结果总数: {total_lottery_results}")
    if prize_distribution:
        log("  🎁 奖品分布:")
        for title, count in prize_distribution.items():
            log(f"    - {title}: {count}")
    if not password_error and not other_failed:
        log("  🎉 所有账号抽奖流程正常!")
    else:
        if password_error:
            labels = [masked_label(r) for r in password_error]
            log(f"  ⚠️ 密码错误账号: {', '.join(labels)}")
        if other_failed:
            labels = [masked_label(r) for r in other_failed]
            log(f"  ⚠️ 抽奖失败账号: {', '.join(labels)}")

    log("=" * 70)

def should_notify(failed_exists):
    mode = os.getenv('NOTIFY_ON', 'always').strip().lower()
    if mode in ('never', 'none', 'off', 'false', '0'):
        return False
    if mode in ('failure', 'fail', 'error', 'errors'):
        return failed_exists
    return True

def write_results_json(path, all_results, total_accounts):
    try:
        sanitized = []
        group_name = os.getenv('GROUP_NAME', '') or os.getenv('BATCH_NAME', '')
        group_number = safe_int(os.getenv('GROUP_NUMBER'), 0)
        execution_order = safe_int(os.getenv('EXECUTION_ORDER'), 0)
        for r in all_results:
            invoice_money = record_invoice_money(r)
            sanitized.append({
                "account_index": r.get("account_index"),
                "execution_order": execution_order or r.get("account_index"),
                "group_name": group_name,
                "group_number": group_number,
                "group_position": f"{group_number}组账号{r.get('account_index')}" if group_number > 0 else f"账号{r.get('account_index')}",
                "sign_success": r.get("sign_success"),
                "sign_status": r.get("sign_status"),
                "initial_points": r.get("initial_points"),
                "final_points": r.get("final_points"),
                "points_reward": r.get("points_reward"),
                "invoice_money": invoice_money,
                "consumption_amount": invoice_money,
                "has_reward": r.get("has_reward"),
                "password_error": r.get("password_error"),
                "risk_controlled": r.get("risk_controlled"),
                "next_day_success": False,
                "task_start_date": r.get("task_start_date"),
                "sign_completed_at": r.get("sign_completed_at"),
                "retry_count": r.get("retry_count"),
                "is_final_retry": r.get("is_final_retry"),
                "detail_reason": r.get("detail_reason"),
                "sign_time": r.get("sign_time"),
                "sign_ip": r.get("sign_ip"),
                "activity_records": r.get("activity_records") or make_empty_extra_records(),
            })

        payload = {
            "generated_at": datetime.now().isoformat(),
            "batch_name": os.getenv('BATCH_NAME', ''),
            "group_name": group_name,
            "group_number": group_number,
            "total_accounts": total_accounts,
            "results": sanitized,
        }
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"结果已写入: {path}")
    except Exception as e:
        log(f"写入结果失败: {e}")

def main():
    if len(sys.argv) < 3:
        print("用法: python script.py \"账号1,账号2\" \"密码1,密码2\" [失败退出标志]")
        sys.exit(1)

    usernames = [u.strip() for u in sys.argv[1].split(',') if u.strip()]
    passwords = [p.strip() for p in sys.argv[2].split(',') if p.strip()]
    enable_failure_exit = len(sys.argv) >= 4 and sys.argv[3].lower() == 'true'

    log(f"失败退出功能: {'开启' if enable_failure_exit else '关闭'}")
    if len(usernames) != len(passwords):
        log("❌ 账号与密码数量不匹配!")
        sys.exit(1)

    total = len(usernames)
    log(f"总计 {total} 个账号，开始抽奖流程")

    index_base = 1
    env_index = os.getenv('ACCOUNT_INDEX')
    if env_index:
        try:
            index_base = int(env_index)
        except ValueError:
            log(f"⚠️ ACCOUNT_INDEX 无效: {env_index}，已使用 1")
            index_base = 1

    all_results = []
    for offset, (u, p) in enumerate(zip(usernames, passwords)):
        account_index = index_base + offset
        res = process_single_account(u, p, account_index, total)
        all_results.append(res)
        if offset < total - 1:
            time.sleep(random.uniform(5, 10))

    if any(should_retry(r) for r in all_results):
        all_results = final_retry(all_results, usernames, passwords, total)

    print_summary(all_results, total)

    result_json_path = os.getenv('RESULT_JSON_PATH')
    if result_json_path:
        write_results_json(result_json_path, all_results, total)

    failed_exists = any(not r['sign_success'] and not r.get('password_error') for r in all_results) or any(r.get('password_error') for r in all_results)
    if enable_failure_exit and failed_exists:
        log("❌ 存在失败账号，退出码设为1")
        sys.exit(1)

    log("✅ 抽奖程序正常结束")
    sys.exit(0)

if __name__ == "__main__":
    main()
