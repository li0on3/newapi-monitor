from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    pending = dict(updates)
    output: list[str] = []
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0]
        if key in pending:
            output.append(f"{key}={pending.pop(key)}")
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(output) + "\n", encoding="utf-8", newline="\n")


def request(base_url: str, path: str, headers: dict[str, str], method: str = "GET", payload: Any = None) -> dict[str, Any]:
    body = None
    request_headers = dict(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error
    if not isinstance(result, dict) or result.get("success") is False:
        raise RuntimeError(f"{method} {path}: {result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a minimal New API relay token for real channel probes")
    parser.add_argument("--env-file", default=str(Path(__file__).with_name(".env")))
    parser.add_argument("--name", default="newapi-monitor-probe")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--group", default="default")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    env = read_env(env_path)
    base_url = env.get("NEW_API_BASE_URL", "").rstrip("/")
    access_token = env.get("NEW_API_ACCESS_TOKEN", "")
    user_id = int(env.get("NEW_API_USER_ID", "0") or 0)
    if not base_url or not access_token or user_id <= 0:
        raise RuntimeError("NEW_API_BASE_URL, NEW_API_ACCESS_TOKEN and NEW_API_USER_ID are required")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "New-Api-User": str(user_id),
    }

    token_result = request(base_url, "/api/token/?p=1&page_size=100", headers)
    tokens = (token_result.get("data") or {}).get("items") or []
    token = next((item for item in tokens if str(item.get("name") or "") == args.name), None)
    created = False
    if token is None:
        request(
            base_url,
            "/api/token/",
            headers,
            method="POST",
            payload={
                "name": args.name,
                "remain_quota": 0,
                "expired_time": -1,
                "unlimited_quota": True,
                "model_limits_enabled": True,
                "model_limits": args.model,
                "allow_ips": "",
                "group": args.group,
                "cross_group_retry": False,
            },
        )
        created = True
        token_result = request(base_url, "/api/token/?p=1&page_size=100", headers)
        tokens = (token_result.get("data") or {}).get("items") or []
        token = next((item for item in tokens if str(item.get("name") or "") == args.name), None)
    if token is None:
        raise RuntimeError("probe token was not found after creation")
    token_id = int(token.get("id") or 0)
    key_result = request(base_url, f"/api/token/{token_id}/key", headers, method="POST")
    relay_key = str((key_result.get("data") or {}).get("key") or "")
    if not relay_key:
        raise RuntimeError("New API returned an empty relay token")
    update_env(env_path, {"RELAY_API_TOKEN": relay_key})
    print(
        json.dumps(
            {
                "created": created,
                "token_id": token_id,
                "name": args.name,
                "model": args.model,
                "group": args.group,
                "env_updated": str(env_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
