from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from dashboard_http import open_without_redirects


class NewAPIConsoleError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class NewAPIConsoleClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 12,
        max_response_bytes: int = 8 * 1024 * 1024,
        opener: Callable[..., Any] = open_without_redirects,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max(1, max_response_bytes)
        self.opener = opener

    def _request(
        self,
        session_cookie: str,
        user_id: int,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if not session_cookie or user_id <= 0:
            raise NewAPIConsoleError(401, "New API session is required")
        if not path.startswith("/api/") or "://" in path or ".." in path:
            raise NewAPIConsoleError(500, "invalid upstream route")
        encoded_query = urllib.parse.urlencode(
            {key: value for key, value in (query or {}).items() if value not in {None, ""}},
            doseq=True,
        )
        url = self.base_url + path + (f"?{encoded_query}" if encoded_query else "")
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Cookie": f"session={session_cookie}",
            "New-Api-User": str(user_id),
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            status = error.code if error.code in {400, 401, 403, 404, 409, 429} else 502
            raise NewAPIConsoleError(status, f"New API request failed with HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NewAPIConsoleError(502, "New API is currently unreachable") from error
        if len(raw) > self.max_response_bytes:
            raise NewAPIConsoleError(502, "New API response is too large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NewAPIConsoleError(502, "New API returned invalid JSON") from error
        if isinstance(payload, dict) and payload.get("success") is False:
            message = str(payload.get("message") or "New API rejected the request")[:500]
            raise NewAPIConsoleError(400, message)
        if isinstance(payload, dict) and "success" in payload and "data" in payload:
            return payload["data"]
        return payload

    @staticmethod
    def _number(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _positive_number(value: Any, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return number if math.isfinite(number) and number > 0 else default

    @staticmethod
    def _token(item: Any) -> dict[str, Any]:
        value = item if isinstance(item, dict) else {}
        allow_ips = value.get("allow_ips")
        return {
            "id": NewAPIConsoleClient._number(value.get("id")),
            "name": str(value.get("name") or "")[:50],
            "masked_key": str(value.get("key") or "")[:128],
            "status": NewAPIConsoleClient._number(value.get("status")),
            "created_time": NewAPIConsoleClient._number(value.get("created_time")),
            "accessed_time": NewAPIConsoleClient._number(value.get("accessed_time")),
            "expired_time": NewAPIConsoleClient._number(value.get("expired_time"), -1),
            "remain_quota": NewAPIConsoleClient._number(value.get("remain_quota")),
            "used_quota": NewAPIConsoleClient._number(value.get("used_quota")),
            "unlimited_quota": bool(value.get("unlimited_quota")),
            "model_limits_enabled": bool(value.get("model_limits_enabled")),
            "model_limits": str(value.get("model_limits") or "")[:8192],
            "allow_ips": str(allow_ips or "")[:4096],
            "group": str(value.get("group") or "")[:128],
            "cross_group_retry": bool(value.get("cross_group_retry")),
        }

    @staticmethod
    def _page(data: Any, normalizer: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        value = data if isinstance(data, dict) else {}
        items = value.get("items") if isinstance(value.get("items"), list) else []
        return {
            "page": max(1, NewAPIConsoleClient._number(value.get("page"), 1)),
            "page_size": max(1, NewAPIConsoleClient._number(value.get("page_size"), len(items) or 20)),
            "total": max(0, NewAPIConsoleClient._number(value.get("total"), len(items))),
            "items": [normalizer(item) for item in items],
        }

    def status(self, session_cookie: str, user_id: int) -> dict[str, Any]:
        data = self._request(session_cookie, user_id, "GET", "/api/status")
        value = data if isinstance(data, dict) else {}
        return {
            "version": str(value.get("version") or ""),
            "system_name": str(value.get("system_name") or "New API")[:128],
            "server_address": str(value.get("server_address") or "")[:2048],
            "docs_link": str(value.get("docs_link") or "")[:2048],
            "quota_per_unit": self._positive_number(value.get("quota_per_unit"), 500000),
            "quota_display_type": str(value.get("quota_display_type") or "USD")[:32],
        }

    def self_info(self, session_cookie: str, user_id: int) -> dict[str, Any]:
        data = self._request(session_cookie, user_id, "GET", "/api/user/self")
        value = data if isinstance(data, dict) else {}
        return {
            "id": self._number(value.get("id")),
            "username": str(value.get("username") or "")[:128],
            "display_name": str(value.get("display_name") or value.get("username") or "")[:128],
            "role": self._number(value.get("role")),
            "status": self._number(value.get("status")),
            "group": str(value.get("group") or "")[:128],
            "quota": self._number(value.get("quota")),
            "used_quota": self._number(value.get("used_quota")),
            "request_count": self._number(value.get("request_count")),
        }

    def models(self, session_cookie: str, user_id: int) -> list[str]:
        data = self._request(session_cookie, user_id, "GET", "/api/user/models")
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("items"), list):
            items = data["items"]
        elif isinstance(data, dict):
            items = [item for models in data.values() if isinstance(models, list) for item in models]
        else:
            items = []
        result: list[str] = []
        for item in items:
            name = str(item.get("id") or item.get("model_name") or item.get("name") or "") if isinstance(item, dict) else str(item)
            if name and name not in result:
                result.append(name[:256])
        return result[:2000]

    def groups(self, session_cookie: str, user_id: int) -> list[str]:
        data = self._request(session_cookie, user_id, "GET", "/api/user/self/groups")
        if isinstance(data, dict):
            values = list(data.keys())
        elif isinstance(data, list):
            values = data
        else:
            values = []
        return [str(value)[:128] for value in values if str(value).strip()][:500]

    def list_tokens(
        self,
        session_cookie: str,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
        token: str = "",
    ) -> dict[str, Any]:
        path = "/api/token/search" if keyword or token else "/api/token/"
        query = {"p": page, "page_size": page_size, "keyword": keyword, "token": token}
        return self._page(self._request(session_cookie, user_id, "GET", path, query=query), self._token)

    def get_token(self, session_cookie: str, user_id: int, token_id: int) -> dict[str, Any]:
        return self._token(self._request(session_cookie, user_id, "GET", f"/api/token/{token_id}"))

    def create_token(self, session_cookie: str, user_id: int, payload: dict[str, Any]) -> None:
        self._request(session_cookie, user_id, "POST", "/api/token/", body=payload)

    def update_token(self, session_cookie: str, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self._token(self._request(session_cookie, user_id, "PUT", "/api/token/", body=payload))

    def set_token_status(self, session_cookie: str, user_id: int, token_id: int, status: int) -> dict[str, Any]:
        data = self._request(
            session_cookie,
            user_id,
            "PUT",
            "/api/token/",
            query={"status_only": "true"},
            body={"id": token_id, "status": status},
        )
        return self._token(data)

    def delete_token(self, session_cookie: str, user_id: int, token_id: int) -> None:
        self._request(session_cookie, user_id, "DELETE", f"/api/token/{token_id}")

    def batch_delete_tokens(self, session_cookie: str, user_id: int, token_ids: list[int]) -> int:
        data = self._request(session_cookie, user_id, "POST", "/api/token/batch", body={"ids": token_ids})
        return self._number(data)

    def reveal_token(self, session_cookie: str, user_id: int, token_id: int) -> str:
        data = self._request(session_cookie, user_id, "POST", f"/api/token/{token_id}/key")
        return str(data.get("key") or "") if isinstance(data, dict) else ""

    @staticmethod
    def _series_item(item: Any) -> dict[str, Any]:
        value = item if isinstance(item, dict) else {}
        return {
            "created_at": NewAPIConsoleClient._number(value.get("created_at")),
            "username": str(value.get("username") or "")[:128],
            "model_name": str(value.get("model_name") or "")[:256],
            "count": NewAPIConsoleClient._number(value.get("count")),
            "quota": NewAPIConsoleClient._number(value.get("quota")),
            "token_used": NewAPIConsoleClient._number(value.get("token_used")),
        }

    @staticmethod
    def _flow_item(item: Any) -> dict[str, Any]:
        value = item if isinstance(item, dict) else {}
        return {
            "username": str(value.get("username") or "")[:128],
            "node_name": str(value.get("node_name") or "")[:128],
            "token_id": NewAPIConsoleClient._number(value.get("token_id")),
            "token_name": str(value.get("token_name") or "")[:128],
            "use_group": str(value.get("use_group") or "")[:128],
            "channel_id": NewAPIConsoleClient._number(value.get("channel_id")),
            "channel_name": str(value.get("channel_name") or "")[:128],
            "model_name": str(value.get("model_name") or "")[:256],
            "token_used": NewAPIConsoleClient._number(value.get("token_used")),
            "count": NewAPIConsoleClient._number(value.get("count")),
            "quota": NewAPIConsoleClient._number(value.get("quota")),
        }

    def analytics(
        self,
        session_cookie: str,
        user_id: int,
        source_role: int,
        start_timestamp: int,
        end_timestamp: int,
        username: str = "",
    ) -> dict[str, Any]:
        is_admin = source_role >= 10
        query = {
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "username": username if is_admin else "",
        }
        series_path = "/api/data/" if is_admin else "/api/data/self"
        flow_path = "/api/data/flow" if is_admin else "/api/data/flow/self"
        stat_path = "/api/log/stat" if is_admin else "/api/log/self/stat"
        series_raw = self._request(session_cookie, user_id, "GET", series_path, query=query)
        flow_raw = self._request(session_cookie, user_id, "GET", flow_path, query=query)
        stat_raw = self._request(session_cookie, user_id, "GET", stat_path, query=query)
        series = [self._series_item(item) for item in series_raw] if isinstance(series_raw, list) else []
        flow = [self._flow_item(item) for item in flow_raw] if isinstance(flow_raw, list) else []
        stat = stat_raw if isinstance(stat_raw, dict) else {}
        return {
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "scope": "global" if is_admin else "self",
            "series": series,
            "flow": flow,
            "stat": {
                "quota": self._number(stat.get("quota")),
                "rpm": self._number(stat.get("rpm")),
                "tpm": self._number(stat.get("tpm")),
            },
            "summary": {
                "requests": sum(item["count"] for item in series),
                "quota": sum(item["quota"] for item in series),
                "tokens": sum(item["token_used"] for item in series),
                "models": len({item["model_name"] for item in series if item["model_name"]}),
            },
        }

    @staticmethod
    def _log(item: Any, include_admin: bool) -> dict[str, Any]:
        value = item if isinstance(item, dict) else {}
        raw_other = value.get("other")
        if isinstance(raw_other, str):
            try:
                other = json.loads(raw_other) if raw_other else {}
            except json.JSONDecodeError:
                other = {"raw": raw_other[:4000]}
        else:
            other = dict(raw_other) if isinstance(raw_other, dict) else {}
        if not include_admin:
            other.pop("admin_info", None)
            other.pop("audit_info", None)
            other.pop("stream_status", None)
        return {
            "id": NewAPIConsoleClient._number(value.get("id")),
            "created_at": NewAPIConsoleClient._number(value.get("created_at")),
            "type": NewAPIConsoleClient._number(value.get("type")),
            "content": str(value.get("content") or "")[:4000],
            "username": str(value.get("username") or "")[:128],
            "token_name": str(value.get("token_name") or "")[:128],
            "model_name": str(value.get("model_name") or "")[:256],
            "quota": NewAPIConsoleClient._number(value.get("quota")),
            "prompt_tokens": NewAPIConsoleClient._number(value.get("prompt_tokens")),
            "completion_tokens": NewAPIConsoleClient._number(value.get("completion_tokens")),
            "use_time": NewAPIConsoleClient._number(value.get("use_time")),
            "is_stream": bool(value.get("is_stream")),
            "channel_id": NewAPIConsoleClient._number(value.get("channel")),
            "channel_name": str(value.get("channel_name") or "")[:128] if include_admin else "",
            "group": str(value.get("group") or "")[:128],
            "request_id": str(value.get("request_id") or "")[:128],
            "upstream_request_id": str(value.get("upstream_request_id") or "")[:256],
            "other": other,
        }

    def list_logs(
        self,
        session_cookie: str,
        user_id: int,
        source_role: int,
        page: int = 1,
        page_size: int = 20,
        **filters: Any,
    ) -> dict[str, Any]:
        is_admin = source_role >= 10
        query = {"p": page, "page_size": page_size}
        allowed = {
            "type", "start_timestamp", "end_timestamp", "token_name", "model_name",
            "group", "request_id", "upstream_request_id",
        }
        if is_admin:
            allowed.update({"username", "channel"})
        query.update({key: value for key, value in filters.items() if key in allowed})
        path = "/api/log/" if is_admin else "/api/log/self"
        data = self._request(session_cookie, user_id, "GET", path, query=query)
        return self._page(data, lambda item: self._log(item, is_admin))

    def log_stat(
        self,
        session_cookie: str,
        user_id: int,
        source_role: int,
        **filters: Any,
    ) -> dict[str, int]:
        is_admin = source_role >= 10
        allowed = {"type", "start_timestamp", "end_timestamp", "token_name", "model_name", "group"}
        if is_admin:
            allowed.update({"username", "channel"})
        path = "/api/log/stat" if is_admin else "/api/log/self/stat"
        data = self._request(
            session_cookie,
            user_id,
            "GET",
            path,
            query={key: value for key, value in filters.items() if key in allowed},
        )
        value = data if isinstance(data, dict) else {}
        return {
            "quota": self._number(value.get("quota")),
            "rpm": self._number(value.get("rpm")),
            "tpm": self._number(value.get("tpm")),
        }
