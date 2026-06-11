import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests


WORKFLOWS = [
    {"workflow_file": "sign-batch1.yml", "artifact_name": "batch1-result", "group_number": 1, "group_name": "1组"},
    {"workflow_file": "sign-batch2.yml", "artifact_name": "batch2-result", "group_number": 2, "group_name": "2组"},
    {"workflow_file": "sign-batch3.yml", "artifact_name": "batch3-result", "group_number": 3, "group_name": "3组"},
    {"workflow_file": "sign-batch4.yml", "artifact_name": "batch4-result", "group_number": 4, "group_name": "4组"},
]

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def api_request(url: str, token: str, params=None):
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        params=params,
        timeout=40,
    )
    response.raise_for_status()
    return response.json()


def iso_to_local_date(text: str) -> str:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")


def determine_target_date() -> str:
    hint = (os.getenv("TARGET_DATE_HINT") or "").strip()
    if hint:
        return iso_to_local_date(hint)
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def pick_run(repo: str, token: str, workflow_file: str, target_date: str):
    api_url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/runs"
    payload = api_request(api_url, token, params={"status": "completed", "per_page": 30})
    runs = payload.get("workflow_runs", [])
    for run in runs:
        source_time = run.get("created_at") or run.get("run_started_at") or run.get("updated_at")
        if source_time and iso_to_local_date(source_time) == target_date:
            return run
    return None


def download_artifact(repo: str, token: str, run_id: int, artifact_name: str, target_dir: str):
    api_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
    payload = api_request(api_url, token, params={"per_page": 100})
    for artifact in payload.get("artifacts", []):
        if artifact.get("expired"):
            continue
        if artifact.get("name") != artifact_name:
            continue
        response = requests.get(
            artifact["archive_download_url"],
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=60,
            allow_redirects=True,
        )
        response.raise_for_status()
        os.makedirs(target_dir, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
            zip_file.extractall(target_dir)
        return artifact
    return None


def main():
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "results"
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        print("缺少 GITHUB_TOKEN 或 GITHUB_REPOSITORY", flush=True)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    target_date = determine_target_date()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "batches": [],
    }

    found_any = False
    for item in WORKFLOWS:
        batch = dict(item)
        batch["found"] = False
        batch["reason"] = ""
        run = pick_run(repo, token, item["workflow_file"], target_date)
        if not run:
            batch["reason"] = "未找到当日 workflow run"
            manifest["batches"].append(batch)
            continue

        batch["run_id"] = run.get("id")
        batch["run_url"] = run.get("html_url")
        batch["conclusion"] = run.get("conclusion")
        target_dir = os.path.join(output_dir, f"group{item['group_number']}")
        artifact = download_artifact(repo, token, run["id"], item["artifact_name"], target_dir)
        if not artifact:
            batch["reason"] = "未找到结果 artifact"
            manifest["batches"].append(batch)
            continue

        batch["found"] = True
        batch["artifact_id"] = artifact.get("id")
        batch["artifact_name"] = artifact.get("name")
        batch["extract_dir"] = target_dir
        manifest["batches"].append(batch)
        found_any = True

    with open(os.path.join(output_dir, "manifest.json"), "w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    sys.exit(0 if found_any else 1)


if __name__ == "__main__":
    main()
