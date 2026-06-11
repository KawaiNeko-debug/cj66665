import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime


RISK_CONTROL_MESSAGE = os.getenv("RISK_CONTROL_MESSAGE", "抽奖失败，疑似触发活动限制").strip()
RISK_PAUSE_SECONDS = max(0, int(os.getenv("RISK_PAUSE_SECONDS", "600") or 600))
MAX_RISK_PAUSES = max(0, int(os.getenv("MAX_RISK_PAUSES", "2") or 2))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(BASE_DIR, "script.py")


def log(message: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def truthy(value) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


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


def load_accounts() -> list[dict]:
    raw = os.getenv("ACCOUNTS", "") or ""
    group_name = (os.getenv("GROUP_NAME") or os.getenv("BATCH_NAME") or "").strip()
    group_number = safe_int(os.getenv("GROUP_NUMBER"), 0)
    accounts = []
    for index, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or "," not in line:
            continue
        username, password = line.split(",", 1)
        accounts.append(
            {
                "account_index": index,
                "username": username.strip(),
                "password": password.strip(),
                "group_name": group_name,
                "group_number": group_number,
                "group_position": f"{group_number}组账号{index}" if group_number > 0 else f"账号{index}",
            }
        )
    return accounts


def shuffle_accounts(accounts: list[dict]) -> list[dict]:
    shuffled = [dict(account) for account in accounts]
    seed = os.getenv("SIGN_RANDOM_SEED")
    if seed:
        random.Random(seed).shuffle(shuffled)
        log(f"使用固定随机种子打乱账号顺序: {seed}")
    else:
        random.shuffle(shuffled)
        log("已随机打乱账号执行顺序")
    for execution_order, account in enumerate(shuffled, start=1):
        account["execution_order"] = execution_order
    log("本次执行顺序: " + ", ".join(str(account["account_index"]) for account in shuffled))
    return shuffled


def build_placeholder_result(account: dict, status="抽奖异常", reason="工作流未生成 result.json") -> dict:
    return {
        "account_index": account["account_index"],
        "execution_order": account.get("execution_order", 0),
        "username": account["username"],
        "masked_username": account["username"][:-4] + "****" if len(account["username"]) > 4 else "*" * len(account["username"]),
        "group_name": account.get("group_name", ""),
        "group_number": account.get("group_number", 0),
        "group_position": account.get("group_position", ""),
        "sign_success": False,
        "sign_status": status,
        "initial_points": 0.0,
        "final_points": 0.0,
        "points_reward": 0.0,
        "has_reward": False,
        "password_error": False,
        "risk_controlled": False,
<<<<<<< HEAD
        "banned_account": False,
        "next_day_success": False,
        "task_start_date": os.getenv("SIGN_TASK_START_DATE", ""),
        "sign_completed_at": "",
        "activity_records": {"lottery": []},
=======
>>>>>>> parent of 33341dd (1)
        "retry_count": 0,
        "is_final_retry": False,
        "detail_reason": reason,
        "sign_time": "",
        "sign_ip": "",
        "pause_applied": False,
    }


def normalize_result(account: dict, result_path: str) -> dict:
    normalized = build_placeholder_result(account)
    if not os.path.exists(result_path):
        return normalized

    try:
        with open(result_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception as exc:
        normalized["detail_reason"] = f"读取临时结果失败: {exc}"
        return normalized

    results = payload.get("results") if isinstance(payload, dict) else None
    raw = results[0] if isinstance(results, list) and results else payload if isinstance(payload, dict) else {}
    if not isinstance(raw, dict):
        return normalized

    normalized.update(
        {
            "sign_success": truthy(raw.get("sign_success")),
            "sign_status": str(raw.get("sign_status") or normalized["sign_status"]).strip(),
            "initial_points": safe_float(raw.get("initial_points"), 0.0),
            "final_points": safe_float(raw.get("final_points"), 0.0),
            "points_reward": safe_float(raw.get("points_reward"), 0.0),
            "has_reward": truthy(raw.get("has_reward")),
            "password_error": truthy(raw.get("password_error")),
            "risk_controlled": truthy(raw.get("risk_controlled")),
<<<<<<< HEAD
            "banned_account": truthy(raw.get("banned_account")),
            "next_day_success": truthy(raw.get("next_day_success")),
            "task_start_date": str(raw.get("task_start_date") or "").strip(),
            "sign_completed_at": str(raw.get("sign_completed_at") or "").strip(),
            "activity_records": raw.get("activity_records") or {"lottery": []},
=======
>>>>>>> parent of 33341dd (1)
            "retry_count": safe_int(raw.get("retry_count"), 0),
            "is_final_retry": truthy(raw.get("is_final_retry")),
            "detail_reason": str(raw.get("detail_reason") or "").strip(),
            "sign_time": str(raw.get("sign_time") or "").strip(),
            "sign_ip": str(raw.get("sign_ip") or "").strip(),
        }
    )

    if not normalized["detail_reason"]:
        if normalized["password_error"]:
            normalized["detail_reason"] = "密码错误"
        elif normalized["risk_controlled"]:
            normalized["detail_reason"] = RISK_CONTROL_MESSAGE
        elif normalized["sign_status"]:
            normalized["detail_reason"] = normalized["sign_status"]

    if RISK_CONTROL_MESSAGE and RISK_CONTROL_MESSAGE in normalized["detail_reason"]:
        normalized["risk_controlled"] = True
        normalized["sign_status"] = "抽奖风控"

    return normalized


class PauseController:
    def __init__(self, pause_seconds: int, max_pauses: int):
        self.pause_seconds = pause_seconds
        self.max_pauses = max_pauses
        self.pause_count = 0
        self.cooldown_until = 0.0

    def wait_if_needed(self, stage: str = ""):
        remaining = self.cooldown_until - time.time()
        if remaining <= 0:
            return
        detail = f"（{stage}）" if stage else ""
        log(f"风控暂停中，还需等待 {int(remaining)} 秒{detail}")
        time.sleep(remaining)
        self.cooldown_until = 0.0

    def trigger(self, account: dict, reason: str) -> bool:
        if self.pause_seconds <= 0 or self.max_pauses <= 0:
            return False
        if self.pause_count >= self.max_pauses:
            log(f"{account['group_position']} 命中风控，但本次已达到最大暂停次数 {self.max_pauses}")
            return False
        self.pause_count += 1
        self.cooldown_until = max(self.cooldown_until, time.time() + self.pause_seconds)
        log(
            f"{account['group_position']} 命中风控，暂停后续账号 {self.pause_seconds} 秒 "
            f"（第 {self.pause_count}/{self.max_pauses} 次）。原因：{reason or RISK_CONTROL_MESSAGE}"
        )
        return True


def run_single_account(account: dict, temp_dir: str) -> dict:
    result_path = os.path.join(temp_dir, f"result-{account['account_index']}.json")
    env = os.environ.copy()
    env["RESULT_JSON_PATH"] = result_path
    env["ACCOUNT_INDEX"] = str(account["account_index"])
    env["GROUP_NAME"] = account.get("group_name", "")
    env["GROUP_NUMBER"] = str(account.get("group_number", 0))

    command = [sys.executable, SCRIPT_PATH, account["username"], account["password"], "false"]
    completed = subprocess.run(command, cwd=os.getcwd(), env=env, check=False)
    result = normalize_result(account, result_path)
    result["subprocess_exit_code"] = completed.returncode
    return result


def write_batch_result(path: str, results: list[dict], controller: PauseController):
    sanitized_results = []
    for item in sorted(results, key=lambda row: row["account_index"]):
        sanitized_results.append(
            {
                "account_index": item["account_index"],
                "execution_order": item.get("execution_order", 0),
                "group_name": item.get("group_name", ""),
                "group_number": item.get("group_number", 0),
                "group_position": item.get("group_position", ""),
                "sign_success": item["sign_success"],
                "sign_status": item["sign_status"],
                "initial_points": item["initial_points"],
                "final_points": item["final_points"],
                "points_reward": item["points_reward"],
                "has_reward": item["has_reward"],
                "password_error": item["password_error"],
                "risk_controlled": item["risk_controlled"],
                "retry_count": item["retry_count"],
                "is_final_retry": item["is_final_retry"],
                "detail_reason": item["detail_reason"],
                "sign_time": item.get("sign_time", ""),
                "sign_ip": item.get("sign_ip", ""),
<<<<<<< HEAD
                "activity_records": item.get("activity_records") or {"lottery": []},
=======
>>>>>>> parent of 33341dd (1)
                "pause_applied": item["pause_applied"],
            }
        )
    payload = {
        "generated_at": datetime.now().isoformat(),
        "batch_name": os.getenv("BATCH_NAME", "") or os.getenv("GROUP_NAME", ""),
        "group_name": os.getenv("GROUP_NAME", "") or os.getenv("BATCH_NAME", ""),
        "group_number": safe_int(os.getenv("GROUP_NUMBER"), 0),
        "total_accounts": len(results),
        "risk_pause_seconds": controller.pause_seconds,
        "risk_pause_count": controller.pause_count,
        "results": sanitized_results,
    }
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    log(f"批次结果已写入 {path}")


def print_summary(results: list[dict], controller: PauseController):
    success_count = sum(1 for item in results if item["sign_success"])
    risk_count = sum(1 for item in results if item["risk_controlled"] and not item["sign_success"])
    failed_count = sum(1 for item in results if not item["sign_success"])
    total_reward = sum(safe_float(item["points_reward"], 0.0) for item in results)
    log("=" * 60)
    log(f"批次总账号数: {len(results)}")
<<<<<<< HEAD
    log(f"抽奖成功: {success_count}")
    log(f"账号封禁: {banned_count}")
    log(f"抽奖风控: {risk_count}")
    log(f"抽奖失败: {failed_count}")
=======
    log(f"签到成功: {success_count}")
    log(f"签到风控: {risk_count}")
    log(f"签到失败: {failed_count}")
>>>>>>> parent of 33341dd (1)
    log(f"总奖励: +{total_reward:.1f} 金豆")
    log(f"风控暂停次数: {controller.pause_count}/{controller.max_pauses}")
    log("=" * 60)


def main():
    accounts = load_accounts()
    if not accounts:
        print("未从 ACCOUNTS 环境变量读取到账号", flush=True)
        sys.exit(1)

    enable_failure_exit = truthy(os.getenv("ENABLE_FAILURE_EXIT", "false"))
    result_json_path = os.getenv("RESULT_JSON_PATH", "result.json")
    controller = PauseController(RISK_PAUSE_SECONDS, MAX_RISK_PAUSES)
    shuffled_accounts = shuffle_accounts(accounts)

    temp_dir = tempfile.mkdtemp(prefix="lottery-batch-")
    try:
        results = []
        for offset, account in enumerate(shuffled_accounts):
            controller.wait_if_needed(f"{account['group_position']} 开始前")
            result = run_single_account(account, temp_dir)
            if result["risk_controlled"]:
                result["pause_applied"] = controller.trigger(account, result.get("detail_reason", ""))
            results.append(result)
            if offset < len(shuffled_accounts) - 1:
                time.sleep(random.uniform(5, 10))

        write_batch_result(result_json_path, results, controller)
        print_summary(results, controller)

        if enable_failure_exit and any(not item["sign_success"] for item in results):
            sys.exit(1)
        sys.exit(0)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
