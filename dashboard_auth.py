from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator


class AuthStore:
    def __init__(self, database_path: str, session_seconds: int = 7 * 86400):
        self.database_path = database_path
        self.session_seconds = max(300, session_seconds)
        self._dummy_salt = b"newapi-monitor-dummy-salt"
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dashboard_users (
                    username TEXT PRIMARY KEY,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dashboard_sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    remote_addr TEXT NOT NULL,
                    user_agent TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dashboard_session_expiry
                    ON dashboard_sessions(expires_at);

                CREATE TABLE IF NOT EXISTS dashboard_login_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempted_at INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    remote_addr TEXT NOT NULL,
                    success INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dashboard_login_time
                    ON dashboard_login_audit(attempted_at);
                """
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

    def bootstrap_admin(self, username: str, password: str, now: int | None = None) -> bool:
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("dashboard username cannot be empty")
        if len(password) < 12:
            raise ValueError("dashboard password must contain at least 12 characters")
        timestamp = int(time.time()) if now is None else now
        salt = os.urandom(16)
        digest = self._derive(password, salt)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO dashboard_users(
                    username, password_salt, password_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (normalized_username, salt, digest, timestamp, timestamp),
            )
            connection.commit()
            return cursor.rowcount == 1

    def set_password(self, username: str, password: str, now: int | None = None) -> None:
        if len(password) < 12:
            raise ValueError("dashboard password must contain at least 12 characters")
        timestamp = int(time.time()) if now is None else now
        salt = os.urandom(16)
        digest = self._derive(password, salt)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE dashboard_users
                SET password_salt = ?, password_hash = ?, updated_at = ?
                WHERE username = ?
                """,
                (salt, digest, timestamp, username.strip()),
            )
            connection.execute(
                "DELETE FROM dashboard_sessions WHERE username = ?",
                (username.strip(),),
            )
            connection.commit()
            if cursor.rowcount != 1:
                raise ValueError("dashboard user does not exist")

    def verify_password(self, username: str, password: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_salt, password_hash FROM dashboard_users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        if row is None:
            expected = self._derive("invalid-password", self._dummy_salt)
            candidate = self._derive(password, self._dummy_salt)
            hmac.compare_digest(expected, candidate)
            return False
        candidate = self._derive(password, bytes(row["password_salt"]))
        return hmac.compare_digest(bytes(row["password_hash"]), candidate)

    def create_session(
        self,
        username: str,
        remote_addr: str = "",
        user_agent: str = "",
        now: int | None = None,
    ) -> str:
        timestamp = int(time.time()) if now is None else now
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dashboard_sessions(
                    token_hash, username, created_at, expires_at,
                    last_seen_at, remote_addr, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    username.strip(),
                    timestamp,
                    timestamp + self.session_seconds,
                    timestamp,
                    remote_addr[:128],
                    user_agent[:512],
                ),
            )
            connection.commit()
        return token

    def resolve_session(self, token: str, now: int | None = None) -> str | None:
        if not token:
            return None
        timestamp = int(time.time()) if now is None else now
        token_hash = self._token_hash(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT username, expires_at, last_seen_at
                FROM dashboard_sessions WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if int(row["expires_at"]) <= timestamp:
                connection.execute("DELETE FROM dashboard_sessions WHERE token_hash = ?", (token_hash,))
                connection.commit()
                return None
            if timestamp - int(row["last_seen_at"]) >= 60:
                connection.execute(
                    "UPDATE dashboard_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (timestamp, token_hash),
                )
                connection.commit()
            return str(row["username"])

    def revoke_session(self, token: str) -> None:
        if not token:
            return
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM dashboard_sessions WHERE token_hash = ?",
                (self._token_hash(token),),
            )
            connection.commit()

    def record_login(self, username: str, remote_addr: str, success: bool, now: int | None = None) -> None:
        timestamp = int(time.time()) if now is None else now
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dashboard_login_audit(attempted_at, username, remote_addr, success)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp, username[:128], remote_addr[:128], int(success)),
            )
            connection.execute(
                "DELETE FROM dashboard_login_audit WHERE attempted_at < ?",
                (timestamp - 30 * 86400,),
            )
            connection.execute(
                "DELETE FROM dashboard_sessions WHERE expires_at <= ?",
                (timestamp,),
            )
            connection.commit()

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
