from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator


KEY_GROUP_COLORS = {"slate", "emerald", "blue", "amber", "violet", "rose"}


class KeyGroupError(ValueError):
    pass


class KeyGroupStore:
    def __init__(self, database_path: str):
        self.database_path = database_path
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS console_key_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL,
                    name TEXT COLLATE NOCASE NOT NULL,
                    color TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(owner_user_id, name),
                    UNIQUE(owner_user_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_console_key_groups_owner
                    ON console_key_groups(owner_user_id, sort_order, name);
                """
            )
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'console_key_group_memberships'"
            ).fetchone()
            if table_exists is not None:
                columns = connection.execute(
                    "PRAGMA table_info(console_key_group_memberships)"
                ).fetchall()
                primary_key = [
                    str(row["name"])
                    for row in sorted(columns, key=lambda row: int(row["pk"]) or 99)
                    if int(row["pk"]) > 0
                ]
                if primary_key != ["owner_user_id", "token_id", "group_id"]:
                    connection.executescript(
                        """
                        BEGIN IMMEDIATE;
                        DROP INDEX IF EXISTS idx_console_key_group_memberships_group;
                        ALTER TABLE console_key_group_memberships
                            RENAME TO console_key_group_memberships_legacy;
                        CREATE TABLE console_key_group_memberships (
                            owner_user_id INTEGER NOT NULL,
                            token_id INTEGER NOT NULL,
                            group_id INTEGER NOT NULL,
                            assigned_at INTEGER NOT NULL,
                            PRIMARY KEY(owner_user_id, token_id, group_id),
                            FOREIGN KEY(owner_user_id, group_id)
                                REFERENCES console_key_groups(owner_user_id, id) ON DELETE CASCADE
                        );
                        INSERT OR IGNORE INTO console_key_group_memberships(
                            owner_user_id, token_id, group_id, assigned_at
                        )
                        SELECT owner_user_id, token_id, group_id, assigned_at
                        FROM console_key_group_memberships_legacy;
                        DROP TABLE console_key_group_memberships_legacy;
                        COMMIT;
                        """
                    )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS console_key_group_memberships (
                    owner_user_id INTEGER NOT NULL,
                    token_id INTEGER NOT NULL,
                    group_id INTEGER NOT NULL,
                    assigned_at INTEGER NOT NULL,
                    PRIMARY KEY(owner_user_id, token_id, group_id),
                    FOREIGN KEY(owner_user_id, group_id)
                        REFERENCES console_key_groups(owner_user_id, id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_console_key_group_memberships_group
                    ON console_key_group_memberships(owner_user_id, group_id, token_id);
                """
            )
            connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _name(value: str) -> str:
        name = str(value or "").strip()
        if not name or len(name) > 48:
            raise KeyGroupError("group name must contain 1 to 48 characters")
        return name

    @staticmethod
    def _color(value: str) -> str:
        color = str(value or "slate").strip().lower()
        if color not in KEY_GROUP_COLORS:
            raise KeyGroupError("unsupported group color")
        return color

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "owner_user_id": int(row["owner_user_id"]),
            "name": str(row["name"]),
            "color": str(row["color"]),
            "sort_order": int(row["sort_order"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "key_count": int(row["key_count"]) if "key_count" in row.keys() else 0,
        }

    def list_groups(self, owner_user_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT groups.*, COUNT(memberships.token_id) AS key_count
                FROM console_key_groups AS groups
                LEFT JOIN console_key_group_memberships AS memberships
                    ON memberships.owner_user_id = groups.owner_user_id
                    AND memberships.group_id = groups.id
                WHERE groups.owner_user_id = ?
                GROUP BY groups.id
                ORDER BY groups.sort_order, groups.name COLLATE NOCASE, groups.id
                """,
                (int(owner_user_id),),
            ).fetchall()
        return [self._row(row) for row in rows]

    def create_group(self, owner_user_id: int, name: str, color: str) -> dict[str, Any]:
        if owner_user_id <= 0:
            raise KeyGroupError("invalid owner user id")
        normalized_name = self._name(name)
        normalized_color = self._color(color)
        now = int(time.time())
        try:
            with self._connect() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM console_key_groups WHERE owner_user_id = ?",
                    (owner_user_id,),
                ).fetchone()[0]
                if int(count) >= 50:
                    raise KeyGroupError("group limit reached")
                cursor = connection.execute(
                    """
                    INSERT INTO console_key_groups(
                        owner_user_id, name, color, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (owner_user_id, normalized_name, normalized_color, now, now),
                )
                connection.commit()
                group_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise KeyGroupError("group already exists") from error
        return self.get_group(owner_user_id, group_id)

    def get_group(self, owner_user_id: int, group_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT groups.*, COUNT(memberships.token_id) AS key_count
                FROM console_key_groups AS groups
                LEFT JOIN console_key_group_memberships AS memberships
                    ON memberships.owner_user_id = groups.owner_user_id
                    AND memberships.group_id = groups.id
                WHERE groups.owner_user_id = ? AND groups.id = ?
                GROUP BY groups.id
                """,
                (int(owner_user_id), int(group_id)),
            ).fetchone()
        if row is None:
            raise KeyGroupError("group not found")
        return self._row(row)

    def update_group(
        self,
        owner_user_id: int,
        group_id: int,
        name: str,
        color: str,
    ) -> dict[str, Any]:
        normalized_name = self._name(name)
        normalized_color = self._color(color)
        now = int(time.time())
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE console_key_groups
                    SET name = ?, color = ?, updated_at = ?
                    WHERE owner_user_id = ? AND id = ?
                    """,
                    (normalized_name, normalized_color, now, owner_user_id, group_id),
                )
                if cursor.rowcount != 1:
                    raise KeyGroupError("group not found")
                connection.commit()
        except sqlite3.IntegrityError as error:
            raise KeyGroupError("group already exists") from error
        return self.get_group(owner_user_id, group_id)

    def delete_group(self, owner_user_id: int, group_id: int) -> dict[str, Any]:
        group = self.get_group(owner_user_id, group_id)
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM console_key_groups WHERE owner_user_id = ? AND id = ?",
                (owner_user_id, group_id),
            )
            if cursor.rowcount != 1:
                raise KeyGroupError("group not found")
            connection.commit()
        return group

    def membership_map(self, owner_user_id: int) -> dict[int, list[int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT token_id, group_id
                FROM console_key_group_memberships
                WHERE owner_user_id = ?
                ORDER BY token_id, group_id
                """,
                (owner_user_id,),
            ).fetchall()
        memberships: dict[int, list[int]] = {}
        for row in rows:
            memberships.setdefault(int(row["token_id"]), []).append(int(row["group_id"]))
        return memberships

    def assign_tokens(
        self,
        owner_user_id: int,
        token_ids: list[int],
        group_ids: list[int],
    ) -> int:
        normalized = list(dict.fromkeys(int(token_id) for token_id in token_ids if int(token_id) > 0))
        if not normalized or len(normalized) > 100:
            raise KeyGroupError("token_ids must contain 1 to 100 positive integers")
        normalized_groups = list(
            dict.fromkeys(int(group_id) for group_id in group_ids if int(group_id) > 0)
        )
        if len(normalized_groups) > 50:
            raise KeyGroupError("group_ids must contain at most 50 positive integers")
        now = int(time.time())
        with self._connect() as connection:
            if normalized_groups:
                placeholders = ",".join("?" for _ in normalized_groups)
                rows = connection.execute(
                    f"""
                    SELECT id FROM console_key_groups
                    WHERE owner_user_id = ? AND id IN ({placeholders})
                    """,
                    (owner_user_id, *normalized_groups),
                ).fetchall()
                if {int(row["id"]) for row in rows} != set(normalized_groups):
                    raise KeyGroupError("group not found")
            token_placeholders = ",".join("?" for _ in normalized)
            connection.execute(
                f"""
                DELETE FROM console_key_group_memberships
                WHERE owner_user_id = ? AND token_id IN ({token_placeholders})
                """,
                (owner_user_id, *normalized),
            )
            if normalized_groups:
                connection.executemany(
                    """
                    INSERT INTO console_key_group_memberships(
                        owner_user_id, token_id, group_id, assigned_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (owner_user_id, token_id, group_id, now)
                        for token_id in normalized
                        for group_id in normalized_groups
                    ],
                )
            connection.commit()
        return len(normalized)

    def set_group_members(
        self,
        owner_user_id: int,
        group_id: int,
        token_ids: list[int],
    ) -> int:
        normalized = list(dict.fromkeys(int(token_id) for token_id in token_ids if int(token_id) > 0))
        if len(normalized) > 2000:
            raise KeyGroupError("token_ids must contain at most 2000 positive integers")
        now = int(time.time())
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM console_key_groups WHERE owner_user_id = ? AND id = ?",
                (owner_user_id, group_id),
            ).fetchone()
            if exists is None:
                raise KeyGroupError("group not found")
            current_rows = connection.execute(
                """
                SELECT token_id FROM console_key_group_memberships
                WHERE owner_user_id = ? AND group_id = ?
                """,
                (owner_user_id, group_id),
            ).fetchall()
            current = {int(row["token_id"]) for row in current_rows}
            desired = set(normalized)
            removed = current - desired
            added = desired - current
            if removed:
                placeholders = ",".join("?" for _ in removed)
                connection.execute(
                    f"""
                    DELETE FROM console_key_group_memberships
                    WHERE owner_user_id = ? AND group_id = ?
                      AND token_id IN ({placeholders})
                    """,
                    (owner_user_id, group_id, *sorted(removed)),
                )
            if added:
                connection.executemany(
                    """
                    INSERT INTO console_key_group_memberships(
                        owner_user_id, token_id, group_id, assigned_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [(owner_user_id, token_id, group_id, now) for token_id in sorted(added)],
                )
            connection.commit()
        return len(removed) + len(added)

    def remove_tokens(self, owner_user_id: int, token_ids: list[int]) -> int:
        normalized = list(dict.fromkeys(int(token_id) for token_id in token_ids if int(token_id) > 0))
        if not normalized:
            return 0
        with self._connect() as connection:
            placeholders = ",".join("?" for _ in normalized)
            cursor = connection.execute(
                f"""
                DELETE FROM console_key_group_memberships
                WHERE owner_user_id = ? AND token_id IN ({placeholders})
                """,
                (owner_user_id, *normalized),
            )
            connection.commit()
        return max(cursor.rowcount, 0)


def build_key_usage_workspace(
    tokens: list[dict[str, Any]],
    flow_items: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    memberships: dict[int, list[int]],
    start_timestamp: int,
    end_timestamp: int,
) -> dict[str, Any]:
    group_by_id = {int(group["id"]): dict(group) for group in groups}
    token_names = {
        int(token.get("id") or 0): str(token.get("name") or "")[:128]
        for token in tokens
        if int(token.get("id") or 0) > 0
    }
    token_usage: dict[int, dict[str, Any]] = {}
    excluded_deleted_key_usage = {"requests": 0, "quota": 0, "tokens": 0}

    def usage_for(token_id: int) -> dict[str, Any]:
        if token_id not in token_usage:
            groups_for_token = [
                group_by_id[group_id]
                for group_id in memberships.get(token_id, [])
                if group_id in group_by_id
            ]
            first_group = groups_for_token[0] if groups_for_token else None
            token_usage[token_id] = {
                "token_id": token_id,
                "token_name": token_names.get(token_id, ""),
                "key_group_ids": [int(group["id"]) for group in groups_for_token],
                "key_groups": [
                    {
                        "id": int(group["id"]),
                        "name": str(group["name"]),
                        "color": str(group["color"]),
                    }
                    for group in groups_for_token
                ],
                "key_group_id": int(first_group["id"]) if first_group else None,
                "key_group_name": str(first_group["name"]) if first_group else "",
                "key_group_color": str(first_group["color"]) if first_group else "slate",
                "requests": 0,
                "quota": 0,
                "tokens": 0,
                "models": set(),
            }
        return token_usage[token_id]

    for token_id in token_names:
        usage_for(token_id)
    for item in flow_items:
        token_id = int(item.get("token_id") or 0)
        if token_id not in token_names:
            excluded_deleted_key_usage["requests"] += int(item.get("count") or 0)
            excluded_deleted_key_usage["quota"] += int(item.get("quota") or 0)
            excluded_deleted_key_usage["tokens"] += int(item.get("token_used") or 0)
            continue
        usage = usage_for(token_id)
        if not usage["token_name"]:
            usage["token_name"] = str(item.get("token_name") or "")[:128]
        usage["requests"] += int(item.get("count") or 0)
        usage["quota"] += int(item.get("quota") or 0)
        usage["tokens"] += int(item.get("token_used") or 0)
        model_name = str(item.get("model_name") or "")[:256]
        if model_name:
            usage["models"].add(model_name)

    group_usage = {
        group_id: {"requests": 0, "quota": 0, "tokens": 0, "models": set()}
        for group_id in group_by_id
    }
    ungrouped_usage = {"requests": 0, "quota": 0, "tokens": 0, "models": set()}
    for usage in token_usage.values():
        group_ids = usage["key_group_ids"]
        buckets = [group_usage[group_id] for group_id in group_ids] if group_ids else [ungrouped_usage]
        for bucket in buckets:
            bucket["requests"] += usage["requests"]
            bucket["quota"] += usage["quota"]
            bucket["tokens"] += usage["tokens"]
            bucket["models"].update(usage["models"])

    def serialize_usage(value: dict[str, Any]) -> dict[str, int]:
        return {
            "requests": int(value["requests"]),
            "quota": int(value["quota"]),
            "tokens": int(value["tokens"]),
            "models": len(value["models"]),
        }

    serialized_tokens: dict[str, dict[str, Any]] = {}
    for token_id, usage in token_usage.items():
        serialized_tokens[str(token_id)] = {
            **usage,
            "models": sorted(usage["models"])[:100],
        }
    current_group_counts = {group_id: 0 for group_id in group_by_id}
    for token_id in token_names:
        for group_id in memberships.get(token_id, []):
            if group_id in current_group_counts:
                current_group_counts[group_id] += 1
    serialized_groups = [
        {
            **group,
            "key_count": current_group_counts[int(group["id"])],
            "usage": serialize_usage(group_usage[int(group["id"])]),
        }
        for group in groups
    ]
    summary_source = {"requests": 0, "quota": 0, "tokens": 0, "models": set()}
    for usage in token_usage.values():
        summary_source["requests"] += usage["requests"]
        summary_source["quota"] += usage["quota"]
        summary_source["tokens"] += usage["tokens"]
        summary_source["models"].update(usage["models"])
    active_group_ids = set(group_by_id)
    ungrouped_count = sum(
        1
        for token_id in token_names
        if not any(group_id in active_group_ids for group_id in memberships.get(token_id, []))
    )
    return {
        "start_timestamp": int(start_timestamp),
        "end_timestamp": int(end_timestamp),
        "usage_attribution": "current_multi_membership",
        "summary_scope": "current_keys",
        "excluded_deleted_key_usage": excluded_deleted_key_usage,
        "summary": {
            **serialize_usage(summary_source),
            "keys": len(token_names),
            "groups": len(groups),
        },
        "groups": serialized_groups,
        "ungrouped": {
            "key_count": ungrouped_count,
            "usage": serialize_usage(ungrouped_usage),
        },
        "token_usage": serialized_tokens,
    }
