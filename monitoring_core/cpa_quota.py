from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from dashboard_http import open_without_redirects
from monitoring_core.alerting import AlertEvent


WEEKLY_WINDOW_MIN_SECONDS = 6 * 86_400
WEEKLY_WINDOW_MAX_SECONDS = 8 * 86_400
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ANTIGRAVITY_QUOTA_URLS = (
    "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
)


class CpaQuotaError(RuntimeError):
    pass


def _clamp_percent(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    if not math.isfinite(parsed):
        return 0.0
    return round(min(100.0, max(0.0, parsed)), 2)


def _parse_timestamp(value: Any) -> int:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return max(0, int(value))
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return max(0, int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()))
    except ValueError:
        return 0


def _slug(value: Any) -> str:
    normalized = "-".join(
        part for part in "".join(
            character.lower() if character.isalnum() else " "
            for character in str(value or "")
        ).split() if part
    )
    return normalized[:96] or "weekly"


def mask_account(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未标记账号"
    if "@" in text:
        local, domain = text.split("@", 1)
        prefix = local[:2] if len(local) > 1 else local[:1]
        return f"{prefix}***@{domain}"
    if len(text) <= 4:
        return text[:1] + "***"
    return f"{text[:2]}***{text[-2:]}"


def credential_identifier(provider: str, identity: str) -> str:
    digest = hashlib.sha256(f"{provider.strip().lower()}\0{identity.strip().lower()}".encode("utf-8")).hexdigest()
    return digest[:24]


def _weekly_windows(rate_limit: Any) -> list[tuple[str, dict[str, Any]]]:
    if not isinstance(rate_limit, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for source_name in ("primary", "secondary"):
        window = rate_limit.get(f"{source_name}_window")
        if not isinstance(window, dict):
            continue
        seconds = int(window.get("limit_window_seconds") or 0)
        if WEEKLY_WINDOW_MIN_SECONDS <= seconds <= WEEKLY_WINDOW_MAX_SECONDS:
            result.append((source_name, window))
    return result


def parse_codex_weekly_windows(
    payload: dict[str, Any],
    credential_id: str,
    account_label: str,
    observed_at: int,
) -> list[dict[str, Any]]:
    plan_type = str(payload.get("plan_type") or "").strip()
    plan_label = plan_type.replace("_", " ").title() or "Codex"
    result: list[dict[str, Any]] = []

    def append_windows(rate_limit: Any, prefix: str, name: str) -> None:
        for source_name, window in _weekly_windows(rate_limit):
            used_percent = _clamp_percent(window.get("used_percent"))
            reset_at = _parse_timestamp(window.get("reset_at"))
            if not reset_at:
                reset_at = observed_at + max(0, int(window.get("reset_after_seconds") or 0))
            result.append(
                {
                    "credential_id": credential_id,
                    "provider": "codex",
                    "account_label": account_label,
                    "plan_label": plan_label,
                    "window_id": f"{prefix}:{source_name}",
                    "window_name": name,
                    "remaining_percent": round(100.0 - used_percent, 2),
                    "used_percent": used_percent,
                    "reset_at": reset_at,
                    "window_seconds": int(window.get("limit_window_seconds") or 0),
                    "observed_at": int(observed_at),
                }
            )

    append_windows(payload.get("rate_limit"), "base", "Main weekly quota")
    for additional in payload.get("additional_rate_limits") or []:
        if not isinstance(additional, dict):
            continue
        feature = additional.get("metered_feature") or additional.get("limit_name") or "additional"
        name = str(additional.get("limit_name") or feature).strip() or "Additional weekly quota"
        append_windows(additional.get("rate_limit"), f"additional:{_slug(feature)}", name)
    return result


def parse_antigravity_weekly_windows(
    payload: dict[str, Any],
    credential_id: str,
    account_label: str,
    observed_at: int,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in payload.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("displayName") or group.get("name") or "Antigravity models").strip()
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict) or str(bucket.get("window") or "").lower() != "weekly":
                continue
            remaining_percent = _clamp_percent(float(bucket.get("remainingFraction") or 0) * 100.0)
            result.append(
                {
                    "credential_id": credential_id,
                    "provider": "antigravity",
                    "account_label": account_label,
                    "plan_label": "Antigravity",
                    "window_id": str(bucket.get("bucketId") or _slug(group_name)),
                    "window_name": group_name,
                    "remaining_percent": remaining_percent,
                    "used_percent": round(100.0 - remaining_percent, 2),
                    "reset_at": _parse_timestamp(bucket.get("resetTime")),
                    "window_seconds": 604_800,
                    "observed_at": int(observed_at),
                }
            )
    return result


def aggregate_activity(stats: dict[str, Any], now: int | None = None) -> dict[str, Any]:
    timestamp = int(time.time()) if now is None else int(now)
    totals = {
        "requests_1h": 0,
        "requests_6h": 0,
        "tokens_1h": 0,
        "tokens_6h": 0,
        "requests_24h": int((stats.get("summary") or {}).get("requests") or 0),
        "tokens_24h": int((stats.get("summary") or {}).get("total_tokens") or 0),
    }
    for point in stats.get("series") or []:
        if not isinstance(point, dict):
            continue
        point_at = _parse_timestamp(point.get("hour") or point.get("time") or point.get("timestamp"))
        if not point_at or point_at > timestamp + 300:
            continue
        age = timestamp - point_at
        requests = max(0, int(point.get("requests") or 0))
        tokens = max(
            0,
            int(
                point.get("total_tokens")
                or (
                    int(point.get("input_tokens") or 0)
                    + int(point.get("output_tokens") or 0)
                    + int(point.get("reasoning_tokens") or 0)
                )
            ),
        )
        if age <= 21_600:
            totals["requests_6h"] += requests
            totals["tokens_6h"] += tokens
        if age <= 3_600:
            totals["requests_1h"] += requests
            totals["tokens_1h"] += tokens

    if not totals["requests_24h"]:
        totals["requests_24h"] = totals["requests_6h"]
    if not totals["tokens_24h"]:
        totals["tokens_24h"] = totals["tokens_6h"]
    if totals["tokens_6h"] > 0:
        pressure_ratio = totals["tokens_1h"] / max(1.0, totals["tokens_6h"] / 6.0)
    elif totals["requests_6h"] > 0:
        pressure_ratio = totals["requests_1h"] / max(1.0, totals["requests_6h"] / 6.0)
    else:
        pressure_ratio = 0.0
    totals["pressure_ratio"] = round(min(10.0, max(0.0, pressure_ratio)), 2)
    return totals


def forecast_quota(
    samples: Iterable[dict[str, Any]],
    threshold_percent: float = 20.0,
    activity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = sorted(
        (
            dict(sample)
            for sample in samples
            if int(sample.get("observed_at") or 0) > 0
        ),
        key=lambda sample: int(sample.get("observed_at") or 0),
    )
    base = {
        "status": "collecting",
        "confidence": "low",
        "burn_percent_per_hour": 0.0,
        "observed_burn_percent_per_hour": 0.0,
        "seconds_to_threshold": None,
        "seconds_to_exhaustion": None,
        "threshold_at": None,
        "exhaustion_at": None,
        "reset_before_threshold": False,
        "reset_before_exhaustion": False,
        "sample_count": len(ordered),
        "sample_span_seconds": 0,
        "pressure_ratio": round(float((activity or {}).get("pressure_ratio") or 0), 2),
    }
    if len(ordered) < 2:
        return base

    latest = ordered[-1]
    latest_reset_at = int(latest.get("reset_at") or 0)
    cycle = [
        sample
        for sample in ordered
        if not latest_reset_at or int(sample.get("reset_at") or 0) == latest_reset_at
    ]
    if len(cycle) < 2:
        return base
    span = int(cycle[-1]["observed_at"]) - int(cycle[0]["observed_at"])
    base["sample_count"] = len(cycle)
    base["sample_span_seconds"] = max(0, span)
    if span < 7_200:
        return base

    slopes: list[float] = []
    for previous, current in zip(cycle, cycle[1:]):
        elapsed = int(current["observed_at"]) - int(previous["observed_at"])
        if elapsed <= 0:
            continue
        decrease = float(previous.get("remaining_percent") or 0) - float(current.get("remaining_percent") or 0)
        if decrease > 0.01:
            slopes.append(decrease / (elapsed / 3_600.0))
    if not slopes:
        return {**base, "status": "stable", "confidence": "low"}

    observed_rate = statistics.median(slopes)
    pressure_ratio = float((activity or {}).get("pressure_ratio") or 0)
    pressure_factor = min(1.5, max(0.75, 0.5 + pressure_ratio * 0.5)) if pressure_ratio > 0 else 1.0
    projected_rate = max(0.01, observed_rate * pressure_factor)
    remaining = _clamp_percent(latest.get("remaining_percent"))
    observed_at = int(latest.get("observed_at") or 0)
    seconds_to_threshold = 0 if remaining <= threshold_percent else round(
        (remaining - threshold_percent) / projected_rate * 3_600
    )
    seconds_to_exhaustion = round(remaining / projected_rate * 3_600)
    threshold_at = observed_at + seconds_to_threshold
    exhaustion_at = observed_at + seconds_to_exhaustion
    confidence = "high" if span >= 43_200 and len(slopes) >= 5 else "medium" if span >= 14_400 and len(slopes) >= 2 else "low"
    return {
        **base,
        "status": "projected",
        "confidence": confidence,
        "burn_percent_per_hour": round(projected_rate, 3),
        "observed_burn_percent_per_hour": round(observed_rate, 3),
        "seconds_to_threshold": seconds_to_threshold,
        "seconds_to_exhaustion": seconds_to_exhaustion,
        "threshold_at": threshold_at,
        "exhaustion_at": exhaustion_at,
        "reset_before_threshold": bool(latest_reset_at and latest_reset_at <= threshold_at),
        "reset_before_exhaustion": bool(latest_reset_at and latest_reset_at <= exhaustion_at),
    }


class CpaQuotaAlertTracker:
    def __init__(
        self,
        states: dict[str, dict[str, Any]] | None = None,
        threshold_percent: float = 20.0,
        recovery_percent: float = 25.0,
        repeat_seconds: int = 21_600,
    ):
        self.states = {str(key): dict(value or {}) for key, value in dict(states or {}).items()}
        self.threshold_percent = float(threshold_percent)
        self.recovery_percent = max(float(recovery_percent), self.threshold_percent)
        self.repeat_seconds = max(300, int(repeat_seconds))

    def evaluate(
        self,
        sample: dict[str, Any],
        forecast: dict[str, Any],
        now: int | None = None,
    ) -> list[AlertEvent]:
        timestamp = int(time.time()) if now is None else int(now)
        state_key = f"{sample.get('credential_id')}:{sample.get('window_id')}"
        state = dict(self.states.get(state_key) or {})
        active = bool(state.get("active"))
        last_notified_at = int(state.get("last_notified_at") or 0)
        remaining = _clamp_percent(sample.get("remaining_percent"))
        events: list[AlertEvent] = []

        if remaining <= self.threshold_percent:
            should_notify = not active or timestamp - last_notified_at >= self.repeat_seconds
            if should_notify:
                repeated = active
                events.append(
                    AlertEvent(
                        "cpa_quota_low",
                        f"CPA 周配额{'持续' if repeated else ''}不足：{sample.get('window_name') or 'Weekly quota'}",
                        self._alert_body(sample, forecast, remaining),
                        key=f"cpa:quota:{sample.get('credential_id')}:{sample.get('window_id')}",
                        severity="critical" if remaining <= 10 else "warning",
                        metadata={
                            "source": "cpa_quota",
                            "provider": str(sample.get("provider") or ""),
                            "credential_id": str(sample.get("credential_id") or ""),
                            "account_label": str(sample.get("account_label") or ""),
                            "window_id": str(sample.get("window_id") or ""),
                            "remaining_percent": remaining,
                            "reset_at": int(sample.get("reset_at") or 0),
                            "forecast": dict(forecast),
                        },
                    )
                )
                last_notified_at = timestamp
            active = True
        elif active and remaining >= self.recovery_percent:
            events.append(
                AlertEvent(
                    "cpa_quota_recovered",
                    f"CPA 周配额恢复：{sample.get('window_name') or 'Weekly quota'}",
                    (
                        f"账号：{sample.get('account_label') or '未标记账号'}\n"
                        f"配额池：{sample.get('window_name') or 'Weekly quota'}\n"
                        f"当前剩余：{remaining:.1f}%（恢复阈值 {self.recovery_percent:.1f}%）\n"
                        f"下次重置：{self._time_text(int(sample.get('reset_at') or 0))}"
                    ),
                    key=f"cpa:quota:{sample.get('credential_id')}:{sample.get('window_id')}",
                    severity="info",
                    recovery=True,
                    metadata={
                        "source": "cpa_quota",
                        "provider": str(sample.get("provider") or ""),
                        "credential_id": str(sample.get("credential_id") or ""),
                        "account_label": str(sample.get("account_label") or ""),
                        "window_id": str(sample.get("window_id") or ""),
                        "remaining_percent": remaining,
                    },
                )
            )
            active = False

        self.states[state_key] = {
            "active": active,
            "last_notified_at": last_notified_at,
            "last_remaining_percent": remaining,
            "last_reset_at": int(sample.get("reset_at") or 0),
            "updated_at": timestamp,
        }
        return events

    def _alert_body(self, sample: dict[str, Any], forecast: dict[str, Any], remaining: float) -> str:
        lines = [
            f"数据源：CPA / CLIProxyAPI",
            f"账号：{sample.get('account_label') or '未标记账号'}",
            f"配额池：{sample.get('window_name') or 'Weekly quota'}",
            f"当前剩余：{remaining:.1f}%（告警阈值 {self.threshold_percent:.1f}%）",
            f"下次重置：{self._time_text(int(sample.get('reset_at') or 0))}",
        ]
        if forecast.get("status") == "projected":
            if forecast.get("reset_before_exhaustion"):
                lines.append("趋势判断：按当前消耗速度，预计在耗尽前先迎来周配额重置。")
            else:
                exhaustion_at = int(forecast.get("exhaustion_at") or 0)
                lines.append(f"趋势判断：按近期配额消耗与请求压力，预计 {self._time_text(exhaustion_at)} 附近耗尽。")
            lines.append(f"预测置信度：{forecast.get('confidence') or 'low'}")
        else:
            lines.append("趋势判断：历史样本不足，正在建立预测基线。")
        activity = dict(sample.get("activity") or {})
        if activity:
            lines.append(
                "附近请求：近 1 小时 "
                f"{int(activity.get('requests_1h') or 0)} 次 / {int(activity.get('tokens_1h') or 0):,} Tokens；"
                f"近 6 小时 {int(activity.get('requests_6h') or 0)} 次。"
            )
        lines.append("建议：检查该账号近期流量，并在配额接近耗尽前准备切换或补充账号。")
        return "\n".join(lines)

    @staticmethod
    def _time_text(timestamp: int) -> str:
        if timestamp <= 0:
            return "未知"
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


@dataclass(frozen=True)
class CpaCredential:
    credential_id: str
    provider: str
    account_label: str
    source: str
    auth_index: str
    project_id: str = ""


class CpaQuotaClient:
    def __init__(
        self,
        base_url: str,
        management_key: str,
        timeout_seconds: int = 30,
        max_response_bytes: int = 5 * 1024 * 1024,
        opener: Callable[..., Any] = open_without_redirects,
    ):
        parsed = urllib.parse.urlsplit(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("CPA base URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("CPA base URL must not contain credentials, query or fragment")
        self.base_url = base_url.strip().rstrip("/")
        if self.base_url.endswith("/v0/management"):
            self.base_url = self.base_url[: -len("/v0/management")]
        self.management_key = management_key.strip()
        if not self.management_key:
            raise ValueError("CPA management key is required")
        self.timeout_seconds = max(3, min(60, int(timeout_seconds)))
        self.max_response_bytes = max(1024, int(max_response_bytes))
        self.opener = opener

    def credentials(self) -> list[CpaCredential]:
        payload = self._request_json(f"{self.base_url}/v0/management/auth-files")
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            raise CpaQuotaError("CPA auth-files response is invalid")
        result: list[CpaCredential] = []
        for item in files:
            if not isinstance(item, dict) or bool(item.get("disabled")):
                continue
            provider = str(item.get("provider") or item.get("type") or "").strip().lower()
            if provider not in {"codex", "antigravity"}:
                continue
            auth_index = str(item.get("auth_index") or "").strip()
            source = str(item.get("email") or item.get("account") or item.get("label") or item.get("name") or "").strip()
            if not auth_index or not source:
                continue
            result.append(
                CpaCredential(
                    credential_id=credential_identifier(provider, f"{source}\0{auth_index}"),
                    provider=provider,
                    account_label=mask_account(source),
                    source=source,
                    auth_index=auth_index,
                    project_id=str(item.get("project_id") or "").strip(),
                )
            )
        return result

    def weekly_windows(self, credential: CpaCredential, observed_at: int) -> list[dict[str, Any]]:
        if credential.provider == "codex":
            payload = self._api_call(
                credential,
                "GET",
                CODEX_USAGE_URL,
                {
                    "Authorization": "Bearer $TOKEN$",
                    "Content-Type": "application/json",
                    "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
                },
            )
            return parse_codex_weekly_windows(
                payload,
                credential.credential_id,
                credential.account_label,
                observed_at,
            )
        if credential.provider == "antigravity":
            if not credential.project_id:
                raise CpaQuotaError("Antigravity credential is missing project_id")
            last_error: Exception | None = None
            for url in ANTIGRAVITY_QUOTA_URLS:
                try:
                    payload = self._api_call(
                        credential,
                        "POST",
                        url,
                        {
                            "Authorization": "Bearer $TOKEN$",
                            "Content-Type": "application/json",
                            "User-Agent": "antigravity/cli/1.0.13 (aidev_client; os_type=darwin; arch=arm64)",
                        },
                        {"project": credential.project_id},
                    )
                    return parse_antigravity_weekly_windows(
                        payload,
                        credential.credential_id,
                        credential.account_label,
                        observed_at,
                    )
                except CpaQuotaError as error:
                    last_error = error
            raise CpaQuotaError(str(last_error or "Antigravity quota request failed"))
        return []

    def activity(self, source: str, now: int | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode({"range": "24h", "source": source})
        payload = self._request_json(
            f"{self.base_url}/v0/resource/plugins/cap-token-usage-tracker/stats?{query}"
        )
        return aggregate_activity(payload, now=now)

    def _api_call(
        self,
        credential: CpaCredential,
        method: str,
        url: str,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_payload: dict[str, Any] = {
            "authIndex": credential.auth_index,
            "method": method,
            "url": url,
            "header": headers,
        }
        if data is not None:
            request_payload["data"] = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        outer = self._request_json(
            f"{self.base_url}/v0/management/api-call",
            method="POST",
            payload=request_payload,
        )
        status_code = int(outer.get("status_code") or outer.get("statusCode") or 0)
        body: Any = outer.get("body")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as error:
                raise CpaQuotaError(f"CPA upstream returned invalid JSON (HTTP {status_code or 'unknown'})") from error
        if status_code < 200 or status_code >= 300 or not isinstance(body, dict):
            message = str((body or {}).get("error") or (body or {}).get("message") or "upstream request failed") if isinstance(body, dict) else "upstream request failed"
            raise CpaQuotaError(f"CPA upstream HTTP {status_code or 'unknown'}: {message[:300]}")
        return body

    def _request_json(
        self,
        url: str,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Authorization": f"Bearer {self.management_key}", "Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            raise CpaQuotaError(f"CPA management HTTP {error.code}: {error.reason}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise CpaQuotaError(f"CPA management request failed: {error}") from error
        if len(raw) > self.max_response_bytes:
            raise CpaQuotaError("CPA response exceeded the configured size limit")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CpaQuotaError("CPA management returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise CpaQuotaError("CPA management returned a non-object response")
        return parsed
