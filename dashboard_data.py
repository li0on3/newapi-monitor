from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator


class DashboardRepository:
    def __init__(
        self,
        database_path: str,
        slow_seconds: float = 60.0,
        channel_stale_seconds: int = 900,
        channel_slow_seconds: float = 30.0,
    ):
        self.database_path = database_path
        self.slow_seconds = slow_seconds
        self.channel_stale_seconds = max(60, channel_stale_seconds)
        self.channel_slow_seconds = max(1.0, channel_slow_seconds)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = max(1, math.ceil(len(ordered) * 0.95))
        return round(ordered[rank - 1], 3)

    def summary(
        self,
        now: int | None = None,
        request_window_seconds: int = 86400,
        channel_ids: set[int] | None = None,
        include_operational_incidents: bool = True,
    ) -> dict[str, Any]:
        current_time = int(time.time()) if now is None else now
        since = current_time - request_window_seconds
        with self._connect() as connection:
            channel_rows = connection.execute(
                """
                SELECT c.channel_id, c.status, latest.success, latest.observed_at,
                       latest.elapsed_ms, latest.frt_ms
                FROM channels c
                LEFT JOIN channel_observations latest ON latest.id = (
                    SELECT id FROM channel_observations
                    WHERE channel_id = c.channel_id
                    ORDER BY observed_at DESC, id DESC LIMIT 1
                )
                ORDER BY c.channel_id
                """
            ).fetchall()
            request_parameters: list[Any] = [since]
            request_scope = ""
            if channel_ids is not None:
                scoped_ids = sorted(int(channel_id) for channel_id in channel_ids)
                if scoped_ids:
                    request_scope = " AND channel_id IN (" + ",".join("?" for _ in scoped_ids) + ")"
                    request_parameters.extend(scoped_ids)
                    request_rows = connection.execute(
                        f"""
                        SELECT channel_id, use_time, frt_ms, created_at
                        FROM latency_samples
                        WHERE created_at >= ?{request_scope}
                        """,
                        request_parameters,
                    ).fetchall()
                else:
                    request_rows = []
            else:
                request_rows = connection.execute(
                    """
                    SELECT channel_id, use_time, frt_ms, created_at
                    FROM latency_samples
                    WHERE created_at >= ?
                    """,
                    request_parameters,
                ).fetchall()
            resource_row = connection.execute(
                """
                SELECT created_at, system_cpu, system_memory, system_disk,
                       system_available_mb, system_swap, containers_json
                FROM resource_samples
                ORDER BY created_at DESC, id DESC LIMIT 1
                """
            ).fetchone()
            incident_rows = connection.execute(
                """
                SELECT incident_key, severity
                FROM incidents WHERE status = 'open'
                """
            ).fetchall()
            collector_row = connection.execute(
                "SELECT value FROM kv WHERE key = 'collector_health'"
            ).fetchone()

        incident_rows = [
            row for row in incident_rows
            if not str(row["incident_key"]).startswith("provider:")
        ]
        if not include_operational_incidents:
            incident_rows = [
                row
                for row in incident_rows
                if self._incident_channel_id(str(row["incident_key"])) is not None
            ]

        enabled = [row for row in channel_rows if int(row["status"] or 0) == 1]
        if channel_ids is not None:
            enabled = [row for row in enabled if int(row["channel_id"]) in channel_ids]
            incident_rows = [
                row
                for row in incident_rows
                if (incident_channel_id := self._incident_channel_id(str(row["incident_key"]))) is None
                or incident_channel_id in channel_ids
            ]
        recent = [
            row
            for row in enabled
            if int(row["observed_at"] or 0) >= current_time - self.channel_stale_seconds
        ]
        slow_limit_ms = self.channel_slow_seconds * 1000.0
        delayed = sum(
            1
            for row in recent
            if row["success"] == 1
            and (
                float(row["elapsed_ms"] or 0) > slow_limit_ms
                or float(row["frt_ms"] or 0) > slow_limit_ms
            )
        )
        healthy = sum(
            1
            for row in recent
            if row["success"] == 1
            and not (
                float(row["elapsed_ms"] or 0) > slow_limit_ms
                or float(row["frt_ms"] or 0) > slow_limit_ms
            )
        )
        failed = sum(1 for row in recent if row["success"] == 0)
        unknown = len(enabled) - healthy - delayed - failed
        channel_sync: dict[str, Any] = {"status": "unknown", "age_seconds": 0}
        collectors = self._decode_json(collector_row["value"], {}) if collector_row is not None else {}
        if isinstance(collectors, dict):
            detail = dict(collectors.get("channel_sync") or {}) if isinstance(collectors, dict) else {}
            if detail:
                last_success = int(detail.get("last_success_at") or 0)
                first_attempt = int(detail.get("first_attempt_at") or current_time)
                stale_after = max(1, int(detail.get("stale_after_seconds") or 300))
                age = max(0, current_time - (last_success or first_attempt))
                status = "stale" if age > stale_after else ("ok" if last_success else "starting")
                channel_sync = {
                    "status": status,
                    "age_seconds": age,
                    "stale_after_seconds": stale_after,
                    "last_success_at": last_success,
                    "consecutive_failures": int(detail.get("consecutive_failures") or 0),
                    "last_error": str(detail.get("last_error") or "")[:500],
                }
                if status == "stale":
                    healthy = 0
                    delayed = 0
                    failed = 0
                    unknown = len(enabled)
        collector_freshness = {
            "logs": self._collector_freshness(collectors, "logs", current_time, 120),
            "resources": self._collector_freshness(collectors, "resources", current_time, 90),
        }
        durations = [float(row["use_time"] or 0) for row in request_rows]
        frt_values = [float(row["frt_ms"]) for row in request_rows if row["frt_ms"] is not None]
        slow_limit_ms = self.slow_seconds * 1000.0
        slow_count = sum(
            1
            for row in request_rows
            if float(row["use_time"] or 0) > self.slow_seconds
            or float(row["frt_ms"] or 0) > slow_limit_ms
        )
        resources: dict[str, Any] = {}
        if resource_row is not None:
            resources = {
                "created_at": int(resource_row["created_at"]),
                "system_cpu": resource_row["system_cpu"],
                "system_memory": resource_row["system_memory"],
                "system_disk": resource_row["system_disk"],
                "system_available_mb": resource_row["system_available_mb"],
                "system_swap": resource_row["system_swap"],
                "containers": self._decode_json(resource_row["containers_json"], {}),
            }
        resources.update(collector_freshness["resources"])
        return {
            "generated_at": current_time,
            "channel_sync": channel_sync,
            "channels": {
                "total": len(enabled),
                "healthy": healthy,
                "delayed": delayed,
                "failed": failed,
                "unknown": unknown,
                "stale_after_seconds": self.channel_stale_seconds,
                "slow_after_seconds": self.channel_slow_seconds,
                "last_checked_at": max(
                    (int(row["observed_at"] or 0) for row in enabled),
                    default=0,
                ),
            },
            "requests": {
                "window_seconds": request_window_seconds,
                "total": len(request_rows),
                "slow": slow_count,
                "slow_after_seconds": self.slow_seconds,
                "slow_ratio": round(slow_count / len(request_rows) * 100, 2) if request_rows else 0.0,
                "average_seconds": round(sum(durations) / len(durations), 3) if durations else 0.0,
                "p95_seconds": self._p95(durations),
                "average_frt_ms": round(sum(frt_values) / len(frt_values), 1) if frt_values else None,
                "last_request_at": max((int(row["created_at"] or 0) for row in request_rows), default=0),
                **collector_freshness["logs"],
            },
            "resources": resources,
            "incidents": {
                "open": len(incident_rows),
                "critical": sum(1 for row in incident_rows if str(row["severity"]) == "critical"),
                "warning": sum(1 for row in incident_rows if str(row["severity"]) == "warning"),
            },
        }

    def enabled_channel_ids(self) -> set[int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT channel_id FROM channels WHERE status = 1"
            ).fetchall()
        return {int(row["channel_id"]) for row in rows}

    @staticmethod
    def _incident_channel_id(incident_key: str) -> int | None:
        parts = incident_key.split(":", 2)
        if len(parts) < 2 or parts[0] not in {"channel", "latency"}:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

    def channels(
        self,
        now: int | None = None,
        history_limit: int = 60,
        availability_window_seconds: int = 7 * 86400,
        availability_start_timestamp: int | None = None,
        availability_end_timestamp: int | None = None,
        availability_all_time: bool = False,
    ) -> list[dict[str, Any]]:
        current_time = int(time.time()) if now is None else now
        availability_end = int(availability_end_timestamp or current_time)
        availability_start = (
            0 if availability_all_time else
            int(availability_start_timestamp) if availability_start_timestamp is not None else
            availability_end - availability_window_seconds
        )
        availability_window_seconds = max(1, availability_end - availability_start + 1)
        history_limit = max(1, min(history_limit, 500))
        with self._connect() as connection:
            channel_rows = connection.execute(
                """
                SELECT channel_id, name, channel_type, status, models,
                       channel_group, updated_at
                FROM channels
                WHERE status = 1
                ORDER BY name COLLATE NOCASE, channel_id
                """
            ).fetchall()
            usage_rows = connection.execute(
                """
                SELECT channel_id, use_time, frt_ms, created_at
                FROM latency_samples
                WHERE created_at >= ?
                ORDER BY created_at DESC
                """,
                (current_time - 86400,),
            ).fetchall()
            usage_by_channel: dict[int, list[sqlite3.Row]] = defaultdict(list)
            for row in usage_rows:
                usage_by_channel[int(row["channel_id"] or 0)].append(row)

            result: list[dict[str, Any]] = []
            for channel in channel_rows:
                channel_id = int(channel["channel_id"])
                latest_observation = connection.execute(
                    """
                    SELECT observed_at, success, elapsed_ms, frt_ms, message, source
                    FROM channel_observations
                    WHERE channel_id = ?
                    ORDER BY observed_at DESC, id DESC
                    LIMIT 1
                    """,
                    (channel_id,),
                ).fetchone()
                if latest_observation is not None:
                    latest_source = str(latest_observation["source"] or "builtin")
                    observations = connection.execute(
                        """
                        SELECT observed_at, success, elapsed_ms, frt_ms, message, source
                        FROM channel_observations
                        WHERE channel_id = ? AND source = ?
                        ORDER BY observed_at DESC, id DESC
                        LIMIT ?
                        """,
                        (channel_id, latest_source, history_limit),
                    ).fetchall()
                    availability = connection.execute(
                        """
                        SELECT COUNT(*) AS total,
                               SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
                               MIN(observed_at) AS coverage_start_at,
                               MAX(observed_at) AS coverage_end_at
                        FROM channel_observations
                        WHERE channel_id = ? AND source = ?
                          AND observed_at >= ? AND observed_at <= ?
                        """,
                        (
                            channel_id,
                            latest_source,
                            availability_start,
                            availability_end,
                        ),
                    ).fetchone()
                else:
                    latest_source = ""
                    observations = []
                    availability = None
                channel_usage = usage_by_channel.get(channel_id, [])
                durations = [float(row["use_time"] or 0) for row in channel_usage]
                slow_count = sum(
                    1
                    for row in channel_usage
                    if float(row["use_time"] or 0) > self.slow_seconds
                    or float(row["frt_ms"] or 0) > self.slow_seconds * 1000.0
                )
                history = [self._observation_dict(row) for row in reversed(observations)]
                latest = self._observation_dict(latest_observation) if latest_observation else None
                availability_total = int(availability["total"] or 0) if availability else 0
                availability_successes = int(availability["successes"] or 0) if availability else 0
                result.append(
                    {
                        "channel_id": channel_id,
                        "name": str(channel["name"]),
                        "channel_type": int(channel["channel_type"] or 0),
                        "enabled": True,
                        "raw_status": int(channel["status"] or 0),
                        "models": [
                            item.strip()
                            for item in str(channel["models"] or "").split(",")
                            if item.strip()
                        ],
                        "group": str(channel["channel_group"] or ""),
                        "synced_at": int(channel["updated_at"] or 0),
                        "stale_after_seconds": self.channel_stale_seconds,
                        "slow_after_seconds": self.channel_slow_seconds,
                        "latest": latest,
                        "history": history,
                        "availability": {
                            "window_seconds": availability_window_seconds,
                            "start_timestamp": availability_start,
                            "end_timestamp": availability_end,
                            "all_time": availability_all_time,
                            "source": latest_source,
                            "coverage_start_at": int(availability["coverage_start_at"] or 0)
                            if availability
                            else 0,
                            "coverage_end_at": int(availability["coverage_end_at"] or 0)
                            if availability
                            else 0,
                            "total": availability_total,
                            "successes": availability_successes,
                            "percentage": round(availability_successes / availability_total * 100, 2)
                            if availability_total
                            else None,
                        },
                        "usage_24h": {
                            "requests": len(channel_usage),
                            "slow": slow_count,
                            "average_seconds": round(sum(durations) / len(durations), 3)
                            if durations
                            else 0.0,
                            "p95_seconds": self._p95(durations),
                            "last_request_at": int(channel_usage[0]["created_at"] or 0)
                            if channel_usage
                            else 0,
                        },
                    }
                )
        return result

    def channel(self, channel_id: int, now: int | None = None) -> dict[str, Any] | None:
        item = next((row for row in self.channels(now=now, history_limit=288) if row["channel_id"] == channel_id), None)
        if item is None:
            return None
        item["recent_logs"] = self.logs(channel_id=channel_id, limit=50)["items"]
        return item

    def logs(
        self,
        limit: int = 100,
        offset: int = 0,
        channel_id: int | None = None,
        model_name: str = "",
        username: str = "",
        slow_only: bool = False,
        slow_seconds: float | None = None,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
    ) -> dict[str, Any]:
        page_limit = max(1, min(limit, 200))
        page_offset = max(0, offset)
        threshold = self.slow_seconds if slow_seconds is None else slow_seconds
        clauses: list[str] = []
        parameters: list[Any] = []
        if channel_id is not None:
            clauses.append("channel_id = ?")
            parameters.append(channel_id)
        if model_name.strip():
            clauses.append("model_name = ?")
            parameters.append(model_name.strip())
        if username.strip():
            clauses.append("username = ?")
            parameters.append(username.strip())
        if slow_only:
            clauses.append("(use_time > ? OR COALESCE(frt_ms, 0) > ?)")
            parameters.extend((threshold, threshold * 1000.0))
        if start_timestamp is not None and int(start_timestamp) > 0:
            clauses.append("created_at >= ?")
            parameters.append(int(start_timestamp))
        if end_timestamp is not None and int(end_timestamp) > 0:
            clauses.append("created_at <= ?")
            parameters.append(int(end_timestamp))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            bounds = connection.execute(
                "SELECT MIN(created_at) AS first_at, MAX(created_at) AS last_at FROM latency_samples"
            ).fetchone()
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM latency_samples{where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT created_at, channel_id, channel_name, model_name, use_time, frt_ms,
                       username, token_name, token_id, is_stream, request_id,
                       upstream_request_id, group_name
                FROM latency_samples{where}
                ORDER BY created_at DESC, sample_key DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, page_limit, page_offset],
            ).fetchall()
        return {
            "total": total,
            "limit": page_limit,
            "offset": page_offset,
            "collection_started_at": int(bounds["first_at"] or 0),
            "latest_sample_at": int(bounds["last_at"] or 0),
            "retained_from_at": int(bounds["first_at"] or 0),
            "retained_until_at": int(bounds["last_at"] or 0),
            "slow_after_seconds": self.slow_seconds,
            "items": [dict(row) for row in rows],
        }

    def resources(
        self,
        now: int | None = None,
        hours: int = 24,
        limit: int = 1440,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        all_time: bool = False,
        sampling_interval_seconds: int = 15,
    ) -> dict[str, Any]:
        current_time = int(time.time()) if now is None else now
        sample_limit = max(1, min(limit, 5000))
        sampling_interval = max(1, int(sampling_interval_seconds))
        requested_end = min(int(end_timestamp or current_time), current_time)
        requested_start = int(start_timestamp) if start_timestamp is not None else requested_end - max(1, int(hours)) * 3600
        with self._connect() as connection:
            if all_time:
                bounds = connection.execute(
                    "SELECT MIN(created_at) AS first_at, MAX(created_at) AS last_at FROM resource_samples"
                ).fetchone()
                requested_start = int(bounds["first_at"] or requested_end)
                requested_end = min(requested_end, int(bounds["last_at"] or requested_end))
            requested_seconds = max(1, requested_end - requested_start + 1)
            requested_hours = max(1, math.ceil(requested_seconds / 3600))
            bucket_seconds = max(1, math.ceil(requested_seconds / sample_limit))
            if bucket_seconds > 15:
                bucket_seconds = math.ceil(bucket_seconds / 15) * 15
            rows = connection.execute(
                """
                SELECT CAST((created_at - ?) / ? AS INTEGER) * ? + ? AS created_at,
                       AVG(system_cpu) AS system_cpu,
                       AVG(system_memory) AS system_memory,
                       AVG(system_disk) AS system_disk,
                       AVG(system_available_mb) AS system_available_mb,
                       AVG(system_swap) AS system_swap
                FROM resource_samples
                WHERE created_at >= ? AND created_at <= ?
                GROUP BY CAST((created_at - ?) / ? AS INTEGER)
                ORDER BY created_at
                LIMIT ?
                """,
                (
                    requested_start,
                    bucket_seconds,
                    bucket_seconds,
                    requested_start,
                    requested_start,
                    requested_end,
                    requested_start,
                    bucket_seconds,
                    sample_limit,
                ),
            ).fetchall()
            raw_summary = connection.execute(
                """
                SELECT COUNT(*) AS sample_count,
                       MIN(created_at) AS actual_start,
                       MAX(created_at) AS actual_end,
                       MIN(system_cpu) AS system_cpu_min,
                       AVG(system_cpu) AS system_cpu_average,
                       MAX(system_cpu) AS system_cpu_max,
                       MIN(system_memory) AS system_memory_min,
                       AVG(system_memory) AS system_memory_average,
                       MAX(system_memory) AS system_memory_max,
                       MIN(system_disk) AS system_disk_min,
                       AVG(system_disk) AS system_disk_average,
                       MAX(system_disk) AS system_disk_max,
                       MIN(system_swap) AS system_swap_min,
                       AVG(system_swap) AS system_swap_average,
                       MAX(system_swap) AS system_swap_max
                FROM resource_samples
                WHERE created_at >= ? AND created_at <= ?
                """,
                (requested_start, requested_end),
            ).fetchone()
            latest_row = connection.execute(
                """
                SELECT created_at, system_cpu, system_memory, system_disk,
                       system_available_mb, system_swap, containers_json
                FROM resource_samples
                WHERE created_at >= ? AND created_at <= ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (requested_start, requested_end),
            ).fetchone()
            collector_row = connection.execute(
                "SELECT value FROM kv WHERE key = 'collector_health'"
            ).fetchone()
        samples = [{**dict(row), "containers": {}} for row in rows]
        latest: dict[str, Any] | None = None
        if latest_row is not None:
            latest = {
                "created_at": int(latest_row["created_at"]),
                "system_cpu": latest_row["system_cpu"],
                "system_memory": latest_row["system_memory"],
                "system_disk": latest_row["system_disk"],
                "system_available_mb": latest_row["system_available_mb"],
                "system_swap": latest_row["system_swap"],
                "containers": self._decode_json(latest_row["containers_json"], {}),
            }
        if samples and latest is not None:
            samples[-1]["containers"] = self._decode_json(
                latest_row["containers_json"],
                {},
            )
        actual_start = int(raw_summary["actual_start"] or 0) if raw_summary else 0
        actual_end = int(raw_summary["actual_end"] or 0) if raw_summary else 0
        covered_seconds = max(0, actual_end - actual_start + bucket_seconds) if samples else 0
        span_coverage_ratio = min(1.0, covered_seconds / requested_seconds)
        sample_count = int(raw_summary["sample_count"] or 0) if raw_summary else 0
        expected_sample_count = max(1, math.ceil(requested_seconds / sampling_interval))
        sample_coverage_ratio = min(1.0, sample_count / expected_sample_count)
        metric_summary: dict[str, dict[str, float | None]] = {}
        for field in ("system_cpu", "system_memory", "system_disk", "system_swap"):
            metric_summary[field] = {
                "min": raw_summary[f"{field}_min"] if raw_summary else None,
                "average": raw_summary[f"{field}_average"] if raw_summary else None,
                "max": raw_summary[f"{field}_max"] if raw_summary else None,
            }
        collectors = self._decode_json(collector_row["value"], {}) if collector_row is not None else {}
        return {
            "generated_at": current_time,
            "hours": requested_hours,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "all_time": all_time,
            "actual_start": actual_start,
            "actual_end": actual_end,
            "coverage_ratio": round(sample_coverage_ratio, 4),
            "coverage_basis": "expected_sample_count",
            "sample_coverage_ratio": round(sample_coverage_ratio, 4),
            "span_coverage_ratio": round(span_coverage_ratio, 4),
            "expected_sample_count": expected_sample_count,
            "sampling_interval_seconds": sampling_interval,
            "bucket_seconds": bucket_seconds,
            "trend_aggregation": "bucket_average",
            "sample_count": sample_count,
            "latest": latest,
            "summary": metric_summary,
            **self._collector_freshness(collectors, "resources", current_time, 90),
            "samples": samples,
        }

    def provider_status(
        self,
        provider: str,
        now: int | None = None,
        stale_after_seconds: int = 180,
    ) -> dict[str, Any]:
        current_time = int(time.time()) if now is None else int(now)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT observed_at, payload_json
                FROM provider_status_samples
                WHERE provider = ?
                ORDER BY observed_at DESC, id DESC LIMIT 1
                """,
                (provider,),
            ).fetchone()
        if row is None:
            return {
                "provider": provider,
                "available": False,
                "stale": True,
                "observed_at": 0,
                "indicator": "unknown",
                "description": "Waiting for first sample",
                "components": [],
                "incidents": [],
                "active_incident_count": 0,
                "degraded_component_count": 0,
            }
        payload = self._decode_json(row["payload_json"], {})
        components = [item for item in payload.get("components", []) if isinstance(item, dict)]
        incidents = [item for item in payload.get("incidents", []) if isinstance(item, dict)]
        active_incidents = [item for item in incidents if str(item.get("status")) != "resolved"]
        observed_at = int(row["observed_at"])
        return {
            **payload,
            "provider": provider,
            "available": True,
            "observed_at": observed_at,
            "age_seconds": max(0, current_time - observed_at),
            "stale": current_time - observed_at > max(1, stale_after_seconds),
            "components": components,
            "incidents": active_incidents,
            "active_incident_count": len(active_incidents),
            "degraded_component_count": sum(
                str(item.get("status") or "unknown") != "operational"
                for item in components
            ),
        }

    def incidents(
        self,
        status: str = "all",
        severity: str = "all",
        category: str = "all",
        query: str = "",
        window_hours: int = 0,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        limit: int = 50,
        offset: int = 0,
        now: int | None = None,
    ) -> dict[str, Any]:
        current_time = int(time.time()) if now is None else now
        page_limit = max(1, min(limit, 100))
        page_offset = max(0, offset)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, incident_key, kind, severity, title, body, resolution_body,
                       legacy_cause_missing, status,
                       started_at, updated_at, resolved_at, last_notified_at, metadata_json,
                       acknowledged_at, acknowledged_by, acknowledgement_note
                FROM incidents
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,
                         updated_at DESC, id DESC
                """,
            ).fetchall()

        normalized_query = query.strip().casefold()
        minimum_started_at = int(start_timestamp or 0)
        if not minimum_started_at and window_hours:
            minimum_started_at = current_time - max(0, window_hours) * 3600
        maximum_started_at = int(end_timestamp or current_time)
        prepared: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item_category = self._incident_category(str(item["incident_key"]), str(item["kind"]))
            if severity in {"info", "warning", "critical"} and item["severity"] != severity:
                continue
            if category != "all" and item_category != category:
                continue
            if minimum_started_at and int(item["started_at"]) < minimum_started_at:
                continue
            if maximum_started_at and int(item["started_at"]) > maximum_started_at:
                continue
            if normalized_query:
                searchable = "\n".join(
                    str(item.get(field) or "")
                    for field in ("title", "body", "resolution_body", "incident_key", "kind")
                ).casefold()
                if normalized_query not in searchable:
                    continue

            resolved_at = int(item["resolved_at"]) if item["resolved_at"] is not None else None
            resolution_body = str(item.get("resolution_body") or "")
            cause_body = str(item.get("body") or "")
            legacy_cause_missing = bool(item.get("legacy_cause_missing"))
            item["category"] = item_category
            item["legacy_cause_missing"] = legacy_cause_missing
            item["body"] = "" if legacy_cause_missing else cause_body
            item["resolution_body"] = resolution_body
            item["metadata"] = self._decode_json(item.pop("metadata_json", "{}"), {})
            item["duration_seconds"] = max(
                0,
                (resolved_at or current_time) - int(item["started_at"]),
            )
            prepared.append(item)

        resolved = [item for item in prepared if item["status"] == "resolved"]
        open_items = [item for item in prepared if item["status"] == "open"]
        average_resolution_seconds = (
            round(sum(int(item["duration_seconds"]) for item in resolved) / len(resolved))
            if resolved
            else 0
        )
        summary = {
            "open": len(open_items),
            "critical_open": sum(item["severity"] == "critical" for item in open_items),
            "warning_open": sum(item["severity"] == "warning" for item in open_items),
            "resolved": len(resolved),
            "resolved_24h": sum(
                item["resolved_at"] is not None
                and int(item["resolved_at"]) >= current_time - 86_400
                for item in resolved
            ),
            "average_resolution_seconds": average_resolution_seconds,
        }
        filtered = prepared
        if status in {"open", "resolved"}:
            filtered = [item for item in prepared if item["status"] == status]
        return {
            "generated_at": current_time,
            "total": len(filtered),
            "limit": page_limit,
            "offset": page_offset,
            "summary": summary,
            "items": filtered[page_offset:page_offset + page_limit],
        }

    @staticmethod
    def _incident_category(incident_key: str, kind: str) -> str:
        prefix = incident_key.partition(":")[0].strip().lower()
        if prefix in {"channel", "latency", "resource", "container", "service", "collector", "provider"}:
            return prefix
        lowered_kind = kind.lower()
        for candidate in ("channel", "latency", "resource", "container", "service", "collector", "provider"):
            if candidate in lowered_kind:
                return candidate
        return "other"

    @staticmethod
    def _collector_freshness(
        collectors: Any,
        collector_name: str,
        current_time: int,
        default_stale_after: int,
    ) -> dict[str, Any]:
        detail = dict(collectors.get(collector_name) or {}) if isinstance(collectors, dict) else {}
        if not detail:
            return {
                "collector_status": "unknown",
                "collector_age_seconds": 0,
                "collector_stale_after_seconds": default_stale_after,
                "last_collected_at": 0,
            }
        last_success = int(detail.get("last_success_at") or 0)
        first_attempt = int(detail.get("first_attempt_at") or current_time)
        stale_after = max(1, int(detail.get("stale_after_seconds") or default_stale_after))
        age = max(0, current_time - (last_success or first_attempt))
        return {
            "collector_status": "stale" if age > stale_after else ("ok" if last_success else "starting"),
            "collector_age_seconds": age,
            "collector_stale_after_seconds": stale_after,
            "last_collected_at": last_success,
        }

    @staticmethod
    def _observation_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "observed_at": int(row["observed_at"]),
            "success": bool(row["success"]),
            "elapsed_ms": round(float(row["elapsed_ms"] or 0), 1),
            "frt_ms": round(float(row["frt_ms"]), 1) if row["frt_ms"] is not None else None,
            "message": str(row["message"] or ""),
            "source": str(row["source"] or "builtin"),
        }

    @staticmethod
    def _decode_json(value: Any, default: Any) -> Any:
        if not isinstance(value, str) or not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
