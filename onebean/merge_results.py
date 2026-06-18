from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime


def truthy(value) -> bool:
    if value is True:
        return True
    if value in (False, None):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def safe_int(value, default=0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def load_single_result(path: str):
    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception:
        return None
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    row["_batch_name"] = payload.get("batch_name") if isinstance(payload, dict) else ""
    row["_group_name"] = payload.get("group_name") if isinstance(payload, dict) else ""
    row["_group_number"] = payload.get("group_number") if isinstance(payload, dict) else 0
    return row


def score(row: dict):
    return (
        1 if truthy(row.get("sign_success")) else 0,
        safe_int(row.get("retry_count"), 0),
        0 if truthy(row.get("password_error")) else 1,
    )


def pick_result(initial: dict, retry: dict | None):
    if retry is None:
        return initial
    return retry if score(retry) >= score(initial) else initial


def main() -> int:
    results_dir = sys.argv[1] if len(sys.argv) > 1 else "artifacts"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "merged/result.json"
    initial_map = {}
    retry_map = {}
    for path in glob.glob(os.path.join(results_dir, "**", "result.json"), recursive=True):
        row = load_single_result(path)
        if not row:
            continue
        account_index = safe_int(row.get("account_index"), 0)
        if account_index <= 0:
            continue
        normalized_path = path.replace("\\", "/").lower()
        if "/retry-result-" in normalized_path:
            retry_map[account_index] = row
        elif "/initial-result-" in normalized_path:
            initial_map[account_index] = row
        else:
            initial_map[account_index] = row

    merged = []
    for account_index in sorted(set(initial_map) | set(retry_map)):
        merged.append(pick_result(initial_map.get(account_index, retry_map.get(account_index)), retry_map.get(account_index)))

    group_name = "onebean"
    group_number = 1
    payload = {
        "generated_at": datetime.now().isoformat(),
        "batch_name": group_name,
        "group_name": group_name,
        "group_number": group_number,
        "total_accounts": len(merged),
        "results": [],
    }
    for row in sorted(merged, key=lambda item: safe_int(item.get("account_index"), 0)):
        account_index = safe_int(row.get("account_index"), 0)
        payload["results"].append(
            {
                "account_index": account_index,
                "execution_order": safe_int(row.get("execution_order"), 0),
                "group_name": row.get("group_name") or group_name,
                "group_number": safe_int(row.get("group_number"), group_number),
                "group_position": row.get("group_position") or f"{group_number}组账号{account_index}",
                "username": row.get("username") or row.get("masked_username") or f"账号{account_index}",
                "masked_username": row.get("masked_username") or "",
                "sign_success": truthy(row.get("sign_success")),
                "sign_status": row.get("sign_status") or "",
                "has_reward": truthy(row.get("has_reward")),
                "password_error": truthy(row.get("password_error")),
                "risk_controlled": truthy(row.get("risk_controlled")),
                "retry_count": safe_int(row.get("retry_count"), 0),
                "is_final_retry": truthy(row.get("is_final_retry")),
                "detail_reason": row.get("detail_reason") or "",
                "sign_time": row.get("sign_time") or "",
                "sign_ip": row.get("sign_ip") or "",
                "activity_records": row.get("activity_records") or {"onebean": []},
            }
        )
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    print(json.dumps({"merged": len(merged), "output": output_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
