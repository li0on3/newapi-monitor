from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from monitoring_core.alerting import (
    AlertEvent,
    ChannelObservation,
    LatencySummary,
    _parse_other,
    is_channel_test_log,
    summarize_logs,
)
from monitoring_core.policies import channel_maintenance_state

class StateStore:
    def __init__(self, path: str):
        self.path = path
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
                last_notified_at INTEGER NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                acknowledged_at INTEGER,
                acknowledged_by TEXT NOT NULL DEFAULT '',
                acknowledgement_note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_incident_status_time ON incidents(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_incident_key ON incidents(incident_key, id);

            CREATE TABLE IF NOT EXISTS notification_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_key TEXT NOT NULL UNIQUE,
                destination TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                incident_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at INTEGER NOT NULL,
                lease_until INTEGER,
                last_error TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                delivered_at INTEGER,
                priority TEXT NOT NULL DEFAULT 'info'
            );
            CREATE INDEX IF NOT EXISTS idx_notification_outbox_due
                ON notification_outbox(status, next_attempt_at, id);

            CREATE TABLE IF NOT EXISTS provider_status_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                observed_at INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_provider_status_time
                ON provider_status_samples(provider, observed_at DESC, id DESC);
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
        if "metadata_json" not in incident_columns:
            self.connection.execute(
                "ALTER TABLE incidents ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
            )
        for column, declaration in (
            ("acknowledged_at", "INTEGER"),
            ("acknowledged_by", "TEXT NOT NULL DEFAULT ''"),
            ("acknowledgement_note", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in incident_columns:
                self.connection.execute(f"ALTER TABLE incidents ADD COLUMN {column} {declaration}")
        outbox_columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(notification_outbox)").fetchall()
        }
        if "priority" not in outbox_columns:
            self.connection.execute(
                "ALTER TABLE notification_outbox ADD COLUMN priority TEXT NOT NULL DEFAULT 'info'"
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

    def record_provider_status(
        self,
        provider: str,
        payload: dict[str, Any],
        observed_at: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if observed_at is None else int(observed_at)
        normalized = dict(payload)
        normalized["provider"] = str(provider)
        normalized["observed_at"] = timestamp
        self.connection.execute(
            "DELETE FROM provider_status_samples WHERE provider = ?",
            (str(provider),),
        )
        self.connection.execute(
            "INSERT INTO provider_status_samples(provider, observed_at, payload_json) VALUES (?, ?, ?)",
            (str(provider), timestamp, json.dumps(normalized, ensure_ascii=False)),
        )
        self.connection.commit()

    def provider_local_impact(
        self,
        provider: str,
        now: int | None = None,
        stale_after_seconds: int = 900,
    ) -> dict[str, int]:
        if provider != "openai":
            return {"total": 0, "healthy": 0, "failed": 0, "unknown": 0}
        timestamp = int(time.time()) if now is None else int(now)
        rows = self.connection.execute(
            """
            SELECT c.channel_id, c.models, latest.success, latest.observed_at
            FROM channels c
            LEFT JOIN channel_observations latest ON latest.id = (
                SELECT id FROM channel_observations
                WHERE channel_id = c.channel_id
                ORDER BY observed_at DESC, id DESC LIMIT 1
            )
            WHERE c.status = 1
            """
        ).fetchall()
        prefixes = ("gpt-", "o1", "o3", "o4", "codex", "text-embedding", "dall-e")
        related = [
            row for row in rows
            if any(
                model.strip().lower().startswith(prefixes)
                for model in str(row["models"] or "").split(",")
                if model.strip()
            )
        ]
        healthy = 0
        failed = 0
        for row in related:
            if not row["observed_at"] or int(row["observed_at"]) < timestamp - stale_after_seconds:
                continue
            if int(row["success"] or 0) == 1:
                healthy += 1
            else:
                failed += 1
        return {
            "total": len(related),
            "healthy": healthy,
            "failed": failed,
            "unknown": len(related) - healthy - failed,
        }

    def record_alert_events(self, events: Iterable[AlertEvent], now: int | None = None) -> list[int]:
        timestamp = int(time.time()) if now is None else now
        incident_ids: list[int] = []
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
                        SET status = 'resolved', updated_at = ?, resolved_at = ?, resolution_body = ?,
                            metadata_json = ?
                        WHERE id = ?
                        """,
                        (
                            timestamp,
                            timestamp,
                            event.body,
                            json.dumps(event.metadata, ensure_ascii=False),
                            int(open_row["id"]),
                        ),
                    )
                    incident_ids.append(int(open_row["id"]))
                continue
            if open_row is None:
                cursor = self.connection.execute(
                    """
                    INSERT INTO incidents(
                        incident_key, kind, severity, title, body, status,
                        started_at, updated_at, resolved_at, last_notified_at, metadata_json,
                        resolution_body
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident_key,
                        event.kind,
                        event.severity,
                        event.title,
                        event.body,
                        "resolved" if event.auto_resolve else "open",
                        timestamp,
                        timestamp,
                        timestamp if event.auto_resolve else None,
                        0,
                        json.dumps(event.metadata, ensure_ascii=False),
                        "事件已记录，无持续异常状态。" if event.auto_resolve else "",
                    ),
                )
                incident_ids.append(int(cursor.lastrowid))
            else:
                self.connection.execute(
                    """
                    UPDATE incidents
                    SET kind = ?, severity = ?, title = ?, body = ?,
                        updated_at = ?, metadata_json = ?
                        WHERE id = ?
                    """,
                    (
                        event.kind,
                        event.severity,
                        event.title,
                        event.body,
                        timestamp,
                        json.dumps(event.metadata, ensure_ascii=False),
                        int(open_row["id"]),
                    ),
                )
                incident_ids.append(int(open_row["id"]))
        self.connection.commit()
        return list(dict.fromkeys(incident_ids))

    def enqueue_notifications(
        self,
        subject: str,
        body: str,
        destinations: Iterable[str],
        incident_ids: Iterable[int] = (),
        priority: str = "info",
        now: int | None = None,
    ) -> list[int]:
        timestamp = int(time.time()) if now is None else int(now)
        batch_key = secrets.token_hex(16)
        encoded_incident_ids = json.dumps(
            list(dict.fromkeys(int(item) for item in incident_ids if int(item) > 0))
        )
        notification_ids: list[int] = []
        for destination in dict.fromkeys(str(item) for item in destinations if str(item)):
            cursor = self.connection.execute(
                """
                INSERT INTO notification_outbox(
                    delivery_key, destination, subject, body, incident_ids_json,
                    status, attempts, next_attempt_at, lease_until, last_error,
                    created_at, updated_at, delivered_at, priority
                ) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, NULL, '', ?, ?, NULL, ?)
                """,
                (
                    f"{batch_key}:{destination}",
                    destination,
                    subject,
                    body,
                    encoded_incident_ids,
                    timestamp,
                    timestamp,
                    timestamp,
                    priority if priority in {"info", "warning", "critical"} else "info",
                ),
            )
            notification_ids.append(int(cursor.lastrowid))
        self.connection.commit()
        return notification_ids

    def claim_due_notifications(
        self,
        now: int | None = None,
        limit: int = 20,
        lease_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        timestamp = int(time.time()) if now is None else int(now)
        page_limit = max(1, min(int(limit), 100))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending', lease_until = NULL, updated_at = ?
                WHERE status = 'sending' AND COALESCE(lease_until, 0) <= ?
                """,
                (timestamp, timestamp),
            )
            ids = [
                int(row["id"])
                for row in self.connection.execute(
                    """
                    SELECT id FROM notification_outbox
                    WHERE status = 'pending' AND next_attempt_at <= ?
                    ORDER BY next_attempt_at, id
                    LIMIT ?
                    """,
                    (timestamp, page_limit),
                ).fetchall()
            ]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.connection.execute(
                    f"""
                    UPDATE notification_outbox
                    SET status = 'sending', attempts = attempts + 1,
                        lease_until = ?, updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    [timestamp + max(30, int(lease_seconds)), timestamp, *ids],
                )
                rows = self.connection.execute(
                    f"SELECT * FROM notification_outbox WHERE id IN ({placeholders}) ORDER BY id",
                    ids,
                ).fetchall()
            else:
                rows = []
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return [dict(row) for row in rows]

    def mark_notification_delivered(self, notification_id: int, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        row = self.connection.execute(
            "SELECT incident_ids_json FROM notification_outbox WHERE id = ?",
            (int(notification_id),),
        ).fetchone()
        if row is None:
            return
        cursor = self.connection.execute(
            """
            UPDATE notification_outbox
            SET status = 'delivered', delivered_at = ?, updated_at = ?,
                lease_until = NULL, last_error = ''
            WHERE id = ? AND status = 'sending'
            """,
            (timestamp, timestamp, int(notification_id)),
        )
        if cursor.rowcount != 1:
            self.connection.commit()
            return
        try:
            incident_ids = [int(item) for item in json.loads(str(row["incident_ids_json"]))]
        except (TypeError, ValueError, json.JSONDecodeError):
            incident_ids = []
        if incident_ids:
            placeholders = ",".join("?" for _ in incident_ids)
            self.connection.execute(
                f"UPDATE incidents SET last_notified_at = ? WHERE id IN ({placeholders})",
                [timestamp, *incident_ids],
            )
        self.connection.commit()

    def mark_notification_failed(
        self,
        notification_id: int,
        error: str,
        next_attempt_seconds: int,
        dead: bool = False,
        now: int | None = None,
    ) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        self.connection.execute(
            """
            UPDATE notification_outbox
            SET status = ?, next_attempt_at = ?, lease_until = NULL,
                last_error = ?, updated_at = ?
            WHERE id = ? AND status = 'sending'
            """,
            (
                "dead" if dead else "pending",
                timestamp + max(1, int(next_attempt_seconds)),
                str(error).strip()[:1000],
                timestamp,
                int(notification_id),
            ),
        )
        self.connection.commit()

    def notification(self, notification_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM notification_outbox WHERE id = ?",
            (int(notification_id),),
        ).fetchone()
        if row is None:
            raise KeyError("notification not found")
        return self._notification_dict(row)

    def notifications(
        self,
        status: str = "all",
        destination: str = "all",
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        now: int | None = None,
    ) -> dict[str, Any]:
        timestamp = int(time.time()) if now is None else int(now)
        allowed_statuses = {"pending", "sending", "delivered", "dead", "cancelled"}
        if status != "all" and status not in allowed_statuses:
            raise ValueError("invalid notification status")
        where: list[str] = []
        parameters: list[Any] = []
        if status != "all":
            where.append("status = ?")
            parameters.append(status)
        if destination != "all":
            where.append("destination = ?")
            parameters.append(destination)
        normalized_query = query.strip()
        if normalized_query:
            where.append("(subject LIKE ? OR body LIKE ? OR last_error LIKE ? OR delivery_key LIKE ?)")
            pattern = f"%{normalized_query}%"
            parameters.extend([pattern, pattern, pattern, pattern])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        page_limit = max(1, min(int(limit), 100))
        page_offset = max(0, int(offset))
        total = int(self.connection.execute(
            f"SELECT COUNT(*) FROM notification_outbox {clause}",
            parameters,
        ).fetchone()[0])
        rows = self.connection.execute(
            f"SELECT * FROM notification_outbox {clause} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            [*parameters, page_limit, page_offset],
        ).fetchall()
        count_rows = self.connection.execute(
            "SELECT status, COUNT(*) AS total FROM notification_outbox GROUP BY status"
        ).fetchall()
        destination_rows = self.connection.execute(
            "SELECT DISTINCT destination FROM notification_outbox ORDER BY destination"
        ).fetchall()
        counts = {item: 0 for item in allowed_statuses}
        counts.update({str(row["status"]): int(row["total"]) for row in count_rows})
        return {
            "generated_at": timestamp,
            "total": total,
            "limit": page_limit,
            "offset": page_offset,
            "counts": counts,
            "destinations": [str(row["destination"]) for row in destination_rows],
            "items": [self._notification_dict(row) for row in rows],
        }

    def retry_notifications(self, notification_ids: Iterable[int], now: int | None = None) -> int:
        timestamp = int(time.time()) if now is None else int(now)
        ids = self._validated_notification_ids(notification_ids)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self._notification_rows(ids)
            blocked = [str(row["status"]) for row in rows if str(row["status"]) not in {"pending", "dead", "cancelled"}]
            if blocked:
                raise ValueError(f"cannot retry notification in {blocked[0]} status")
            placeholders = ",".join("?" for _ in ids)
            cursor = self.connection.execute(
                f"""
                UPDATE notification_outbox
                SET status = 'pending', attempts = 0, next_attempt_at = ?, lease_until = NULL,
                    last_error = '', delivered_at = NULL, updated_at = ?
                WHERE id IN ({placeholders}) AND status IN ('pending', 'dead', 'cancelled')
                """,
                [timestamp, timestamp, *ids],
            )
            if cursor.rowcount != len(ids):
                raise ValueError("notification status changed during retry")
            self.connection.commit()
            return int(cursor.rowcount)
        except Exception:
            self.connection.rollback()
            raise

    def cancel_notifications(self, notification_ids: Iterable[int], now: int | None = None) -> int:
        timestamp = int(time.time()) if now is None else int(now)
        ids = self._validated_notification_ids(notification_ids)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self._notification_rows(ids)
            blocked = [str(row["status"]) for row in rows if str(row["status"]) not in {"pending", "dead"}]
            if blocked:
                raise ValueError(f"cannot cancel notification in {blocked[0]} status")
            placeholders = ",".join("?" for _ in ids)
            cursor = self.connection.execute(
                f"""
                UPDATE notification_outbox
                SET status = 'cancelled', lease_until = NULL, updated_at = ?,
                    last_error = CASE WHEN last_error = '' THEN 'cancelled by administrator' ELSE last_error END
                WHERE id IN ({placeholders}) AND status IN ('pending', 'dead')
                """,
                [timestamp, *ids],
            )
            if cursor.rowcount != len(ids):
                raise ValueError("notification status changed during cancellation")
            self.connection.commit()
            return int(cursor.rowcount)
        except Exception:
            self.connection.rollback()
            raise

    def defer_notification(self, notification_id: int, next_attempt_at: int, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else int(now)
        self.connection.execute(
            """
            UPDATE notification_outbox
            SET status = 'pending', attempts = CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END,
                next_attempt_at = ?, lease_until = NULL, updated_at = ?
            WHERE id = ? AND status = 'sending'
            """,
            (max(timestamp + 1, int(next_attempt_at)), timestamp, int(notification_id)),
        )
        self.connection.commit()

    def acknowledge_incident(
        self,
        incident_id: int,
        actor: str,
        note: str = "",
        now: int | None = None,
    ) -> dict[str, Any]:
        timestamp = int(time.time()) if now is None else int(now)
        cursor = self.connection.execute(
            """
            UPDATE incidents
            SET acknowledged_at = ?, acknowledged_by = ?, acknowledgement_note = ?
            WHERE id = ?
            """,
            (timestamp, actor.strip()[:128], note.strip()[:1000], int(incident_id)),
        )
        if cursor.rowcount != 1:
            self.connection.rollback()
            raise KeyError("incident not found")
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM incidents WHERE id = ?", (int(incident_id),)).fetchone()
        return dict(row)

    @staticmethod
    def _notification_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["incident_ids"] = [int(value) for value in json.loads(str(item.pop("incident_ids_json")))]
        except (TypeError, ValueError, json.JSONDecodeError):
            item["incident_ids"] = []
        return item

    @staticmethod
    def _validated_notification_ids(notification_ids: Iterable[int]) -> list[int]:
        ids = list(dict.fromkeys(int(value) for value in notification_ids if int(value) > 0))
        if not ids or len(ids) > 100:
            raise ValueError("provide between 1 and 100 notification IDs")
        return ids

    def _notification_rows(self, ids: list[int]) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"SELECT id, status FROM notification_outbox WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
        if len(rows) != len(ids):
            raise KeyError("notification not found")
        return rows

    def cancel_disabled_notifications(self, active_destinations: Iterable[str], now: int | None = None) -> int:
        timestamp = int(time.time()) if now is None else int(now)
        active = tuple(dict.fromkeys(str(item) for item in active_destinations if str(item)))
        if active:
            # Only the number of SQL placeholders is dynamic; destination values stay parameterized.
            placeholders = ",".join("?" for _ in active)
            cursor = self.connection.execute(
                f"""
                UPDATE notification_outbox
                SET status = 'cancelled', lease_until = NULL, updated_at = ?,
                    last_error = 'notification destination disabled'
                WHERE status IN ('pending', 'sending')
                  AND destination NOT IN ({placeholders})
                """,
                [timestamp, *active],
            )
        else:
            cursor = self.connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'cancelled', lease_until = NULL, updated_at = ?,
                    last_error = 'notification destination disabled'
                WHERE status IN ('pending', 'sending')
                """,
                (timestamp,),
            )
        self.connection.commit()
        return int(cursor.rowcount)

    def resolve_open_incidents(
        self,
        incident_prefix: str,
        resolution_body: str,
        now: int | None = None,
    ) -> int:
        timestamp = int(time.time()) if now is None else int(now)
        cursor = self.connection.execute(
            """
            UPDATE incidents
            SET status = 'resolved', updated_at = ?, resolved_at = ?, resolution_body = ?
            WHERE status = 'open' AND incident_key LIKE ?
            """,
            (timestamp, timestamp, resolution_body, f"{incident_prefix}%"),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def has_open_incident(self, incident_key: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM incidents WHERE incident_key = ? AND status = 'open' LIMIT 1",
            (incident_key,),
        ).fetchone()
        return row is not None

    def reconcile_channel_incidents(
        self,
        channels: Iterable[dict[str, Any]],
        channel_settings: dict[int, dict[str, Any]] | None = None,
        now: int | None = None,
    ) -> int:
        timestamp = int(time.time()) if now is None else int(now)
        settings = channel_settings or {}
        scope: dict[int, tuple[bool, str]] = {}
        for channel in channels:
            channel_id = int(channel.get("id") or 0)
            if channel_id <= 0:
                continue
            config = dict(settings.get(channel_id) or {})
            enabled = int(channel.get("status") or 0) == 1
            maintenance, maintenance_reason = channel_maintenance_state(config, now=timestamp)
            alert_enabled = bool(config.get("alert_enabled", True))
            if not enabled:
                reason = "渠道已在 New API 中禁用，该事件因监控范围变更结束。"
            elif maintenance:
                reason = f"渠道已进入维护状态（{maintenance_reason}），该事件因监控范围变更结束。"
            elif not alert_enabled:
                reason = "渠道告警已关闭，该事件因监控范围变更结束。"
            else:
                reason = ""
            scope[channel_id] = (enabled and not maintenance and alert_enabled, reason)

        rows = self.connection.execute(
            """
            SELECT id, incident_key FROM incidents
            WHERE status = 'open'
              AND (incident_key LIKE 'channel:%' OR incident_key LIKE 'latency:%')
            """
        ).fetchall()
        resolved = 0
        for row in rows:
            incident_key = str(row["incident_key"])
            parts = incident_key.split(":", 2)
            try:
                channel_id = int(parts[1])
            except (IndexError, ValueError):
                continue
            active, reason = scope.get(
                channel_id,
                (False, "渠道已从 New API 删除，该事件因监控范围变更结束。"),
            )
            if active:
                continue
            self.connection.execute(
                """
                UPDATE incidents
                SET status = 'resolved', updated_at = ?, resolved_at = ?, resolution_body = ?
                WHERE id = ?
                """,
                (timestamp, timestamp, reason, int(row["id"])),
            )
            resolved += 1
        self.connection.commit()
        return resolved

    def active_channel_ids(
        self,
        channel_settings: dict[int, dict[str, Any]] | None = None,
        now: int | None = None,
    ) -> set[int]:
        settings = channel_settings or {}
        timestamp = int(time.time()) if now is None else int(now)
        rows = self.connection.execute(
            "SELECT channel_id FROM channels WHERE status = 1"
        ).fetchall()
        return {
            int(row["channel_id"])
            for row in rows
            if not channel_maintenance_state(
                settings.get(int(row["channel_id"])) or {},
                now=timestamp,
            )[0]
            and bool((settings.get(int(row["channel_id"])) or {}).get("alert_enabled", True))
        }

    def ingest_logs(
        self,
        logs: Iterable[dict[str, Any]],
        excluded_token_names: Iterable[str] = (),
    ) -> int:
        inserted, _groups = self.ingest_logs_with_groups(logs, excluded_token_names)
        return inserted

    def ingest_logs_with_groups(
        self,
        logs: Iterable[dict[str, Any]],
        excluded_token_names: Iterable[str] = (),
    ) -> tuple[int, set[tuple[int, str, str]]]:
        inserted = 0
        groups: set[tuple[int, str, str]] = set()
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
            if cursor.rowcount:
                groups.add(
                    (
                        int(log.get("channel") or 0),
                        str(log.get("channel_name") or ""),
                        str(log.get("model_name") or "unknown"),
                    )
                )
        self.connection.commit()
        return inserted, groups

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
            SELECT sample_key, use_time, frt_ms, created_at, request_id
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

    def prune(
        self,
        before_timestamp: int,
        incident_before_timestamp: int | None = None,
        delivery_before_timestamp: int | None = None,
    ) -> None:
        self.connection.execute("DELETE FROM latency_samples WHERE created_at < ?", (before_timestamp,))
        self.connection.execute("DELETE FROM channel_observations WHERE observed_at < ?", (before_timestamp,))
        self.connection.execute("DELETE FROM resource_samples WHERE created_at < ?", (before_timestamp,))
        self.connection.execute("DELETE FROM provider_status_samples WHERE observed_at < ?", (before_timestamp,))
        if incident_before_timestamp is not None:
            self.connection.execute(
                "DELETE FROM incidents WHERE status = 'resolved' AND resolved_at < ?",
                (int(incident_before_timestamp),),
            )
        if delivery_before_timestamp is not None:
            self.connection.execute(
                """
                DELETE FROM notification_outbox
                WHERE status IN ('delivered', 'dead', 'cancelled') AND updated_at < ?
                """,
                (int(delivery_before_timestamp),),
            )
        self.connection.commit()

    def maintain(
        self,
        raw_before_timestamp: int,
        incident_before_timestamp: int,
        delivery_before_timestamp: int,
    ) -> dict[str, Any]:
        self.prune(
            raw_before_timestamp,
            incident_before_timestamp=incident_before_timestamp,
            delivery_before_timestamp=delivery_before_timestamp,
        )
        checkpoint = self.connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        database_path = Path(self.path)
        wal_path = Path(f"{self.path}-wal")
        return {
            "database_bytes": database_path.stat().st_size if database_path.exists() else 0,
            "wal_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
            "checkpoint_busy": int(checkpoint[0]) if checkpoint else 0,
            "checkpoint_log_frames": int(checkpoint[1]) if checkpoint else 0,
            "checkpointed_frames": int(checkpoint[2]) if checkpoint else 0,
        }

    def storage_health(
        self,
        now: int | None = None,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
    ) -> dict[str, Any]:
        timestamp = int(time.time()) if now is None else int(now)
        database_path = Path(self.path)
        wal_path = Path(f"{self.path}-wal")
        database_bytes = database_path.stat().st_size if database_path.exists() else 0
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        outbox = self.connection.execute(
            """
            SELECT
                SUM(CASE WHEN status IN ('pending', 'sending') THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'dead' THEN 1 ELSE 0 END) AS dead,
                MIN(CASE WHEN status IN ('pending', 'sending') THEN created_at END) AS oldest_pending
            FROM notification_outbox
            """
        ).fetchone()
        oldest_pending = int(outbox["oldest_pending"] or 0)
        total_bytes = database_bytes + wal_bytes
        return {
            "database_bytes": database_bytes,
            "wal_bytes": wal_bytes,
            "total_bytes": total_bytes,
            "max_bytes": max(1, int(max_bytes)),
            "over_capacity": total_bytes > max(1, int(max_bytes)),
            "outbox_pending": int(outbox["pending"] or 0),
            "outbox_dead": int(outbox["dead"] or 0),
            "oldest_pending_age_seconds": max(0, timestamp - oldest_pending) if oldest_pending else 0,
        }
