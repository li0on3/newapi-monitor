from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, cast

@dataclass(frozen=True)
class AlertEvent:
    kind: str
    title: str
    body: str
    key: str = ""
    severity: str = "warning"
    recovery: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    notify: bool = True
    auto_resolve: bool = False


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
        durations = cast(list[float], bucket["durations"])
        durations.append(use_time)
        if use_time > slow_seconds:
            bucket["slow"] = int(bucket["slow"]) + 1
        if isinstance(frt, (int, float)) and frt > 0:
            frt_values = cast(list[float], bucket["frt"])
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
        newest_sample_key = self._sample_key(recent[0]) if recent else ""
        decision = evaluate_latency_window(recent, self.slow_seconds, self.hard_limit_seconds)
        state = dict(
            self.states.get(key)
            or {
                "active": False,
                "last_notified": 0.0,
                "last_sample_key": "",
                "notified_before": False,
            }
        )
        events: list[AlertEvent] = []

        if newest_sample_key and newest_sample_key == str(state.get("last_sample_key") or ""):
            return events

        state["last_sample_key"] = newest_sample_key
        last_five = recent[:5]
        five_healthy = len(last_five) >= 5 and all(
            not self._sample_is_bad(sample) for sample in last_five
        )

        if state.get("active") and five_healthy:
            events.append(
                AlertEvent(
                    kind="latency_recovered",
                    title=f"耗时恢复：{label}",
                    body="最近连续5次新请求均未超过耗时阈值。",
                    key=f"latency:{key}",
                    severity="info",
                    recovery=True,
                )
            )
            state = {
                "active": False,
                "last_notified": current_time,
                "last_sample_key": newest_sample_key,
                "notified_before": False,
            }
        elif decision.triggered:
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

        self.states[key] = state
        return events

    def _sample_is_bad(self, sample: dict[str, Any]) -> bool:
        return (
            float(sample.get("use_time") or 0) > self.slow_seconds
            or float(sample.get("frt_ms") or 0) > self.slow_seconds * 1000.0
        )

    @staticmethod
    def _sample_key(sample: dict[str, Any]) -> str:
        explicit = str(sample.get("sample_key") or sample.get("request_id") or "").strip()
        if explicit:
            return explicit
        return "|".join(
            str(sample.get(field) or "")
            for field in ("created_at", "use_time", "frt_ms")
        )


class ChannelStateTracker:
    def __init__(
        self,
        states: dict[str, Any] | None = None,
        failure_threshold: int = 2,
        recovery_threshold: int = 2,
    ):
        self.states = dict(states or {})
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_threshold = max(1, recovery_threshold)

    @staticmethod
    def failure_class(message: str) -> str:
        normalized = message.lower()
        if any(marker in normalized for marker in (
            "http 401", "http 403", "unauthorized", "forbidden", "无权访问", "权限",
        )):
            return "auth"
        if any(marker in normalized for marker in (
            "http 429", "http 500", "http 502", "http 503", "http 504",
            "upstream 429", "upstream 500", "upstream 502", "upstream 503", "upstream 504",
            "timeout", "timed out", "temporarily unavailable", "connection reset",
            "connection refused", "临时不可用", "超时",
        )):
            return "transient"
        return "persistent"

    @staticmethod
    def _normalize_state(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return {
                "status": str(raw.get("status") or "unknown"),
                "failures": max(0, int(raw.get("failures") or 0)),
                "successes": max(0, int(raw.get("successes") or 0)),
                "failure_class": str(raw.get("failure_class") or ""),
            }
        if raw in {"ok", "failed"}:
            return {"status": raw, "failures": 0, "successes": 0, "failure_class": ""}
        return {"status": "unknown", "failures": 0, "successes": 0, "failure_class": ""}

    def evaluate(self, observations: Iterable[ChannelObservation]) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        for observation in observations:
            key = str(observation.channel_id)
            state = self._normalize_state(self.states.get(key))
            if observation.success:
                state["failures"] = 0
                state["failure_class"] = ""
                if state["status"] == "failed":
                    state["successes"] += 1
                    if state["successes"] >= self.recovery_threshold:
                        state = {"status": "ok", "failures": 0, "successes": 0, "failure_class": ""}
                        events.append(
                            AlertEvent(
                                kind="channel_recovered",
                                title=f"渠道恢复：{observation.name}",
                                body=(
                                    f"渠道ID：{observation.channel_id}\n"
                                    f"已连续成功 {self.recovery_threshold} 次\n"
                                    f"探测耗时：{observation.elapsed_seconds:.3f}s"
                                ),
                                key=f"channel:{observation.channel_id}",
                                severity="info",
                                recovery=True,
                            )
                        )
                else:
                    state = {"status": "ok", "failures": 0, "successes": 0, "failure_class": ""}
                self.states[key] = state
                continue

            state["successes"] = 0
            state["failures"] += 1
            state["failure_class"] = self.failure_class(observation.message)
            if state["status"] != "failed" and state["failures"] >= self.failure_threshold:
                state["status"] = "failed"
                events.append(
                    AlertEvent(
                        kind="channel_failed",
                        title=f"渠道异常：{observation.name}",
                        body=(
                            f"渠道ID：{observation.channel_id}\n"
                            f"已连续失败 {state['failures']} 次\n"
                            f"探测耗时：{observation.elapsed_seconds:.3f}s\n"
                            f"错误：{observation.message or '未知错误'}"
                        ),
                        key=f"channel:{observation.channel_id}",
                        severity="warning" if state["failure_class"] == "transient" else "critical",
                    )
                )
            self.states[key] = state
        return events


class ProbeCredentialStateTracker:
    def __init__(self, state: dict[str, Any] | None = None, recovery_threshold: int = 2):
        self.state = dict(state or {})
        self.recovery_threshold = max(1, recovery_threshold)

    def evaluate(self, observations: Iterable[ChannelObservation]) -> tuple[list[AlertEvent], set[int]]:
        items = list(observations)
        auth_failures = [
            item for item in items
            if not item.success and ChannelStateTracker.failure_class(item.message) == "auth"
        ]
        enabled_count = len(items)
        common_failure = enabled_count >= 2 and len(auth_failures) >= max(2, math.ceil(enabled_count / 2))
        active = bool(self.state.get("active", False))
        events: list[AlertEvent] = []
        suppressed: set[int] = set()
        if common_failure:
            suppressed = {item.channel_id for item in auth_failures}
            self.state = {"active": True, "successes": 0}
            if not active:
                sample = auth_failures[0].message or "New API拒绝了渠道探测请求"
                events.append(
                    AlertEvent(
                        kind="probe_auth_failed",
                        title="监控探测凭证或分组权限异常",
                        body=(
                            f"同一轮有 {len(auth_failures)}/{enabled_count} 个渠道返回相同类型的认证或分组错误。\n"
                            "已抑制对应渠道故障，避免将监控凭证问题误报为多个上游故障。\n"
                            f"示例错误：{sample}"
                        ),
                        key="probe:credential",
                        severity="critical",
                    )
                )
            return events, suppressed

        if active:
            successes = int(self.state.get("successes") or 0) + 1
            if successes >= self.recovery_threshold:
                self.state = {"active": False, "successes": 0}
                events.append(
                    AlertEvent(
                        kind="probe_auth_recovered",
                        title="监控探测凭证与分组权限恢复",
                        body=f"连续 {self.recovery_threshold} 轮未再出现多渠道共同认证错误。",
                        key="probe:credential",
                        severity="info",
                        recovery=True,
                    )
                )
            else:
                self.state = {"active": True, "successes": successes}
        return events, suppressed


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


class ContainerStateTracker:
    def __init__(self, states: dict[str, dict[str, Any]] | None = None):
        self.states = {
            str(name): dict(value or {})
            for name, value in dict(states or {}).items()
        }

    def evaluate(self, containers: dict[str, dict[str, Any]]) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        for name, raw in containers.items():
            detail = dict(raw or {})
            status = str(detail.get("status") or "unknown")
            restarts = max(0, int(detail.get("restarts") or 0))
            oom_killed = bool(detail.get("oom_killed"))
            previous = self.states.get(str(name))

            if previous is not None:
                previous_status = str(previous.get("status") or "unknown")
                previous_restarts = max(0, int(previous.get("restarts") or 0))
                previous_oom = bool(previous.get("oom_killed"))
                if status != "running" and status != previous_status:
                    events.append(
                        AlertEvent(
                            "container_failed",
                            f"容器异常：{name}",
                            f"容器状态：{status}\n{detail.get('error', '')}",
                            key=f"container:{name}",
                            severity="critical",
                        )
                    )
                elif status == "running" and previous_status not in {"running", "unknown"}:
                    events.append(
                        AlertEvent(
                            "container_recovered",
                            f"容器恢复：{name}",
                            f"容器状态：{status}",
                            key=f"container:{name}",
                            severity="info",
                            recovery=True,
                        )
                    )
                if restarts > previous_restarts:
                    events.append(
                        AlertEvent(
                            "container_restarted",
                            f"容器发生重启：{name}",
                            f"重启次数从 {previous_restarts} 增加到 {restarts}",
                            key=f"container-restart:{name}",
                            severity="warning",
                            auto_resolve=True,
                        )
                    )
                if oom_killed and not previous_oom:
                    events.append(
                        AlertEvent(
                            "container_oom",
                            f"容器 OOM：{name}",
                            "容器因内存不足被系统终止。",
                            key=f"container-oom:{name}",
                            severity="critical",
                        )
                    )
                elif previous_oom and not oom_killed:
                    events.append(
                        AlertEvent(
                            "container_oom_recovered",
                            f"容器 OOM 状态恢复：{name}",
                            "容器当前未处于 OOMKilled 状态。",
                            key=f"container-oom:{name}",
                            severity="info",
                            recovery=True,
                        )
                    )

            self.states[str(name)] = {
                "status": status,
                "restarts": restarts,
                "oom_killed": oom_killed,
            }
        return events
