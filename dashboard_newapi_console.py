from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from dashboard_http import open_without_redirects


SELF_DATA_RANGE_SECONDS = 30 * 86400


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
            raise NewAPIConsoleError(401, "Account session is required")
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
            raise NewAPIConsoleError(status, f"Account service request failed with HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NewAPIConsoleError(502, "Account service is currently unreachable") from error
        if len(raw) > self.max_response_bytes:
            raise NewAPIConsoleError(502, "Account service response is too large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NewAPIConsoleError(502, "Account service returned invalid JSON") from error
        if isinstance(payload, dict) and payload.get("success") is False:
            message = str(payload.get("message") or "Account service rejected the request")[:500]
            message = re.sub(r"new[\s_-]*api", "account service", message, flags=re.IGNORECASE)
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
    def _required_positive_number(value: Any, field: str, context: str) -> float:
        if isinstance(value, bool):
            raise NewAPIConsoleError(502, f"Account service returned invalid {context} field: {field}")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise NewAPIConsoleError(
                502, f"Account service returned invalid {context} field: {field}"
            ) from error
        if not math.isfinite(number) or number <= 0:
            raise NewAPIConsoleError(502, f"Account service returned invalid {context} field: {field}")
        return number

    @staticmethod
    def _required_integer(
        value: Any,
        field: str,
        context: str,
        minimum: int | None = 0,
    ) -> int:
        if isinstance(value, bool):
            raise NewAPIConsoleError(502, f"Account service returned invalid {context} field: {field}")
        if isinstance(value, int):
            number = value
        elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
            number = int(value)
        elif isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            number = int(value.strip())
        else:
            raise NewAPIConsoleError(502, f"Account service returned invalid {context} field: {field}")
        if minimum is not None and number < minimum:
            raise NewAPIConsoleError(502, f"Account service returned invalid {context} field: {field}")
        return number

    @staticmethod
    def _required_boolean(value: Any, field: str, context: str) -> bool:
        if not isinstance(value, bool):
            raise NewAPIConsoleError(502, f"Account service returned invalid {context} field: {field}")
        return value

    @staticmethod
    def _analytics_integer(value: Any, field: str) -> int:
        return NewAPIConsoleClient._required_integer(value, field, "analytics")

    @staticmethod
    def _token(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise NewAPIConsoleError(502, "Account service returned invalid token data")
        value = item
        allow_ips = value.get("allow_ips")
        return {
            "id": NewAPIConsoleClient._required_integer(value.get("id"), "id", "token", minimum=1),
            "name": str(value.get("name") or "")[:50],
            "masked_key": str(value.get("key") or "")[:128],
            "status": NewAPIConsoleClient._required_integer(value.get("status"), "status", "token"),
            "created_time": NewAPIConsoleClient._required_integer(
                value.get("created_time"), "created_time", "token"
            ),
            "accessed_time": NewAPIConsoleClient._required_integer(
                value.get("accessed_time"), "accessed_time", "token"
            ),
            "expired_time": NewAPIConsoleClient._required_integer(
                value.get("expired_time"), "expired_time", "token", minimum=-1
            ),
            "remain_quota": NewAPIConsoleClient._required_integer(
                value.get("remain_quota"), "remain_quota", "token", minimum=None
            ),
            "used_quota": NewAPIConsoleClient._required_integer(
                value.get("used_quota"), "used_quota", "token"
            ),
            "unlimited_quota": NewAPIConsoleClient._required_boolean(
                value.get("unlimited_quota"), "unlimited_quota", "token"
            ),
            "model_limits_enabled": NewAPIConsoleClient._required_boolean(
                value.get("model_limits_enabled"), "model_limits_enabled", "token"
            ),
            "model_limits": str(value.get("model_limits") or "")[:8192],
            "allow_ips": str(allow_ips or "")[:4096],
            "group": str(value.get("group") or "")[:128],
            "cross_group_retry": NewAPIConsoleClient._required_boolean(
                value.get("cross_group_retry"), "cross_group_retry", "token"
            ),
        }

    @staticmethod
    def _page(
        data: Any,
        normalizer: Callable[[Any], dict[str, Any]],
        *,
        expected_page: int | None = None,
        expected_page_size: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise NewAPIConsoleError(502, "Account service returned invalid pagination data")
        value = data
        items = value["items"]
        if any(not isinstance(item, dict) for item in items):
            raise NewAPIConsoleError(502, "Account service returned invalid pagination data")
        page = NewAPIConsoleClient._required_integer(value.get("page"), "page", "pagination")
        page_size = NewAPIConsoleClient._required_integer(value.get("page_size"), "page_size", "pagination")
        total = NewAPIConsoleClient._required_integer(value.get("total"), "total", "pagination")
        if (
            page < 1
            or page_size < 1
            or len(items) > page_size
            or total < len(items)
            or (items and total < (page - 1) * page_size + len(items))
            or (not items and total > (page - 1) * page_size)
            or (expected_page is not None and page != expected_page)
            or (expected_page_size is not None and page_size != expected_page_size)
        ):
            raise NewAPIConsoleError(502, "Account service returned invalid pagination data")
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": [normalizer(item) for item in items],
        }

    def status(self, session_cookie: str, user_id: int) -> dict[str, Any]:
        data = self._request(session_cookie, user_id, "GET", "/api/status")
        if not isinstance(data, dict):
            raise NewAPIConsoleError(502, "Account service returned invalid status data")
        value = data
        return {
            "version": str(value.get("version") or ""),
            "system_name": str(value.get("system_name") or "New API")[:128],
            "server_address": str(value.get("server_address") or "")[:2048],
            "docs_link": str(value.get("docs_link") or "")[:2048],
            "quota_per_unit": self._required_positive_number(
                value.get("quota_per_unit"), "quota_per_unit", "status"
            ),
            "quota_display_type": str(value.get("quota_display_type") or "USD")[:32],
        }

    def self_info(self, session_cookie: str, user_id: int) -> dict[str, Any]:
        data = self._request(session_cookie, user_id, "GET", "/api/user/self")
        if not isinstance(data, dict):
            raise NewAPIConsoleError(502, "Account service returned invalid account data")
        value = data
        account_id = self._required_integer(value.get("id"), "id", "account")
        if account_id != user_id:
            raise NewAPIConsoleError(502, "Account service returned account identity mismatch")
        return {
            "id": account_id,
            "username": str(value.get("username") or "")[:128],
            "display_name": str(value.get("display_name") or value.get("username") or "")[:128],
            "role": self._required_integer(value.get("role"), "role", "account"),
            "status": self._required_integer(value.get("status"), "status", "account"),
            "group": str(value.get("group") or "")[:128],
            "quota": self._required_integer(value.get("quota"), "quota", "account"),
            "used_quota": self._required_integer(value.get("used_quota"), "used_quota", "account"),
            "request_count": self._required_integer(value.get("request_count"), "request_count", "account"),
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
            raise NewAPIConsoleError(502, "Account service returned invalid model catalog")
        result: list[str] = []
        for item in items:
            if isinstance(item, dict):
                name = str(item.get("id") or item.get("model_name") or item.get("name") or "").strip()
            elif isinstance(item, str):
                name = item.strip()
            else:
                raise NewAPIConsoleError(502, "Account service returned invalid model catalog")
            if not name:
                raise NewAPIConsoleError(502, "Account service returned invalid model catalog")
            if name not in result:
                result.append(name[:256])
        return result

    def groups(self, session_cookie: str, user_id: int) -> list[str]:
        data = self._request(session_cookie, user_id, "GET", "/api/user/self/groups")
        if isinstance(data, dict):
            values = list(data.keys())
        elif isinstance(data, list):
            values = data
        else:
            raise NewAPIConsoleError(502, "Account service returned invalid group catalog")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise NewAPIConsoleError(502, "Account service returned invalid group catalog")
        return [value.strip()[:128] for value in values]

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
        return self._page(
            self._request(session_cookie, user_id, "GET", path, query=query),
            self._token,
            expected_page=page,
            expected_page_size=page_size,
        )

    def list_all_tokens(
        self,
        session_cookie: str,
        user_id: int,
        page_size: int = 100,
        max_items: int = 2000,
    ) -> list[dict[str, Any]]:
        page_size = max(1, min(int(page_size), 100))
        max_items = max(1, min(int(max_items), 10000))
        page = 1
        result: list[dict[str, Any]] = []
        seen: set[int] = set()
        expected_total: int | None = None
        while True:
            current = self.list_tokens(session_cookie, user_id, page=page, page_size=page_size)
            total = int(current["total"])
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise NewAPIConsoleError(502, "New API token pagination changed during the request")
            if total > max_items:
                raise NewAPIConsoleError(400, f"too many API keys; maximum supported is {max_items}")
            items = current["items"]
            result_size = len(result)
            for item in items:
                token_id = int(item.get("id") or 0)
                if token_id in seen:
                    raise NewAPIConsoleError(502, "New API token pagination is inconsistent")
                seen.add(token_id)
                result.append(item)
            if len(result) == total:
                return result
            if not items or len(result) <= result_size or len(result) > total:
                raise NewAPIConsoleError(502, "New API token pagination is inconsistent")
            page += 1

    def self_flow(
        self,
        session_cookie: str,
        user_id: int,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[dict[str, Any]]:
        effective_start, _ = self._self_range_start(
            session_cookie,
            user_id,
            start_timestamp,
            end_timestamp,
        )
        if effective_start > end_timestamp:
            return []
        data = self._request_self_range(
            session_cookie,
            user_id,
            "/api/data/flow/self",
            effective_start,
            end_timestamp,
        )
        return [self._flow_item(item) for item in data]

    @staticmethod
    def _range_chunks(start_timestamp: int, end_timestamp: int) -> list[tuple[int, int]]:
        chunks: list[tuple[int, int]] = []
        cursor = max(1, int(start_timestamp))
        end = int(end_timestamp)
        while cursor <= end:
            chunk_end = min(end, cursor + SELF_DATA_RANGE_SECONDS)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + 1
        return chunks

    def _request_self_range(
        self,
        session_cookie: str,
        user_id: int,
        path: str,
        start_timestamp: int,
        end_timestamp: int,
    ) -> list[Any]:
        result: list[Any] = []
        for chunk_start, chunk_end in self._range_chunks(start_timestamp, end_timestamp):
            data = self._request(
                session_cookie,
                user_id,
                "GET",
                path,
                query={"start_timestamp": chunk_start, "end_timestamp": chunk_end},
            )
            if not isinstance(data, list):
                raise NewAPIConsoleError(502, "Account service returned invalid analytics data")
            result.extend(data)
        return result

    def _self_range_start(
        self,
        session_cookie: str,
        user_id: int,
        start_timestamp: int,
        end_timestamp: int,
    ) -> tuple[int, dict[str, Any] | None]:
        if end_timestamp - start_timestamp <= SELF_DATA_RANGE_SECONDS:
            return start_timestamp, None
        query = {
            "type": 2,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "p": 1,
            "page_size": 1,
        }
        first_page_raw = self._request(session_cookie, user_id, "GET", "/api/log/self", query=query)
        first_page = self._page(
            first_page_raw,
            lambda item: item,
            expected_page=1,
            expected_page_size=1,
        )
        total = first_page["total"]
        items = first_page["items"]
        if total == 0:
            return end_timestamp + 1, first_page
        oldest_items = items
        if total > 1:
            oldest_page_raw = self._request(
                session_cookie,
                user_id,
                "GET",
                "/api/log/self",
                query={**query, "p": total},
            )
            oldest_page = self._page(
                oldest_page_raw,
                lambda item: item,
                expected_page=total,
                expected_page_size=1,
            )
            if oldest_page["total"] != total:
                raise NewAPIConsoleError(502, "Account service returned incomplete log bounds")
            oldest_items = oldest_page["items"]
        oldest_at = min(
            (
                self._required_integer(item.get("created_at"), "created_at", "pagination")
                for item in oldest_items
            ),
            default=0,
        )
        if oldest_at <= 0:
            raise NewAPIConsoleError(502, "Account service returned incomplete log bounds")
        bucket_start = oldest_at - oldest_at % 3600
        return max(start_timestamp, max(1, bucket_start)), first_page

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
        return self._required_integer(data, "deleted", "token")

    def reveal_token(self, session_cookie: str, user_id: int, token_id: int) -> str:
        data = self._request(session_cookie, user_id, "POST", f"/api/token/{token_id}/key")
        if not isinstance(data, dict) or not isinstance(data.get("key"), str) or not data["key"]:
            raise NewAPIConsoleError(502, "Account service returned invalid token key data")
        return data["key"]

    @staticmethod
    def _series_item(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise NewAPIConsoleError(502, "Account service returned invalid analytics data")
        value = item
        return {
            "created_at": NewAPIConsoleClient._analytics_integer(value.get("created_at"), "created_at"),
            "username": str(value.get("username") or "")[:128],
            "model_name": str(value.get("model_name") or "")[:256],
            "count": NewAPIConsoleClient._analytics_integer(value.get("count"), "count"),
            "quota": NewAPIConsoleClient._analytics_integer(value.get("quota"), "quota"),
            "token_used": NewAPIConsoleClient._analytics_integer(value.get("token_used"), "token_used"),
        }

    @staticmethod
    def _flow_item(item: Any) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise NewAPIConsoleError(502, "Account service returned invalid analytics data")
        value = item
        return {
            "username": str(value.get("username") or "")[:128],
            "node_name": str(value.get("node_name") or "")[:128],
            "token_id": NewAPIConsoleClient._analytics_integer(value.get("token_id", 0), "token_id"),
            "token_name": str(value.get("token_name") or "")[:128],
            "use_group": str(value.get("use_group") or "")[:128],
            "channel_id": NewAPIConsoleClient._analytics_integer(value.get("channel_id", 0), "channel_id"),
            "channel_name": str(value.get("channel_name") or "")[:128],
            "model_name": str(value.get("model_name") or "")[:256],
            "token_used": NewAPIConsoleClient._analytics_integer(value.get("token_used"), "token_used"),
            "count": NewAPIConsoleClient._analytics_integer(value.get("count"), "count"),
            "quota": NewAPIConsoleClient._analytics_integer(value.get("quota"), "quota"),
        }

    @staticmethod
    def _log_statistics(data: Any) -> dict[str, int]:
        if not isinstance(data, dict):
            raise NewAPIConsoleError(502, "Account service returned invalid log statistics data")
        return {
            "quota": NewAPIConsoleClient._required_integer(
                data.get("quota"), "quota", "log statistics"
            ),
            "rpm": NewAPIConsoleClient._required_integer(
                data.get("rpm"), "rpm", "log statistics"
            ),
            "tpm": NewAPIConsoleClient._required_integer(
                data.get("tpm"), "tpm", "log statistics"
            ),
        }

    def analytics(
        self,
        session_cookie: str,
        user_id: int,
        source_role: int,
        start_timestamp: int,
        end_timestamp: int,
        username: str = "",
        scope: str = "auto",
    ) -> dict[str, Any]:
        is_admin = source_role >= 10
        requested_scope = scope.strip().lower()
        if requested_scope not in {"auto", "global", "self"}:
            raise NewAPIConsoleError(400, "invalid analytics scope")
        if requested_scope == "global" and not is_admin:
            raise NewAPIConsoleError(403, "global analytics requires an administrator")
        use_global_scope = is_admin and requested_scope != "self"
        query = {
            "type": 2,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "username": username if use_global_scope else "",
        }
        series_path = "/api/data/" if use_global_scope else "/api/data/self"
        flow_path = "/api/data/flow" if use_global_scope else "/api/data/flow/self"
        stat_path = "/api/log/stat" if use_global_scope else "/api/log/self/stat"
        cached_log_page: dict[str, Any] | None = None
        if use_global_scope:
            series_raw = self._request(session_cookie, user_id, "GET", series_path, query=query)
            flow_raw = self._request(session_cookie, user_id, "GET", flow_path, query=query)
        else:
            effective_start, cached_log_page = self._self_range_start(
                session_cookie,
                user_id,
                start_timestamp,
                end_timestamp,
            )
            if effective_start > end_timestamp:
                series_raw = []
                flow_raw = []
            else:
                series_raw = self._request_self_range(
                    session_cookie,
                    user_id,
                    series_path,
                    effective_start,
                    end_timestamp,
                )
                flow_raw = self._request_self_range(
                    session_cookie,
                    user_id,
                    flow_path,
                    effective_start,
                    end_timestamp,
                )
        stat_raw = self._request(session_cookie, user_id, "GET", stat_path, query=query)
        log_path = "/api/log/" if use_global_scope else "/api/log/self"
        log_query = {
            "type": 2,
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "p": 1,
            "page_size": 1,
        }
        if use_global_scope and username:
            log_query["username"] = username
        log_page_raw = cached_log_page or self._request(
            session_cookie,
            user_id,
            "GET",
            log_path,
            query=log_query,
        )
        if not isinstance(series_raw, list) or not isinstance(flow_raw, list):
            raise NewAPIConsoleError(502, "Account service returned invalid analytics data")
        series = [self._series_item(item) for item in series_raw]
        flow = [self._flow_item(item) for item in flow_raw]
        stat = self._log_statistics(stat_raw)
        log_page = self._page(
            log_page_raw,
            lambda item: item,
            expected_page=1,
            expected_page_size=1,
        )
        attributed_requests = sum(item["count"] for item in series)
        flow_requests = sum(item["count"] for item in flow)
        attributed_quota = sum(item["quota"] for item in series)
        flow_quota = sum(item["quota"] for item in flow)
        total_quota = stat["quota"]
        total_requests = log_page["total"]
        model_request_delta = total_requests - attributed_requests
        flow_request_delta = total_requests - flow_requests
        model_quota_delta = total_quota - attributed_quota
        flow_quota_delta = total_quota - flow_quota
        return {
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "scope": "global" if use_global_scope else "self",
            "series": series,
            "flow": flow,
            "stat": stat,
            "summary": {
                "requests": total_requests,
                "attributed_requests": attributed_requests,
                "unattributed_requests": max(0, model_request_delta),
                "model_request_delta": model_request_delta,
                "flow_requests": flow_requests,
                "flow_unattributed_requests": max(0, flow_request_delta),
                "flow_request_delta": flow_request_delta,
                "quota": total_quota,
                "attributed_quota": attributed_quota,
                "unattributed_quota": max(0, model_quota_delta),
                "model_quota_delta": model_quota_delta,
                "flow_quota": flow_quota,
                "flow_quota_delta": flow_quota_delta,
                "tokens": sum(item["token_used"] for item in series),
                "models": len({item["model_name"] for item in series if item["model_name"]}),
            },
            "reconciliation": {
                "requests_exact": True,
                "quota_exact": True,
                "request_source": "live_logs",
                "quota_source": "live_logs",
                "attribution_source": "hourly_projection",
            },
        }

    @staticmethod
    def _log(item: Any, include_admin: bool) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise NewAPIConsoleError(502, "Account service returned invalid log data")
        value = item
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
            "id": NewAPIConsoleClient._required_integer(value.get("id"), "id", "log"),
            "created_at": NewAPIConsoleClient._required_integer(
                value.get("created_at"), "created_at", "log"
            ),
            "type": NewAPIConsoleClient._required_integer(value.get("type"), "type", "log"),
            "content": str(value.get("content") or "")[:4000],
            "username": str(value.get("username") or "")[:128],
            "token_name": str(value.get("token_name") or "")[:128],
            "model_name": str(value.get("model_name") or "")[:256],
            "quota": NewAPIConsoleClient._required_integer(
                value.get("quota"), "quota", "log", minimum=None
            ),
            "prompt_tokens": NewAPIConsoleClient._required_integer(
                value.get("prompt_tokens"), "prompt_tokens", "log"
            ),
            "completion_tokens": NewAPIConsoleClient._required_integer(
                value.get("completion_tokens"), "completion_tokens", "log"
            ),
            "use_time": NewAPIConsoleClient._required_integer(
                value.get("use_time"), "use_time", "log"
            ),
            "is_stream": NewAPIConsoleClient._required_boolean(
                value.get("is_stream"), "is_stream", "log"
            ),
            "channel_id": NewAPIConsoleClient._required_integer(
                value.get("channel"), "channel", "log"
            ),
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
        return self._page(
            data,
            lambda item: self._log(item, is_admin),
            expected_page=page,
            expected_page_size=page_size,
        )

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
        return self._log_statistics(data)
