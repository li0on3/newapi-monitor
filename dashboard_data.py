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
    ):
        self.database_path = database_path
        self.slow_seconds = slow_seconds
        self.channel_stale_seconds = max(60, channel_stale_seconds)

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
    ) -> dict[str, Any]:
        current_time = int(time.time()) if now is None else now
        since = current_time - request_window_seconds
        with self._connect() as connection:
            channel_rows = connection.execute(
                """
                SELECT c.channel_id, c.status, latest.success, latest.observed_at
                FROM channels c
                LEFT JOIN channel_observations latest ON latest.id = (
                    SELECT id FROM channel_observations
                    WHERE channel_id = c.channel_id
                    ORDER BY observed_at DESC, id DESC LIMIT 1
                )
                ORDER BY c.channel_id
                """
            ).fetchall()
            request_rows = connection.execute(
                """
                SELECT channel_id, use_time, frt_ms, created_at
                FROM latency_samples
                WHERE created_at >= ?
                """,
                (since,),
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

        enabled = [row for row in channel_rows if int(row["status"] or 0) == 1]
        if channel_ids is not None:
            enabled = [row for row in enabled if int(row["channel_id"]) in channel_ids]
            request_rows = [row for row in request_rows if int(row["channel_id"] or 0) in channel_ids]
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
        healthy = sum(1 for row in recent if row["success"] == 1)
        failed = sum(1 for row in recent if row["success"] == 0)
        unknown = len(enabled) - healthy - failed
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
        return {
            "generated_at": current_time,
            "channels": {
                "total": len(enabled),
                "healthy": healthy,
                "failed": failed,
                "unknown": unknown,
                "last_checked_at": max(
                    (int(row["observed_at"] or 0) for row in enabled),
                    default=0,
                ),
            },
            "requests": {
                "window_seconds": request_window_seconds,
                "total": len(request_rows),
                "slow": slow_count,
                "slow_ratio": round(slow_count / len(request_rows) * 100, 2) if request_rows else 0.0,
                "average_seconds": round(sum(durations) / len(durations), 3) if durations else 0.0,
                "p95_seconds": self._p95(durations),
                "average_frt_ms": round(sum(frt_values) / len(frt_values), 1) if frt_values else None,
                "last_request_at": max((int(row["created_at"] or 0) for row in request_rows), default=0),
            },
            "resources": resources,
            "incidents": {
                "open": len(incident_rows),
                "critical": sum(1 for row in incident_rows if str(row["severity"]) == "critical"),
            },
        }

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
    ) -> list[dict[str, Any]]:
        current_time = int(time.time()) if now is None else now
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
                               SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes
                        FROM channel_observations
                        WHERE channel_id = ? AND source = ? AND observed_at >= ?
                        """,
                        (channel_id, latest_source, current_time - availability_window_seconds),
                    ).fetchone()
                else:
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
                        "latest": latest,
                        "history": history,
                        "availability": {
                            "window_seconds": availability_window_seconds,
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
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
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
            "items": [dict(row) for row in rows],
        }

    def resources(self, now: int | None = None, hours: int = 24, limit: int = 1440) -> dict[str, Any]:
        current_time = int(time.time()) if now is None else now
        sample_limit = max(1, min(limit, 5000))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, system_cpu, system_memory, system_disk,
                       system_available_mb, system_swap, containers_json
                FROM resource_samples
                WHERE created_at >= ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (current_time - max(1, hours) * 3600, sample_limit),
            ).fetchall()
        samples = []
        for row in reversed(rows):
            item = dict(row)
            item["containers"] = self._decode_json(item.pop("containers_json"), {})
            samples.append(item)
        return {"generated_at": current_time, "hours": hours, "samples": samples}

    def incidents(self, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        page_limit = max(1, min(limit, 500))
        parameters: list[Any] = []
        where = ""
        if status in {"open", "resolved"}:
            where = " WHERE status = ?"
            parameters.append(status)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, incident_key, kind, severity, title, body, status,
                       started_at, updated_at, resolved_at, last_notified_at
                FROM incidents{where}
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,
                         updated_at DESC, id DESC
                LIMIT ?
                """,
                [*parameters, page_limit],
            ).fetchall()
        return [dict(row) for row in rows]

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
