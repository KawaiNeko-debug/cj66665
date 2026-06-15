import os
import sys
import json
import glob
import time
import ssl
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header

import requests

# 可选：本地跑时用 .env，不强依赖 python-dotenv（GitHub Actions 不装也能跑）
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

# 统一东八区时间
os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    time.tzset()
except Exception:
    pass


def truthy(v) -> bool:
    if v is True:
        return True
    if v is False or v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "y", "on")


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


def record_invoice_money(record: dict) -> float:
    return pick_money_value(
        record.get("invoice_money"),
        record.get("consumption_amount"),
        record.get("invoiceMoney"),
    )


def mask_account(acc: str) -> str:
    if not acc:
        return ""
    s = str(acc)
    if len(s) <= 4:
        return "*" * len(s)
    return s[:-4] + "****"


def load_accounts_from_env() -> list[str]:
    """
    ACCOUNTS:
      user1,pass1
      user2,pass2
    返回 user 列表
    """
    data = os.getenv("ACCOUNTS", "") or ""
    accounts = []
    for line in data.splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        user, _ = line.split(",", 1)
        accounts.append(user.strip())
    return accounts


def parse_generated_at_ts(payload: dict, fallback_path: str) -> float:
    """
    payload.get("generated_at") 可能是 isoformat；否则用文件 mtime
    """
    ga = payload.get("generated_at")
    if isinstance(ga, str) and ga.strip():
        s = ga.strip()
        try:
            # 兼容 "2026-02-21T..." 这种
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            pass
    try:
        return os.path.getmtime(fallback_path)
    except Exception:
        return time.time()


def normalize_records(data, source_path: str) -> list[dict]:
    """
    将各种 result.json 结构归一化为 record 列表：
    - dict 且包含 results(list)
    - list
    - 单 dict
    """
    records: list[dict] = []
    payload_generated_at = None
    if isinstance(data, dict):
        payload_generated_at = parse_generated_at_ts(data, source_path)

    def wrap_record(r: dict) -> dict:
        idx = r.get("account_index")
        account_index = safe_int(idx, default=0) if idx is not None else 0
        sign_status = str(r.get("sign_status") or "").strip()
        rec = {
            "account_index": account_index,
            "sign_success": truthy(r.get("sign_success")),
            "sign_status": sign_status,
            "initial_points": safe_float(r.get("initial_points"), default=0.0),
            "final_points": safe_float(r.get("final_points"), default=0.0),
            "points_reward": safe_float(r.get("points_reward"), default=0.0),
            "invoice_money": record_invoice_money(r),
            "consumption_amount": record_invoice_money(r),
            "has_reward": truthy(r.get("has_reward")),
            "password_error": truthy(r.get("password_error")),
            "retry_count": safe_int(r.get("retry_count"), default=0),
            "is_final_retry": truthy(r.get("is_final_retry")),
            "activity_records": r.get("activity_records") or {"lottery": []},
            "_source": source_path,
            "_generated_at": payload_generated_at or parse_generated_at_ts({}, source_path),
        }
        return rec

    if isinstance(data, dict) and isinstance(data.get("results"), list):
        for r in data["results"]:
            if isinstance(r, dict):
                records.append(wrap_record(r))
        return records

    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict):
                records.append(wrap_record(r))
        return records

    if isinstance(data, dict):
        records.append(wrap_record(data))
        return records

    return records


def find_json_files(results_dir: str) -> list[str]:
    """
    兼容：
      results/**/result.json
      results/**/*.json
    """
    patterns = [
        os.path.join(results_dir, "**", "result.json"),
        os.path.join(results_dir, "**", "*.json"),
    ]
    paths: set[str] = set()
    for pat in patterns:
        for p in glob.glob(pat, recursive=True):
            if os.path.isfile(p):
                paths.add(p)
    return sorted(paths)


def load_results(results_dir: str) -> list[dict]:
    results: list[dict] = []
    for path in find_json_files(results_dir):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.extend(normalize_records(data, path))
        except Exception:
            continue
    return results


def is_success_record(r: dict) -> bool:
    """
    兼容极端情况下 sign_success 没写对，但 sign_status 表示抽奖成功。
    """
    if truthy(r.get("sign_success")):
        return True
    status = str(r.get("sign_status") or "")
    return any(k in status for k in ("抽奖成功", "抽奖完成"))


def pick_better(old: dict, new: dict) -> dict:
    """
    同一个 account_index 可能出现多份结果：选择更“可信”的。
    优先级：
      成功 > 失败
      状态更具体 > 空/未知
      retry_count 更大
      generated_at 更新
    """
    def score(x: dict):
        success = 1 if is_success_record(x) else 0
        status = str(x.get("sign_status") or "").strip()
        status_quality = 1 if status and status != "未知" else 0
        retry = safe_int(x.get("retry_count"), 0)
        ts = float(x.get("_generated_at") or 0.0)
        return (success, status_quality, retry, ts)

    return new if score(new) >= score(old) else old


def map_reason(res: dict) -> str:
    if truthy(res.get("password_error")):
        return "密码错误❌"
    status = str(res.get("sign_status", "") or "")
    if "Token" in status or "token" in status:
        return "Token获取失败❗"
    if "未进入首页" in status:
        return "未进入首页❗"
    if "登录失败" in status:
        return "登录失败❗"
    if "抽奖失败" in status:
        return "抽奖失败❗"
    if "执行异常" in status:
        return "执行异常❗"
    if not status or status == "未知":
        return "未知情况❓"
    # 给出更直观的原因（把原始状态带出来）
    return f"{status}❗"


def build_message(group: str, total: int, results_by_index: dict[int, dict], account_labels: list[str]):
    date_str = datetime.now().strftime("%Y年%m月%d日")
    title = f"{date_str} 📊 任务总结"
    if group:
        title += f" - {group}"

    success_list = []
    failed_list = []

    success_count = 0
    total_lottery_results = 0
    prize_account_count = 0
    distribution: dict[str, int] = {}

    def label_for(i: int) -> str:
        if 1 <= i <= len(account_labels):
            return account_labels[i - 1]
        return f"账号{i}"

    def lottery_titles(record: dict) -> list[str]:
        rows = (record.get("activity_records") or {}).get("lottery") or []
        titles = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("prizeTitle") or "").strip()
            if title:
                titles.append(title)
        return titles

    for i in range(1, total + 1):
        label = label_for(i)
        r = results_by_index.get(i)

        if not r:
            failed_list.append((label, "缺少结果文件"))
            continue

        status = str(r.get("sign_status") or "未知")
        titles = lottery_titles(r)
        if titles:
            prize_account_count += 1
            total_lottery_results += len(titles)
            for title_text in titles:
                distribution[title_text] = distribution.get(title_text, 0) + 1

        if is_success_record(r):
            success_count += 1
            prize_text = "、".join(titles) if titles else "未解析到奖品"
            success_list.append((label, status, prize_text))
        else:
            failed_list.append((label, map_reason(r)))

    success_rate = (success_count / total * 100) if total else 0.0

    lines = []
    lines.append(title)
    lines.append("=" * 50)
    lines.append("")

    # -------- 先列异常 --------


    # -------- 再列成功 --------
    if success_list:
        lines.append("✅ 正常账户")
        for label, status, prize_text in success_list:
            lines.append(f"{label}：{status}（{prize_text}）")
        lines.append("")
    if failed_list:
        lines.append("❌ 出现异常的账户")
        for label, reason in failed_list:
            lines.append(f"{label}：{reason}")
        lines.append("")
    else:
        lines.append("✅ 无异常账户")
        lines.append("")
    # -------- 最后统计 --------
    lines.append("📈 总体统计")
    lines.append(f"  ├── 总账号数: {total}")
    lines.append(f"  ├── 抽奖成功: {success_count}/{total}")
    lines.append(f"  ├── 有中奖记录账号: {prize_account_count}")
    lines.append(f"  ├── 抽奖结果总数: {total_lottery_results}")
    lines.append(f"  └── 抽奖成功率: {success_rate:.1f}%")
    if distribution:
        lines.append("")
        lines.append("🎁 奖品分布")
        for title_text, count in sorted(distribution.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  ├── {title_text}: {count}")
    lines.append("")
    lines.append("=" * 50)

    return "\n".join(lines), len(failed_list), total_lottery_results, prize_account_count > 0

def split_text(text: str, limit: int = 3900) -> list[str]:
    """
    Telegram 单条 message 有长度上限，超长自动分段。
    这里按行拼接，尽量保持可读性。
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    cur = ""
    for line in text.splitlines(True):
        if len(cur) + len(line) > limit and cur:
            parts.append(cur)
            cur = ""
        if len(line) > limit:
            # 单行超长，硬切
            if cur:
                parts.append(cur)
                cur = ""
            for i in range(0, len(line), limit):
                parts.append(line[i:i + limit])
            continue
        cur += line
    if cur:
        parts.append(cur)
    return parts


def telegram_credentials() -> tuple[str, str]:
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TG_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
        or os.getenv("TG_TOKEN")
        or ""
    ).strip()
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TG_CHAT_ID")
        or os.getenv("TELEGRAM_TO")
        or os.getenv("TG_TO")
        or ""
    ).strip()
    return token, chat_id


def send_telegram(text: str) -> bool:
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for part in split_text(text):
        try:
            resp = requests.post(url, json={"chat_id": chat_id, "text": part}, timeout=12)
            if resp.status_code != 200:
                ok = False
        except Exception:
            ok = False
    return ok


def send_email(subject: str, text: str) -> bool:
    host = os.getenv("SMTP_HOST")
    port = safe_int(os.getenv("SMTP_PORT", "465"), 465)
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    to_addr = os.getenv("SMTP_TO")
    from_addr = os.getenv("SMTP_FROM") or user

    if not host or not user or not password or not to_addr or not from_addr:
        return False

    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = from_addr
    msg["To"] = to_addr

    use_ssl = truthy(os.getenv("SMTP_USE_SSL", os.getenv("SMTP_SSL", "true")))

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=12)
        else:
            server = smtplib.SMTP(host, port, timeout=12)
            server.starttls(context=ssl.create_default_context())

        server.login(user, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
        server.quit()
        return True
    except Exception:
        return False


def parse_channels() -> list[str]:
    raw = (os.getenv("NOTIFY_CHANNELS") or "").strip()
    if raw:
        return [c.strip().lower() for c in raw.split(",") if c.strip()]
    # 自动推断
    channels = []
    token, chat_id = telegram_credentials()
    if token and chat_id:
        channels.append("telegram")
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_TO"):
        channels.append("email")
    return channels


def main():
    results_dir = sys.argv[1] if len(sys.argv) > 1 else "results"

    group = (os.getenv("GROUP_NAME") or os.getenv("BATCH_NAME") or "").strip()

    account_labels = load_accounts_from_env()
    env_expected = os.getenv("EXPECTED_TOTAL") or os.getenv("TOTAL_ACCOUNTS") or "0"
    expected_total = len(account_labels) if account_labels else safe_int(env_expected, 0)

    results = load_results(results_dir)
    # debug 信息：让你能一眼看出是不是“压根没下载到结果”
    print(f"[aggregate] found_records={len(results)} dir={results_dir}")

    results_by_index: dict[int, dict] = {}
    for r in results:
        idx = safe_int(r.get("account_index"), 0)
        if idx <= 0:
            continue
        if idx in results_by_index:
            results_by_index[idx] = pick_better(results_by_index[idx], r)
        else:
            results_by_index[idx] = r

    if expected_total <= 0:
        expected_total = max(results_by_index.keys()) if results_by_index else 0

    message, failed_count, total_lottery_results, has_reward = build_message(
        group=group,
        total=expected_total,
        results_by_index=results_by_index,
        account_labels=account_labels,
    )

    channels = parse_channels()
    sent = False

    # 标题（email 用）
    date_str = datetime.now().strftime("%Y年%m月%d日")
    subject = f"{date_str} 任务总结"
    if group:
        subject += f" - {group}"

    if "telegram" in channels:
        sent = send_telegram(message) or sent
    if "email" in channels or "smtp" in channels:
        sent = send_email(subject, message) or sent

    print(
        f"[summary] total={expected_total} failed={failed_count} "
        f"lottery_results={total_lottery_results} has_prize_record={'yes' if has_reward else 'no'} "
        f"sent={'yes' if sent else 'no'}"
    )

    # 可选：让汇总 job 根据失败数退出 1（默认不开）
    if truthy(os.getenv("FAIL_ON_FAILURE", "false")) and failed_count > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
