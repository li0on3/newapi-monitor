from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import math
import os
import queue
import smtplib
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Iterable


LOGGER = logging.getLogger("newapi-monitor")


def request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"notification endpoint returned HTTP {error.code}: {body}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"notification endpoint unavailable: {error}") from error
    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError("notification endpoint returned invalid JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError("notification endpoint returned a non-object response")
    return result


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class AlertEvent:
    kind: str
    title: str
    body: str
    key: str = ""
    severity: str = "warning"
    recovery: bool = False


@dataclass(frozen=True)
class ChannelObservation:
    channel_id: int
    name: str
    success: bool
    elapsed_seconds: float
    message: str
    source: str = "builtin"
    first_response_ms: float | None = None


@dataclass(frozen=True)
class LatencySummary:
    channel_id: int
    channel_name: str
    model_name: str
    count: int
    average_seconds: float
    p95_seconds: float
    average_frt_ms: float | None
    slow_count: int


@dataclass(frozen=True)
class LatencyWindowDecision:
    triggered: bool
    critical: bool
    sample_count: int
    bad_last5: int
    bad_last10: int
    max_total_seconds: float
    max_frt_ms: float
    reason: str


@dataclass(frozen=True)
class RealProbeRule:
    channel_id: int
    model: str
    path: str
    request_format: str
    prompt: str = "1"
    max_output_tokens: int = 1


@dataclass(frozen=True)
class RealProbeResult:
    success: bool
    elapsed_seconds: float
    first_response_ms: float | None
    message: str


def build_auth_headers(access_token: str, user_id: int) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "New-Api-User": str(user_id),
    }


def _parse_other(other: Any) -> dict[str, Any]:
    if isinstance(other, dict):
        return other
    if not isinstance(other, str) or not other.strip():
        return {}
    try:
        parsed = json.loads(other)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def is_channel_test_log(log: dict[str, Any]) -> bool:
    return str(log.get("token_name") or "").strip() == "模型测试" or str(
        log.get("content") or ""
    ).strip() == "模型测试"


def parse_real_probe_rules(raw: str) -> dict[int, RealProbeRule]:
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("REAL_PROBE_RULES must be a JSON object")
    rules: dict[int, RealProbeRule] = {}
    for channel_key, item in payload.items():
        if not isinstance(item, dict):
            raise ValueError(f"invalid real probe rule for channel {channel_key}")
        channel_id = int(channel_key)
        model = str(item.get("model") or "").strip()
        if channel_id <= 0 or not model:
            raise ValueError(f"invalid real probe rule for channel {channel_key}")
        request_format = str(item.get("format") or "responses").strip().lower()
        default_paths = {
            "responses": "/v1/responses",
            "chat": "/v1/chat/completions",
            "anthropic": "/v1/messages",
        }
        default_path = default_paths.get(request_format, "/v1/responses")
        rules[channel_id] = RealProbeRule(
            channel_id=channel_id,
            model=model,
            path=str(item.get("path") or default_path),
            request_format=request_format,
            prompt=str(item.get("prompt") or "1"),
            max_output_tokens=max(1, int(item.get("max_output_tokens") or 1)),
        )
    return rules


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def summarize_logs(logs: Iterable[dict[str, Any]], slow_seconds: float) -> list[LatencySummary]:
    grouped: dict[tuple[int, str, str], dict[str, list[float] | int]] = {}
    for log in logs:
        channel_id = int(log.get("channel") or 0)
        channel_name = str(log.get("channel_name") or f"channel-{channel_id}")
        model_name = str(log.get("model_name") or "unknown")
        use_time = float(log.get("use_time") or 0)
        other = _parse_other(log.get("other"))
        frt = other.get("frt")

        key = (channel_id, channel_name, model_name)
        bucket = grouped.setdefault(key, {"durations": [], "frt": [], "slow": 0})
        durations = bucket["durations"]
        assert isinstance(durations, list)
        durations.append(use_time)
        if use_time > slow_seconds:
            bucket["slow"] = int(bucket["slow"]) + 1
        if isinstance(frt, (int, float)) and frt > 0:
            frt_values = bucket["frt"]
            assert isinstance(frt_values, list)
            frt_values.append(float(frt))

    result: list[LatencySummary] = []
    for (channel_id, channel_name, model_name), bucket in grouped.items():
        durations = list(bucket["durations"])
        frt_values = list(bucket["frt"])
        result.append(
            LatencySummary(
                channel_id=channel_id,
                channel_name=channel_name,
                model_name=model_name,
                count=len(durations),
                average_seconds=round(sum(durations) / len(durations), 3),
                p95_seconds=round(_percentile(durations, 0.95), 3),
                average_frt_ms=(round(sum(frt_values) / len(frt_values), 1) if frt_values else None),
                slow_count=int(bucket["slow"]),
            )
        )
    return sorted(result, key=lambda item: (-item.count, item.channel_name, item.model_name))


def evaluate_latency_window(
    samples: Iterable[dict[str, Any]],
    slow_seconds: float = 60.0,
    hard_limit_seconds: float = 180.0,
) -> LatencyWindowDecision:
    recent = list(samples)[:10]
    slow_limit_ms = slow_seconds * 1000.0
    hard_limit_ms = hard_limit_seconds * 1000.0

    def is_bad(sample: dict[str, Any]) -> bool:
        use_time = float(sample.get("use_time") or 0)
        frt_ms = float(sample.get("frt_ms") or 0)
        return use_time > slow_seconds or frt_ms > slow_limit_ms

    bad_flags = [is_bad(sample) for sample in recent]
    bad_last5 = sum(bad_flags[:5])
    bad_last10 = sum(bad_flags[:10])
    max_total = max((float(sample.get("use_time") or 0) for sample in recent), default=0.0)
    max_frt = max((float(sample.get("frt_ms") or 0) for sample in recent), default=0.0)
    critical = max_total > hard_limit_seconds or max_frt > hard_limit_ms
    three_of_five = len(recent) >= 5 and bad_last5 >= 3
    five_of_ten = len(recent) >= 10 and bad_last10 >= 5
    triggered = critical or three_of_five or five_of_ten

    reasons: list[str] = []
    if critical:
        reasons.append(f"单次超过 {hard_limit_seconds:.0f}s")
    if three_of_five:
        reasons.append(f"近5次有{bad_last5}次超过 {slow_seconds:.0f}s")
    if five_of_ten:
        reasons.append(f"近10次有{bad_last10}次超过 {slow_seconds:.0f}s")
    return LatencyWindowDecision(
        triggered=triggered,
        critical=critical,
        sample_count=len(recent),
        bad_last5=bad_last5,
        bad_last10=bad_last10,
        max_total_seconds=max_total,
        max_frt_ms=max_frt,
        reason="；".join(reasons),
    )


class LatencyStateTracker:
    def __init__(
        self,
        states: dict[str, dict[str, Any]] | None = None,
        slow_seconds: float = 60.0,
        hard_limit_seconds: float = 180.0,
        reminder_seconds: int = 1800,
    ):
        self.states = dict(states or {})
        self.slow_seconds = slow_seconds
        self.hard_limit_seconds = hard_limit_seconds
        self.reminder_seconds = reminder_seconds

    def evaluate(
        self,
        key: str,
        label: str,
        samples: Iterable[dict[str, Any]],
        now: float | None = None,
    ) -> list[AlertEvent]:
        current_time = time.time() if now is None else now
        recent = list(samples)[:10]
        decision = evaluate_latency_window(recent, self.slow_seconds, self.hard_limit_seconds)
        state = dict(self.states.get(key) or {"active": False, "last_notified": 0.0})
        events: list[AlertEvent] = []

        if decision.triggered:
            should_notify = not state["active"] or current_time - float(state["last_notified"]) >= self.reminder_seconds
            if should_notify:
                state["active"] = True
                state["last_notified"] = current_time
                values = ", ".join(
                    f"{float(sample.get('use_time') or 0):.0f}s/"
                    f"{float(sample.get('frt_ms') or 0) / 1000.0:.1f}s"
                    for sample in recent
                )
                events.append(
                    AlertEvent(
                        kind="latency_high" if not state.get("notified_before") else "latency_reminder",
                        title=f"耗时异常：{label}",
                        body=(
                            f"规则：{decision.reason}\n"
                            f"最大总耗时：{decision.max_total_seconds:.0f}s\n"
                            f"最大首字耗时：{decision.max_frt_ms / 1000.0:.1f}s\n"
                            f"最近请求（总耗时/首字）：{values}"
                        ),
                        key=f"latency:{key}",
                        severity="critical" if decision.critical else "warning",
                    )
                )
                state["notified_before"] = True
        elif state["active"] and len(recent) >= 5:
            last_five = evaluate_latency_window(recent[:5], self.slow_seconds, self.hard_limit_seconds)
            if last_five.bad_last5 == 0:
                events.append(
                    AlertEvent(
                        kind="latency_recovered",
                        title=f"耗时恢复：{label}",
                        body="最近连续5次请求均未超过耗时阈值。",
                        key=f"latency:{key}",
                        severity="info",
                        recovery=True,
                    )
                )
                state = {"active": False, "last_notified": current_time, "notified_before": False}

        self.states[key] = state
        return events


class ChannelStateTracker:
    def __init__(self, states: dict[str, str] | None = None):
        self.states = dict(states or {})

    def evaluate(self, observations: Iterable[ChannelObservation]) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        for observation in observations:
            key = str(observation.channel_id)
            new_state = "ok" if observation.success else "failed"
            old_state = self.states.get(key)
            self.states[key] = new_state
            if old_state == new_state:
                continue
            if new_state == "failed":
                events.append(
                    AlertEvent(
                        kind="channel_failed",
                        title=f"渠道异常：{observation.name}",
                        body=(
                            f"渠道ID：{observation.channel_id}\n"
                            f"探测耗时：{observation.elapsed_seconds:.3f}s\n"
                            f"错误：{observation.message or '未知错误'}"
                        ),
                        key=f"channel:{observation.channel_id}",
                        severity="critical",
                    )
                )
            elif old_state is not None:
                events.append(
                    AlertEvent(
                        kind="channel_recovered",
                        title=f"渠道恢复：{observation.name}",
                        body=(
                            f"渠道ID：{observation.channel_id}\n"
                            f"探测耗时：{observation.elapsed_seconds:.3f}s"
                        ),
                        key=f"channel:{observation.channel_id}",
                        severity="info",
                        recovery=True,
                    )
                )
        return events


class ServiceStateTracker:
    def __init__(self, state: str = "unknown"):
        self.state = state

    def evaluate(self, success: bool, message: str = "") -> list[AlertEvent]:
        new_state = "ok" if success else "failed"
        old_state = self.state
        self.state = new_state
        if old_state == new_state or (old_state == "unknown" and success):
            return []
        if success:
            return [
                AlertEvent(
                    kind="service_recovered",
                    title="New API服务恢复",
                    body="管理接口已恢复访问",
                    key="service:newapi",
                    severity="info",
                    recovery=True,
                )
            ]
        return [
            AlertEvent(
                kind="service_failed",
                title="New API服务异常",
                body=f"管理接口访问失败：{message or '未知错误'}",
                key="service:newapi",
                severity="critical",
            )
        ]


class CollectorFreshnessTracker:
    def __init__(self, states: dict[str, str] | None = None):
        self.states = dict(states or {})

    def evaluate(self, collectors: dict[str, dict[str, Any]]) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        labels = {
            "channel_sync": "渠道同步",
            "channel_probe": "渠道探测",
            "logs": "使用日志",
            "resources": "机器资源",
        }
        for name, detail in collectors.items():
            current = str(detail.get("status") or "starting")
            previous = self.states.get(name, "starting")
            if current == "stale" and previous != "stale":
                age = int(detail.get("age_seconds") or 0)
                threshold = int(detail.get("stale_after_seconds") or 0)
                error = str(detail.get("last_error") or "")
                body = f"最后成功采集距今 {age}s，失效阈值 {threshold}s。"
                if error:
                    body += f"\n最近错误：{error}"
                events.append(
                    AlertEvent(
                        "collector_stale",
                        f"采集器异常：{labels.get(name, name)}",
                        body,
                        key=f"collector:{name}",
                        severity="critical" if name in {"channel_sync", "channel_probe"} else "warning",
                    )
                )
            elif current == "ok" and previous == "stale":
                events.append(
                    AlertEvent(
                        "collector_recovered",
                        f"采集器恢复：{labels.get(name, name)}",
                        f"{labels.get(name, name)}采集已恢复，最新数据距今 {int(detail.get('age_seconds') or 0)}s。",
                        key=f"collector:{name}",
                        severity="info",
                        recovery=True,
                    )
                )
            self.states[name] = current
        return events


class ResourceStateTracker:
    def __init__(
        self,
        thresholds: dict[str, float],
        sustain_seconds: int,
        states: dict[str, dict[str, Any]] | None = None,
        recovery_ratio: float = 0.9,
    ):
        self.thresholds = thresholds
        self.sustain_seconds = sustain_seconds
        self.states = dict(states or {})
        self.recovery_ratio = recovery_ratio

    def evaluate(self, metrics: dict[str, float], now: float | None = None) -> list[AlertEvent]:
        current_time = time.time() if now is None else now
        events: list[AlertEvent] = []
        for name, threshold in self.thresholds.items():
            if name not in metrics:
                continue
            value = float(metrics[name])
            state = dict(self.states.get(name) or {"since": None, "alerted": False})
            if value > threshold:
                if state["since"] is None:
                    state["since"] = current_time
                if not state["alerted"] and current_time - float(state["since"]) >= self.sustain_seconds:
                    state["alerted"] = True
                    events.append(
                        AlertEvent(
                            kind="resource_high",
                            title=f"资源告警：{name}",
                            body=f"当前值：{value:.1f}%\n阈值：{threshold:.1f}%",
                            key=f"resource:{name}",
                            severity="critical",
                        )
                    )
            elif state["alerted"]:
                if value <= threshold * self.recovery_ratio:
                    events.append(
                        AlertEvent(
                            kind="resource_recovered",
                            title=f"资源恢复：{name}",
                            body=f"当前值：{value:.1f}%\n恢复阈值：{threshold * self.recovery_ratio:.1f}%",
                            key=f"resource:{name}",
                            severity="info",
                            recovery=True,
                        )
                    )
                    state = {"since": None, "alerted": False}
            else:
                state["since"] = None
            self.states[name] = state
        return events


@dataclass(frozen=True)
class Config:
    base_url: str
    access_token: str
    relay_api_token: str
    user_id: int
    state_db: str
    poll_seconds: int
    channel_sync_interval_seconds: int
    channel_interval_seconds: int
    log_interval_seconds: int
    resource_interval_seconds: int
    report_interval_seconds: int
    log_overlap_seconds: int
    log_initial_lookback_seconds: int
    slow_request_seconds: float
    latency_hard_limit_seconds: float
    latency_reminder_seconds: int
    channel_slow_seconds: float
    resource_sustain_seconds: int
    system_cpu_threshold: float
    system_memory_threshold: float
    system_disk_threshold: float
    container_cpu_threshold: float
    container_memory_threshold: float
    docker_container_name: str
    docker_container_names: tuple[str, ...]
    disk_path: str
    real_probe_rules: dict[int, RealProbeRule]
    channel_settings: dict[int, dict[str, Any]]
    excluded_token_names: tuple[str, ...]
    retention_days: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_to: list[str]
    smtp_starttls: bool
    smtp_ssl: bool
    email_enabled: bool
    wecom_app_enabled: bool
    wecom_corp_id: str
    wecom_agent_id: int
    wecom_app_secret: str
    wecom_to_user: str
    wecom_to_party: str
    wecom_to_tag: str
    wecom_webhook_enabled: bool
    wecom_webhook_url: str
    feishu_app_enabled: bool
    feishu_app_id: str
    feishu_app_secret: str
    feishu_receive_id_type: str
    feishu_receive_id: str
    feishu_webhook_enabled: bool
    feishu_webhook_url: str
    feishu_webhook_secret: str
    send_startup_email: bool
    subject_prefix: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls.from_values({})

    @classmethod
    def from_values(cls, values: dict[str, Any]) -> "Config":
        def value(key: str, env_name: str, default: Any) -> Any:
            if key in values:
                return values[key]
            raw = os.getenv(env_name)
            if raw is None:
                return default
            if isinstance(default, bool):
                return raw.strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(default, int) and not isinstance(default, bool):
                return int(raw)
            if isinstance(default, float):
                return float(raw)
            return raw

        smtp_to_value = value("smtp_to", "SMTP_TO", "")
        recipients = (
            [str(item).strip() for item in smtp_to_value if str(item).strip()]
            if isinstance(smtp_to_value, list)
            else [item.strip() for item in str(smtp_to_value).split(",") if item.strip()]
        )
        rules_value = values.get("real_probe_rules")
        rules_raw = json.dumps(rules_value, ensure_ascii=False) if isinstance(rules_value, dict) else str(
            value("real_probe_rules", "REAL_PROBE_RULES", "")
        )
        channel_settings_value = values.get("channel_settings", {})
        channel_settings = {
            int(channel_id): dict(config)
            for channel_id, config in channel_settings_value.items()
            if isinstance(config, dict)
        } if isinstance(channel_settings_value, dict) else {}
        container_names_value = value(
            "docker_container_names",
            "DOCKER_CONTAINER_NAMES",
            os.getenv("DOCKER_CONTAINER_NAME", ""),
        )
        excluded_value = value(
            "excluded_token_names",
            "EXCLUDED_TOKEN_NAMES",
            "模型测试,newapi-monitor-probe",
        )
        return cls(
            base_url=str(value("new_api_base_url", "NEW_API_BASE_URL", "http://new-api:3000")).rstrip("/"),
            access_token=str(value("new_api_access_token", "NEW_API_ACCESS_TOKEN", "")),
            relay_api_token=str(value("relay_api_token", "RELAY_API_TOKEN", "")),
            user_id=int(value("new_api_user_id", "NEW_API_USER_ID", 0)),
            state_db=str(value("state_db", "STATE_DB", "/data/monitor.db")),
            poll_seconds=int(value("poll_seconds", "POLL_SECONDS", 10)),
            channel_sync_interval_seconds=int(value("channel_sync_interval_seconds", "CHANNEL_SYNC_INTERVAL_SECONDS", 5)),
            channel_interval_seconds=int(value("channel_interval_seconds", "CHANNEL_INTERVAL_SECONDS", 300)),
            log_interval_seconds=int(value("log_interval_seconds", "LOG_INTERVAL_SECONDS", 300)),
            resource_interval_seconds=int(value("resource_interval_seconds", "RESOURCE_INTERVAL_SECONDS", 60)),
            report_interval_seconds=int(value("report_interval_seconds", "REPORT_INTERVAL_SECONDS", 86400)),
            log_overlap_seconds=int(value("log_overlap_seconds", "LOG_OVERLAP_SECONDS", 60)),
            log_initial_lookback_seconds=int(value("log_initial_lookback_seconds", "LOG_INITIAL_LOOKBACK_SECONDS", 3600)),
            slow_request_seconds=float(value("slow_request_seconds", "SLOW_REQUEST_SECONDS", 60.0)),
            latency_hard_limit_seconds=float(value("latency_hard_limit_seconds", "LATENCY_HARD_LIMIT_SECONDS", 180.0)),
            latency_reminder_seconds=int(value("latency_reminder_seconds", "LATENCY_REMINDER_SECONDS", 1800)),
            channel_slow_seconds=float(value("channel_slow_seconds", "CHANNEL_SLOW_SECONDS", 30.0)),
            resource_sustain_seconds=int(value("resource_sustain_seconds", "RESOURCE_SUSTAIN_SECONDS", 600)),
            system_cpu_threshold=float(value("system_cpu_threshold", "SYSTEM_CPU_THRESHOLD", 85.0)),
            system_memory_threshold=float(value("system_memory_threshold", "SYSTEM_MEMORY_THRESHOLD", 85.0)),
            system_disk_threshold=float(value("system_disk_threshold", "SYSTEM_DISK_THRESHOLD", 80.0)),
            container_cpu_threshold=float(value("container_cpu_threshold", "CONTAINER_CPU_THRESHOLD", 90.0)),
            container_memory_threshold=float(value("container_memory_threshold", "CONTAINER_MEMORY_THRESHOLD", 90.0)),
            docker_container_name=str(value("docker_container_name", "DOCKER_CONTAINER_NAME", "")),
            docker_container_names=tuple(
                item.strip()
                for item in (container_names_value if isinstance(container_names_value, list) else str(container_names_value).split(","))
                if item.strip()
            ),
            disk_path=str(value("disk_path", "DISK_PATH", "/")),
            real_probe_rules=parse_real_probe_rules(rules_raw),
            channel_settings=channel_settings,
            excluded_token_names=tuple(
                item.strip()
                for item in (excluded_value if isinstance(excluded_value, list) else str(excluded_value).split(","))
                if item.strip()
            ),
            retention_days=max(8, int(value("retention_days", "RETENTION_DAYS", 90))),
            smtp_host=str(value("smtp_host", "SMTP_HOST", "")),
            smtp_port=int(value("smtp_port", "SMTP_PORT", 25)),
            smtp_user=str(value("smtp_user", "SMTP_USER", "")),
            smtp_password=str(value("smtp_password", "SMTP_PASSWORD", "")),
            smtp_from=str(value("smtp_from", "SMTP_FROM", "newapi-monitor@localhost")),
            smtp_to=recipients,
            smtp_starttls=bool(value("smtp_starttls", "SMTP_STARTTLS", False)),
            smtp_ssl=bool(value("smtp_ssl", "SMTP_SSL", False)),
            email_enabled=bool(value("email_enabled", "EMAIL_ENABLED", bool(recipients))),
            wecom_app_enabled=bool(value("wecom_app_enabled", "WECOM_APP_ENABLED", False)),
            wecom_corp_id=str(value("wecom_corp_id", "WECOM_CORP_ID", "")),
            wecom_agent_id=int(value("wecom_agent_id", "WECOM_AGENT_ID", 0)),
            wecom_app_secret=str(value("wecom_app_secret", "WECOM_APP_SECRET", "")),
            wecom_to_user=str(value("wecom_to_user", "WECOM_TO_USER", "@all")),
            wecom_to_party=str(value("wecom_to_party", "WECOM_TO_PARTY", "")),
            wecom_to_tag=str(value("wecom_to_tag", "WECOM_TO_TAG", "")),
            wecom_webhook_enabled=bool(value("wecom_webhook_enabled", "WECOM_WEBHOOK_ENABLED", False)),
            wecom_webhook_url=str(value("wecom_webhook_url", "WECOM_WEBHOOK_URL", "")),
            feishu_app_enabled=bool(value("feishu_app_enabled", "FEISHU_APP_ENABLED", False)),
            feishu_app_id=str(value("feishu_app_id", "FEISHU_APP_ID", "")),
            feishu_app_secret=str(value("feishu_app_secret", "FEISHU_APP_SECRET", "")),
            feishu_receive_id_type=str(value("feishu_receive_id_type", "FEISHU_RECEIVE_ID_TYPE", "chat_id")),
            feishu_receive_id=str(value("feishu_receive_id", "FEISHU_RECEIVE_ID", "")),
            feishu_webhook_enabled=bool(value("feishu_webhook_enabled", "FEISHU_WEBHOOK_ENABLED", False)),
            feishu_webhook_url=str(value("feishu_webhook_url", "FEISHU_WEBHOOK_URL", "")),
            feishu_webhook_secret=str(value("feishu_webhook_secret", "FEISHU_WEBHOOK_SECRET", "")),
            send_startup_email=bool(value("send_startup_email", "SEND_STARTUP_EMAIL", True)),
            subject_prefix=str(value("subject_prefix", "SUBJECT_PREFIX", "[New API监控]")),
        )

    def validate(self) -> None:
        missing = []
        if not self.access_token:
            missing.append("NEW_API_ACCESS_TOKEN")
        if self.user_id <= 0:
            missing.append("NEW_API_USER_ID")
        if self.real_probe_rules and not self.relay_api_token:
            missing.append("RELAY_API_TOKEN")
        if self.email_enabled:
            if not self.smtp_host:
                missing.append("SMTP_HOST")
            if not self.smtp_to:
                missing.append("SMTP_TO")
        if self.wecom_app_enabled:
            if not self.wecom_corp_id:
                missing.append("WECOM_CORP_ID")
            if self.wecom_agent_id <= 0:
                missing.append("WECOM_AGENT_ID")
            if not self.wecom_app_secret:
                missing.append("WECOM_APP_SECRET")
            if not any((self.wecom_to_user, self.wecom_to_party, self.wecom_to_tag)):
                missing.append("WECOM_RECIPIENT")
        if self.wecom_webhook_enabled and not self.wecom_webhook_url:
            missing.append("WECOM_WEBHOOK_URL")
        if self.feishu_app_enabled:
            if not self.feishu_app_id:
                missing.append("FEISHU_APP_ID")
            if not self.feishu_app_secret:
                missing.append("FEISHU_APP_SECRET")
            if not self.feishu_receive_id:
                missing.append("FEISHU_RECEIVE_ID")
        if self.feishu_webhook_enabled and not self.feishu_webhook_url:
            missing.append("FEISHU_WEBHOOK_URL")
        if missing:
            raise ValueError("missing required settings: " + ", ".join(missing))


class NewAPIClient:
    def __init__(self, config: Config, timeout_seconds: int = 45):
        self.base_url = config.base_url
        self.headers = build_auth_headers(config.access_token, config.user_id)
        self.timeout_seconds = timeout_seconds

    def _request(self, path: str, allow_failure: bool = False) -> dict[str, Any]:
        request = urllib.request.Request(self.base_url + path, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {body}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise RuntimeError(str(error)) from error
        if not isinstance(payload, dict):
            raise RuntimeError("New API returned a non-object response")
        if payload.get("success") is False and not allow_failure:
            raise RuntimeError(str(payload.get("message") or "New API request failed"))
        return payload

    def get_channels(self) -> list[dict[str, Any]]:
        payload = self._request("/api/channel/?page=1&page_size=1000")
        data = payload.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def test_channel(self, channel_id: int) -> dict[str, Any]:
        return self._request(f"/api/channel/test/{channel_id}", allow_failure=True)

    def get_logs(self, start_timestamp: int, end_timestamp: int) -> list[dict[str, Any]]:
        all_items: list[dict[str, Any]] = []
        page = 1
        page_size = 100
        while True:
            query = urllib.parse.urlencode(
                {
                    "type": 2,
                    "start_timestamp": start_timestamp,
                    "end_timestamp": end_timestamp,
                    "p": page,
                    "page_size": page_size,
                }
            )
            payload = self._request(f"/api/log/?{query}")
            data = payload.get("data") or {}
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                break
            all_items.extend(items)
            total = int(data.get("total") or len(all_items))
            if len(all_items) >= total or len(items) < page_size:
                break
            page += 1
        return all_items


class RelayProbeClient:
    def __init__(self, config: Config, timeout_seconds: int = 75):
        self.base_url = config.base_url
        self.api_token = config.relay_api_token
        self.timeout_seconds = timeout_seconds

    def probe(self, rule: RealProbeRule) -> RealProbeResult:
        if rule.request_format == "responses":
            payload = {
                "model": rule.model,
                "input": rule.prompt,
                "max_output_tokens": rule.max_output_tokens,
                "stream": True,
            }
        elif rule.request_format == "chat":
            payload = {
                "model": rule.model,
                "messages": [{"role": "user", "content": rule.prompt}],
                "max_tokens": rule.max_output_tokens,
                "stream": True,
            }
        elif rule.request_format == "anthropic":
            payload = {
                "model": rule.model,
                "messages": [{"role": "user", "content": rule.prompt}],
                "max_tokens": rule.max_output_tokens,
                "stream": True,
            }
        else:
            return RealProbeResult(False, 0.0, None, f"unsupported probe format: {rule.request_format}")

        channel_token = f"sk-{self.api_token.removeprefix('sk-')}-{rule.channel_id}"
        headers = {
            "Authorization": f"Bearer {channel_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": "newapi-monitor-probe/1.0",
        }
        if rule.request_format == "anthropic":
            headers["x-api-key"] = channel_token
            headers["anthropic-version"] = "2023-06-01"

        request = urllib.request.Request(
            self.base_url + rule.path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        first_response_ms: float | None = None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                content_type = str(response.headers.get("Content-Type") or "").lower()
                received_payload = False
                if "text/event-stream" in content_type:
                    while True:
                        line = response.readline()
                        if not line:
                            break
                        stripped = line.strip()
                        if not stripped or stripped.startswith(b":"):
                            continue
                        if stripped.startswith(b"data:") and stripped != b"data: [DONE]":
                            received_payload = True
                            if first_response_ms is None:
                                first_response_ms = (time.monotonic() - started) * 1000.0
                else:
                    body = response.read()
                    received_payload = bool(body.strip())
                    if received_payload:
                        first_response_ms = (time.monotonic() - started) * 1000.0
                elapsed = time.monotonic() - started
                if not received_payload:
                    return RealProbeResult(False, elapsed, first_response_ms, "upstream returned an empty response")
                return RealProbeResult(True, elapsed, first_response_ms, "")
        except urllib.error.HTTPError as error:
            elapsed = time.monotonic() - started
            body = error.read().decode("utf-8", errors="replace")[:500]
            return RealProbeResult(False, elapsed, first_response_ms, f"HTTP {error.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return RealProbeResult(False, time.monotonic() - started, first_response_ms, str(error))


class StateStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS latency_samples (
                sample_key TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                channel_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                use_time REAL NOT NULL,
                frt_ms REAL
            )
            """
        )
        for column, declaration in (
            ("username", "TEXT NOT NULL DEFAULT ''"),
            ("token_name", "TEXT NOT NULL DEFAULT ''"),
            ("token_id", "INTEGER NOT NULL DEFAULT 0"),
            ("is_stream", "INTEGER NOT NULL DEFAULT 0"),
            ("request_id", "TEXT NOT NULL DEFAULT ''"),
            ("upstream_request_id", "TEXT NOT NULL DEFAULT ''"),
            ("group_name", "TEXT NOT NULL DEFAULT ''"),
        ):
            existing = {
                str(row["name"])
                for row in self.connection.execute("PRAGMA table_info(latency_samples)").fetchall()
            }
            if column not in existing:
                self.connection.execute(f"ALTER TABLE latency_samples ADD COLUMN {column} {declaration}")
        self.connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_latency_created_at ON latency_samples(created_at);
            CREATE INDEX IF NOT EXISTS idx_latency_channel_model ON latency_samples(channel_id, model_name, created_at);

            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                channel_type INTEGER NOT NULL,
                status INTEGER NOT NULL,
                models TEXT NOT NULL,
                channel_group TEXT NOT NULL,
                base_url TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channel_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                channel_name TEXT NOT NULL,
                success INTEGER NOT NULL,
                elapsed_ms REAL NOT NULL,
                frt_ms REAL,
                message TEXT NOT NULL,
                source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_channel_observation_time
                ON channel_observations(channel_id, observed_at);

            CREATE TABLE IF NOT EXISTS resource_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                system_cpu REAL,
                system_memory REAL,
                system_disk REAL,
                system_available_mb REAL,
                system_swap REAL,
                containers_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_resource_created_at ON resource_samples(created_at);

            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                resolution_body TEXT NOT NULL DEFAULT '',
                legacy_cause_missing INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                started_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                resolved_at INTEGER,
                last_notified_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_incident_status_time ON incidents(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_incident_key ON incidents(incident_key, id);
            """
        )
        incident_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(incidents)").fetchall()
        }
        added_resolution_body = "resolution_body" not in incident_columns
        if "resolution_body" not in incident_columns:
            self.connection.execute(
                "ALTER TABLE incidents ADD COLUMN resolution_body TEXT NOT NULL DEFAULT ''"
            )
        added_legacy_marker = "legacy_cause_missing" not in incident_columns
        if added_legacy_marker:
            self.connection.execute(
                "ALTER TABLE incidents ADD COLUMN legacy_cause_missing INTEGER NOT NULL DEFAULT 0"
            )
        if added_resolution_body:
            self.connection.execute(
                """
                UPDATE incidents
                SET resolution_body = body, legacy_cause_missing = 1
                WHERE status = 'resolved'
                """
            )
        elif added_legacy_marker:
            self.connection.execute(
                """
                UPDATE incidents
                SET legacy_cause_missing = 1
                WHERE status = 'resolved' AND resolution_body = body AND body != ''
                """
            )
        self.connection.commit()

    def get_json(self, key: str, default: Any) -> Any:
        row = self.connection.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return default

    def set_json(self, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.connection.commit()

    def record_collector_result(
        self,
        name: str,
        success: bool,
        error: str = "",
        stale_after_seconds: int = 300,
        now: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        statuses = self.get_json("collector_health", {})
        current = dict(statuses.get(name) or {})
        current.setdefault("first_attempt_at", timestamp)
        current["last_attempt_at"] = timestamp
        current["stale_after_seconds"] = max(1, int(stale_after_seconds))
        if success:
            current["last_success_at"] = timestamp
            current["consecutive_failures"] = 0
            current["last_error"] = ""
        else:
            current["consecutive_failures"] = int(current.get("consecutive_failures") or 0) + 1
            current["last_error"] = str(error).strip()[:1000]
        statuses[name] = current
        self.set_json("collector_health", statuses)

    def ensure_collector(
        self,
        name: str,
        stale_after_seconds: int,
        now: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        statuses = self.get_json("collector_health", {})
        current = dict(statuses.get(name) or {})
        current.setdefault("first_attempt_at", timestamp)
        current["stale_after_seconds"] = max(1, int(stale_after_seconds))
        current.setdefault("consecutive_failures", 0)
        current.setdefault("last_error", "")
        statuses[name] = current
        self.set_json("collector_health", statuses)

    def collector_health(self, now: int | None = None) -> dict[str, dict[str, Any]]:
        timestamp = int(time.time()) if now is None else int(now)
        result: dict[str, dict[str, Any]] = {}
        for name, raw in dict(self.get_json("collector_health", {})).items():
            detail = dict(raw or {})
            last_success = int(detail.get("last_success_at") or 0)
            first_attempt = int(detail.get("first_attempt_at") or timestamp)
            stale_after = max(1, int(detail.get("stale_after_seconds") or 300))
            reference = last_success or first_attempt
            age = max(0, timestamp - reference)
            detail["age_seconds"] = age
            detail["status"] = "stale" if age > stale_after else ("ok" if last_success else "starting")
            result[str(name)] = detail
        return result

    def upsert_channels(self, channels: Iterable[dict[str, Any]], now: int | None = None) -> None:
        updated_at = int(time.time()) if now is None else now
        channel_ids: list[int] = []
        for channel in channels:
            channel_id = int(channel.get("id") or 0)
            if channel_id <= 0:
                continue
            channel_ids.append(channel_id)
            self.connection.execute(
                """
                INSERT INTO channels(
                    channel_id, name, channel_type, status, models, channel_group, base_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    name = excluded.name,
                    channel_type = excluded.channel_type,
                    status = excluded.status,
                    models = excluded.models,
                    channel_group = excluded.channel_group,
                    base_url = excluded.base_url,
                    updated_at = excluded.updated_at
                """,
                (
                    channel_id,
                    str(channel.get("name") or f"channel-{channel_id}"),
                    int(channel.get("type") or 0),
                    int(channel.get("status") or 0),
                    str(channel.get("models") or ""),
                    str(channel.get("group") or ""),
                    str(channel.get("base_url") or ""),
                    updated_at,
                ),
            )
        if channel_ids:
            placeholders = ",".join("?" for _ in channel_ids)
            self.connection.execute(
                f"DELETE FROM channels WHERE channel_id NOT IN ({placeholders})",
                channel_ids,
            )
        else:
            self.connection.execute("DELETE FROM channels")
        self.connection.commit()

    def insert_channel_observations(
        self,
        observations: Iterable[ChannelObservation],
        observed_at: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if observed_at is None else observed_at
        self.connection.executemany(
            """
            INSERT INTO channel_observations(
                observed_at, channel_id, channel_name, success, elapsed_ms, frt_ms, message, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    timestamp,
                    item.channel_id,
                    item.name,
                    int(item.success),
                    item.elapsed_seconds * 1000.0,
                    item.first_response_ms,
                    item.message,
                    item.source,
                )
                for item in observations
            ],
        )
        self.connection.commit()

    def insert_resource_sample(
        self,
        metrics: dict[str, float],
        details: dict[str, Any],
        created_at: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if created_at is None else created_at
        self.connection.execute(
            """
            INSERT INTO resource_samples(
                created_at, system_cpu, system_memory, system_disk,
                system_available_mb, system_swap, containers_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                metrics.get("system_cpu"),
                metrics.get("system_memory"),
                metrics.get("system_disk"),
                metrics.get("system_available_mb"),
                metrics.get("system_swap"),
                json.dumps(details.get("containers") or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def record_alert_events(self, events: Iterable[AlertEvent], now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else now
        for event in events:
            incident_key = event.key or event.kind
            open_row = self.connection.execute(
                """
                SELECT id FROM incidents
                WHERE incident_key = ? AND status = 'open'
                ORDER BY id DESC LIMIT 1
                """,
                (incident_key,),
            ).fetchone()
            if event.recovery:
                if open_row is not None:
                    self.connection.execute(
                        """
                        UPDATE incidents
                        SET status = 'resolved', updated_at = ?, resolved_at = ?, resolution_body = ?
                        WHERE id = ?
                        """,
                        (timestamp, timestamp, event.body, int(open_row["id"])),
                    )
                continue
            if open_row is None:
                self.connection.execute(
                    """
                    INSERT INTO incidents(
                        incident_key, kind, severity, title, body, status,
                        started_at, updated_at, resolved_at, last_notified_at
                    ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, NULL, ?)
                    """,
                    (
                        incident_key,
                        event.kind,
                        event.severity,
                        event.title,
                        event.body,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE incidents
                    SET kind = ?, severity = ?, title = ?, body = ?,
                        updated_at = ?, last_notified_at = ?
                    WHERE id = ?
                    """,
                    (
                        event.kind,
                        event.severity,
                        event.title,
                        event.body,
                        timestamp,
                        timestamp,
                        int(open_row["id"]),
                    ),
                )
        self.connection.commit()

    def has_open_incident(self, incident_key: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM incidents WHERE incident_key = ? AND status = 'open' LIMIT 1",
            (incident_key,),
        ).fetchone()
        return row is not None

    def ingest_logs(
        self,
        logs: Iterable[dict[str, Any]],
        excluded_token_names: Iterable[str] = (),
    ) -> int:
        inserted = 0
        excluded_tokens = {item.strip() for item in excluded_token_names if item.strip()}
        for log in logs:
            if is_channel_test_log(log) or str(log.get("token_name") or "").strip() in excluded_tokens:
                continue
            created_at = int(log.get("created_at") or 0)
            request_id = str(log.get("request_id") or "")
            if request_id:
                sample_key = request_id
            else:
                raw_key = "|".join(
                    str(log.get(field) or "")
                    for field in ("id", "created_at", "channel", "model_name", "use_time")
                )
                sample_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
            other = _parse_other(log.get("other"))
            frt = other.get("frt")
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO latency_samples(
                    sample_key, created_at, channel_id, channel_name, model_name, use_time, frt_ms,
                    username, token_name, token_id, is_stream, request_id, upstream_request_id, group_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample_key,
                    created_at,
                    int(log.get("channel") or 0),
                    str(log.get("channel_name") or ""),
                    str(log.get("model_name") or "unknown"),
                    float(log.get("use_time") or 0),
                    float(frt) if isinstance(frt, (int, float)) and frt > 0 else None,
                    str(log.get("username") or ""),
                    str(log.get("token_name") or ""),
                    int(log.get("token_id") or 0),
                    int(bool(log.get("is_stream"))),
                    request_id,
                    str(log.get("upstream_request_id") or ""),
                    str(log.get("group") or ""),
                ),
            )
            inserted += cursor.rowcount
        self.connection.commit()
        return inserted

    def recent_latency_groups(self, since_timestamp: int) -> list[tuple[int, str, str]]:
        rows = self.connection.execute(
            """
            SELECT DISTINCT channel_id, channel_name, model_name
            FROM latency_samples
            WHERE created_at >= ?
            """,
            (since_timestamp,),
        ).fetchall()
        return [(int(row[0]), str(row[1]), str(row[2])) for row in rows]

    def recent_latency_samples(
        self,
        channel_id: int,
        model_name: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT use_time, frt_ms, created_at, request_id
            FROM latency_samples
            WHERE channel_id = ? AND model_name = ?
            ORDER BY created_at DESC, sample_key DESC
            LIMIT ?
            """,
            (channel_id, model_name, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def latency_summary(self, since_timestamp: int, slow_seconds: float) -> list[LatencySummary]:
        rows = self.connection.execute(
            """
            SELECT channel_id, channel_name, model_name, use_time, frt_ms
            FROM latency_samples
            WHERE created_at >= ?
            """,
            (since_timestamp,),
        ).fetchall()
        logs = [
            {
                "channel": row[0],
                "channel_name": row[1],
                "model_name": row[2],
                "use_time": row[3],
                "other": {"frt": row[4]} if row[4] is not None else {},
            }
            for row in rows
        ]
        return summarize_logs(logs, slow_seconds)

    def prune(self, before_timestamp: int) -> None:
        self.connection.execute("DELETE FROM latency_samples WHERE created_at < ?", (before_timestamp,))
        self.connection.execute("DELETE FROM channel_observations WHERE observed_at < ?", (before_timestamp,))
        self.connection.execute("DELETE FROM resource_samples WHERE created_at < ?", (before_timestamp,))
        self.connection.commit()


class ResourceCollector:
    def __init__(self, disk_path: str, docker_container_names: Iterable[str]):
        self.disk_path = disk_path
        self.docker_container_names = tuple(dict.fromkeys(docker_container_names))
        self._docker_client = None

    def collect(self) -> tuple[dict[str, float], dict[str, Any]]:
        try:
            import psutil
        except ImportError as error:
            raise RuntimeError("psutil is required for resource monitoring") from error

        host_proc = os.getenv("HOST_PROC", "").strip()
        if host_proc:
            psutil.PROCFS_PATH = host_proc
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        metrics: dict[str, float] = {
            "system_cpu": float(psutil.cpu_percent(interval=0.2)),
            "system_memory": float(memory.percent),
            "system_disk": float(psutil.disk_usage(self.disk_path).percent),
            "system_available_mb": float(memory.available / 1024 / 1024),
            "system_swap": float(swap.percent),
        }
        details: dict[str, Any] = {"containers": {}}

        if self.docker_container_names:
            try:
                import docker

                if self._docker_client is None:
                    self._docker_client = docker.from_env()
                for index, name in enumerate(self.docker_container_names):
                    try:
                        container = self._docker_client.containers.get(name)
                        container.reload()
                        stats = container.stats(stream=False)
                        cpu_delta = (
                            stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
                            - stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
                        )
                        system_delta = (
                            stats.get("cpu_stats", {}).get("system_cpu_usage", 0)
                            - stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
                        )
                        online_cpus = stats.get("cpu_stats", {}).get("online_cpus") or 1
                        container_cpu = 0.0
                        if cpu_delta > 0 and system_delta > 0:
                            container_cpu = cpu_delta / system_delta * online_cpus * 100.0
                        memory_stats = stats.get("memory_stats", {})
                        memory_usage = float(memory_stats.get("usage") or 0)
                        memory_limit = float(memory_stats.get("limit") or 0)
                        container_memory = memory_usage / memory_limit * 100.0 if memory_limit > 0 else 0.0
                        item = {
                            "status": container.status,
                            "restarts": int(container.attrs.get("RestartCount") or 0),
                            "cpu": container_cpu,
                            "memory": container_memory,
                            "memory_mb": memory_usage / 1024 / 1024,
                            "oom_killed": bool(container.attrs.get("State", {}).get("OOMKilled")),
                        }
                    except Exception as error:
                        item = {
                            "status": "unknown",
                            "restarts": 0,
                            "cpu": 0.0,
                            "memory": 0.0,
                            "memory_mb": 0.0,
                            "oom_killed": False,
                            "error": str(error),
                        }
                    details["containers"][name] = item
                    if index == 0:
                        metrics["container_cpu"] = float(item["cpu"])
                        metrics["container_memory"] = float(item["memory"])
                        details["container_status"] = item["status"]
                        details["container_restarts"] = item["restarts"]
                        if item.get("error"):
                            details["container_error"] = item["error"]
            except Exception as error:
                details["container_status"] = "unknown"
                details["container_error"] = str(error)
        return metrics, details


class Mailer:
    name = "email"

    def __init__(self, config: Config):
        self.config = config

    def send(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = f"{self.config.subject_prefix} {subject}"
        message["From"] = self.config.smtp_from
        message["To"] = ", ".join(self.config.smtp_to)
        message.set_content(body)
        message.add_alternative(notification_html(subject, body), subtype="html")

        if self.config.smtp_ssl:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=20,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=20)
        with client:
            if self.config.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
            if self.config.smtp_user:
                client.login(self.config.smtp_user, self.config.smtp_password)
            client.send_message(message)


def notification_text(prefix: str, subject: str, body: str, limit: int = 3800) -> str:
    content = f"{prefix} {subject}\n\n{body}".strip()
    if len(content) <= limit:
        return content
    return content[: limit - 12] + "\n…内容已截断"


def notification_html(subject: str, body: str) -> str:
    blocks: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        escaped = html.escape(line)
        if line.startswith("【") and line.endswith("】"):
            blocks.append(f"<h2>{html.escape(line[1:-1])}</h2>")
        elif line.startswith("结论："):
            blocks.append(f'<div class="summary">{escaped}</div>')
        elif line.startswith(("🔴", "🟠", "🟢", "✅", "❌", "⚪", "ℹ️")):
            blocks.append(f'<div class="item">{escaped}</div>')
        elif raw_line.startswith("   "):
            blocks.append(f'<div class="detail">{escaped}</div>')
        else:
            blocks.append(f"<p>{escaped}</p>")
    return "".join(
        [
            "<!doctype html><html><head><meta charset=\"utf-8\"><style>",
            "body{margin:0;background:#f4f7fb;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}",
            ".wrap{max-width:680px;margin:0 auto;padding:28px 18px}.panel{background:#fff;border:1px solid #e6ebf2;border-radius:16px;padding:26px;box-shadow:0 10px 30px rgba(31,42,68,.08)}",
            "h1{margin:0 0 18px;font-size:24px}h2{margin:24px 0 10px;padding-top:18px;border-top:1px solid #edf0f5;font-size:17px}",
            "p{margin:8px 0;color:#5a6475;font-size:14px}.summary{padding:14px 16px;background:#f0f7ff;border-left:4px solid #3578e5;border-radius:8px;font-weight:700;line-height:1.6}",
            ".item{margin:8px 0;padding:10px 12px;background:#f8fafc;border-radius:8px;line-height:1.5}.detail{margin:-5px 0 8px 34px;color:#657084;font-size:13px}",
            ".foot{margin-top:22px;color:#8a94a6;font-size:12px;text-align:center}</style></head><body><div class=\"wrap\"><div class=\"panel\">",
            f"<h1>{html.escape(subject)}</h1>",
            *blocks,
            '<div class="foot">New API Monitor · Automated notification</div></div></div></body></html>',
        ]
    )


def _human_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 1:
        return f"{seconds * 1000:.0f}毫秒"
    if seconds < 60:
        return f"{seconds:.1f}秒"
    total_seconds = int(round(seconds))
    minutes, remainder = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes:02d}分{remainder:02d}秒"
    return f"{minutes}分{remainder:02d}秒"


def build_periodic_report(
    channels: Iterable[ChannelObservation],
    latency: Iterable[LatencySummary],
    resources: dict[str, float],
    resource_details: dict[str, Any],
    *,
    slow_seconds: float,
    period_seconds: int,
    channel_slow_seconds: float = 30.0,
    resource_thresholds: dict[str, float] | None = None,
    generated_at: int | None = None,
) -> tuple[str, str]:
    generated_at = int(time.time()) if generated_at is None else generated_at
    channel_items = sorted(list(channels), key=lambda item: (item.success, -item.elapsed_seconds, item.name))
    failed_channels = [item for item in channel_items if not item.success]
    slow_channels = [
        item for item in channel_items
        if item.success and item.elapsed_seconds >= channel_slow_seconds
    ]
    latency_items = sorted(
        list(latency),
        key=lambda item: (
            not (item.p95_seconds >= slow_seconds or item.average_seconds >= slow_seconds),
            -item.p95_seconds,
            -item.slow_count,
            -item.count,
        ),
    )
    risky_latency = [
        item
        for item in latency_items
        if item.p95_seconds >= slow_seconds or item.average_seconds >= slow_seconds
    ]

    effective_resource_thresholds = {
        "system_cpu": 85.0,
        "system_memory": 85.0,
        "system_disk": 80.0,
        "system_swap": 80.0,
        "container_cpu": 85.0,
        "container_memory": 85.0,
    }
    effective_resource_thresholds.update(resource_thresholds or {})
    risky_resources = [
        key
        for key, threshold in effective_resource_thresholds.items()
        if key in resources and float(resources[key]) >= threshold
    ]
    container_status = str(resource_details.get("container_status") or "unknown")
    container_restarts = int(resource_details.get("container_restarts") or 0)
    container_abnormal = container_status not in {"running", "healthy"} or container_restarts > 0

    if failed_channels or risky_resources or container_abnormal:
        status = "存在异常"
    elif risky_latency or slow_channels:
        status = "需要关注"
    else:
        status = "运行正常"

    findings: list[str] = []
    if failed_channels:
        findings.append(f"异常渠道 {len(failed_channels)} 个")
    elif channel_items:
        findings.append("渠道全部可用")
    else:
        findings.append("暂无渠道探测数据")
    if risky_latency:
        findings.append(f"发现 {len(risky_latency)} 个高延迟模型")
    if risky_resources:
        findings.append(f"{len(risky_resources)} 项资源超过阈值")
    if container_abnormal:
        findings.append("容器状态需要检查")

    period_label = (
        f"最近 {period_seconds // 86400} 天"
        if period_seconds >= 86400 and period_seconds % 86400 == 0
        else f"最近 {max(1, period_seconds // 3600)} 小时"
    )
    conclusion = findings[0]
    if len(findings) > 1:
        conclusion += "，但" + "，并".join(findings[1:])
    lines = [
        f"{'🔴' if status == '存在异常' else '🟠' if status == '需要关注' else '🟢'} New API 监控周期报告",
        f"结论：{conclusion}。",
        f"报告周期：{period_label} · 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(generated_at))}",
        "",
        "【渠道健康】",
    ]
    if not channel_items:
        lines.append("⚪ 暂无探测数据")
    for item in channel_items:
        if item.success:
            icon = "🟠" if item.elapsed_seconds >= channel_slow_seconds else "✅"
            note = " · 探测偏慢" if item.elapsed_seconds >= channel_slow_seconds else ""
            lines.append(f"{icon} {item.name} · {_human_duration(item.elapsed_seconds)}{note}")
        else:
            message = item.message.strip().replace("\n", " ") or "探测失败"
            lines.append(f"❌ {item.name} · {message[:160]}")

    lines.extend(["", "【请求性能】"])
    if not latency_items:
        lines.append("⚪ 当前周期暂无消费日志")
    for item in latency_items:
        slow_ratio = item.slow_count / item.count * 100 if item.count else 0.0
        risky = item.p95_seconds >= slow_seconds or item.average_seconds >= slow_seconds
        icon = "🔴" if risky else "✅"
        first_response = "暂无" if item.average_frt_ms is None else _human_duration(item.average_frt_ms / 1000)
        lines.append(f"{icon} {item.channel_name} / {item.model_name}")
        lines.append(
            f"   P95 {_human_duration(item.p95_seconds)} · 平均 {_human_duration(item.average_seconds)} · "
            f"首字 {first_response}"
        )
        lines.append(f"   慢请求 {item.slow_count}/{item.count}（{slow_ratio:.1f}%） · 总请求 {item.count}")

    lines.extend(["", "【主机与容器】"])
    resource_labels = [
        ("system_cpu", "CPU"),
        ("system_memory", "内存"),
        ("system_disk", "磁盘"),
        ("system_swap", "Swap"),
        ("container_cpu", "容器 CPU"),
        ("container_memory", "容器内存"),
    ]
    if not resources and not resource_details:
        lines.append("⚪ 暂无资源数据")
    else:
        metric_parts = []
        for key, label in resource_labels:
            if key not in resources:
                continue
            icon = "🔴" if float(resources[key]) >= effective_resource_thresholds[key] else "✅"
            metric_parts.append(f"{icon} {label} {float(resources[key]):.1f}%")
        lines.extend(metric_parts)
        if "system_available_mb" in resources:
            available_mb = float(resources["system_available_mb"])
            available_text = f"{available_mb / 1024:.1f} GB" if available_mb >= 1024 else f"{available_mb:.0f} MB"
            lines.append(f"ℹ️ 可用内存 {available_text}")
        if resource_details:
            status_icon = "✅" if not container_abnormal else "🔴"
            lines.append(f"{status_icon} 容器 {container_status} · 重启 {container_restarts} 次")

    lines.extend(["", "提示：🔴 需立即处理 · 🟠 建议关注 · ✅ 正常"])
    return f"周期报告 · {status}", "\n".join(lines)


class WeComAppNotifier:
    name = "wecom_app"

    def __init__(
        self,
        corp_id: str,
        agent_id: int,
        secret: str,
        to_user: str,
        to_party: str,
        to_tag: str,
        prefix: str = "[New API监控]",
    ):
        self.corp_id = corp_id
        self.agent_id = agent_id
        self.secret = secret
        self.to_user = to_user
        self.to_party = to_party
        self.to_tag = to_tag
        self.prefix = prefix
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def _token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken?" + urllib.parse.urlencode(
            {"corpid": self.corp_id, "corpsecret": self.secret}
        )
        result = request_json(url)
        if int(result.get("errcode") or 0) != 0 or not result.get("access_token"):
            raise RuntimeError(f"WeCom token failed: {result.get('errmsg') or result.get('errcode')}")
        self._access_token = str(result["access_token"])
        self._access_token_expires_at = now + max(60, int(result.get("expires_in") or 7200) - 300)
        return self._access_token

    def send(self, subject: str, body: str) -> None:
        token = self._token()
        url = "https://qyapi.weixin.qq.com/cgi-bin/message/send?" + urllib.parse.urlencode(
            {"access_token": token}
        )
        result = request_json(
            url,
            {
                "touser": self.to_user,
                "toparty": self.to_party,
                "totag": self.to_tag,
                "msgtype": "text",
                "agentid": self.agent_id,
                "text": {"content": notification_text(self.prefix, subject, body)},
                "safe": 0,
                "enable_duplicate_check": 1,
                "duplicate_check_interval": 1800,
            },
        )
        if int(result.get("errcode") or 0) != 0:
            raise RuntimeError(f"WeCom application failed: {result.get('errmsg') or result.get('errcode')}")


class WeComWebhookNotifier:
    name = "wecom_webhook"

    def __init__(self, webhook_url: str, prefix: str = "[New API监控]"):
        self.webhook_url = webhook_url
        self.prefix = prefix

    def send(self, subject: str, body: str) -> None:
        result = request_json(
            self.webhook_url,
            {
                "msgtype": "text",
                "text": {"content": notification_text(self.prefix, subject, body)},
            },
        )
        if int(result.get("errcode") or 0) != 0:
            raise RuntimeError(f"WeCom webhook failed: {result.get('errmsg') or result.get('errcode')}")


class FeishuAppNotifier:
    name = "feishu_app"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        receive_id_type: str,
        receive_id: str,
        prefix: str = "[New API监控]",
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.receive_id_type = receive_id_type
        self.receive_id = receive_id
        self.prefix = prefix
        self._tenant_token = ""
        self._tenant_token_expires_at = 0.0

    def _token(self) -> str:
        now = time.time()
        if self._tenant_token and now < self._tenant_token_expires_at:
            return self._tenant_token
        result = request_json(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        if int(result.get("code") or 0) != 0 or not result.get("tenant_access_token"):
            raise RuntimeError(f"Feishu token failed: {result.get('msg') or result.get('code')}")
        self._tenant_token = str(result["tenant_access_token"])
        self._tenant_token_expires_at = now + max(60, int(result.get("expire") or 7200) - 300)
        return self._tenant_token

    def send(self, subject: str, body: str) -> None:
        token = self._token()
        url = "https://open.feishu.cn/open-apis/im/v1/messages?" + urllib.parse.urlencode(
            {"receive_id_type": self.receive_id_type}
        )
        result = request_json(
            url,
            {
                "receive_id": self.receive_id,
                "msg_type": "text",
                "content": json.dumps(
                    {"text": notification_text(self.prefix, subject, body)},
                    ensure_ascii=False,
                ),
            },
            {"Authorization": f"Bearer {token}"},
        )
        if int(result.get("code") or 0) != 0:
            raise RuntimeError(f"Feishu application failed: {result.get('msg') or result.get('code')}")


class FeishuWebhookNotifier:
    name = "feishu_webhook"

    def __init__(self, webhook_url: str, secret: str = "", prefix: str = "[New API监控]"):
        self.webhook_url = webhook_url
        self.secret = secret
        self.prefix = prefix

    def send(self, subject: str, body: str) -> None:
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": notification_text(self.prefix, subject, body)},
        }
        if self.secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{self.secret}".encode("utf-8")
            payload["timestamp"] = timestamp
            payload["sign"] = base64.b64encode(
                hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
            ).decode("ascii")
        result = request_json(self.webhook_url, payload)
        if int(result.get("code") or result.get("StatusCode") or 0) != 0:
            raise RuntimeError(
                f"Feishu webhook failed: {result.get('msg') or result.get('StatusMessage') or result.get('code')}"
            )


class NotificationDispatcher:
    def __init__(self, config: Config, test_channel: str | None = None):
        self.senders: list[Any] = []
        if config.email_enabled or test_channel == "email":
            self.senders.append(Mailer(config))
        if config.wecom_app_enabled or test_channel == "wecom_app":
            self.senders.append(
                WeComAppNotifier(
                    config.wecom_corp_id,
                    config.wecom_agent_id,
                    config.wecom_app_secret,
                    config.wecom_to_user,
                    config.wecom_to_party,
                    config.wecom_to_tag,
                    config.subject_prefix,
                )
            )
        if config.wecom_webhook_enabled or test_channel == "wecom_webhook":
            self.senders.append(WeComWebhookNotifier(config.wecom_webhook_url, config.subject_prefix))
        if config.feishu_app_enabled or test_channel == "feishu_app":
            self.senders.append(
                FeishuAppNotifier(
                    config.feishu_app_id,
                    config.feishu_app_secret,
                    config.feishu_receive_id_type,
                    config.feishu_receive_id,
                    config.subject_prefix,
                )
            )
        if config.feishu_webhook_enabled or test_channel == "feishu_webhook":
            self.senders.append(
                FeishuWebhookNotifier(
                    config.feishu_webhook_url,
                    config.feishu_webhook_secret,
                    config.subject_prefix,
                )
            )

    def send(self, subject: str, body: str, channel: str = "all") -> dict[str, list[str]]:
        selected = [sender for sender in self.senders if channel == "all" or sender.name == channel]
        if channel != "all" and not selected:
            raise ValueError(f"notification channel is not enabled: {channel}")
        succeeded: list[str] = []
        failed: list[str] = []
        errors: list[str] = []
        for sender in selected:
            try:
                sender.send(subject, body)
                succeeded.append(sender.name)
            except Exception as error:
                failed.append(sender.name)
                errors.append(f"{sender.name}: {error}")
                LOGGER.exception("notification delivery failed: %s", sender.name)
        if selected and not succeeded:
            raise RuntimeError("; ".join(errors))
        return {"succeeded": succeeded, "failed": failed}


class ChannelSyncWorker:
    def __init__(
        self,
        client: NewAPIClient,
        store: StateStore,
        on_snapshot: Callable[[list[dict[str, Any]]], None],
        on_result: Callable[[bool, str], None] | None = None,
    ):
        self.client = client
        self.store = store
        self.on_snapshot = on_snapshot
        self.on_result = on_result

    def sync_once(self) -> list[dict[str, Any]]:
        channels = self.client.get_channels()
        self.store.upsert_channels(channels)
        self.on_snapshot(channels)
        return channels

    def run(self, stop_event: threading.Event, interval_seconds: int) -> None:
        try:
            while not stop_event.is_set():
                error_message = ""
                try:
                    channels = self.sync_once()
                    success = True
                    LOGGER.info(
                        "channel sync complete: total=%d enabled=%d",
                        len(channels),
                        sum(int(channel.get("status") or 0) == 1 for channel in channels),
                    )
                except Exception as error:
                    success = False
                    error_message = str(error)
                    LOGGER.exception("channel sync failed")
                # Every completed attempt refreshes collector freshness. Alert state
                # transitions are deduplicated separately by ServiceStateTracker.
                if self.on_result is not None:
                    self.on_result(success, error_message)
                if stop_event.wait(max(1, interval_seconds)):
                    break
        finally:
            self.store.connection.close()


class MonitorApp:
    def __init__(self, config: Config):
        config.validate()
        self.config = config
        self.client = NewAPIClient(config)
        self.relay_probe_client = RelayProbeClient(config) if config.real_probe_rules else None
        self.store = StateStore(config.state_db)
        self.notifier = NotificationDispatcher(config)
        self.resource_collector = ResourceCollector(config.disk_path, config.docker_container_names)
        self.service_tracker = ServiceStateTracker(str(self.store.get_json("service_state", "unknown")))
        self.channel_tracker = ChannelStateTracker(self.store.get_json("channel_states", {}))
        self.latency_tracker = LatencyStateTracker(
            self.store.get_json("latency_states", {}),
            slow_seconds=config.slow_request_seconds,
            hard_limit_seconds=config.latency_hard_limit_seconds,
            reminder_seconds=config.latency_reminder_seconds,
        )
        thresholds = {
            "system_cpu": config.system_cpu_threshold,
            "system_memory": config.system_memory_threshold,
            "system_disk": config.system_disk_threshold,
            "container_cpu": config.container_cpu_threshold,
            "container_memory": config.container_memory_threshold,
        }
        self.resource_tracker = ResourceStateTracker(
            thresholds,
            config.resource_sustain_seconds,
            self.store.get_json("resource_states", {}),
        )
        self.collector_thresholds = {
            "channel_sync": max(60, config.channel_sync_interval_seconds * 4),
            "channel_probe": max(300, config.channel_interval_seconds * 3),
            "logs": max(120, config.log_interval_seconds * 4),
            "resources": max(90, config.resource_interval_seconds * 4),
        }
        for collector_name, threshold in self.collector_thresholds.items():
            self.store.ensure_collector(collector_name, threshold)
        self.collector_tracker = CollectorFreshnessTracker(
            self.store.get_json("collector_alert_states", {})
        )
        self.channel_sync_results: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self.channel_snapshot: list[dict[str, Any]] | None = None
        self.latest_channels: list[ChannelObservation] = []
        self.latest_resources: dict[str, float] = {}
        self.latest_resource_details: dict[str, Any] = {}
        saved_container_states = self.store.get_json("container_states", {})
        if not saved_container_states:
            legacy_state = str(self.store.get_json("container_state", "unknown"))
            saved_container_states = {config.docker_container_name: legacy_state} if config.docker_container_name else {}
        self.container_states = dict(saved_container_states)
        self.container_restarts = dict(self.store.get_json("container_restarts", {}))

    def _send_events(self, events: list[AlertEvent]) -> None:
        if not events:
            return
        self.store.record_alert_events(events)
        subject = "；".join(event.title for event in events)
        body = "\n\n".join(f"[{event.title}]\n{event.body}" for event in events)
        result = self.notifier.send(subject, body)
        LOGGER.info(
            "sent %d alert events through %s; failed=%s",
            len(events),
            ",".join(result["succeeded"]) or "none",
            ",".join(result["failed"]) or "none",
        )

    def _record_collector_result(self, name: str, success: bool, error: str = "") -> None:
        self.store.record_collector_result(
            name,
            success,
            error,
            stale_after_seconds=self.collector_thresholds[name],
        )

    def _evaluate_collector_health(self) -> None:
        previous_states = dict(self.collector_tracker.states)
        events = self.collector_tracker.evaluate(self.store.collector_health())
        try:
            self._send_events(events)
        except Exception:
            self.collector_tracker.states = previous_states
            raise
        self.store.set_json("collector_alert_states", self.collector_tracker.states)

    def _record_service_availability(self, success: bool, message: str = "") -> None:
        previous_state = self.service_tracker.state
        events = self.service_tracker.evaluate(success, message)
        try:
            self._send_events(events)
        except Exception:
            self.service_tracker.state = previous_state
            raise
        self.store.set_json("service_state", self.service_tracker.state)

    def sync_channels(self) -> None:
        try:
            channels = self.client.get_channels()
        except Exception as error:
            self._record_service_availability(False, str(error))
            raise
        self._record_service_availability(True)
        self.store.upsert_channels(channels)
        self.channel_snapshot = channels
        LOGGER.info(
            "channel sync complete: total=%d enabled=%d",
            len(channels),
            sum(int(channel.get("status") or 0) == 1 for channel in channels),
        )

    def _publish_channel_snapshot(self, channels: list[dict[str, Any]]) -> None:
        self.channel_snapshot = channels

    def _queue_channel_sync_result(self, success: bool, message: str) -> None:
        self.channel_sync_results.put((success, message))

    def _drain_channel_sync_results(self) -> None:
        while True:
            try:
                success, message = self.channel_sync_results.get_nowait()
            except queue.Empty:
                return
            try:
                self._record_collector_result("channel_sync", success, message)
                self._record_service_availability(success, message)
            except Exception:
                LOGGER.exception("channel sync state notification failed")

    def _run_channel_sync_worker(self, stop_event: threading.Event) -> None:
        try:
            worker = ChannelSyncWorker(
                NewAPIClient(self.config, timeout_seconds=15),
                StateStore(self.config.state_db),
                self._publish_channel_snapshot,
                self._queue_channel_sync_result,
            )
            worker.run(stop_event, self.config.channel_sync_interval_seconds)
        except Exception:
            LOGGER.exception("channel sync worker stopped unexpectedly")

    def check_channels(self) -> None:
        if self.channel_snapshot is None:
            self.sync_channels()
        observations: list[ChannelObservation] = []
        for channel in list(self.channel_snapshot or []):
            channel_id = int(channel.get("id") or 0)
            name = str(channel.get("name") or f"channel-{channel_id}")
            if channel_id <= 0 or int(channel.get("status") or 0) != 1:
                continue
            channel_config = self.config.channel_settings.get(channel_id, {})
            if channel_config.get("maintenance_mode"):
                continue
            if not any(
                int(current.get("id") or 0) == channel_id
                and int(current.get("status") or 0) == 1
                for current in self.channel_snapshot or []
            ):
                continue
            started = time.monotonic()
            try:
                probe_rule = self.config.real_probe_rules.get(channel_id)
                if probe_rule is not None and self.relay_probe_client is not None:
                    probe = self.relay_probe_client.probe(probe_rule)
                    elapsed = probe.elapsed_seconds
                    first_response_ms = probe.first_response_ms
                    success = probe.success
                    message = probe.message
                    source = "real"
                    if success and (
                        elapsed > self.config.channel_slow_seconds
                        or (first_response_ms or 0) > self.config.channel_slow_seconds * 1000.0
                    ):
                        success = False
                        message = (
                            f"真实请求耗时超过阈值 {self.config.channel_slow_seconds:.0f}s："
                            f"总耗时 {elapsed:.3f}s，首字 {(first_response_ms or 0) / 1000.0:.3f}s"
                        )
                else:
                    result = self.client.test_channel(channel_id)
                    elapsed = float(result.get("time") or (time.monotonic() - started))
                    first_response_ms = None
                    success = bool(result.get("success"))
                    message = str(result.get("message") or "")
                    source = "builtin"
                    if success and elapsed > self.config.channel_slow_seconds:
                        success = False
                        message = f"探测耗时 {elapsed:.3f}s 超过阈值 {self.config.channel_slow_seconds:.3f}s"
            except Exception as error:
                elapsed = time.monotonic() - started
                first_response_ms = None
                success = False
                message = str(error)
                source = "real" if channel_id in self.config.real_probe_rules else "builtin"
            observations.append(
                ChannelObservation(
                    channel_id,
                    name,
                    success,
                    elapsed,
                    message,
                    source,
                    first_response_ms,
                )
            )

        self.latest_channels = observations
        self.store.insert_channel_observations(observations)
        previous_states = dict(self.channel_tracker.states)
        alert_observations = [
            item for item in observations
            if self.config.channel_settings.get(item.channel_id, {}).get("alert_enabled", True)
        ]
        events = self.channel_tracker.evaluate(alert_observations)
        try:
            self._send_events(events)
        except Exception:
            self.channel_tracker.states = previous_states
            raise
        self.store.set_json("channel_states", self.channel_tracker.states)
        LOGGER.info("channel check complete: total=%d healthy=%d", len(observations), sum(item.success for item in observations))

    def collect_logs(self) -> None:
        now = int(time.time())
        last_cursor = int(
            self.store.get_json("log_cursor", now - self.config.log_initial_lookback_seconds)
        )
        start_timestamp = max(0, last_cursor - self.config.log_overlap_seconds)
        logs = self.client.get_logs(start_timestamp, now)
        inserted = self.store.ingest_logs(logs, self.config.excluded_token_names)
        previous_latency_states = dict(self.latency_tracker.states)
        latency_events: list[AlertEvent] = []
        for channel_id, channel_name, model_name in self.store.recent_latency_groups(
            now - self.config.retention_days * 86400
        ):
            samples = self.store.recent_latency_samples(channel_id, model_name, 10)
            latency_events.extend(
                self.latency_tracker.evaluate(
                    f"{channel_id}:{model_name}",
                    f"{channel_name}/{model_name}",
                    samples,
                    now=now,
                )
            )
        try:
            self._send_events(latency_events)
        except Exception:
            self.latency_tracker.states = previous_latency_states
            raise
        self.store.set_json("latency_states", self.latency_tracker.states)
        self.store.set_json("log_cursor", now)
        self.store.prune(now - self.config.retention_days * 86400)
        LOGGER.info("log collection complete: fetched=%d inserted=%d", len(logs), inserted)

    def collect_resources(self) -> None:
        metrics, details = self.resource_collector.collect()
        self.latest_resources = metrics
        self.latest_resource_details = details
        self.store.insert_resource_sample(metrics, details)
        events = self.resource_tracker.evaluate(metrics)

        for container_name, container in (details.get("containers") or {}).items():
            new_container_state = str(container.get("status") or "unknown")
            previous_state = str(self.container_states.get(container_name) or "unknown")
            previous_restarts = int(self.container_restarts.get(container_name) or 0)
            new_restarts = int(container.get("restarts") or 0)
            incident_key = f"container:{container_name}"
            if new_container_state == "running" and self.store.has_open_incident(incident_key):
                events.append(
                    AlertEvent(
                        "container_recovered",
                        f"容器恢复：{container_name}",
                        f"容器状态：{new_container_state}",
                        key=incident_key,
                        severity="info",
                        recovery=True,
                    )
                )
            elif new_container_state != previous_state and new_container_state != "running":
                events.append(
                    AlertEvent(
                        "container_failed",
                        f"容器异常：{container_name}",
                        f"容器状态：{new_container_state}\n{container.get('error', '')}",
                        key=incident_key,
                        severity="critical",
                    )
                )
            if new_restarts > previous_restarts and previous_restarts > 0:
                events.append(
                    AlertEvent(
                        "container_restarted",
                        f"容器发生重启：{container_name}",
                        f"重启次数从 {previous_restarts} 增加到 {new_restarts}",
                        key=f"container-restart:{container_name}",
                        severity="warning",
                    )
                )
            if bool(container.get("oom_killed")):
                events.append(
                    AlertEvent(
                        "container_oom",
                        f"容器 OOM：{container_name}",
                        "容器因内存不足被系统终止。",
                        key=f"container-oom:{container_name}",
                        severity="critical",
                    )
                )
            self.container_states[container_name] = new_container_state
            self.container_restarts[container_name] = new_restarts

        self.store.set_json("resource_states", self.resource_tracker.states)
        self.store.set_json("container_states", self.container_states)
        self.store.set_json("container_restarts", self.container_restarts)
        self._send_events(events)
        LOGGER.info("resource collection complete: %s", json.dumps(metrics, ensure_ascii=False))

    def send_report(self) -> None:
        now = int(time.time())
        summary = self.store.latency_summary(now - self.config.report_interval_seconds, self.config.slow_request_seconds)
        subject, body = build_periodic_report(
            self.latest_channels,
            summary,
            self.latest_resources,
            self.latest_resource_details,
            slow_seconds=self.config.slow_request_seconds,
            period_seconds=self.config.report_interval_seconds,
            channel_slow_seconds=self.config.channel_slow_seconds,
            resource_thresholds={
                "system_cpu": self.config.system_cpu_threshold,
                "system_memory": self.config.system_memory_threshold,
                "system_disk": self.config.system_disk_threshold,
                "container_cpu": self.config.container_cpu_threshold,
                "container_memory": self.config.container_memory_threshold,
            },
            generated_at=now,
        )
        self.notifier.send(subject, body)
        LOGGER.info("periodic report sent")

    def run_forever(self, stop_event: Any | None = None) -> None:
        if self.config.send_startup_email:
            try:
                self.notifier.send("监控程序启动", f"监控目标：{self.config.base_url}")
            except Exception:
                LOGGER.exception("startup email failed")

        channel_sync_stop = threading.Event()
        channel_sync_thread = threading.Thread(
            target=self._run_channel_sync_worker,
            args=(channel_sync_stop,),
            name="newapi-channel-sync",
            daemon=True,
        )
        channel_sync_thread.start()
        next_channel = 0.0
        next_log = 0.0
        next_resource = 0.0
        next_report = time.monotonic() + self.config.report_interval_seconds
        try:
            while stop_event is None or not stop_event.is_set():
                self._drain_channel_sync_results()
                now = time.monotonic()
                if now >= next_channel and self.channel_snapshot is not None:
                    try:
                        self.check_channels()
                        self._record_collector_result("channel_probe", True)
                    except Exception as error:
                        self._record_collector_result("channel_probe", False, str(error))
                        LOGGER.exception("channel check failed")
                    next_channel = now + self.config.channel_interval_seconds
                if now >= next_log:
                    try:
                        self.collect_logs()
                        self._record_collector_result("logs", True)
                    except Exception as error:
                        self._record_collector_result("logs", False, str(error))
                        LOGGER.exception("log collection failed")
                    next_log = now + self.config.log_interval_seconds
                if now >= next_resource:
                    try:
                        self.collect_resources()
                        self._record_collector_result("resources", True)
                    except Exception as error:
                        self._record_collector_result("resources", False, str(error))
                        LOGGER.exception("resource collection failed")
                    next_resource = now + self.config.resource_interval_seconds
                if now >= next_report:
                    try:
                        self.send_report()
                    except Exception:
                        LOGGER.exception("periodic report failed")
                    next_report = now + self.config.report_interval_seconds
                try:
                    self._evaluate_collector_health()
                except Exception:
                    LOGGER.exception("collector freshness notification failed")
                if stop_event is None:
                    time.sleep(self.config.poll_seconds)
                elif stop_event.wait(self.config.poll_seconds):
                    break
        finally:
            channel_sync_stop.set()
            channel_sync_thread.join(timeout=20)
            self._drain_channel_sync_results()
            self.store.connection.close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = Config.from_env()
    MonitorApp(config).run_forever()


if __name__ == "__main__":
    main()
