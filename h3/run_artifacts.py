import json
import os
import re
import sys
import time

import requests


PAUSE_PREFIX = os.getenv("PAUSE_ARTIFACT_PREFIX", "pause-signal")
RISK_PAUSE_SECONDS = max(0, int(os.getenv("RISK_PAUSE_SECONDS", "600") or 600))
MAX_RISK_PAUSES = max(0, int(os.getenv("MAX_RISK_PAUSES", "2") or 2))
NAME_RE = re.compile(rf"^{re.escape(PAUSE_PREFIX)}-(\d+)-(\d+)$")


def github_get(url: str, token: str, params=None):
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def list_artifacts() -> list[dict]:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if not token or not repo or not run_id:
        return []
    artifacts = []
    page = 1
    while True:
        payload = github_get(
            f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts",
            token,
            params={"per_page": 100, "page": page},
        )
        batch = payload.get("artifacts", [])
        artifacts.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return artifacts


def pause_artifacts() -> list[dict]:
    matched = []
    for artifact in list_artifacts():
        name = artifact.get("name") or ""
        m = NAME_RE.match(name)
        if not m:
            continue
        matched.append(
            {
                "name": name,
                "pause_until": int(m.group(1)),
                "account_index": int(m.group(2)),
            }
        )
    return matched


def write_output(key: str, value: str):
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as file:
        file.write(f"{key}={value}\n")


def command_wait():
    pauses = pause_artifacts()
    if not pauses:
        print("[pause] no signal")
        return 0
    now = int(time.time())
    pause_until = max(item["pause_until"] for item in pauses)
    remaining = pause_until - now
    print(f"[pause] found={len(pauses)} max_until={pause_until} now={now} remaining={remaining}")
    if remaining > 0:
        time.sleep(remaining)
    return 0


def command_plan_pause(result_path: str):
    write_output("should_upload", "false")
    write_output("artifact_name", "")
    if MAX_RISK_PAUSES <= 0 or RISK_PAUSE_SECONDS <= 0:
        return 0

    try:
        with open(result_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception:
        return 0

    rows = payload.get("results") if isinstance(payload, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else {}
    if not isinstance(row, dict):
        return 0
    if not row.get("risk_controlled"):
        return 0

    pauses = pause_artifacts()
    if len(pauses) >= MAX_RISK_PAUSES:
        print(f"[pause] max reached {len(pauses)}/{MAX_RISK_PAUSES}")
        return 0

    account_index = int(row.get("account_index") or os.getenv("ACCOUNT_INDEX") or 0)
    pause_until = int(time.time()) + RISK_PAUSE_SECONDS
    artifact_name = f"{PAUSE_PREFIX}-{pause_until}-{account_index}"
    write_output("should_upload", "true")
    write_output("artifact_name", artifact_name)
    write_output("pause_until", str(pause_until))
    print(f"[pause] emit {artifact_name}")
    return 0


def main():
    if len(sys.argv) < 2:
        print("usage: python run_artifacts.py [wait|plan-pause result.json]")
        return 1
    command = sys.argv[1]
    if command == "wait":
        return command_wait()
    if command == "plan-pause":
        if len(sys.argv) < 3:
            return 1
        return command_plan_pause(sys.argv[2])
    return 1


if __name__ == "__main__":
    sys.exit(main())
