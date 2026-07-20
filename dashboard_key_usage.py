from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from threading import Lock
from typing import Any, Callable


ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}


class KeyUsageError(RuntimeError):
    pass


def role_allows_key_lookup(role: str, minimum_role: str) -> bool:
    if role not in ROLE_ORDER or minimum_role not in ROLE_ORDER:
        return False
    return ROLE_ORDER[role] >= ROLE_ORDER[minimum_role]


class SlidingWindowRateLimiter:
    def __init__(self):
        self.buckets: dict[str, deque[float]] = {}
        self.lock = Lock()

    def consume(self, key: str, attempts: int, window_seconds: int = 60) -> int:
        now = time.time()
        with self.lock:
            bucket = self.buckets.setdefault(key, deque())
            while bucket and now - bucket[0] >= window_seconds:
                bucket.popleft()
            if len(bucket) >= attempts:
                return max(1, math.ceil(window_seconds - (now - bucket[0])))
            bucket.append(now)
        return 0


class KeyUsageClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 12,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    def query(self, api_key: str, log_limit: int, quota_per_unit: float) -> dict[str, Any]:
        usage_payload = self._request("/api/usage/token/", api_key)
        logs_payload = self._request("/api/log/token", api_key)
        usage_data = usage_payload.get("data")
        logs_data = logs_payload.get("data")
        if not isinstance(usage_data, dict) or not isinstance(logs_data, list):
            raise KeyUsageError("New API 返回的数据格式不受支持")

        unit = quota_per_unit if quota_per_unit > 0 else 500_000.0
        granted = self._number(usage_data.get("total_granted"))
        used = self._number(usage_data.get("total_used"))
        available = self._number(usage_data.get("total_available"))
        unlimited = bool(usage_data.get("unlimited_quota"))
        usage = {
            "name": str(usage_data.get("name") or "未命名 Key"),
            "total_granted": granted,
            "total_used": used,
            "total_available": available,
            "total_granted_display": round(granted / unit, 6),
            "total_used_display": round(used / unit, 6),
            "total_available_display": round(available / unit, 6),
            "used_percentage": None if unlimited or granted <= 0 else round(used / granted * 100, 2),
            "unlimited_quota": unlimited,
            "expires_at": int(self._number(usage_data.get("expires_at"))),
            "model_limits_enabled": bool(usage_data.get("model_limits_enabled")),
            "model_limits": usage_data.get("model_limits") if isinstance(usage_data.get("model_limits"), dict) else {},
        }

        calls = [self._normalize_log(item, unit) for item in logs_data[: max(1, log_limit)] if isinstance(item, dict)]
        durations = sorted(float(item["use_time"]) for item in calls)
        model_counts = Counter(str(item["model_name"] or "unknown") for item in calls)
        summary = {
            "calls": len(calls),
            "prompt_tokens": sum(int(item["prompt_tokens"]) for item in calls),
            "completion_tokens": sum(int(item["completion_tokens"]) for item in calls),
            "total_tokens": sum(int(item["prompt_tokens"]) + int(item["completion_tokens"]) for item in calls),
            "quota": sum(float(item["quota"]) for item in calls),
            "quota_display": round(sum(float(item["quota"]) for item in calls) / unit, 6),
            "average_seconds": round(sum(durations) / len(durations), 3) if durations else 0,
            "p95_seconds": round(durations[min(len(durations) - 1, math.ceil(len(durations) * 0.95) - 1)], 3) if durations else 0,
            "models": [{"name": name, "calls": count} for name, count in model_counts.most_common()],
        }
        return {
            "queried_at": int(time.time()),
            "quota_per_unit": unit,
            "usage": usage,
            "summary": summary,
            "calls": calls,
        }

    def _request(self, path: str, api_key: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise KeyUsageError("Key 无效、已过期或无权读取用量") from error
            raise KeyUsageError(f"New API 查询失败（HTTP {error.code}）") from error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise KeyUsageError("暂时无法连接 New API，请稍后重试") from error
        if not isinstance(payload, dict):
            raise KeyUsageError("New API 返回的数据格式不受支持")
        if payload.get("success") is False or payload.get("code") is False:
            raise KeyUsageError("Key 无效、已过期或无权读取用量")
        return payload

    @staticmethod
    def _number(value: Any) -> float:
        try:
            result = float(value or 0)
            return result if math.isfinite(result) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _normalize_log(self, item: dict[str, Any], unit: float) -> dict[str, Any]:
        other = item.get("other")
        if isinstance(other, str):
            try:
                other = json.loads(other)
            except json.JSONDecodeError:
                other = {}
        if not isinstance(other, dict):
            other = {}
        quota = self._number(item.get("quota"))
        frt = self._number(other.get("frt"))
        return {
            "id": int(self._number(item.get("id"))),
            "created_at": int(self._number(item.get("created_at"))),
            "type": int(self._number(item.get("type"))),
            "model_name": str(item.get("model_name") or ""),
            "quota": quota,
            "quota_display": round(quota / unit, 6),
            "prompt_tokens": int(self._number(item.get("prompt_tokens"))),
            "completion_tokens": int(self._number(item.get("completion_tokens"))),
            "use_time": self._number(item.get("use_time")),
            "frt_ms": frt if frt > 0 else None,
            "is_stream": bool(item.get("is_stream")),
            "channel_id": int(self._number(item.get("channel"))),
            "request_id": str(item.get("request_id") or ""),
            "upstream_request_id": str(item.get("upstream_request_id") or ""),
            "group": str(item.get("group") or ""),
            "content": str(item.get("content") or "")[:500],
        }
