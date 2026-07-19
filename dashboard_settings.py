from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from cryptography.fernet import Fernet, InvalidToken


SECRET_KEYS = {"new_api_access_token", "relay_api_token", "smtp_password"}
MONITOR_ROLES = {"viewer", "operator", "admin"}


class SettingsStore:
    def __init__(
        self,
        database_path: str,
        defaults: dict[str, Any] | None = None,
        secret_key: str = "",
    ):
        self.database_path = database_path
        self.defaults = dict(defaults or {})
        self.cipher = (
            Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode("utf-8")).digest()))
            if secret_key else None
        )
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitor_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitor_channel_settings (
                    channel_id INTEGER PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitor_user_permissions (
                    username TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitor_config_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    remote_addr TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_config_audit_time
                    ON monitor_config_audit(created_at DESC);
                CREATE TABLE IF NOT EXISTS monitor_metadata (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO monitor_metadata(key, value) VALUES ('config_version', 1);
                """
            )
            now = int(time.time())
            for key, value in self.defaults.items():
                connection.execute(
                    "INSERT OR IGNORE INTO monitor_settings(key, value_json, updated_at, updated_by) VALUES (?, ?, ?, ?)",
                    (key, json.dumps(self._encode_value(key, value), ensure_ascii=False), now, "bootstrap"),
                )
            if self.cipher is not None:
                rows = connection.execute(
                    "SELECT key, value_json FROM monitor_settings WHERE key IN (?, ?, ?)",
                    tuple(sorted(SECRET_KEYS)),
                ).fetchall()
                for row in rows:
                    key = str(row["key"])
                    stored = json.loads(str(row["value_json"]))
                    if not isinstance(stored, dict) or "$encrypted" not in stored:
                        connection.execute(
                            "UPDATE monitor_settings SET value_json = ? WHERE key = ?",
                            (json.dumps(self._encode_value(key, stored), ensure_ascii=False), key),
                        )
            connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def runtime_values(self) -> dict[str, Any]:
        values = dict(self.defaults)
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value_json FROM monitor_settings").fetchall()
        for row in rows:
            key = str(row["key"])
            values[key] = self._decode_value(key, json.loads(str(row["value_json"])))
        return values

    def public_values(self) -> dict[str, Any]:
        values = self.runtime_values()
        return {key: ("********" if key in SECRET_KEYS and value else value) for key, value in values.items()}

    def version(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM monitor_metadata WHERE key = 'config_version'"
            ).fetchone()
        return int(row["value"] if row else 1)

    def update_settings(
        self,
        updates: dict[str, Any],
        actor: str,
        remote_addr: str = "",
    ) -> dict[str, Any]:
        unknown = set(updates) - set(self.defaults)
        if unknown:
            raise ValueError("unknown settings: " + ", ".join(sorted(unknown)))
        current = self.runtime_values()
        effective_updates = {
            key: value
            for key, value in updates.items()
            if not (key in SECRET_KEYS and (value is None or str(value) == ""))
        }
        if not effective_updates:
            return self.public_values()
        now = int(time.time())
        before = {key: self._audit_value(key, current.get(key)) for key in effective_updates}
        after = {key: self._audit_value(key, value) for key, value in effective_updates.items()}
        with self._connect() as connection:
            for key, value in effective_updates.items():
                connection.execute(
                    """
                    INSERT INTO monitor_settings(key, value_json, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by
                    """,
                    (key, json.dumps(self._encode_value(key, value), ensure_ascii=False), now, actor),
                )
            self._audit(connection, now, actor, "settings.update", "system", before, after, remote_addr)
            self._bump_version(connection)
            connection.commit()
        return self.public_values()

    def channel_settings(self) -> dict[int, dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT channel_id, config_json FROM monitor_channel_settings"
            ).fetchall()
        return {int(row["channel_id"]): json.loads(str(row["config_json"])) for row in rows}

    def bootstrap_channel_settings(self, settings: dict[int, dict[str, Any]]) -> None:
        if not settings:
            return
        now = int(time.time())
        with self._connect() as connection:
            changed = False
            for channel_id, config in settings.items():
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO monitor_channel_settings(channel_id, config_json, updated_at, updated_by)
                    VALUES (?, ?, ?, 'bootstrap')
                    """,
                    (int(channel_id), json.dumps(config, ensure_ascii=False), now),
                )
                changed = changed or cursor.rowcount == 1
            if changed:
                self._bump_version(connection)
            connection.commit()

    def update_channel(
        self,
        channel_id: int,
        updates: dict[str, Any],
        actor: str,
        remote_addr: str = "",
    ) -> dict[str, Any]:
        if channel_id <= 0:
            raise ValueError("invalid channel id")
        allowed = {
            "display_enabled", "display_name", "sort_order", "probe_enabled",
            "probe_model", "probe_path", "probe_format", "probe_prompt",
            "max_output_tokens", "alert_enabled", "maintenance_mode",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError("unknown channel settings: " + ", ".join(sorted(unknown)))
        current = self.channel_settings().get(channel_id, {})
        merged = {**current, **updates}
        merged["display_enabled"] = bool(merged.get("display_enabled", True))
        merged["probe_enabled"] = bool(merged.get("probe_enabled", False))
        merged["alert_enabled"] = bool(merged.get("alert_enabled", True))
        merged["maintenance_mode"] = bool(merged.get("maintenance_mode", False))
        merged["sort_order"] = int(merged.get("sort_order", 0))
        merged["max_output_tokens"] = max(1, min(4096, int(merged.get("max_output_tokens", 1))))
        if merged.get("probe_format") not in {None, "", "responses", "chat", "anthropic"}:
            raise ValueError("probe_format must be responses, chat or anthropic")
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO monitor_channel_settings(channel_id, config_json, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (channel_id, json.dumps(merged, ensure_ascii=False), now, actor),
            )
            self._audit(connection, now, actor, "channel.update", str(channel_id), current, merged, remote_addr)
            self._bump_version(connection)
            connection.commit()
        return merged

    def decorate_channels(
        self,
        channels: list[dict[str, Any]],
        include_hidden: bool = False,
    ) -> list[dict[str, Any]]:
        settings = self.channel_settings()
        result = []
        for source in channels:
            item = dict(source)
            config = settings.get(int(item["channel_id"]), {})
            display_enabled = bool(config.get("display_enabled", True))
            item["source_name"] = item.get("name", "")
            item["name"] = str(config.get("display_name") or item.get("name") or "")
            item["display_enabled"] = display_enabled
            item["monitor_config"] = config
            if include_hidden or (bool(item.get("enabled")) and display_enabled):
                result.append(item)
        return sorted(
            result,
            key=lambda item: (
                int(item.get("monitor_config", {}).get("sort_order", 0)),
                str(item.get("name", "")).lower(),
                int(item.get("channel_id", 0)),
            ),
        )

    def real_probe_rules(self) -> dict[str, dict[str, Any]]:
        rules: dict[str, dict[str, Any]] = {}
        for channel_id, config in self.channel_settings().items():
            if not config.get("probe_enabled") or not str(config.get("probe_model") or "").strip():
                continue
            request_format = str(config.get("probe_format") or "responses")
            default_paths = {
                "responses": "/v1/responses",
                "chat": "/v1/chat/completions",
                "anthropic": "/v1/messages",
            }
            rules[str(channel_id)] = {
                "model": str(config["probe_model"]).strip(),
                "format": request_format,
                "path": str(config.get("probe_path") or default_paths.get(request_format, "/v1/responses")),
                "prompt": str(config.get("probe_prompt") or "1"),
                "max_output_tokens": int(config.get("max_output_tokens") or 1),
            }
        return rules

    def resolve_role(self, username: str, source_role: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM monitor_user_permissions WHERE username = ?", (username,)
            ).fetchone()
        if row:
            return str(row["role"])
        if source_role >= 100:
            return "admin"
        if source_role >= 10:
            return "operator"
        return None

    def users(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT username, role, updated_at, updated_by FROM monitor_user_permissions ORDER BY username"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_user_role(self, username: str, role: str | None, actor: str, remote_addr: str = "") -> None:
        normalized = username.strip()
        if not normalized:
            raise ValueError("username is required")
        if role is not None and role not in MONITOR_ROLES:
            raise ValueError("invalid monitor role")
        now = int(time.time())
        before_role = None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM monitor_user_permissions WHERE username = ?", (normalized,)
            ).fetchone()
            before_role = str(row["role"]) if row else None
            if role is None:
                connection.execute("DELETE FROM monitor_user_permissions WHERE username = ?", (normalized,))
            else:
                connection.execute(
                    """
                    INSERT INTO monitor_user_permissions(username, role, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET role=excluded.role, updated_at=excluded.updated_at, updated_by=excluded.updated_by
                    """,
                    (normalized, role, now, actor),
                )
            self._audit(connection, now, actor, "user-role.update", normalized, {"role": before_role}, {"role": role}, remote_addr)
            self._bump_version(connection)
            connection.commit()

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM monitor_config_audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _audit_value(key: str, value: Any) -> Any:
        return "********" if key in SECRET_KEYS and value else value

    def _encode_value(self, key: str, value: Any) -> Any:
        if key not in SECRET_KEYS or self.cipher is None or value in {None, ""}:
            return value
        token = self.cipher.encrypt(str(value).encode("utf-8")).decode("ascii")
        return {"$encrypted": token}

    def _decode_value(self, key: str, value: Any) -> Any:
        if key not in SECRET_KEYS or not isinstance(value, dict) or "$encrypted" not in value:
            return value
        if self.cipher is None:
            raise RuntimeError("MONITOR_SECRET_KEY is required to decrypt stored settings")
        try:
            return self.cipher.decrypt(str(value["$encrypted"]).encode("ascii")).decode("utf-8")
        except InvalidToken as error:
            raise RuntimeError("MONITOR_SECRET_KEY cannot decrypt stored settings") from error

    @staticmethod
    def _bump_version(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE monitor_metadata SET value = value + 1 WHERE key = 'config_version'"
        )

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        now: int,
        actor: str,
        action: str,
        target: str,
        before: Any,
        after: Any,
        remote_addr: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO monitor_config_audit(created_at, actor, action, target, before_json, after_json, remote_addr)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now, actor, action, target,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                remote_addr[:128],
            ),
        )
