from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from dashboard_http import open_without_redirects


class NewAPISessionVerifier:
    def __init__(
        self,
        base_url: Callable[[], str],
        cache_seconds: int = 30,
        timeout_seconds: int = 8,
        max_response_bytes: int = 1024 * 1024,
        opener: Callable[..., Any] = open_without_redirects,
    ):
        self.base_url = base_url
        self.cache_seconds = max(0, cache_seconds)
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max(1, max_response_bytes)
        self.opener = opener
        self.cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self.lock = threading.Lock()

    def verify(self, session_cookie: str, user_id: str) -> dict[str, Any] | None:
        if (
            not session_cookie
            or len(session_cookie) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in session_cookie)
            or not user_id.isdigit()
            or int(user_id) <= 0
        ):
            return None
        cache_key = hashlib.sha256(f"{user_id}:{session_cookie}".encode()).hexdigest()
        now = time.time()
        with self.lock:
            cached = self.cache.get(cache_key)
            if cached and cached[0] > now:
                return dict(cached[1]) if cached[1] else None
        request = urllib.request.Request(
            self.base_url().rstrip("/") + "/api/user/self",
            headers={
                "Cookie": f"session={session_cookie}",
                "New-Api-User": user_id,
                "Accept": "application/json",
            },
        )
        identity = None
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
            if len(raw) > self.max_response_bytes:
                raise ValueError("New API identity response is too large")
            payload = json.loads(raw.decode("utf-8"))
            data = payload.get("data") if isinstance(payload, dict) and payload.get("success") else None
            if (
                isinstance(data, dict)
                and int(data.get("status") or 0) == 1
                and int(data.get("id") or 0) == int(user_id)
            ):
                identity = {
                    "source": "newapi",
                    "user_id": int(data.get("id") or 0),
                    "username": str(data.get("username") or ""),
                    "display_name": str(data.get("display_name") or data.get("username") or ""),
                    "source_role": int(data.get("role") or 0),
                    "status": int(data.get("status") or 0),
                }
                if not identity["username"]:
                    identity = None
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            identity = None
        with self.lock:
            self.cache[cache_key] = (now + self.cache_seconds, identity)
            if len(self.cache) > 2048:
                self.cache = {key: value for key, value in self.cache.items() if value[0] > now}
        return dict(identity) if identity else None
