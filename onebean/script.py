from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


ROOT_DIR = Path(__file__).resolve().parents[1]
H3_SCRIPT = ROOT_DIR / "h3" / "script.py"
load_dotenv(ROOT_DIR / ".env")

ONEBEAN_ACTIVITY_ID = int(os.getenv("ONEBEAN_ACTIVITY_ID", "69") or 69)
ONEBEAN_CLIENT_TYPE = os.getenv("ONEBEAN_CLIENT_TYPE", "WEB").strip() or "WEB"
ONEBEAN_SECRET_KEY_VALUE = (
    os.getenv("ONEBEAN_SECRET_KEY_VALUE")
    or os.getenv("SECRET_KEY_VALUE")
    or os.getenv("HEADER_SECRET_KEY_VALUE")
    or "defaultKeyId".encode("utf-8").hex()
).strip()
RECEIVE_COUPON_PATH = "/api/appPlatform/couponPage/receiveCoupon"
QUERY_COUPON_GROUP_PATH = "/api/cgi/operationService/front/customerCoupon/queryCustomerCouponGroup"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(message: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def load_h3():
    spec = importlib.util.spec_from_file_location("onebean_h3_legacy", H3_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 h3 脚本: {H3_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_int(value, default=0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def safe_float(value, default=0.0) -> float:
    try:
        return float(str(value).strip())
    except Exception:
        return default


def mask_account(account: str) -> str:
    value = str(account or "")
    if len(value) <= 4:
        return "*" * len(value)
    return value[:-4] + "****"


def truncate_text(value: Any, limit: int = 1200) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(truncated,len={len(text)})"


def redact_sensitive(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"([A-Za-z0-9_-]{24,})", lambda m: m.group(1)[:6] + "***" + m.group(1)[-4:], text)
    return text


def parse_ms_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
        if number <= 0:
            return ""
        return datetime.fromtimestamp(number / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    parsed = parse_ms_date(text)
    if parsed:
        return parsed
    return text[:10]


def build_coupon_records(ids: list[str], payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    records: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        dto = item.get("couponResponseDto")
        if not isinstance(dto, dict):
            dto = {}
        config = dto.get("couponConfig")
        if not isinstance(config, dict):
            config = {}
        discount = dto.get("couponDiscountConfig")
        if not isinstance(discount, dict):
            discount = {}
        quantity = safe_int(item.get("quantity"), 1)
        title = str(dto.get("name") or item.get("name") or "未知奖品").strip()
        start_date = normalize_date(config.get("startDate") or dto.get("startTime"))
        expiry_date = normalize_date(config.get("endDate") or dto.get("endTime"))
        records.append(
            {
                "activity": "onebean",
                "customer_coupon_id": ids[index] if index < len(ids) else "",
                "coupon_id": str(dto.get("id") or "").strip(),
                "title": title,
                "prize_name": title,
                "quantity": quantity,
                "denomination": discount.get("couponDenomination", ""),
                "min_consume_money": dto.get("minConsumeMoney", ""),
                "start_date": start_date,
                "expiry_date": expiry_date,
                "business_line": str(dto.get("businessLine") or "").strip(),
            }
        )
    return records


def launch_browser(playwright):
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-web-security",
    ]
    if str(os.getenv("ONEBEAN_USE_SYSTEM_CHROME", "true")).strip().lower() not in {"0", "false", "no", "off"}:
        try:
            return playwright.chromium.launch(channel="chrome", headless=True, args=args)
        except Exception as exc:
            log(f"系统浏览器启动失败，回退默认浏览器: {type(exc).__name__}: {truncate_text(exc, 200)}")
    return playwright.chromium.launch(headless=True, args=args)


class OneBeanClient:
    def __init__(self, h3, access_token: str, page, user_agent: str, account_index: int):
        self.h3 = h3
        self.base_url = str(h3.BASE_URL).rstrip("/")
        self.activity_referer = f"{self.base_url}/pages/coupon-page/index?id={ONEBEAN_ACTIVITY_ID}"
        self.page = page
        self.account_index = account_index
        token_header = h3.HEADER_ACCESS_TOKEN
        client_type_header = h3.HEADER_CLIENT_TYPE
        secret_key_header = h3.HEADER_SECRET_KEY or "secretkey"
        headers = {
            "user-agent": user_agent,
            "accept": "application/json, text/plain, */*",
            "accept-language": os.getenv("ACCEPT_LANGUAGE", "zh-CN,zh;q=0.9"),
            "origin": self.base_url,
            "referer": self.activity_referer,
            "content-type": "application/json;charset=UTF-8",
            token_header: access_token,
            client_type_header: ONEBEAN_CLIENT_TYPE,
            secret_key_header: ONEBEAN_SECRET_KEY_VALUE,
        }
        cookie = self.browser_cookie_header()
        if cookie:
            headers["cookie"] = cookie
        xsrf = self.browser_xsrf_token()
        if xsrf:
            headers["x-xsrf-token"] = xsrf
        self.headers = headers

    def browser_cookie_header(self) -> str:
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

    def browser_xsrf_token(self) -> str:
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
                        if (index < 0) continue;
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

    def post_json(self, path: str, payload: dict[str, Any], tag: str) -> dict[str, Any] | None:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = dict(self.headers)
        headers.pop("content-length", None)
        headers.pop("Content-Length", None)
        try:
            response = requests.post(url, headers=headers, data=body, timeout=20)
        except Exception as exc:
            log(f"账号{self.account_index} - {tag}请求异常: {type(exc).__name__}: {exc}")
            return None
        try:
            data = response.json()
        except Exception:
            log(f"账号{self.account_index} - {tag}响应不是 JSON: HTTP {response.status_code} {truncate_text(response.text, 800)}")
            return None
        if response.status_code != 200:
            log(f"账号{self.account_index} - {tag}请求失败 HTTP {response.status_code}: {redact_sensitive(truncate_text(json.dumps(data, ensure_ascii=False), 1000))}")
            return data
        if isinstance(data, dict) and data.get("success") is False:
            log(f"账号{self.account_index} - {tag}返回 success=false: {redact_sensitive(truncate_text(json.dumps(data, ensure_ascii=False), 1000))}")
        return data

    def execute(self) -> tuple[bool, str, list[dict[str, Any]]]:
        receive = self.post_json(RECEIVE_COUPON_PATH, {"id": ONEBEAN_ACTIVITY_ID}, "领取1豆奖品")
        if not isinstance(receive, dict):
            return False, "领取接口无响应", []
        ids_raw = receive.get("data")
        ids = [str(item).strip() for item in ids_raw] if isinstance(ids_raw, list) else []
        ids = [item for item in ids if item]
        if not (receive.get("success") is True and ids):
            message = str(receive.get("message") or receive.get("msg") or "领取失败，未返回券ID")
            return False, message, []

        log(f"账号{self.account_index} - 已领取券ID: {', '.join(ids)}")
        detail = self.post_json(QUERY_COUPON_GROUP_PATH, {"customerCouponIds": ids}, "查询1豆奖品")
        if not isinstance(detail, dict):
            return False, "查询奖品详情无响应", []
        if detail.get("success") is not True:
            message = str(detail.get("message") or detail.get("msg") or "查询奖品详情失败")
            return False, message, []
        records = build_coupon_records(ids, detail)
        if not records:
            return False, "查询成功但未解析到奖品", []
        names = "、".join(item["title"] for item in records)
        log(f"账号{self.account_index} - 获得奖品: {names}")
        return True, f"领取成功，获得{sum(safe_int(item.get('quantity'), 1) for item in records)}个奖品", records


def write_result(path: str, result: dict[str, Any], total_accounts: int):
    payload = {
        "generated_at": datetime.now().isoformat(),
        "batch_name": os.getenv("GROUP_NAME", "onebean"),
        "group_name": os.getenv("GROUP_NAME", "onebean"),
        "group_number": safe_int(os.getenv("GROUP_NUMBER"), 1),
        "total_accounts": total_accounts,
        "results": [result],
    }
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    log(f"结果已写入: {path}")


def default_result(username: str, account_index: int, total_accounts: int) -> dict[str, Any]:
    group_number = safe_int(os.getenv("GROUP_NUMBER"), 1)
    return {
        "account_index": account_index,
        "execution_order": safe_int(os.getenv("EXECUTION_ORDER"), account_index),
        "username": username,
        "masked_username": mask_account(username),
        "group_name": os.getenv("GROUP_NAME", "onebean"),
        "group_number": group_number,
        "group_position": f"{group_number}组账号{account_index}" if group_number > 0 else f"账号{account_index}",
        "sign_success": False,
        "sign_status": "未知",
        "has_reward": False,
        "password_error": False,
        "risk_controlled": False,
        "retry_count": safe_int(os.getenv("RETRY_COUNT"), 0),
        "is_final_retry": str(os.getenv("IS_FINAL_RETRY", "")).lower() == "true",
        "detail_reason": "",
        "sign_time": "",
        "sign_ip": "",
        "activity_records": {"onebean": []},
        "total_accounts": total_accounts,
    }


def run_account(username: str, password: str, account_index: int, total_accounts: int) -> dict[str, Any]:
    h3 = load_h3()
    result = default_result(username, account_index, total_accounts)
    user_agent = h3.get_runtime_user_agent()
    default_width = 393 if h3.is_mp_weixin_client() else 375
    default_height = 873 if h3.is_mp_weixin_client() else 812
    default_scale = 2.75 if h3.is_mp_weixin_client() else 2
    viewport_width = safe_int(os.getenv("BROWSER_VIEWPORT_WIDTH"), default_width)
    viewport_height = safe_int(os.getenv("BROWSER_VIEWPORT_HEIGHT"), default_height)
    device_scale_factor = safe_float(os.getenv("BROWSER_DEVICE_SCALE_FACTOR"), default_scale)

    with h3.sync_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = launch_browser(playwright)
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": viewport_width, "height": viewport_height},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                device_scale_factor=device_scale_factor,
                has_touch=True,
                is_mobile=True,
            )
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                window.chrome = {runtime: {}};
                window.__wxjs_environment = 'miniprogram';
                window.WeixinJSBridge = window.WeixinJSBridge || {};
                """
            )
            page = context.new_page()
            secretkey_holder = {"value": None}
            token_holder = {"value": None}

            def handle_route(route):
                headers = {key.lower(): value for key, value in route.request.headers.items()}
                secret_header = (h3.HEADER_SECRET_KEY or "secretkey").lower()
                key = headers.get(secret_header)
                if key:
                    secretkey_holder["value"] = key
                token = headers.get((h3.HEADER_ACCESS_TOKEN or "").lower())
                if not token:
                    for header_name in h3.HEADER_ACCESS_TOKEN_FALLBACKS:
                        token = headers.get(header_name)
                        if token:
                            break
                if token:
                    token_holder["value"] = token
                route.continue_()

            context.route(f"**{h3.LOGIN_API_PATH}*", handle_route)

            log(f"账号{account_index} - 打开移动登录页")
            page.goto(h3.PASSPORT_URL, timeout=60000)
            account_selector = 'input[placeholder*="手机号"], input[placeholder*="邮箱"], input[type="text"]'
            page.wait_for_selector(account_selector, timeout=30000)
            page.locator(account_selector).first.fill(username)

            agree_selector = "#__layout > div > div > div > div > div:nth-child(3) > form > div.mt-30.mb-32 > div.consent-agreement > div > img:nth-child(2)"
            try:
                page.locator(agree_selector).click(timeout=5000)
                log(f"账号{account_index} - 已点击同意协议控件")
            except Exception:
                pass

            first_login_btn = "#__layout > div > div > div > div > div:nth-child(3) > form > button"
            try:
                page.locator(first_login_btn).click(timeout=5000)
            except Exception:
                page.locator("form button").first.click(timeout=5000)

            password_xpath = "/html/body/div[1]/div/div/div/div/div/div[2]/div[2]/form/div[2]/div/div[1]/div[1]/input"
            password_selectors = [f"xpath={password_xpath}", 'input[type="password"]', 'input[placeholder*="密码"]']
            password_filled = False
            for selector in password_selectors:
                try:
                    locator = page.locator(selector).first
                    if locator.count():
                        locator.fill(password, timeout=10000)
                        password_filled = True
                        break
                except Exception:
                    continue
            if not password_filled:
                raise RuntimeError("未找到密码输入框")

            second_login_btn = "#__layout > div > div > div > div > div:nth-child(2) > div:nth-child(2) > form > button"
            try:
                page.locator(second_login_btn).click(timeout=5000)
            except Exception:
                page.locator('form button[type="submit"], form button').last.click(timeout=5000)

            if not h3.solve_slider_with_bezier(page):
                result.update(
                    {
                        "sign_status": "滑块未通过",
                        "detail_reason": "登录滑块未通过",
                    }
                )
                return result

            monitor_start = time.time()
            home_found = False
            while time.time() - monitor_start < 7:
                try:
                    if page.locator("text=/账号或密码不正确|用户名或密码错误|密码错误|登录失败/").is_visible(timeout=500):
                        result.update(
                            {
                                "password_error": True,
                                "sign_status": "密码错误",
                                "detail_reason": "登录页提示账号或密码错误",
                            }
                        )
                        return result
                except Exception:
                    pass
                try:
                    page.wait_for_selector(h3.HOME_SELECTOR, timeout=500)
                    home_found = True
                    break
                except PlaywrightTimeoutError:
                    continue
            if not home_found:
                page.wait_for_selector(h3.HOME_SELECTOR, timeout=23000)
            log(f"账号{account_index} - 登录步骤完成")

            access_token = h3.extract_token_from_local_storage(page) or h3.wait_token_from_requests(token_holder, timeout=8)
            if not access_token:
                page.reload(wait_until="networkidle")
                access_token = h3.extract_token_from_local_storage(page) or h3.wait_token_from_requests(token_holder, timeout=8)
            if not access_token:
                result.update({"sign_status": "Token提取失败", "detail_reason": "登录后未提取到 access token"})
                return result

            client = OneBeanClient(h3, access_token, page, user_agent, account_index)
            success, message, records = client.execute()
            result.update(
                {
                    "sign_success": success,
                    "sign_status": "领取成功" if success else "领取失败",
                    "has_reward": bool(records),
                    "detail_reason": "" if success else message,
                    "sign_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "activity_records": {"onebean": records},
                }
            )
            if success:
                result["detail_reason"] = message
            return result
        except Exception as exc:
            result.update(
                {
                    "sign_status": "执行异常",
                    "detail_reason": f"{type(exc).__name__}: {truncate_text(exc, 500)}",
                }
            )
            log(f"账号{account_index} - 执行异常: {exc}")
            return result
        finally:
            if context:
                context.close()
            if browser:
                browser.close()


def main():
    if len(sys.argv) < 3:
        print('用法: python onebean/script.py "账号" "密码" [失败退出标志]')
        raise SystemExit(1)
    usernames = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
    passwords = [item.strip() for item in sys.argv[2].split(",") if item.strip()]
    enable_failure_exit = len(sys.argv) >= 4 and str(sys.argv[3]).lower() == "true"
    if len(usernames) != len(passwords):
        log("账号与密码数量不匹配")
        raise SystemExit(1)
    index_base = safe_int(os.getenv("ACCOUNT_INDEX"), 1)
    results = []
    for offset, (username, password) in enumerate(zip(usernames, passwords)):
        account_index = index_base + offset
        results.append(run_account(username, password, account_index, len(usernames)))
        if offset < len(usernames) - 1:
            time.sleep(random.uniform(3, 6))
    result_path = os.getenv("RESULT_JSON_PATH")
    if result_path:
        write_result(result_path, results[0] if len(results) == 1 else results[-1], len(usernames))
    failed = any(not item.get("sign_success") for item in results)
    if enable_failure_exit and failed:
        raise SystemExit(1)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
