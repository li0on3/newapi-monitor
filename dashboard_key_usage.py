from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from threading import Lock
from typing import Any, Callable

from dashboard_http import open_without_redirects


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
        max_response_bytes: int = 4 * 1024 * 1024,
        opener: Callable[..., Any] = open_without_redirects,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max(1, max_response_bytes)
        self.opener = opener

    def query(self, api_key: str, log_limit: int, quota_per_unit: float) -> dict[str, Any]:
        usage_payload = self._request("/api/usage/token/", api_key)
        logs_payload = self._request("/api/log/token", api_key)
        usage_data = usage_payload.get("data")
        logs_data = logs_payload.get("data")
        if not isinstance(usage_data, dict) or not isinstance(logs_data, list):
            raise KeyUsageError("New API 返回的数据格式不受支持")

        if not math.isfinite(quota_per_unit) or quota_per_unit <= 0:
            raise KeyUsageError("额度换算单位无效")
        configured_unit = float(quota_per_unit)
        granted = self._required_number(usage_data.get("total_granted"), "total_granted", "额度", integer=True)
        used = self._required_number(usage_data.get("total_used"), "total_used", "额度", integer=True, minimum=0)
        available = self._required_number(usage_data.get("total_available"), "total_available", "额度", integer=True)
        unlimited = self._required_boolean(usage_data.get("unlimited_quota"), "unlimited_quota", "额度")
        model_limits = usage_data.get("model_limits")
        if not isinstance(model_limits, dict):
            raise KeyUsageError("New API 返回的额度字段无效：model_limits")
        usage = {
            "name": str(usage_data.get("name") or "未命名 Key"),
            "total_granted": granted,
            "total_used": used,
            "total_available": available,
            "used_percentage": None if unlimited or granted <= 0 else round(used / granted * 100, 2),
            "unlimited_quota": unlimited,
            "expires_at": self._required_number(
                usage_data.get("expires_at"), "expires_at", "额度", integer=True, minimum=0
            ),
            "model_limits_enabled": self._required_boolean(
                usage_data.get("model_limits_enabled"), "model_limits_enabled", "额度"
            ),
            "model_limits": model_limits,
        }

        if any(not isinstance(item, dict) for item in logs_data):
            raise KeyUsageError("New API 返回的调用数据格式不受支持")
        calls = [self._normalize_log(item, 1.0) for item in logs_data[: max(1, log_limit)]]
        status_payload = self._request("/api/status", "")
        status_data = status_payload.get("data")
        if not isinstance(status_data, dict):
            raise KeyUsageError("New API 返回的额度换算单位无效")
        unit = self._required_number(status_data.get("quota_per_unit"), "quota_per_unit", "额度换算")
        if unit <= 0:
            raise KeyUsageError("New API 返回的额度换算单位无效")
        usage.update({
            "total_granted_display": round(granted / unit, 6),
            "total_used_display": round(used / unit, 6),
            "total_available_display": round(available / unit, 6),
        })
        for item in calls:
            item["quota_display"] = round(float(item["quota"]) / unit, 6)
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
            "quota_per_unit_source": "new_api_status",
            "configured_quota_per_unit": configured_unit,
            "quota_per_unit_matches_config": math.isclose(
                float(unit), configured_unit, rel_tol=0.0, abs_tol=1e-9
            ),
            "summary_scope": "recent_calls",
            "log_limit": max(1, log_limit),
            "returned_calls": len(calls),
            "logs_may_be_truncated": len(logs_data) > len(calls) or len(calls) >= max(1, log_limit),
            "usage": usage,
            "summary": summary,
            "calls": calls,
        }

    def _request(self, path: str, api_key: str) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            self.base_url + path,
            headers=headers,
        )
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            if error.code in {401, 403}:
                raise KeyUsageError("Key 无效、已过期或无权读取用量") from error
            raise KeyUsageError(f"New API 查询失败（HTTP {error.code}）") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise KeyUsageError("暂时无法连接 New API，请稍后重试") from error
        if len(raw) > self.max_response_bytes:
            raise KeyUsageError("New API 返回的数据过大")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise KeyUsageError("New API 返回的数据格式不受支持") from error
        if not isinstance(payload, dict):
            raise KeyUsageError("New API 返回的数据格式不受支持")
        if payload.get("success") is False or payload.get("code") is False:
            raise KeyUsageError("Key 无效、已过期或无权读取用量")
        return payload

    @staticmethod
    def _required_number(
        value: Any,
        field: str,
        context: str,
        *,
        integer: bool = False,
        minimum: float | None = None,
    ) -> int | float:
        if isinstance(value, bool):
            raise KeyUsageError(f"New API 返回的{context}字段无效：{field}")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise KeyUsageError(f"New API 返回的{context}字段无效：{field}") from error
        if not math.isfinite(result) or (integer and not result.is_integer()):
            raise KeyUsageError(f"New API 返回的{context}字段无效：{field}")
        if minimum is not None and result < minimum:
            raise KeyUsageError(f"New API 返回的{context}字段无效：{field}")
        return int(result) if integer else result

    @staticmethod
    def _required_boolean(value: Any, field: str, context: str) -> bool:
        if not isinstance(value, bool):
            raise KeyUsageError(f"New API 返回的{context}字段无效：{field}")
        return value

    def _normalize_log(self, item: dict[str, Any], unit: float) -> dict[str, Any]:
        other = item.get("other")
        if isinstance(other, str):
            try:
                other = json.loads(other)
            except json.JSONDecodeError:
                other = {}
        if not isinstance(other, dict):
            other = {}
        quota = self._required_number(item.get("quota"), "quota", "调用", integer=True)
        frt = (
            self._required_number(other.get("frt"), "frt", "调用", minimum=0)
            if other.get("frt") is not None
            else 0.0
        )
        return {
            "id": self._required_number(item.get("id"), "id", "调用", integer=True, minimum=0),
            "created_at": self._required_number(
                item.get("created_at"), "created_at", "调用", integer=True, minimum=0
            ),
            "type": self._required_number(item.get("type"), "type", "调用", integer=True, minimum=0),
            "model_name": str(item.get("model_name") or ""),
            "quota": quota,
            "quota_display": round(quota / unit, 6),
            "prompt_tokens": self._required_number(
                item.get("prompt_tokens"), "prompt_tokens", "调用", integer=True, minimum=0
            ),
            "completion_tokens": self._required_number(
                item.get("completion_tokens"), "completion_tokens", "调用", integer=True, minimum=0
            ),
            "use_time": self._required_number(item.get("use_time"), "use_time", "调用", minimum=0),
            "frt_ms": frt if frt > 0 else None,
            "is_stream": self._required_boolean(item.get("is_stream"), "is_stream", "调用"),
            "channel_id": self._required_number(
                item.get("channel"), "channel", "调用", integer=True, minimum=0
            ),
            "request_id": str(item.get("request_id") or ""),
            "upstream_request_id": str(item.get("upstream_request_id") or ""),
            "group": str(item.get("group") or ""),
            "content": str(item.get("content") or "")[:500],
        }
