from __future__ import annotations

import hashlib
import hmac
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class SetupError(RuntimeError):
    pass


def hash_setup_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_setup_token(token: str, expected_hash: str) -> bool:
    if not token or not expected_hash:
        return False
    return hmac.compare_digest(hash_setup_token(token), expected_hash.strip().lower())


class NewAPIProvisioner:
    def __init__(self, opener: Any | None = None, timeout_seconds: int = 30):
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        base_url: str,
        path: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:500]
            raise SetupError(f"New API returned HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SetupError(f"New API connection failed: {error}") from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SetupError("New API returned invalid JSON") from error
        if not isinstance(result, dict):
            raise SetupError("New API returned an invalid response")
        if result.get("success") is False:
            raise SetupError(str(result.get("message") or "New API request failed")[:500])
        return result

    def provision(self, base_url: str, username: str, password: str) -> dict[str, Any]:
        normalized_url = base_url.strip().rstrip("/")
        parsed = urllib.parse.urlsplit(normalized_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SetupError("New API address must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SetupError("New API address must not contain credentials, query or fragment")

        login = self._request(
            normalized_url,
            "/api/user/login",
            method="POST",
            payload={"username": username, "password": password},
        )
        user_id = int((login.get("data") or {}).get("id") or 0)
        if user_id <= 0:
            raise SetupError("New API login response did not include a valid user ID")
        session_headers = {"New-Api-User": str(user_id)}
        management = self._request(
            normalized_url,
            "/api/user/token",
            headers=session_headers,
        )
        access_token = str(management.get("data") or "").strip()
        if not access_token:
            raise SetupError("New API did not return a management access token")
        management_headers = {
            "Authorization": f"Bearer {access_token}",
            "New-Api-User": str(user_id),
        }

        tokens = self._request(
            normalized_url,
            "/api/token/?page=1&page_size=100",
            headers=management_headers,
        )
        items = ((tokens.get("data") or {}).get("items") or [])
        probe = next((item for item in items if item.get("name") == "newapi-monitor-probe"), None)
        if probe is None:
            self._request(
                normalized_url,
                "/api/token/",
                method="POST",
                headers=management_headers,
                payload={
                    "name": "newapi-monitor-probe",
                    "expired_time": -1,
                    "unlimited_quota": True,
                    "model_limits_enabled": False,
                    "model_limits": "",
                    "group": "default",
                },
            )
            tokens = self._request(
                normalized_url,
                "/api/token/?page=1&page_size=100",
                headers=management_headers,
            )
            items = ((tokens.get("data") or {}).get("items") or [])
            probe = next((item for item in items if item.get("name") == "newapi-monitor-probe"), None)
        if probe is None or int(probe.get("id") or 0) <= 0:
            raise SetupError("Probe token was not found after creation")
        key_result = self._request(
            normalized_url,
            f"/api/token/{int(probe['id'])}/key",
            method="POST",
            headers=management_headers,
        )
        relay_token = str(((key_result.get("data") or {}).get("key")) or "").strip()
        if not relay_token:
            raise SetupError("New API did not return the probe token key")
        return {
            "new_api_base_url": normalized_url,
            "new_api_user_id": user_id,
            "new_api_access_token": access_token,
            "relay_api_token": relay_token,
        }

    def validate_management_token(self, base_url: str, user_id: int, access_token: str) -> None:
        self._request(
            base_url.strip().rstrip("/"),
            "/api/channel/?page=1&page_size=1",
            headers={
                "Authorization": f"Bearer {access_token}",
                "New-Api-User": str(user_id),
            },
        )
