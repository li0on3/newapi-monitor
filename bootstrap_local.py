from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import pathlib
import urllib.error
import urllib.request


def request(opener, base_url, path, method="GET", payload=None, headers=None):
    body = None
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with opener.open(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {detail}") from error


def update_env(path: pathlib.Path, updates: dict[str, str]):
    existing = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            existing[key] = value
    existing.update(updates)
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in existing.items()) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--env-file", default=str(pathlib.Path(__file__).with_name(".env.local")))
    args = parser.parse_args()

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    setup = request(opener, args.base_url, "/api/setup")
    if not bool((setup.get("data") or {}).get("status")):
        result = request(
            opener,
            args.base_url,
            "/api/setup",
            method="POST",
            payload={
                "username": args.username,
                "password": args.password,
                "confirmPassword": args.password,
                "SelfUseModeEnabled": True,
                "DemoSiteEnabled": False,
            },
        )
        if not result.get("success"):
            raise RuntimeError(f"setup failed: {result}")

    login = request(
        opener,
        args.base_url,
        "/api/user/login",
        method="POST",
        payload={"username": args.username, "password": args.password},
    )
    if not login.get("success"):
        raise RuntimeError(f"login failed: {login}")
    user_id = int((login.get("data") or {}).get("id") or 0)
    if user_id <= 0:
        raise RuntimeError(f"invalid login response: {login}")

    session_headers = {"New-Api-User": str(user_id)}
    generated = request(opener, args.base_url, "/api/user/token", headers=session_headers)
    if not generated.get("success"):
        raise RuntimeError(f"management token generation failed: {generated}")
    access_token = str(generated.get("data") or "")
    management_headers = {
        "Authorization": f"Bearer {access_token}",
        "New-Api-User": str(user_id),
    }

    channels = request(opener, args.base_url, "/api/channel/?page=1&page_size=100", headers=management_headers)
    channel_items = ((channels.get("data") or {}).get("items") or [])
    if not any(item.get("name") == "local-mock-channel" for item in channel_items):
        added = request(
            opener,
            args.base_url,
            "/api/channel/",
            method="POST",
            headers=management_headers,
            payload={
                "mode": "single",
                "channel": {
                    "type": 1,
                    "key": "sk-local-mock",
                    "name": "local-mock-channel",
                    "status": 1,
                    "weight": 1,
                    "models": "gpt-3.5-turbo",
                    "group": "default",
                    "base_url": "http://mock-upstream:8080",
                    "auto_ban": 0,
                    "test_model": "gpt-3.5-turbo",
                },
            },
        )
        if not added.get("success"):
            raise RuntimeError(f"channel creation failed: {added}")

    tokens = request(opener, args.base_url, "/api/token/?page=1&page_size=100", headers=management_headers)
    token_items = ((tokens.get("data") or {}).get("items") or [])
    relay = next((item for item in token_items if item.get("name") == "local-monitor-relay"), None)
    if relay is None:
        created = request(
            opener,
            args.base_url,
            "/api/token/",
            method="POST",
            headers=management_headers,
            payload={
                "name": "local-monitor-relay",
                "expired_time": -1,
                "unlimited_quota": True,
                "model_limits_enabled": False,
                "model_limits": "",
                "group": "default",
            },
        )
        if not created.get("success"):
            raise RuntimeError(f"relay token creation failed: {created}")
        tokens = request(opener, args.base_url, "/api/token/?page=1&page_size=100", headers=management_headers)
        token_items = ((tokens.get("data") or {}).get("items") or [])
        relay = next((item for item in token_items if item.get("name") == "local-monitor-relay"), None)
    if relay is None:
        raise RuntimeError("relay token not found after creation")

    token_key_result = request(
        opener,
        args.base_url,
        f"/api/token/{int(relay['id'])}/key",
        method="POST",
        headers=management_headers,
    )
    relay_key = str(((token_key_result.get("data") or {}).get("key")) or "")
    if not relay_key:
        raise RuntimeError(f"relay key retrieval failed: {token_key_result}")

    env_path = pathlib.Path(args.env_file)
    update_env(
        env_path,
        {
            "NEW_API_ACCESS_TOKEN": access_token,
            "NEW_API_USER_ID": str(user_id),
            "RELAY_API_TOKEN": relay_key,
        },
    )
    print(json.dumps({"user_id": user_id, "channel": "local-mock-channel", "env_file": str(env_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
