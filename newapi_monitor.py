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
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Callable, Iterable

from monitoring_core.alerting import (
    AlertEvent,
    ChannelObservation,
    ChannelStateTracker,
    CollectorFreshnessTracker,
    ContainerStateTracker,
    LatencyStateTracker,
    LatencySummary,
    LatencyWindowDecision,
    ProbeCredentialStateTracker,
    RealProbeResult,
    RealProbeRule,
    ResourceStateTracker,
    ServiceStateTracker,
    _parse_other,
    build_auth_headers,
    evaluate_latency_window,
    is_channel_test_log,
    parse_real_probe_rules,
    summarize_logs,
)
from monitoring_core.delivery import AlertPublisher, NotificationOutboxWorker
from monitoring_core.probe_protocol import ProbeProtocolValidator, validate_probe_json
from monitoring_core.state_store import StateStore


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


OPENAI_STATUS_SUMMARY_URL = "https://status.openai.com/api/v2/summary.json"
OPENAI_STATUS_SOURCE_URL = "https://status.openai.com/"
DEFAULT_OPENAI_COMPONENT_NAMES = {
    "Responses",
    "Chat Completions",
    "Codex API",
    "CLI",
}
OPENAI_IMPACT_RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3}


def parse_status_timestamp(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0


class OpenAIStatusClient:
    def __init__(self, fetch_json: Callable[[str, int], dict[str, Any]] | None = None):
        self.fetch_json = fetch_json or self._fetch_json

    @staticmethod
    def _fetch_json(url: str, timeout_seconds: int) -> dict[str, Any]:
        if url != OPENAI_STATUS_SUMMARY_URL:
            raise ValueError("unsupported OpenAI Status endpoint")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "newapi-monitor-provider-status/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read(1_048_577)
        except urllib.error.HTTPError as error:
            detail = error.read(1000).decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI Status returned HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise RuntimeError(f"OpenAI Status unavailable: {error}") from error
        if len(body) > 1_048_576:
            raise RuntimeError("OpenAI Status response exceeds 1 MiB")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("OpenAI Status returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("OpenAI Status returned a non-object response")
        return payload

    @staticmethod
    def _text(value: Any, limit: int = 2000) -> str:
        return str(value or "").strip()[:limit]

    def fetch(
        self,
        timeout_seconds: int = 10,
        observed_at: int | None = None,
    ) -> dict[str, Any]:
        summary = self.fetch_json(OPENAI_STATUS_SUMMARY_URL, timeout_seconds)
        status = summary.get("status")
        page = summary.get("page")
        components_raw = summary.get("components")
        incidents_raw = summary.get("incidents", [])
        if not isinstance(status, dict) or not isinstance(page, dict):
            raise RuntimeError("OpenAI Status summary is missing page or status")
        if not isinstance(components_raw, list):
            raise RuntimeError("OpenAI Status response is missing components")
        if not isinstance(incidents_raw, list):
            raise RuntimeError("OpenAI Status response contains invalid incidents")

        components: list[dict[str, Any]] = []
        for item in components_raw[:500]:
            if not isinstance(item, dict):
                continue
            component_id = self._text(item.get("id"), 128)
            name = self._text(item.get("name"), 256)
            if not component_id or not name:
                continue
            components.append(
                {
                    "id": component_id,
                    "name": name,
                    "status": self._text(item.get("status"), 64) or "unknown",
                    "updated_at": parse_status_timestamp(item.get("updated_at")),
                }
            )

        incidents: list[dict[str, Any]] = []
        for item in incidents_raw[:100]:
            if not isinstance(item, dict):
                continue
            incident_id = self._text(item.get("id"), 128)
            name = self._text(item.get("name"), 512)
            if not incident_id or not name:
                continue
            updates: list[dict[str, Any]] = []
            raw_updates = item.get("incident_updates")
            if isinstance(raw_updates, list):
                for update in raw_updates[:50]:
                    if not isinstance(update, dict):
                        continue
                    updates.append(
                        {
                            "id": self._text(update.get("id"), 128),
                            "status": self._text(update.get("status"), 64) or "unknown",
                            "body": self._text(update.get("body"), 4000),
                            "created_at": parse_status_timestamp(update.get("created_at")),
                            "updated_at": parse_status_timestamp(update.get("updated_at")),
                        }
                    )
            updates.sort(key=lambda update: (int(update["created_at"]), int(update["updated_at"])))
            latest_update = updates[-1] if updates else {
                "id": "",
                "status": self._text(item.get("status"), 64) or "unknown",
                "body": "",
                "created_at": parse_status_timestamp(item.get("updated_at")),
                "updated_at": parse_status_timestamp(item.get("updated_at")),
            }
            incidents.append(
                {
                    "id": incident_id,
                    "name": name,
                    "status": self._text(item.get("status"), 64) or "unknown",
                    "impact": self._text(item.get("impact"), 64) or "none",
                    "created_at": parse_status_timestamp(item.get("created_at")),
                    "updated_at": parse_status_timestamp(item.get("updated_at")),
                    "resolved_at": parse_status_timestamp(item.get("resolved_at")),
                    "latest_update": latest_update,
                    "updates": updates[-20:],
                }
            )

        return {
            "provider": "openai",
            "observed_at": int(time.time()) if observed_at is None else int(observed_at),
            "source_url": OPENAI_STATUS_SOURCE_URL,
            "page_updated_at": parse_status_timestamp(page.get("updated_at")),
            "indicator": self._text(status.get("indicator"), 64) or "unknown",
            "description": self._text(status.get("description"), 512) or "Unknown",
            "components": components,
            "incidents": incidents,
        }


class OpenAIStatusTracker:
    def __init__(
        self,
        initial_state: dict[str, Any] | None = None,
        component_ids: Iterable[str] = (),
        min_impact: str = "major",
        failure_threshold: int = 2,
        recovery_threshold: int = 2,
        alerts_enabled: bool = True,
    ):
        state = dict(initial_state or {})
        self.state = {
            "incidents": dict(state.get("incidents") or {}),
            "components": dict(state.get("components") or {}),
        }
        self.component_ids = {str(item) for item in component_ids if str(item)}
        self.min_impact = min_impact if min_impact in OPENAI_IMPACT_RANK else "major"
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_threshold = max(1, int(recovery_threshold))
        self.alerts_enabled = bool(alerts_enabled)

    @staticmethod
    def _severity(impact: str) -> str:
        return "critical" if impact in {"major", "critical"} else "warning"

    def _component_selected(self, component_id: str, name: str) -> bool:
        if self.component_ids:
            return component_id in self.component_ids
        return name in DEFAULT_OPENAI_COMPONENT_NAMES

    @staticmethod
    def _local_impact_line(local_impact: dict[str, int] | None) -> str:
        if not local_impact:
            return "本地影响：尚无可关联的渠道探测数据"
        total = int(local_impact.get("total") or 0)
        failed = int(local_impact.get("failed") or 0)
        if total <= 0:
            return "本地影响：尚无可关联的 OpenAI 渠道"
        if failed:
            return f"本地影响：{failed}/{total} 个相关渠道异常"
        return f"本地影响：0/{total} 个相关渠道异常，本地暂未复现"

    @staticmethod
    def _incident_metadata(incident: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": "openai",
            "official_id": str(incident.get("id") or ""),
            "source_url": OPENAI_STATUS_SOURCE_URL,
            "impact": str(incident.get("impact") or "none"),
            "phase": str(incident.get("status") or "unknown"),
            "timeline": list(incident.get("updates") or [])[-20:],
        }

    def _incident_body(
        self,
        incident: dict[str, Any],
        local_impact: dict[str, int] | None,
    ) -> str:
        latest = dict(incident.get("latest_update") or {})
        return "\n".join(
            [
                f"官方影响等级：{str(incident.get('impact') or 'none').upper()}",
                f"官方阶段：{str(incident.get('status') or 'unknown')}",
                self._local_impact_line(local_impact),
                f"官方说明：{str(latest.get('body') or '暂无进一步说明')[:4000]}",
                f"来源：{OPENAI_STATUS_SOURCE_URL}",
            ]
        )

    def evaluate(
        self,
        snapshot: dict[str, Any],
        local_impact: dict[str, int] | None = None,
    ) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        current_incident_ids: set[str] = set()
        active_alert_worthy_incident = False
        incident_states = self.state["incidents"]
        for incident in snapshot.get("incidents") or []:
            if not isinstance(incident, dict):
                continue
            incident_id = str(incident.get("id") or "")
            if not incident_id:
                continue
            current_incident_ids.add(incident_id)
            status = str(incident.get("status") or "unknown")
            impact = str(incident.get("impact") or "none")
            latest = dict(incident.get("latest_update") or {})
            signature = "|".join(
                [
                    status,
                    impact,
                    str(incident.get("updated_at") or 0),
                    str(latest.get("status") or ""),
                    str(latest.get("body") or "")[:1000],
                ]
            )
            previous = dict(incident_states.get(incident_id) or {})
            alert_worthy = OPENAI_IMPACT_RANK.get(impact, 0) >= OPENAI_IMPACT_RANK[self.min_impact]
            if status != "resolved" and alert_worthy:
                active_alert_worthy_incident = True
                should_record = not previous.get("alerted") or previous.get("signature") != signature
                should_notify = self.alerts_enabled and previous.get("notified_signature") != signature
                if should_record or should_notify:
                    events.append(
                        AlertEvent(
                            kind="provider_incident",
                            title=f"OpenAI 官方状态异常：{str(incident.get('name') or incident_id)}",
                            body=self._incident_body(incident, local_impact),
                            key=f"provider:openai:incident:{incident_id}",
                            severity=self._severity(impact),
                            metadata=self._incident_metadata(incident),
                            notify=self.alerts_enabled,
                        )
                    )
                    previous["alerted"] = True
                    if self.alerts_enabled:
                        previous["notified_signature"] = signature
            elif status == "resolved" and previous.get("alerted"):
                events.append(
                    AlertEvent(
                        kind="provider_incident_recovered",
                        title=f"OpenAI 官方事件恢复：{str(incident.get('name') or incident_id)}",
                        body=self._incident_body(incident, local_impact),
                        key=f"provider:openai:incident:{incident_id}",
                        severity="info",
                        recovery=True,
                        metadata=self._incident_metadata(incident),
                        notify=self.alerts_enabled,
                    )
                )
                previous["alerted"] = False
                previous["notified_signature"] = ""
            elif status != "resolved" and previous.get("alerted"):
                events.append(
                    AlertEvent(
                        kind="provider_incident_scope_changed",
                        title=f"OpenAI 官方事件低于告警阈值：{str(incident.get('name') or incident_id)}",
                        body="\n".join(
                            [
                                f"当前影响等级：{impact.upper()}",
                                f"配置的最低告警等级：{self.min_impact.upper()}",
                                "官方事件仍可能处于活动状态，但已不再计入监控事件。",
                                f"来源：{OPENAI_STATUS_SOURCE_URL}",
                            ]
                        ),
                        key=f"provider:openai:incident:{incident_id}",
                        severity="info",
                        recovery=True,
                        metadata={
                            **self._incident_metadata(incident),
                            "phase": "below-threshold",
                        },
                        notify=False,
                    )
                )
                previous["alerted"] = False
                previous["notified_signature"] = ""
            previous.update(
                {
                    "name": str(incident.get("name") or incident_id),
                    "status": status,
                    "impact": impact,
                    "signature": signature,
                }
            )
            incident_states[incident_id] = previous

        for incident_id, previous_raw in list(incident_states.items()):
            previous = dict(previous_raw or {})
            if incident_id in current_incident_ids or not previous.get("alerted"):
                continue
            events.append(
                AlertEvent(
                    kind="provider_incident_recovered",
                    title=f"OpenAI 官方事件恢复：{previous.get('name') or incident_id}",
                    body=f"官方事件已不再处于活动状态。\n来源：{OPENAI_STATUS_SOURCE_URL}",
                    key=f"provider:openai:incident:{incident_id}",
                    severity="info",
                    recovery=True,
                    metadata={
                        "provider": "openai",
                        "official_id": incident_id,
                        "source_url": OPENAI_STATUS_SOURCE_URL,
                        "phase": "resolved",
                    },
                    notify=self.alerts_enabled,
                )
            )
            previous["alerted"] = False
            previous["notified_signature"] = ""
            previous["status"] = "resolved"
            incident_states[incident_id] = previous

        component_states = self.state["components"]
        for component_id, previous_raw in list(component_states.items()):
            previous = dict(previous_raw or {})
            if self._component_selected(component_id, str(previous.get("name") or component_id)):
                continue
            if previous.get("alerted"):
                events.append(
                    AlertEvent(
                        kind="provider_component_scope_changed",
                        title=f"OpenAI 组件已移出关注范围：{previous.get('name') or component_id}",
                        body="该组件已从监控配置的关注范围移除，原事件按配置变更结束。",
                        key=f"provider:openai:component:{component_id}",
                        severity="info",
                        recovery=True,
                        metadata={
                            "provider": "openai",
                            "component_id": component_id,
                            "component_name": str(previous.get("name") or component_id),
                            "source_url": OPENAI_STATUS_SOURCE_URL,
                            "phase": "scope-removed",
                        },
                        notify=False,
                    )
                )
            component_states.pop(component_id, None)
        for component in snapshot.get("components") or []:
            if not isinstance(component, dict):
                continue
            component_id = str(component.get("id") or "")
            name = str(component.get("name") or component_id)
            if not self._component_selected(component_id, name):
                continue
            status = str(component.get("status") or "unknown")
            previous = dict(component_states.get(component_id) or {})
            previous.setdefault("failures", 0)
            previous.setdefault("recoveries", 0)
            previous.setdefault("alerted", False)
            if status == "operational":
                previous["failures"] = 0
                if previous["alerted"]:
                    previous["recoveries"] = int(previous["recoveries"]) + 1
                    if previous["recoveries"] >= self.recovery_threshold:
                        events.append(
                            AlertEvent(
                                kind="provider_component_recovered",
                                title=f"OpenAI 组件恢复：{name}",
                                body=f"官方组件状态：operational\n来源：{OPENAI_STATUS_SOURCE_URL}",
                                key=f"provider:openai:component:{component_id}",
                                severity="info",
                                recovery=True,
                                metadata={
                                    "provider": "openai",
                                    "component_id": component_id,
                                    "component_name": name,
                                    "source_url": OPENAI_STATUS_SOURCE_URL,
                                    "phase": "operational",
                                },
                                notify=self.alerts_enabled,
                            )
                        )
                        previous["alerted"] = False
                        previous["notified_signature"] = ""
                        previous["recoveries"] = 0
                else:
                    previous["recoveries"] = 0
            else:
                previous["recoveries"] = 0
                previous["failures"] = int(previous["failures"]) + 1
                should_record = not previous["alerted"] and previous["failures"] >= self.failure_threshold
                should_notify = self.alerts_enabled and previous.get("notified_signature") != status
                if not active_alert_worthy_incident and (should_record or (previous["alerted"] and should_notify)):
                    events.append(
                        AlertEvent(
                            kind="provider_component_failed",
                            title=f"OpenAI 组件异常：{name}",
                            body="\n".join(
                                [
                                    f"官方组件状态：{status}",
                                    self._local_impact_line(local_impact),
                                    f"来源：{OPENAI_STATUS_SOURCE_URL}",
                                ]
                            ),
                            key=f"provider:openai:component:{component_id}",
                            severity="warning",
                            metadata={
                                "provider": "openai",
                                "component_id": component_id,
                                "component_name": name,
                                "source_url": OPENAI_STATUS_SOURCE_URL,
                                "phase": status,
                            },
                            notify=self.alerts_enabled,
                        )
                    )
                    previous["alerted"] = True
                    if self.alerts_enabled:
                        previous["notified_signature"] = status
            previous["name"] = name
            previous["status"] = status
            component_states[component_id] = previous
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
    channel_probe_concurrency: int
    channel_failure_threshold: int
    channel_recovery_threshold: int
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
    incident_retention_days: int
    notification_retention_days: int
    database_maintenance_interval_seconds: int
    database_max_mb: int
    notification_max_attempts: int
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
    openai_status_enabled: bool
    openai_status_alert_enabled: bool
    openai_status_interval_seconds: int
    openai_status_timeout_seconds: int
    openai_status_min_impact: str
    openai_status_component_ids: tuple[str, ...]
    openai_status_failure_threshold: int
    openai_status_recovery_threshold: int
    openai_status_include_in_overall: bool
    openai_status_admin_visible: bool
    openai_status_viewer_visible: bool

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
        component_ids_value = value(
            "openai_status_component_ids",
            "OPENAI_STATUS_COMPONENT_IDS",
            "",
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
            channel_probe_concurrency=max(1, min(16, int(value("channel_probe_concurrency", "CHANNEL_PROBE_CONCURRENCY", 3)))),
            channel_failure_threshold=max(1, min(10, int(value("channel_failure_threshold", "CHANNEL_FAILURE_THRESHOLD", 2)))),
            channel_recovery_threshold=max(1, min(10, int(value("channel_recovery_threshold", "CHANNEL_RECOVERY_THRESHOLD", 2)))),
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
            incident_retention_days=max(30, int(value("incident_retention_days", "INCIDENT_RETENTION_DAYS", 365))),
            notification_retention_days=max(7, int(value("notification_retention_days", "NOTIFICATION_RETENTION_DAYS", 30))),
            database_maintenance_interval_seconds=max(
                3600,
                int(value("database_maintenance_interval_seconds", "DATABASE_MAINTENANCE_INTERVAL_SECONDS", 21600)),
            ),
            database_max_mb=max(128, int(value("database_max_mb", "DATABASE_MAX_MB", 2048))),
            notification_max_attempts=max(1, min(20, int(value("notification_max_attempts", "NOTIFICATION_MAX_ATTEMPTS", 8)))),
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
            openai_status_enabled=bool(value("openai_status_enabled", "OPENAI_STATUS_ENABLED", True)),
            openai_status_alert_enabled=bool(value("openai_status_alert_enabled", "OPENAI_STATUS_ALERT_ENABLED", True)),
            openai_status_interval_seconds=max(30, int(value("openai_status_interval_seconds", "OPENAI_STATUS_INTERVAL_SECONDS", 60))),
            openai_status_timeout_seconds=max(3, min(30, int(value("openai_status_timeout_seconds", "OPENAI_STATUS_TIMEOUT_SECONDS", 10)))),
            openai_status_min_impact=str(value("openai_status_min_impact", "OPENAI_STATUS_MIN_IMPACT", "major")).lower(),
            openai_status_component_ids=tuple(
                dict.fromkeys(
                    str(item).strip()
                    for item in (
                        component_ids_value
                        if isinstance(component_ids_value, list)
                        else str(component_ids_value).split(",")
                    )
                    if str(item).strip()
                )
            ),
            openai_status_failure_threshold=max(1, min(10, int(value("openai_status_failure_threshold", "OPENAI_STATUS_FAILURE_THRESHOLD", 2)))),
            openai_status_recovery_threshold=max(1, min(10, int(value("openai_status_recovery_threshold", "OPENAI_STATUS_RECOVERY_THRESHOLD", 2)))),
            openai_status_include_in_overall=bool(value("openai_status_include_in_overall", "OPENAI_STATUS_INCLUDE_IN_OVERALL", False)),
            openai_status_admin_visible=bool(value("openai_status_admin_visible", "OPENAI_STATUS_ADMIN_VISIBLE", True)),
            openai_status_viewer_visible=bool(value("openai_status_viewer_visible", "OPENAI_STATUS_VIEWER_VISIBLE", True)),
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
        if self.openai_status_min_impact not in OPENAI_IMPACT_RANK:
            raise ValueError("OPENAI_STATUS_MIN_IMPACT must be none, minor, major or critical")
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
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("New API channel response has no data object")
        items = data.get("items")
        if not isinstance(items, list):
            raise RuntimeError("New API channel response has no items list")
        try:
            total = int(data.get("total") if data.get("total") is not None else len(items))
        except (TypeError, ValueError) as error:
            raise RuntimeError("New API channel response has an invalid total") from error
        if not items and total != 0:
            raise RuntimeError("New API channel response is incomplete: total is non-zero but items is empty")
        normalized: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                raise RuntimeError("New API channel response contains a non-object item")
            channel_id = int(item.get("id") or 0)
            if channel_id <= 0 or "status" not in item:
                raise RuntimeError("New API channel response contains an invalid channel item")
            normalized.append(item)
        return normalized

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
                if "text/event-stream" in content_type:
                    validator = ProbeProtocolValidator(rule.request_format)
                    event_name = ""
                    while True:
                        line = response.readline()
                        if not line:
                            break
                        stripped = line.strip()
                        if not stripped or stripped.startswith(b":"):
                            continue
                        if stripped.startswith(b"event:"):
                            event_name = stripped[6:].decode("utf-8", errors="replace").strip()
                            continue
                        if stripped.startswith(b"data:"):
                            was_valid = validator.valid_payload_seen
                            validator.feed(
                                event_name,
                                stripped[5:].decode("utf-8", errors="replace").strip(),
                            )
                            if not was_valid and validator.valid_payload_seen and first_response_ms is None:
                                first_response_ms = (time.monotonic() - started) * 1000.0
                    success, message = validator.result()
                else:
                    body = response.read()
                    if not body.strip():
                        success, message = False, "upstream returned an empty response"
                    else:
                        try:
                            payload = json.loads(body.decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            success, message = False, "upstream returned invalid JSON"
                        else:
                            success, message = validate_probe_json(rule.request_format, payload)
                        if success:
                            first_response_ms = (time.monotonic() - started) * 1000.0
                elapsed = time.monotonic() - started
                return RealProbeResult(success, elapsed, first_response_ms, message)
        except urllib.error.HTTPError as error:
            elapsed = time.monotonic() - started
            body = error.read().decode("utf-8", errors="replace")[:500]
            return RealProbeResult(False, elapsed, first_response_ms, f"HTTP {error.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return RealProbeResult(False, time.monotonic() - started, first_response_ms, str(error))


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

    @property
    def destinations(self) -> tuple[str, ...]:
        return tuple(str(sender.name) for sender in self.senders)


class ChannelSyncWorker:
    def __init__(
        self,
        client: NewAPIClient,
        store: StateStore,
        on_snapshot: Callable[[list[dict[str, Any]]], None],
        on_result: Callable[[bool, str], None] | None = None,
        stale_after_seconds: int = 60,
        channel_settings: dict[int, dict[str, Any]] | None = None,
    ):
        self.client = client
        self.store = store
        self.on_snapshot = on_snapshot
        self.on_result = on_result
        self.stale_after_seconds = stale_after_seconds
        self.channel_settings = channel_settings or {}

    def sync_once(self) -> list[dict[str, Any]]:
        channels = self.client.get_channels()
        self.store.upsert_channels(channels)
        resolved = self.store.reconcile_channel_incidents(channels, self.channel_settings)
        if isinstance(resolved, int) and resolved > 0:
            LOGGER.info("resolved %d channel incidents after scope reconciliation", resolved)
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
                try:
                    self.store.record_collector_result(
                        "channel_sync",
                        success,
                        error_message,
                        stale_after_seconds=self.stale_after_seconds,
                    )
                except Exception:
                    LOGGER.exception("channel sync freshness update failed")
                if self.on_result is not None:
                    self.on_result(success, error_message)
                if stop_event.wait(max(1, interval_seconds)):
                    break
        finally:
            self.store.connection.close()


class ChannelProbeWorker:
    def __init__(
        self,
        config: Config,
        client: NewAPIClient,
        relay_probe_client: RelayProbeClient | None,
        store: StateStore,
        alert_publisher: AlertPublisher,
        snapshot_provider: Callable[[], list[dict[str, Any]]],
        on_observations: Callable[[list[ChannelObservation]], None],
        stale_after_seconds: int,
    ):
        self.config = config
        self.client = client
        self.relay_probe_client = relay_probe_client
        self.store = store
        self.alert_publisher = alert_publisher
        self.snapshot_provider = snapshot_provider
        self.on_observations = on_observations
        self.stale_after_seconds = stale_after_seconds
        self.channel_tracker = ChannelStateTracker(
            self.store.get_json("channel_states", {}),
            failure_threshold=config.channel_failure_threshold,
            recovery_threshold=config.channel_recovery_threshold,
        )
        self.credential_tracker = ProbeCredentialStateTracker(
            self.store.get_json("probe_credential_state", {}),
            recovery_threshold=config.channel_recovery_threshold,
        )

    def _probe_channel(self, channel: dict[str, Any]) -> ChannelObservation:
        channel_id = int(channel.get("id") or 0)
        name = str(channel.get("name") or f"channel-{channel_id}")
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
                    message = f"探测耗时 {elapsed:.3f}s 超过阈值 {self.config.channel_slow_seconds:.3f}s"
        except Exception as error:
            elapsed = time.monotonic() - started
            first_response_ms = None
            success = False
            message = str(error)
            source = "real" if channel_id in self.config.real_probe_rules else "builtin"
        return ChannelObservation(
            channel_id,
            name,
            success,
            elapsed,
            message,
            source,
            first_response_ms,
        )

    def _send_events(self, events: list[AlertEvent]) -> None:
        if not events:
            return
        queued = self.alert_publisher.publish(events)
        LOGGER.info("recorded %d channel alert events; queued=%d", len(events), len(queued))

    def check_once(self) -> list[ChannelObservation]:
        channels = []
        for channel in self.snapshot_provider():
            channel_id = int(channel.get("id") or 0)
            channel_config = self.config.channel_settings.get(channel_id, {})
            if channel_id <= 0 or int(channel.get("status") or 0) != 1:
                continue
            if channel_config.get("maintenance_mode"):
                continue
            channels.append(channel)

        if not channels:
            self.channel_tracker.states = {}
            self.store.set_json("channel_states", self.channel_tracker.states)
            self.on_observations([])
            self.store.record_collector_result(
                "channel_probe", True, "", stale_after_seconds=self.stale_after_seconds
            )
            return []
        active_channel_ids = {int(channel.get("id") or 0) for channel in channels}
        self.channel_tracker.states = {
            key: state
            for key, state in self.channel_tracker.states.items()
            if str(key).isdigit() and int(key) in active_channel_ids
        }
        with ThreadPoolExecutor(
            max_workers=min(self.config.channel_probe_concurrency, len(channels)),
            thread_name_prefix="newapi-channel-probe-request",
        ) as executor:
            observations = list(executor.map(self._probe_channel, channels))

        self.on_observations(observations)
        self.store.insert_channel_observations(observations)
        credential_events, suppressed = self.credential_tracker.evaluate(observations)
        alert_observations = [
            item for item in observations
            if item.channel_id not in suppressed
            and self.config.channel_settings.get(item.channel_id, {}).get("alert_enabled", True)
        ]
        events = credential_events + self.channel_tracker.evaluate(alert_observations)
        self._send_events(events)
        self.store.set_json("channel_states", self.channel_tracker.states)
        self.store.set_json("probe_credential_state", self.credential_tracker.state)
        self.store.record_collector_result(
            "channel_probe", True, "", stale_after_seconds=self.stale_after_seconds
        )
        LOGGER.info(
            "channel check complete: total=%d healthy=%d suppressed=%d",
            len(observations),
            sum(item.success for item in observations),
            len(suppressed),
        )
        return observations

    def run(self, stop_event: threading.Event, interval_seconds: int) -> None:
        try:
            while not stop_event.is_set():
                started = time.monotonic()
                if not self.snapshot_provider():
                    if stop_event.wait(1):
                        break
                    continue
                try:
                    self.check_once()
                except Exception as error:
                    self.store.record_collector_result(
                        "channel_probe", False, str(error), stale_after_seconds=self.stale_after_seconds
                    )
                    LOGGER.exception("channel check failed")
                remaining = max(1.0, interval_seconds - (time.monotonic() - started))
                if stop_event.wait(remaining):
                    break
        finally:
            self.store.connection.close()


class MonitorApp:
    def __init__(self, config: Config):
        config.validate()
        self.config = config
        self.client = NewAPIClient(config)
        self.store = StateStore(config.state_db)
        if not config.openai_status_enabled:
            self.store.resolve_open_incidents(
                "provider:openai:",
                "OpenAI 官方状态监控已关闭，该事件因监控范围变更结束。",
            )
            self.store.set_json("openai_status_state", {})
        self.notifier = NotificationDispatcher(config)
        self.alert_publisher = AlertPublisher(self.store, self.notifier.destinations)
        cancelled_notifications = self.store.cancel_disabled_notifications(self.notifier.destinations)
        if cancelled_notifications:
            LOGGER.info(
                "cancelled %d pending notifications for disabled destinations",
                cancelled_notifications,
            )
        self.outbox_worker = NotificationOutboxWorker(
            self.store,
            self.notifier,
            max_attempts=config.notification_max_attempts,
        )
        self.openai_status_client = OpenAIStatusClient()
        self.openai_status_tracker = OpenAIStatusTracker(
            self.store.get_json("openai_status_state", {}),
            component_ids=config.openai_status_component_ids,
            min_impact=config.openai_status_min_impact,
            failure_threshold=config.openai_status_failure_threshold,
            recovery_threshold=config.openai_status_recovery_threshold,
            alerts_enabled=config.openai_status_alert_enabled,
        )
        self.resource_collector = ResourceCollector(config.disk_path, config.docker_container_names)
        self.service_tracker = ServiceStateTracker(str(self.store.get_json("service_state", "unknown")))
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
        if config.openai_status_enabled:
            self.collector_thresholds["openai_status"] = max(
                90,
                config.openai_status_interval_seconds * 3,
            )
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
        saved_container_health = self.store.get_json("container_health_states", {})
        if not saved_container_health:
            saved_container_health = {
                name: {
                    "status": status,
                    "restarts": int(self.container_restarts.get(name) or 0),
                    "oom_killed": False,
                }
                for name, status in self.container_states.items()
            }
        self.container_tracker = ContainerStateTracker(saved_container_health)

    def _send_events(self, events: list[AlertEvent]) -> None:
        if not events:
            return
        queued = self.alert_publisher.publish(events)
        result = self.outbox_worker.run_once()
        LOGGER.info(
            "recorded %d alert events; queued=%d delivered=%d failed=%d",
            len(events),
            len(queued),
            result["delivered"],
            result["failed"],
        )

    def _send_message(self, subject: str, body: str) -> None:
        queued = self.alert_publisher.publish_message(subject, body)
        result = self.outbox_worker.run_once()
        LOGGER.info(
            "queued notification message: queued=%d delivered=%d failed=%d",
            len(queued),
            result["delivered"],
            result["failed"],
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
                stale_after_seconds=self.collector_thresholds["channel_sync"],
                channel_settings=self.config.channel_settings,
            )
            worker.run(stop_event, self.config.channel_sync_interval_seconds)
        except Exception:
            LOGGER.exception("channel sync worker stopped unexpectedly")

    def _publish_channel_observations(self, observations: list[ChannelObservation]) -> None:
        self.latest_channels = observations

    def _run_channel_probe_worker(self, stop_event: threading.Event) -> None:
        try:
            worker_store = StateStore(self.config.state_db)
            worker = ChannelProbeWorker(
                self.config,
                NewAPIClient(self.config),
                RelayProbeClient(self.config) if self.config.real_probe_rules else None,
                worker_store,
                AlertPublisher(worker_store, self.notifier.destinations),
                lambda: list(self.channel_snapshot or []),
                self._publish_channel_observations,
                stale_after_seconds=self.collector_thresholds["channel_probe"],
            )
            worker.run(stop_event, self.config.channel_interval_seconds)
        except Exception:
            LOGGER.exception("channel probe worker stopped unexpectedly")

    def collect_logs(self) -> None:
        now = int(time.time())
        last_cursor = int(
            self.store.get_json("log_cursor", now - self.config.log_initial_lookback_seconds)
        )
        start_timestamp = max(0, last_cursor - self.config.log_overlap_seconds)
        logs = self.client.get_logs(start_timestamp, now)
        inserted, touched_groups = self.store.ingest_logs_with_groups(
            logs,
            self.config.excluded_token_names,
        )
        previous_latency_states = dict(self.latency_tracker.states)
        active_channel_ids = self.store.active_channel_ids(self.config.channel_settings)
        self.latency_tracker.states = {
            key: state
            for key, state in self.latency_tracker.states.items()
            if key.partition(":")[0].isdigit()
            and int(key.partition(":")[0]) in active_channel_ids
        }
        latency_events: list[AlertEvent] = []
        for channel_id, channel_name, model_name in touched_groups:
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
        LOGGER.info("log collection complete: fetched=%d inserted=%d", len(logs), inserted)

    def maintain_database(self, now: int | None = None, force: bool = False) -> dict[str, Any] | None:
        timestamp = int(time.time()) if now is None else int(now)
        last_maintenance = int(self.store.get_json("last_database_maintenance_at", 0))
        if not force and timestamp - last_maintenance < self.config.database_maintenance_interval_seconds:
            return None
        stats = self.store.maintain(
            timestamp - self.config.retention_days * 86400,
            timestamp - self.config.incident_retention_days * 86400,
            timestamp - self.config.notification_retention_days * 86400,
        )
        self.store.set_json("last_database_maintenance_at", timestamp)
        self.store.set_json("database_stats", stats)
        database_bytes = int(stats.get("database_bytes") or 0) + int(stats.get("wal_bytes") or 0)
        database_limit = self.config.database_max_mb * 1024 * 1024
        capacity_key = "resource:monitor_database"
        if database_bytes > database_limit and not self.store.has_open_incident(capacity_key):
            self._send_events(
                [
                    AlertEvent(
                        "database_capacity_high",
                        "监控数据库容量超限",
                        f"当前占用：{database_bytes / 1024 / 1024:.1f} MB\n配置上限：{self.config.database_max_mb} MB",
                        key=capacity_key,
                        severity="critical",
                    )
                ]
            )
        elif database_bytes <= database_limit * 0.9 and self.store.has_open_incident(capacity_key):
            self._send_events(
                [
                    AlertEvent(
                        "database_capacity_recovered",
                        "监控数据库容量恢复",
                        f"当前占用：{database_bytes / 1024 / 1024:.1f} MB",
                        key=capacity_key,
                        severity="info",
                        recovery=True,
                    )
                ]
            )
        return stats

    def collect_resources(self) -> None:
        metrics, details = self.resource_collector.collect()
        self.latest_resources = metrics
        self.latest_resource_details = details
        self.store.insert_resource_sample(metrics, details)
        events = self.resource_tracker.evaluate(metrics)

        containers = dict(details.get("containers") or {})
        container_events = self.container_tracker.evaluate(containers)
        recovered_keys = {event.key for event in container_events if event.recovery}
        for container_name, container in containers.items():
            incident_key = f"container:{container_name}"
            if (
                str(container.get("status") or "unknown") == "running"
                and incident_key not in recovered_keys
                and self.store.has_open_incident(incident_key)
            ):
                container_events.append(
                    AlertEvent(
                        "container_recovered",
                        f"容器恢复：{container_name}",
                        "容器状态：running",
                        key=incident_key,
                        severity="info",
                        recovery=True,
                    )
                )
        events.extend(container_events)

        self.store.set_json("resource_states", self.resource_tracker.states)
        self.store.set_json("container_health_states", self.container_tracker.states)
        self._send_events(events)
        LOGGER.info("resource collection complete: %s", json.dumps(metrics, ensure_ascii=False))

    def collect_openai_status(self) -> dict[str, Any]:
        snapshot = self.openai_status_client.fetch(
            timeout_seconds=self.config.openai_status_timeout_seconds,
        )
        local_impact = self.store.provider_local_impact(
            "openai",
            now=int(snapshot["observed_at"]),
            stale_after_seconds=max(300, self.config.channel_interval_seconds * 3),
        )
        previous_state = json.loads(json.dumps(self.openai_status_tracker.state))
        events = self.openai_status_tracker.evaluate(snapshot, local_impact)
        try:
            self._send_events(events)
        except Exception:
            self.openai_status_tracker.state = previous_state
            raise
        self.store.record_provider_status("openai", snapshot, observed_at=int(snapshot["observed_at"]))
        self.store.set_json("openai_status_state", self.openai_status_tracker.state)
        LOGGER.info(
            "OpenAI Status collection complete: indicator=%s active_incidents=%d degraded_components=%d",
            snapshot["indicator"],
            sum(str(item.get("status") or "") != "resolved" for item in snapshot["incidents"]),
            sum(str(item.get("status") or "") != "operational" for item in snapshot["components"]),
        )
        return snapshot

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
        self._send_message(subject, body)
        LOGGER.info("periodic report queued")

    def run_forever(
        self,
        stop_event: Any | None = None,
        send_startup_notification: bool = True,
    ) -> None:
        if self.config.send_startup_email and send_startup_notification:
            try:
                self._send_message("监控程序启动", f"监控目标：{self.config.base_url}")
            except Exception:
                LOGGER.exception("startup notification enqueue failed")

        channel_sync_stop = threading.Event()
        channel_probe_stop = threading.Event()
        channel_sync_thread = threading.Thread(
            target=self._run_channel_sync_worker,
            args=(channel_sync_stop,),
            name="newapi-channel-sync",
            daemon=True,
        )
        channel_sync_thread.start()
        channel_probe_thread = threading.Thread(
            target=self._run_channel_probe_worker,
            args=(channel_probe_stop,),
            name="newapi-channel-probe",
            daemon=True,
        )
        channel_probe_thread.start()
        next_log = 0.0
        next_resource = 0.0
        next_openai_status = 0.0
        next_report = time.monotonic() + self.config.report_interval_seconds
        next_database_maintenance = 0.0
        try:
            while stop_event is None or not stop_event.is_set():
                self._drain_channel_sync_results()
                try:
                    delivery_result = self.outbox_worker.run_once()
                    if delivery_result["delivered"] or delivery_result["failed"]:
                        LOGGER.info("outbox processed: %s", delivery_result)
                except Exception:
                    LOGGER.exception("notification outbox processing failed")
                now = time.monotonic()
                if now >= next_database_maintenance:
                    try:
                        self.maintain_database()
                    except Exception:
                        LOGGER.exception("database maintenance failed")
                    next_database_maintenance = now + self.config.database_maintenance_interval_seconds
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
                if self.config.openai_status_enabled and now >= next_openai_status:
                    try:
                        self.collect_openai_status()
                        self._record_collector_result("openai_status", True)
                    except Exception as error:
                        self._record_collector_result("openai_status", False, str(error))
                        LOGGER.exception("OpenAI Status collection failed")
                    next_openai_status = now + self.config.openai_status_interval_seconds
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
            channel_probe_stop.set()
            channel_sync_thread.join(timeout=20)
            channel_probe_thread.join(timeout=90)
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
