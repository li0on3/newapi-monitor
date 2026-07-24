from __future__ import annotations

import asyncio
import logging
import json
import os
import threading
import time
import urllib.parse
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dashboard_auth import AuthStore
from dashboard_data import DashboardRepository
from dashboard_key_usage import KeyUsageClient, KeyUsageError, SlidingWindowRateLimiter, role_allows_key_lookup
from dashboard_newapi_console import NewAPIConsoleClient, NewAPIConsoleError
from dashboard_settings import SECRET_KEYS, SettingsStore
from dashboard_setup import NewAPIProvisioner, SetupError, verify_setup_token
from dashboard_sso import NewAPISessionVerifier
from newapi_monitor import Config, DEFAULT_OPENAI_COMPONENT_NAMES, MonitorApp, NotificationDispatcher, OpenAIStatusClient, StateStore, env_bool, env_int


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("newapi-monitor-dashboard")


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class SetupCompletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setup_token: str = Field(min_length=8, max_length=512)
    new_api_base_url: str = Field(min_length=8, max_length=2048)
    username: str | None = Field(None, min_length=1, max_length=128)
    password: str | None = Field(None, min_length=1, max_length=512)
    new_api_access_token: str | None = Field(None, min_length=1, max_length=4096)
    new_api_user_id: int | None = Field(None, ge=1)
    relay_api_token: str | None = Field(None, min_length=1, max_length=4096)

    @field_validator("new_api_base_url")
    @classmethod
    def validate_setup_base_url(cls, value: str) -> str:
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("new_api_base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("new_api_base_url must not contain credentials, query or fragment")
        return value.strip().rstrip("/")

    @model_validator(mode="after")
    def validate_authentication_mode(self) -> "SetupCompletePayload":
        credential_fields = bool(self.username or self.password)
        token_fields = bool(
            self.new_api_access_token or self.new_api_user_id or self.relay_api_token
        )
        credentials = bool(self.username and self.password)
        tokens = bool(self.new_api_access_token and self.new_api_user_id and self.relay_api_token)
        if credential_fields and not credentials:
            raise ValueError("username and password must be provided together")
        if token_fields and not tokens:
            raise ValueError("management token, user ID and relay token must be provided together")
        if credentials == tokens:
            raise ValueError("provide either New API credentials or explicit tokens")
        return self


class SettingsUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_api_base_url: str | None = Field(None, min_length=8, max_length=2048)
    new_api_access_token: str | None = Field(None, max_length=4096)
    new_api_user_id: int | None = Field(None, ge=1)
    relay_api_token: str | None = Field(None, max_length=4096)
    dashboard_refresh_seconds: int | None = Field(None, ge=2, le=3600)
    channel_sync_interval_seconds: int | None = Field(None, ge=2, le=3600)
    channel_interval_seconds: int | None = Field(None, ge=5, le=86400)
    channel_probe_concurrency: int | None = Field(None, ge=1, le=16)
    channel_failure_threshold: int | None = Field(None, ge=1, le=10)
    channel_recovery_threshold: int | None = Field(None, ge=1, le=10)
    log_interval_seconds: int | None = Field(None, ge=5, le=3600)
    resource_interval_seconds: int | None = Field(None, ge=5, le=3600)
    report_interval_seconds: int | None = Field(None, ge=60, le=604800)
    log_overlap_seconds: int | None = Field(None, ge=1, le=3600)
    log_initial_lookback_seconds: int | None = Field(None, ge=60, le=2592000)
    slow_request_seconds: float | None = Field(None, gt=0, le=86400)
    latency_hard_limit_seconds: float | None = Field(None, gt=0, le=86400)
    latency_reminder_seconds: int | None = Field(None, ge=60, le=604800)
    channel_slow_seconds: float | None = Field(None, gt=0, le=3600)
    resource_sustain_seconds: int | None = Field(None, ge=5, le=86400)
    system_cpu_threshold: float | None = Field(None, gt=0, le=100)
    system_memory_threshold: float | None = Field(None, gt=0, le=100)
    system_disk_threshold: float | None = Field(None, gt=0, le=100)
    container_cpu_threshold: float | None = Field(None, gt=0, le=1000)
    container_memory_threshold: float | None = Field(None, gt=0, le=100)
    docker_container_name: str | None = Field(None, max_length=256)
    docker_container_names: str | None = Field(None, max_length=4096)
    disk_path: str | None = Field(None, min_length=1, max_length=1024)
    excluded_token_names: str | None = Field(None, max_length=4096)
    retention_days: int | None = Field(None, ge=8, le=3650)
    smtp_host: str | None = Field(None, max_length=512)
    smtp_port: int | None = Field(None, ge=1, le=65535)
    smtp_user: str | None = Field(None, max_length=512)
    smtp_password: str | None = Field(None, max_length=4096)
    smtp_from: str | None = Field(None, max_length=512)
    smtp_to: str | None = Field(None, max_length=4096)
    smtp_starttls: bool | None = None
    smtp_ssl: bool | None = None
    email_enabled: bool | None = None
    wecom_app_enabled: bool | None = None
    wecom_corp_id: str | None = Field(None, max_length=128)
    wecom_agent_id: int | None = Field(None, ge=1, le=2_147_483_647)
    wecom_app_secret: str | None = Field(None, max_length=4096)
    wecom_to_user: str | None = Field(None, max_length=4096)
    wecom_to_party: str | None = Field(None, max_length=4096)
    wecom_to_tag: str | None = Field(None, max_length=4096)
    wecom_webhook_enabled: bool | None = None
    wecom_webhook_url: str | None = Field(None, max_length=4096)
    feishu_app_enabled: bool | None = None
    feishu_app_id: str | None = Field(None, max_length=256)
    feishu_app_secret: str | None = Field(None, max_length=4096)
    feishu_receive_id_type: str | None = Field(
        None,
        pattern="^(open_id|user_id|union_id|email|chat_id)$",
    )
    feishu_receive_id: str | None = Field(None, max_length=512)
    feishu_webhook_enabled: bool | None = None
    feishu_webhook_url: str | None = Field(None, max_length=4096)
    feishu_webhook_secret: str | None = Field(None, max_length=4096)
    send_startup_email: bool | None = None
    subject_prefix: str | None = Field(None, max_length=256)
    key_usage_enabled: bool | None = None
    key_usage_min_role: str | None = Field(None, pattern="^(viewer|operator|admin)$")
    key_usage_log_limit: int | None = Field(None, ge=10, le=500)
    key_usage_attempts_per_minute: int | None = Field(None, ge=1, le=120)
    key_usage_quota_per_unit: float | None = Field(None, gt=0, le=1_000_000_000)
    console_enabled: bool | None = None
    console_min_role: str | None = Field(None, pattern="^(viewer|operator|admin)$")
    console_overview_enabled: bool | None = None
    console_analytics_enabled: bool | None = None
    console_keys_enabled: bool | None = None
    console_logs_enabled: bool | None = None
    console_default_days: int | None = Field(None, ge=1, le=30)
    console_write_attempts_per_minute: int | None = Field(None, ge=1, le=120)
    console_reveal_attempts_per_minute: int | None = Field(None, ge=1, le=30)
    openai_status_enabled: bool | None = None
    openai_status_alert_enabled: bool | None = None
    openai_status_interval_seconds: int | None = Field(None, ge=30, le=3600)
    openai_status_timeout_seconds: int | None = Field(None, ge=3, le=30)
    openai_status_min_impact: str | None = Field(
        None,
        pattern="^(none|minor|major|critical)$",
    )
    openai_status_component_ids: list[str] | None = Field(None, max_length=500)
    openai_status_failure_threshold: int | None = Field(None, ge=1, le=10)
    openai_status_recovery_threshold: int | None = Field(None, ge=1, le=10)
    openai_status_include_in_overall: bool | None = None
    openai_status_admin_visible: bool | None = None
    openai_status_viewer_visible: bool | None = None

    @field_validator("openai_status_component_ids")
    @classmethod
    def validate_openai_component_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized: list[str] = []
        for component_id in value:
            item = component_id.strip()
            if not item or len(item) > 128 or any(ord(character) < 32 for character in item):
                raise ValueError("openai_status_component_ids contains an invalid component ID")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("new_api_base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("new_api_base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("new_api_base_url must not contain credentials, query or fragment")
        return value.strip().rstrip("/")

    @field_validator("wecom_webhook_url")
    @classmethod
    def validate_wecom_webhook_url(cls, value: str | None) -> str | None:
        if value in {None, "", "********"}:
            return value
        parsed = urllib.parse.urlsplit(value.strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname != "qyapi.weixin.qq.com"
            or parsed.path != "/cgi-bin/webhook/send"
            or not urllib.parse.parse_qs(parsed.query).get("key")
        ):
            raise ValueError("wecom_webhook_url must be an official WeCom bot webhook")
        return value.strip()

    @field_validator("feishu_webhook_url")
    @classmethod
    def validate_feishu_webhook_url(cls, value: str | None) -> str | None:
        if value in {None, "", "********"}:
            return value
        parsed = urllib.parse.urlsplit(value.strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"open.feishu.cn", "open.larksuite.com"}
            or not parsed.path.startswith("/open-apis/bot/v2/hook/")
        ):
            raise ValueError("feishu_webhook_url must be an official Feishu bot webhook")
        return value.strip()


class NotificationTestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(pattern="^(email|wecom_app|wecom_webhook|feishu_app|feishu_webhook)$")


class KeyUsageQueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=4, max_length=512)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != value or any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("api_key must not contain whitespace or control characters")
        return normalized


class ConsoleTokenPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)
    remain_quota: int = Field(0, ge=0, le=9_000_000_000_000_000)
    expired_time: int = Field(-1, ge=-1, le=4_102_444_800)
    unlimited_quota: bool = False
    model_limits_enabled: bool = False
    model_limits: str = Field("", max_length=8192)
    allow_ips: str = Field("", max_length=4096)
    group: str = Field("", max_length=128)
    cross_group_retry: bool = False

    @field_validator("name")
    @classmethod
    def validate_console_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("text fields must not contain control characters")
        return normalized

    @field_validator("group")
    @classmethod
    def validate_console_text(cls, value: str) -> str:
        normalized = value.strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("text fields must not contain control characters")
        return normalized


class ConsoleTokenStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: int = Field(ge=1, le=2)


class ConsoleBatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("ids")
    @classmethod
    def validate_console_ids(cls, value: list[int]) -> list[int]:
        normalized: list[int] = []
        for token_id in value:
            if token_id <= 0:
                raise ValueError("ids must contain positive integers")
            if token_id not in normalized:
                normalized.append(token_id)
        return normalized


class ChannelSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_enabled: bool | None = None
    overview_admin_visible: bool | None = None
    overview_viewer_visible: bool | None = None
    display_name: str | None = Field(None, max_length=128)
    sort_order: int | None = Field(None, ge=-100000, le=100000)
    probe_enabled: bool | None = None
    probe_model: str | None = Field(None, max_length=256)
    probe_path: str | None = Field(None, max_length=256)
    probe_format: str | None = Field(None, pattern="^(responses|chat|anthropic)$")
    probe_prompt: str | None = Field(None, max_length=256)
    max_output_tokens: int | None = Field(None, ge=1, le=4096)
    alert_enabled: bool | None = None
    maintenance_mode: bool | None = None

    @field_validator("probe_path")
    @classmethod
    def validate_probe_path(cls, value: str | None) -> str | None:
        if value in {None, ""}:
            return value
        if not value.startswith("/") or value.startswith("//") or "://" in value:
            raise ValueError("probe_path must be a relative API path")
        return value


class ChannelVisibilityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: int = Field(ge=1)
    overview_admin_visible: bool
    overview_viewer_visible: bool


class ChannelVisibilityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ChannelVisibilityItem] = Field(min_length=1, max_length=1000)


class AccessRolePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = Field(None, pattern="^(viewer|operator|admin)$")


class LoginRateLimiter:
    def __init__(self, attempts: int = 5, window_seconds: int = 600):
        self.attempts = attempts
        self.window_seconds = window_seconds
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def retry_after(self, key: str, now: float | None = None) -> int:
        current_time = time.time() if now is None else now
        with self.lock:
            bucket = self.failures[key]
            while bucket and current_time - bucket[0] >= self.window_seconds:
                bucket.popleft()
            if len(bucket) < self.attempts:
                return 0
            return max(1, int(self.window_seconds - (current_time - bucket[0])))

    def fail(self, key: str, now: float | None = None) -> None:
        current_time = time.time() if now is None else now
        with self.lock:
            self.failures[key].append(current_time)

    def clear(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)


class Runtime:
    def __init__(self):
        self.state_db = os.getenv("STATE_DB", "/data/monitor.db")
        self.cookie_name = os.getenv("DASHBOARD_COOKIE_NAME", "newapi_monitor_session")
        self.cookie_path = os.getenv("DASHBOARD_COOKIE_PATH", "/") or "/"
        self.cookie_secure = env_bool("DASHBOARD_COOKIE_SECURE", True)
        self.session_seconds = env_int("DASHBOARD_SESSION_SECONDS", 7 * 86400)
        self.monitor_enabled = env_bool("MONITOR_WORKER_ENABLED", True)
        self.trust_proxy_headers = env_bool("TRUST_PROXY_HEADERS", False)
        self.static_dir = Path(os.getenv("DASHBOARD_STATIC_DIR", "/app/static")).resolve()
        self.auth: AuthStore | None = None
        self.settings: SettingsStore | None = None
        self.sso: NewAPISessionVerifier | None = None
        self.repository: DashboardRepository | None = None
        self.monitor_thread: threading.Thread | None = None
        self.monitor_stop = threading.Event()
        self.monitor_error = ""
        self.setup_required = False
        self.setup_token_hash = os.getenv("SETUP_TOKEN_HASH", "").strip().lower()
        self.setup_token_expires_at = env_int("SETUP_TOKEN_EXPIRES_AT", 0)
        self.monitor_lock = threading.Lock()

    def initialize(self) -> None:
        state_store = StateStore(self.state_db)
        state_store.connection.close()
        self.auth = AuthStore(self.state_db, self.session_seconds)
        username = os.getenv("DASHBOARD_ADMIN_USERNAME", "admin").strip()
        password = os.getenv("DASHBOARD_ADMIN_PASSWORD", "")
        if not password:
            raise RuntimeError("DASHBOARD_ADMIN_PASSWORD is required")
        created = self.auth.bootstrap_admin(username, password)
        if not created and env_bool("DASHBOARD_FORCE_PASSWORD_SYNC", False):
            self.auth.set_password(username, password)
        self.settings = SettingsStore(
            self.state_db,
            self._bootstrap_settings(),
            secret_key=os.getenv("MONITOR_SECRET_KEY", ""),
        )
        if not os.getenv("MONITOR_SECRET_KEY", ""):
            LOGGER.warning("MONITOR_SECRET_KEY is not configured; sensitive settings are stored without application-level encryption")
        values = self.settings.runtime_values()
        if not bool(values.get("openai_status_enabled", True)):
            state_store = StateStore(self.state_db)
            state_store.resolve_open_incidents(
                "provider:openai:",
                "OpenAI 官方状态监控已关闭，该事件因监控范围变更结束。",
            )
            state_store.set_json("openai_status_state", {})
            state_store.connection.close()
        configured = bool(
            str(values.get("new_api_base_url") or "").strip()
            and str(values.get("new_api_access_token") or "").strip()
            and int(values.get("new_api_user_id") or 0) > 0
        )
        if not self.settings.is_setup_complete() and configured:
            self.settings.complete_setup("legacy-bootstrap")
        self.setup_required = not self.settings.is_setup_complete()
        bootstrap_rules = json.loads(os.getenv("REAL_PROBE_RULES", "{}") or "{}")
        self.settings.bootstrap_channel_settings({
            int(channel_id): {
                "display_enabled": True,
                "probe_enabled": True,
                "alert_enabled": True,
                "maintenance_mode": False,
                "probe_model": rule.get("model", ""),
                "probe_path": rule.get("path", ""),
                "probe_format": rule.get("format", "responses"),
                "probe_prompt": rule.get("prompt", "1"),
                "max_output_tokens": rule.get("max_output_tokens", 1),
            }
            for channel_id, rule in bootstrap_rules.items()
            if isinstance(rule, dict)
        })
        self.sso = NewAPISessionVerifier(
            lambda: str(self.settings.runtime_values()["new_api_base_url"]),
            cache_seconds=env_int("NEW_API_SSO_CACHE_SECONDS", 30),
        )
        self.refresh_repository()
        if self.monitor_enabled and not self.setup_required:
            self.start_monitor()

    def start_monitor(self) -> None:
        with self.monitor_lock:
            if self.monitor_thread is not None and self.monitor_thread.is_alive():
                return
            self.monitor_stop.clear()
            self.monitor_thread = threading.Thread(
                target=self._run_monitor,
                name="newapi-monitor-worker",
                daemon=True,
            )
            self.monitor_thread.start()

    def shutdown(self) -> None:
        self.monitor_stop.set()
        if self.monitor_thread is not None:
            self.monitor_thread.join(timeout=10)

    def _run_monitor(self) -> None:
        while not self.monitor_stop.is_set():
            try:
                self.monitor_error = ""
                if self.settings is None:
                    raise RuntimeError("settings store is unavailable")
                loaded_version = self.settings.version()
                cycle_stop = threading.Event()
                cycle_error: list[BaseException] = []

                def run_cycle() -> None:
                    try:
                        MonitorApp(self.monitor_config()).run_forever(cycle_stop)
                    except BaseException as error:
                        cycle_error.append(error)

                worker = threading.Thread(target=run_cycle, name="newapi-monitor-cycle", daemon=True)
                worker.start()
                while worker.is_alive() and not self.monitor_stop.wait(2):
                    if self.settings.version() != loaded_version:
                        LOGGER.info("monitor configuration changed; reloading worker")
                        cycle_stop.set()
                        break
                if self.monitor_stop.is_set():
                    cycle_stop.set()
                worker.join(timeout=15)
                if cycle_error:
                    raise cycle_error[0]
                if self.monitor_stop.is_set():
                    return
            except Exception as error:
                self.monitor_error = str(error)
                LOGGER.exception("monitor worker stopped unexpectedly; retrying in 30 seconds")
                if self.monitor_stop.wait(30):
                    return

    def _bootstrap_settings(self) -> dict[str, Any]:
        return {
            "new_api_base_url": os.getenv("NEW_API_BASE_URL", "http://new-api:3000").rstrip("/"),
            "new_api_access_token": os.getenv("NEW_API_ACCESS_TOKEN", ""),
            "new_api_user_id": env_int("NEW_API_USER_ID", 0),
            "relay_api_token": os.getenv("RELAY_API_TOKEN", ""),
            "dashboard_refresh_seconds": env_int("DASHBOARD_REFRESH_SECONDS", 5),
            "channel_sync_interval_seconds": env_int("CHANNEL_SYNC_INTERVAL_SECONDS", 5),
            "channel_interval_seconds": env_int("CHANNEL_INTERVAL_SECONDS", 300),
            "channel_probe_concurrency": env_int("CHANNEL_PROBE_CONCURRENCY", 3),
            "channel_failure_threshold": env_int("CHANNEL_FAILURE_THRESHOLD", 2),
            "channel_recovery_threshold": env_int("CHANNEL_RECOVERY_THRESHOLD", 2),
            "log_interval_seconds": env_int("LOG_INTERVAL_SECONDS", 300),
            "resource_interval_seconds": env_int("RESOURCE_INTERVAL_SECONDS", 60),
            "report_interval_seconds": env_int("REPORT_INTERVAL_SECONDS", 86400),
            "log_overlap_seconds": env_int("LOG_OVERLAP_SECONDS", 60),
            "log_initial_lookback_seconds": env_int("LOG_INITIAL_LOOKBACK_SECONDS", 3600),
            "slow_request_seconds": float(os.getenv("SLOW_REQUEST_SECONDS", "60")),
            "latency_hard_limit_seconds": float(os.getenv("LATENCY_HARD_LIMIT_SECONDS", "180")),
            "latency_reminder_seconds": env_int("LATENCY_REMINDER_SECONDS", 1800),
            "channel_slow_seconds": float(os.getenv("CHANNEL_SLOW_SECONDS", "30")),
            "resource_sustain_seconds": env_int("RESOURCE_SUSTAIN_SECONDS", 600),
            "system_cpu_threshold": float(os.getenv("SYSTEM_CPU_THRESHOLD", "85")),
            "system_memory_threshold": float(os.getenv("SYSTEM_MEMORY_THRESHOLD", "85")),
            "system_disk_threshold": float(os.getenv("SYSTEM_DISK_THRESHOLD", "80")),
            "container_cpu_threshold": float(os.getenv("CONTAINER_CPU_THRESHOLD", "90")),
            "container_memory_threshold": float(os.getenv("CONTAINER_MEMORY_THRESHOLD", "90")),
            "docker_container_name": os.getenv("DOCKER_CONTAINER_NAME", ""),
            "docker_container_names": os.getenv("DOCKER_CONTAINER_NAMES", os.getenv("DOCKER_CONTAINER_NAME", "")),
            "disk_path": os.getenv("DISK_PATH", "/"),
            "excluded_token_names": os.getenv("EXCLUDED_TOKEN_NAMES", "模型测试,newapi-monitor-probe"),
            "retention_days": env_int("RETENTION_DAYS", 90),
            "smtp_host": os.getenv("SMTP_HOST", ""),
            "smtp_port": env_int("SMTP_PORT", 25),
            "smtp_user": os.getenv("SMTP_USER", ""),
            "smtp_password": os.getenv("SMTP_PASSWORD", ""),
            "smtp_from": os.getenv("SMTP_FROM", "newapi-monitor@localhost"),
            "smtp_to": os.getenv("SMTP_TO", ""),
            "smtp_starttls": env_bool("SMTP_STARTTLS", False),
            "smtp_ssl": env_bool("SMTP_SSL", False),
            "email_enabled": env_bool("EMAIL_ENABLED", bool(os.getenv("SMTP_TO", ""))),
            "wecom_app_enabled": env_bool("WECOM_APP_ENABLED", False),
            "wecom_corp_id": os.getenv("WECOM_CORP_ID", ""),
            "wecom_agent_id": env_int("WECOM_AGENT_ID", 0),
            "wecom_app_secret": os.getenv("WECOM_APP_SECRET", ""),
            "wecom_to_user": os.getenv("WECOM_TO_USER", "@all"),
            "wecom_to_party": os.getenv("WECOM_TO_PARTY", ""),
            "wecom_to_tag": os.getenv("WECOM_TO_TAG", ""),
            "wecom_webhook_enabled": env_bool("WECOM_WEBHOOK_ENABLED", False),
            "wecom_webhook_url": os.getenv("WECOM_WEBHOOK_URL", ""),
            "feishu_app_enabled": env_bool("FEISHU_APP_ENABLED", False),
            "feishu_app_id": os.getenv("FEISHU_APP_ID", ""),
            "feishu_app_secret": os.getenv("FEISHU_APP_SECRET", ""),
            "feishu_receive_id_type": os.getenv("FEISHU_RECEIVE_ID_TYPE", "chat_id"),
            "feishu_receive_id": os.getenv("FEISHU_RECEIVE_ID", ""),
            "feishu_webhook_enabled": env_bool("FEISHU_WEBHOOK_ENABLED", False),
            "feishu_webhook_url": os.getenv("FEISHU_WEBHOOK_URL", ""),
            "feishu_webhook_secret": os.getenv("FEISHU_WEBHOOK_SECRET", ""),
            "send_startup_email": env_bool("SEND_STARTUP_EMAIL", True),
            "subject_prefix": os.getenv("SUBJECT_PREFIX", "[New API监控]"),
            "key_usage_enabled": env_bool("KEY_USAGE_ENABLED", True),
            "key_usage_min_role": os.getenv("KEY_USAGE_MIN_ROLE", "admin"),
            "key_usage_log_limit": env_int("KEY_USAGE_LOG_LIMIT", 100),
            "key_usage_attempts_per_minute": env_int("KEY_USAGE_ATTEMPTS_PER_MINUTE", 10),
            "key_usage_quota_per_unit": float(os.getenv("KEY_USAGE_QUOTA_PER_UNIT", "500000")),
            "console_enabled": env_bool("CONSOLE_ENABLED", True),
            "console_min_role": os.getenv("CONSOLE_MIN_ROLE", "viewer"),
            "console_overview_enabled": env_bool("CONSOLE_OVERVIEW_ENABLED", True),
            "console_analytics_enabled": env_bool("CONSOLE_ANALYTICS_ENABLED", True),
            "console_keys_enabled": env_bool("CONSOLE_KEYS_ENABLED", True),
            "console_logs_enabled": env_bool("CONSOLE_LOGS_ENABLED", True),
            "console_default_days": env_int("CONSOLE_DEFAULT_DAYS", 7),
            "console_write_attempts_per_minute": env_int("CONSOLE_WRITE_ATTEMPTS_PER_MINUTE", 30),
            "console_reveal_attempts_per_minute": env_int("CONSOLE_REVEAL_ATTEMPTS_PER_MINUTE", 6),
            "openai_status_enabled": env_bool("OPENAI_STATUS_ENABLED", True),
            "openai_status_alert_enabled": env_bool("OPENAI_STATUS_ALERT_ENABLED", True),
            "openai_status_interval_seconds": env_int("OPENAI_STATUS_INTERVAL_SECONDS", 60),
            "openai_status_timeout_seconds": env_int("OPENAI_STATUS_TIMEOUT_SECONDS", 10),
            "openai_status_min_impact": os.getenv("OPENAI_STATUS_MIN_IMPACT", "major"),
            "openai_status_component_ids": [
                item.strip()
                for item in os.getenv("OPENAI_STATUS_COMPONENT_IDS", "").split(",")
                if item.strip()
            ],
            "openai_status_failure_threshold": env_int("OPENAI_STATUS_FAILURE_THRESHOLD", 2),
            "openai_status_recovery_threshold": env_int("OPENAI_STATUS_RECOVERY_THRESHOLD", 2),
            "openai_status_include_in_overall": env_bool("OPENAI_STATUS_INCLUDE_IN_OVERALL", False),
            "openai_status_admin_visible": env_bool("OPENAI_STATUS_ADMIN_VISIBLE", True),
            "openai_status_viewer_visible": env_bool("OPENAI_STATUS_VIEWER_VISIBLE", True),
        }

    def monitor_config(self) -> Config:
        if self.settings is None:
            raise RuntimeError("settings store is unavailable")
        values = self.settings.runtime_values()
        values["state_db"] = self.state_db
        values["real_probe_rules"] = self.settings.real_probe_rules()
        values["channel_settings"] = self.settings.channel_settings()
        return Config.from_values(values)

    def refresh_repository(self) -> None:
        values = self.settings.runtime_values() if self.settings else self._bootstrap_settings()
        self.repository = DashboardRepository(
            self.state_db,
            slow_seconds=float(values["slow_request_seconds"]),
            channel_stale_seconds=env_int("CHANNEL_STALE_SECONDS", 900),
        )

    def remote_addr(self, request: Request) -> str:
        if self.trust_proxy_headers:
            real_ip = request.headers.get("x-real-ip", "").strip()
            if real_ip:
                return real_ip[:128]
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded[:128]
        return request.client.host[:128] if request.client else "unknown"


runtime = Runtime()
login_limiter = LoginRateLimiter()
setup_limiter = LoginRateLimiter(attempts=5, window_seconds=600)
key_usage_limiter = SlidingWindowRateLimiter()
console_write_limiter = SlidingWindowRateLimiter()
console_reveal_limiter = SlidingWindowRateLimiter()


@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime.initialize()
    try:
        yield
    finally:
        runtime.shutdown()


app = FastAPI(
    title="New API Monitor",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
allowed_hosts = [
    host.strip()
    for host in os.getenv("DASHBOARD_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["localhost", "127.0.0.1"])
app.add_middleware(GZipMiddleware, minimum_size=800)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if (
        request.url.path.startswith("/api/")
        and request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and request.headers.get("x-monitor-request") != "1"
    ):
        return JSONResponse(status_code=403, content={"detail": "request verification failed"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if runtime.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.middleware("http")
async def direct_monitor_prefix(request: Request, call_next):
    path = request.scope.get("path", "")
    if path == "/monitor" or path.startswith("/monitor/"):
        normalized = path[len("/monitor"):] or "/"
        request.scope["path"] = normalized
        request.scope["raw_path"] = normalized.encode("utf-8")
    return await call_next(request)


def require_auth(request: Request) -> dict[str, Any]:
    if runtime.auth is None:
        raise HTTPException(status_code=503, detail="dashboard is starting")
    username = runtime.auth.resolve_session(request.cookies.get(runtime.cookie_name, ""))
    if username is not None:
        return {"username": username, "display_name": username, "role": "admin", "source": "emergency"}
    if runtime.sso is not None and runtime.settings is not None:
        identity = runtime.sso.verify(
            request.cookies.get("session", ""),
            request.headers.get("new-api-user", ""),
        )
        if identity is not None:
            role = runtime.settings.resolve_role(identity["username"], int(identity["source_role"]))
            if role is None:
                raise HTTPException(status_code=403, detail="New API account is not allowed to access monitor")
            return {**identity, "role": role}
    raise HTTPException(status_code=401, detail="authentication required")


AuthenticatedUser = Annotated[dict[str, Any], Depends(require_auth)]


def require_operator(user: AuthenticatedUser) -> dict[str, Any]:
    if user["role"] not in {"operator", "admin"}:
        raise HTTPException(status_code=403, detail="operator permission required")
    return user


def require_admin(user: AuthenticatedUser) -> dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="administrator permission required")
    return user


def require_console_access(
    user: dict[str, Any],
    values: dict[str, Any],
    page: str,
) -> dict[str, Any]:
    if user.get("source") != "newapi" or int(user.get("user_id") or 0) <= 0:
        raise HTTPException(status_code=403, detail="客户控制台必须使用 New API 会话登录")
    if not bool(values.get("console_enabled", True)):
        raise HTTPException(status_code=404, detail="客户控制台未启用")
    role_order = {"viewer": 0, "operator": 1, "admin": 2}
    role = str(user.get("role") or "viewer")
    minimum = str(values.get("console_min_role") or "viewer")
    if role_order.get(role, -1) < role_order.get(minimum, 0):
        raise HTTPException(status_code=403, detail="当前账号无权访问客户控制台")
    if page not in {"overview", "analytics", "keys", "logs"}:
        raise HTTPException(status_code=404, detail="客户控制台页面不存在")
    if not bool(values.get(f"console_{page}_enabled", True)):
        raise HTTPException(status_code=404, detail="该客户控制台页面未启用")
    return user


def console_capabilities(user: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    role_order = {"viewer": 0, "operator": 1, "admin": 2}
    source_available = user.get("source") == "newapi" and int(user.get("user_id") or 0) > 0
    role = str(user.get("role") or "viewer")
    minimum = str(values.get("console_min_role") or "viewer")
    base_available = bool(values.get("console_enabled", True)) and source_available and (
        role_order.get(role, -1) >= role_order.get(minimum, 0)
    )
    pages = {
        page: bool(values.get(f"console_{page}_enabled", True))
        for page in ("overview", "analytics", "keys", "logs")
    } if base_available else {}
    available = base_available and any(pages.values())
    if not available:
        pages = {}
    return {
        "available": available,
        "pages": pages,
        "global_scope": available and int(user.get("source_role") or 0) >= 10,
    }


OperatorUser = Annotated[dict[str, Any], Depends(require_operator)]
AdminUser = Annotated[dict[str, Any], Depends(require_admin)]


def repository() -> DashboardRepository:
    if runtime.repository is None:
        raise HTTPException(status_code=503, detail="dashboard is starting")
    return runtime.repository


@app.get("/api/health")
def health() -> JSONResponse:
    details = system_health_snapshot()
    if details["status"] == "setup_required":
        return JSONResponse(
            status_code=200,
            content={"status": details["status"], "timestamp": details["timestamp"]},
        )
    healthy = details["status"] == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": details["status"], "timestamp": details["timestamp"]},
    )


def system_health_snapshot() -> dict[str, Any]:
    try:
        repository().summary(request_window_seconds=300)
        database_ok = True
        database_error = ""
    except Exception as error:
        database_ok = False
        database_error = str(error)
    monitor_alive = runtime.setup_required or not runtime.monitor_enabled or bool(
        runtime.monitor_thread and runtime.monitor_thread.is_alive()
    )
    monitor_ok = monitor_alive and not runtime.monitor_error
    collectors: dict[str, dict[str, Any]] = {}
    if runtime.monitor_enabled and database_ok:
        try:
            state_store = StateStore(runtime.state_db)
            collectors = state_store.collector_health()
            state_store.connection.close()
        except Exception as error:
            database_ok = False
            database_error = str(error)
    collectors_ok = runtime.setup_required or all(item.get("status") != "stale" for item in collectors.values())
    healthy = database_ok and monitor_ok and collectors_ok
    return {
        "status": "setup_required" if runtime.setup_required and database_ok else ("ok" if healthy else "degraded"),
        "database": "ok" if database_ok else "degraded",
        "database_error": database_error,
        "monitor_worker": "disabled" if not runtime.monitor_enabled else ("running" if monitor_ok else "degraded"),
        "monitor_error": runtime.monitor_error,
        "collectors": collectors,
        "timestamp": int(time.time()),
    }


@app.get("/api/setup/status")
def setup_status() -> dict[str, Any]:
    now = int(time.time())
    return {
        "required": runtime.setup_required,
        "available": bool(
            runtime.setup_required
            and runtime.setup_token_hash
            and runtime.setup_token_expires_at > now
        ),
        "expires_at": runtime.setup_token_expires_at if runtime.setup_required else 0,
    }


@app.post("/api/setup/complete")
async def complete_setup(payload: SetupCompletePayload, request: Request) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    if not runtime.setup_required:
        raise HTTPException(status_code=409, detail="setup is already complete")
    remote_addr = runtime.remote_addr(request)
    retry_after = setup_limiter.retry_after(remote_addr)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="too many failed setup attempts",
            headers={"Retry-After": str(retry_after)},
        )
    now = int(time.time())
    token_valid = (
        runtime.setup_token_expires_at > now
        and verify_setup_token(payload.setup_token, runtime.setup_token_hash)
    )
    if not token_valid:
        setup_limiter.fail(remote_addr)
        await asyncio.sleep(0.35)
        raise HTTPException(status_code=403, detail="invalid or expired setup token")

    provisioner = NewAPIProvisioner()
    try:
        if payload.username and payload.password:
            updates = await asyncio.to_thread(
                provisioner.provision,
                payload.new_api_base_url,
                payload.username,
                payload.password,
            )
        else:
            assert payload.new_api_access_token is not None
            assert payload.new_api_user_id is not None
            assert payload.relay_api_token is not None
            await asyncio.to_thread(
                provisioner.validate_management_token,
                payload.new_api_base_url,
                payload.new_api_user_id,
                payload.new_api_access_token,
            )
            updates = {
                "new_api_base_url": payload.new_api_base_url,
                "new_api_access_token": payload.new_api_access_token,
                "new_api_user_id": payload.new_api_user_id,
                "relay_api_token": payload.relay_api_token,
            }
    except SetupError as error:
        setup_limiter.fail(remote_addr)
        await asyncio.sleep(0.35)
        raise HTTPException(status_code=400, detail=str(error)) from error

    runtime.settings.update_settings(updates, "setup", remote_addr)
    runtime.settings.complete_setup("setup")
    runtime.setup_required = False
    runtime.refresh_repository()
    setup_limiter.clear(remote_addr)
    if runtime.monitor_enabled:
        runtime.start_monitor()
    return {"completed": True, "new_api_user_id": int(updates["new_api_user_id"])}


@app.get("/api/system/status")
def system_status(_: AdminUser) -> dict[str, Any]:
    return system_health_snapshot()


@app.post("/api/auth/login")
async def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    if runtime.auth is None:
        raise HTTPException(status_code=503, detail="dashboard is starting")
    remote_addr = runtime.remote_addr(request)
    retry_after = login_limiter.retry_after(remote_addr)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="too many failed login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    authenticated = runtime.auth.verify_password(payload.username, payload.password)
    runtime.auth.record_login(payload.username, remote_addr, authenticated)
    if not authenticated:
        login_limiter.fail(remote_addr)
        await asyncio.sleep(0.35)
        raise HTTPException(status_code=401, detail="invalid username or password")
    login_limiter.clear(remote_addr)
    token = runtime.auth.create_session(
        payload.username,
        remote_addr=remote_addr,
        user_agent=request.headers.get("user-agent", ""),
    )
    response.set_cookie(
        runtime.cookie_name,
        token,
        max_age=runtime.session_seconds,
        httponly=True,
        secure=runtime.cookie_secure,
        samesite="lax",
        path=runtime.cookie_path,
    )
    return {"authenticated": True, "username": payload.username}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    if runtime.auth is not None:
        runtime.auth.revoke_session(request.cookies.get(runtime.cookie_name, ""))
    response.delete_cookie(
        runtime.cookie_name,
        path=runtime.cookie_path,
        secure=runtime.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return {"authenticated": False}


@app.get("/api/auth/me")
def me(user: AuthenticatedUser) -> dict[str, Any]:
    refresh_seconds = 5
    if runtime.settings is not None:
        refresh_seconds = max(2, int(runtime.settings.runtime_values().get("dashboard_refresh_seconds", 5)))
    key_usage_available = False
    if runtime.settings is not None:
        values = runtime.settings.runtime_values()
        key_usage_available = bool(values.get("key_usage_enabled", True)) and role_allows_key_lookup(
            str(user["role"]), str(values.get("key_usage_min_role", "admin"))
        )
    capabilities = console_capabilities(user, values if runtime.settings is not None else {})
    return {
        "authenticated": True,
        **user,
        "dashboard_refresh_seconds": refresh_seconds,
        "key_usage_available": key_usage_available,
        "console_available": capabilities["available"],
        "console_pages": capabilities["pages"],
        "console_global_scope": capabilities["global_scope"],
    }


@app.post("/api/key-usage/query")
def query_key_usage(payload: KeyUsageQueryPayload, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    values = runtime.settings.runtime_values()
    if not bool(values.get("key_usage_enabled", True)):
        raise HTTPException(status_code=404, detail="Key 用量查询未启用")
    if not role_allows_key_lookup(str(user["role"]), str(values.get("key_usage_min_role", "admin"))):
        raise HTTPException(status_code=403, detail="当前账号无权查询 Key 用量")
    limiter_key = f"{user['username']}:{runtime.remote_addr(request)}"
    retry_after = key_usage_limiter.consume(
        limiter_key,
        max(1, int(values.get("key_usage_attempts_per_minute", 10))),
    )
    if retry_after:
        raise HTTPException(status_code=429, detail=f"查询过于频繁，请 {retry_after} 秒后重试")
    try:
        result = KeyUsageClient(str(values["new_api_base_url"])).query(
            payload.api_key,
            max(10, min(int(values.get("key_usage_log_limit", 100)), 500)),
            float(values.get("key_usage_quota_per_unit", 500_000)),
        )
    except KeyUsageError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    channel_names = {int(item["channel_id"]): str(item["name"]) for item in repository().channels()}
    for item in result["calls"]:
        item["channel_name"] = channel_names.get(int(item["channel_id"]), "")
    return result


def console_values() -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    return runtime.settings.runtime_values()


def console_client(values: dict[str, Any]) -> NewAPIConsoleClient:
    return NewAPIConsoleClient(str(values.get("new_api_base_url") or ""))


def console_identity(
    request: Request,
    user: dict[str, Any],
    values: dict[str, Any],
    page: str,
) -> tuple[str, int]:
    require_console_access(user, values, page)
    session_cookie = request.cookies.get("session", "")
    if not session_cookie:
        raise HTTPException(status_code=401, detail="New API session is required")
    return session_cookie, int(user["user_id"])


def console_time_range(
    start_timestamp: int | None,
    end_timestamp: int | None,
    source_role: int,
    default_days: int,
) -> tuple[int, int]:
    now = int(time.time())
    end = end_timestamp or now
    start = start_timestamp or end - max(1, min(default_days, 30)) * 86400
    if start <= 0 or end <= 0 or start > end:
        raise HTTPException(status_code=422, detail="invalid time range")
    max_seconds = 366 * 86400 if source_role >= 10 else 30 * 86400
    if end - start > max_seconds:
        raise HTTPException(status_code=422, detail="time range is too large")
    return start, end


def console_rate_limit(
    limiter: SlidingWindowRateLimiter,
    request: Request,
    user: dict[str, Any],
    attempts: int,
) -> None:
    retry_after = limiter.consume(
        f"{int(user['user_id'])}:{runtime.remote_addr(request)}",
        max(1, attempts),
    )
    if retry_after:
        raise HTTPException(status_code=429, detail=f"操作过于频繁，请 {retry_after} 秒后重试")


def raise_console_error(error: NewAPIConsoleError) -> None:
    raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.get("/api/console/capabilities")
def get_console_capabilities(user: AuthenticatedUser) -> dict[str, Any]:
    return console_capabilities(user, console_values())


@app.get("/api/console/overview")
def get_console_overview(request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "overview")
    client = console_client(values)
    now = int(time.time())
    try:
        system = client.status(session_cookie, user_id)
        self_info = client.self_info(session_cookie, user_id)
        models = client.models(session_cookie, user_id)
        keys = (
            client.list_tokens(session_cookie, user_id, page=1, page_size=5)
            if bool(values.get("console_keys_enabled", True))
            else {"page": 1, "page_size": 5, "total": 0, "items": []}
        )
        usage = client.log_stat(
            session_cookie,
            user_id,
            int(user.get("source_role") or 0),
            start_timestamp=now - 86400,
            end_timestamp=now,
        )
    except NewAPIConsoleError as error:
        raise_console_error(error)
    return {
        "generated_at": now,
        "system": system,
        "user": self_info,
        "models": {"total": len(models), "items": models[:12]},
        "keys": keys,
        "usage_24h": usage,
        "scope": "global" if int(user.get("source_role") or 0) >= 10 else "self",
    }


@app.get("/api/console/analytics")
def get_console_analytics(
    request: Request,
    user: AuthenticatedUser,
    start_timestamp: int | None = Query(None, ge=1),
    end_timestamp: int | None = Query(None, ge=1),
    username: str = Query("", max_length=128),
) -> dict[str, Any]:
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "analytics")
    source_role = int(user.get("source_role") or 0)
    if username and source_role < 10:
        raise HTTPException(status_code=403, detail="普通用户不能查询其他账号")
    start, end = console_time_range(
        start_timestamp,
        end_timestamp,
        source_role,
        int(values.get("console_default_days", 7)),
    )
    client = console_client(values)
    try:
        result = client.analytics(
            session_cookie,
            user_id,
            source_role,
            start,
            end,
            username=username.strip(),
        )
        result["quota_per_unit"] = client.status(session_cookie, user_id)["quota_per_unit"]
        return result
    except NewAPIConsoleError as error:
        raise_console_error(error)


@app.get("/api/console/keys")
def get_console_keys(
    request: Request,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", max_length=128),
) -> dict[str, Any]:
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    client = console_client(values)
    try:
        result = client.list_tokens(
            session_cookie,
            user_id,
            page=page,
            page_size=page_size,
            keyword=keyword.strip(),
        )
        result["quota_per_unit"] = client.status(session_cookie, user_id)["quota_per_unit"]
        return result
    except NewAPIConsoleError as error:
        raise_console_error(error)


@app.get("/api/console/keys/options")
def get_console_key_options(request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    client = console_client(values)
    try:
        status = client.status(session_cookie, user_id)
        return {
            "models": client.models(session_cookie, user_id),
            "groups": client.groups(session_cookie, user_id),
            "quota_per_unit": status["quota_per_unit"],
        }
    except NewAPIConsoleError as error:
        raise_console_error(error)


@app.post("/api/console/keys")
def create_console_key(
    payload: ConsoleTokenPayload,
    request: Request,
    user: AuthenticatedUser,
) -> dict[str, Any]:
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    console_rate_limit(
        console_write_limiter,
        request,
        user,
        int(values.get("console_write_attempts_per_minute", 30)),
    )
    body = payload.model_dump()
    try:
        console_client(values).create_token(session_cookie, user_id, body)
    except NewAPIConsoleError as error:
        raise_console_error(error)
    if runtime.settings is not None:
        runtime.settings.record_audit(
            str(user["username"]), "console.token.create", "token:new", {}, body,
            runtime.remote_addr(request),
        )
    return {"created": True}


@app.put("/api/console/keys/{token_id}")
def update_console_key(
    token_id: int,
    payload: ConsoleTokenPayload,
    request: Request,
    user: AuthenticatedUser,
) -> dict[str, Any]:
    if token_id <= 0:
        raise HTTPException(status_code=422, detail="invalid token id")
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    console_rate_limit(
        console_write_limiter,
        request,
        user,
        int(values.get("console_write_attempts_per_minute", 30)),
    )
    client = console_client(values)
    try:
        before = client.get_token(session_cookie, user_id, token_id)
        updated = client.update_token(session_cookie, user_id, {"id": token_id, **payload.model_dump()})
    except NewAPIConsoleError as error:
        raise_console_error(error)
    if runtime.settings is not None:
        runtime.settings.record_audit(
            str(user["username"]), "console.token.update", f"token:{token_id}", before, updated,
            runtime.remote_addr(request),
        )
    return {"item": updated}


@app.put("/api/console/keys/{token_id}/status")
def update_console_key_status(
    token_id: int,
    payload: ConsoleTokenStatusPayload,
    request: Request,
    user: AuthenticatedUser,
) -> dict[str, Any]:
    if token_id <= 0:
        raise HTTPException(status_code=422, detail="invalid token id")
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    console_rate_limit(
        console_write_limiter,
        request,
        user,
        int(values.get("console_write_attempts_per_minute", 30)),
    )
    client = console_client(values)
    try:
        before = client.get_token(session_cookie, user_id, token_id)
        updated = client.set_token_status(session_cookie, user_id, token_id, payload.status)
    except NewAPIConsoleError as error:
        raise_console_error(error)
    if runtime.settings is not None:
        runtime.settings.record_audit(
            str(user["username"]), "console.token.status", f"token:{token_id}",
            {"status": before.get("status")}, {"status": updated.get("status")},
            runtime.remote_addr(request),
        )
    return {"item": updated}


@app.delete("/api/console/keys/{token_id}")
def delete_console_key(token_id: int, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    if token_id <= 0:
        raise HTTPException(status_code=422, detail="invalid token id")
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    console_rate_limit(
        console_write_limiter,
        request,
        user,
        int(values.get("console_write_attempts_per_minute", 30)),
    )
    client = console_client(values)
    try:
        before = client.get_token(session_cookie, user_id, token_id)
        client.delete_token(session_cookie, user_id, token_id)
    except NewAPIConsoleError as error:
        raise_console_error(error)
    if runtime.settings is not None:
        runtime.settings.record_audit(
            str(user["username"]), "console.token.delete", f"token:{token_id}", before, {},
            runtime.remote_addr(request),
        )
    return {"deleted": True}


@app.post("/api/console/keys/batch-delete")
def batch_delete_console_keys(
    payload: ConsoleBatchPayload,
    request: Request,
    user: AuthenticatedUser,
) -> dict[str, Any]:
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    console_rate_limit(
        console_write_limiter,
        request,
        user,
        int(values.get("console_write_attempts_per_minute", 30)),
    )
    try:
        deleted = console_client(values).batch_delete_tokens(session_cookie, user_id, payload.ids)
    except NewAPIConsoleError as error:
        raise_console_error(error)
    if runtime.settings is not None:
        runtime.settings.record_audit(
            str(user["username"]), "console.token.batch-delete", "tokens", {"ids": payload.ids},
            {"deleted": deleted}, runtime.remote_addr(request),
        )
    return {"deleted": deleted}


@app.post("/api/console/keys/{token_id}/reveal")
def reveal_console_key(token_id: int, request: Request, user: AuthenticatedUser) -> dict[str, Any]:
    if token_id <= 0:
        raise HTTPException(status_code=422, detail="invalid token id")
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "keys")
    console_rate_limit(
        console_reveal_limiter,
        request,
        user,
        int(values.get("console_reveal_attempts_per_minute", 6)),
    )
    try:
        key = console_client(values).reveal_token(session_cookie, user_id, token_id)
    except NewAPIConsoleError as error:
        raise_console_error(error)
    if not key:
        raise HTTPException(status_code=502, detail="New API did not return the key")
    if runtime.settings is not None:
        runtime.settings.record_audit(
            str(user["username"]), "console.token.reveal", f"token:{token_id}", {},
            {"revealed": True}, runtime.remote_addr(request),
        )
    return {"key": key}


@app.get("/api/console/logs")
def get_console_logs(
    request: Request,
    user: AuthenticatedUser,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=100),
    log_type: int = Query(0, ge=0, le=7),
    start_timestamp: int | None = Query(None, ge=1),
    end_timestamp: int | None = Query(None, ge=1),
    username: str = Query("", max_length=128),
    token_name: str = Query("", max_length=128),
    model_name: str = Query("", max_length=256),
    channel: int = Query(0, ge=0),
    group: str = Query("", max_length=128),
    request_id: str = Query("", max_length=128),
    upstream_request_id: str = Query("", max_length=256),
) -> dict[str, Any]:
    values = console_values()
    session_cookie, user_id = console_identity(request, user, values, "logs")
    source_role = int(user.get("source_role") or 0)
    if source_role < 10 and (username or channel):
        raise HTTPException(status_code=403, detail="普通用户不能查询其他账号或渠道")
    start, end = console_time_range(
        start_timestamp,
        end_timestamp,
        source_role,
        int(values.get("console_default_days", 7)),
    )
    filters = {
        "type": log_type,
        "start_timestamp": start,
        "end_timestamp": end,
        "username": username.strip(),
        "token_name": token_name.strip(),
        "model_name": model_name.strip(),
        "channel": channel,
        "group": group.strip(),
        "request_id": request_id.strip(),
        "upstream_request_id": upstream_request_id.strip(),
    }
    client = console_client(values)
    try:
        result = client.list_logs(
            session_cookie, user_id, source_role, page=page, page_size=page_size, **filters,
        )
        stat_filters_complete = not (request_id or upstream_request_id)
        result["stat"] = (
            client.log_stat(session_cookie, user_id, source_role, **filters)
            if stat_filters_complete
            else None
        )
        result["stat_filters_complete"] = stat_filters_complete
        result["quota_per_unit"] = client.status(session_cookie, user_id)["quota_per_unit"]
        result["scope"] = "global" if source_role >= 10 else "self"
        return result
    except NewAPIConsoleError as error:
        raise_console_error(error)


@app.get("/api/dashboard/summary")
def dashboard_summary(user: AuthenticatedUser) -> dict[str, Any]:
    if runtime.settings is None:
        return repository().summary()
    values = runtime.settings.runtime_values()
    audience = "viewer" if user["role"] == "viewer" else "admin"
    visible = runtime.settings.decorate_channels(
        repository().channels(),
        include_hidden=False,
        audience=audience,
    )
    visible_channel_ids = {int(item["channel_id"]) for item in visible}
    result = repository().summary(channel_ids=visible_channel_ids)
    provider_visible = bool(
        values.get(
            "openai_status_viewer_visible" if audience == "viewer" else "openai_status_admin_visible",
            True,
        )
    )
    if bool(values.get("openai_status_enabled", True)) and provider_visible:
        provider = repository().provider_status(
            "openai",
            stale_after_seconds=max(
                90,
                int(values.get("openai_status_interval_seconds", 60)) * 3,
            ),
        )
        provider["include_in_overall"] = bool(values.get("openai_status_include_in_overall", False))
        monitored_ids = list(values.get("openai_status_component_ids") or [])
        provider["monitored_component_ids"] = monitored_ids
        provider["degraded_component_count"] = sum(
            str(component.get("status") or "unknown") != "operational"
            for component in provider["components"]
            if (
                str(component.get("id") or "") in monitored_ids
                if monitored_ids
                else str(component.get("name") or "") in DEFAULT_OPENAI_COMPONENT_NAMES
            )
        )
        result["provider_status"] = provider
    return result


@app.get("/api/provider-status/openai")
def openai_provider_status(user: AuthenticatedUser) -> dict[str, Any]:
    if runtime.settings is None:
        return repository().provider_status("openai")
    values = runtime.settings.runtime_values()
    audience = "viewer" if user["role"] == "viewer" else "admin"
    visible = bool(
        values.get(
            "openai_status_viewer_visible" if audience == "viewer" else "openai_status_admin_visible",
            True,
        )
    )
    if not visible:
        raise HTTPException(status_code=404, detail="OpenAI Status is not visible for this role")
    result = repository().provider_status(
        "openai",
        stale_after_seconds=max(90, int(values.get("openai_status_interval_seconds", 60)) * 3),
    )
    result["enabled"] = bool(values.get("openai_status_enabled", True))
    result["include_in_overall"] = bool(values.get("openai_status_include_in_overall", False))
    monitored_ids = list(values.get("openai_status_component_ids") or [])
    result["monitored_component_ids"] = monitored_ids
    result["degraded_component_count"] = sum(
        str(component.get("status") or "unknown") != "operational"
        for component in result["components"]
        if (
            str(component.get("id") or "") in monitored_ids
            if monitored_ids
            else str(component.get("name") or "") in DEFAULT_OPENAI_COMPONENT_NAMES
        )
    )
    return result


@app.post("/api/provider-status/openai/test")
def test_openai_provider_status(_: AdminUser) -> dict[str, Any]:
    values = runtime.settings.runtime_values() if runtime.settings is not None else {}
    timeout_seconds = max(3, min(30, int(values.get("openai_status_timeout_seconds", 10))))
    snapshot = OpenAIStatusClient().fetch(timeout_seconds=timeout_seconds)
    active_incidents = [
        item for item in snapshot["incidents"]
        if str(item.get("status") or "") != "resolved"
    ]
    return {
        **snapshot,
        "success": True,
        "available": True,
        "stale": False,
        "age_seconds": 0,
        "component_count": len(snapshot["components"]),
        "incidents": active_incidents,
        "active_incident_count": len(active_incidents),
        "degraded_component_count": sum(
            str(item.get("status") or "unknown") != "operational"
            for item in snapshot["components"]
        ),
    }


@app.get("/api/channels")
def channels(user: AuthenticatedUser) -> dict[str, Any]:
    items = repository().channels()
    if runtime.settings is not None:
        audience = "viewer" if user["role"] == "viewer" else "admin"
        items = runtime.settings.decorate_channels(items, audience=audience)
    return {"items": items}


@app.get("/api/channels/{channel_id}")
def channel(channel_id: int, user: AuthenticatedUser) -> dict[str, Any]:
    item = repository().channel(channel_id)
    if item is None:
        raise HTTPException(status_code=404, detail="channel not found")
    if runtime.settings is not None:
        audience = "viewer" if user["role"] == "viewer" else "admin"
        visible = runtime.settings.decorate_channels([item], include_hidden=False, audience=audience)
        if not visible:
            raise HTTPException(status_code=404, detail="channel not found")
        item = visible[0]
    return item


@app.get("/api/logs")
def logs(
    _: OperatorUser,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    channel_id: int | None = Query(None, ge=1),
    model_name: str = Query("", max_length=256),
    username: str = Query("", max_length=128),
    slow_only: bool = False,
) -> dict[str, Any]:
    return repository().logs(
        limit=limit,
        offset=offset,
        channel_id=channel_id,
        model_name=model_name,
        username=username,
        slow_only=slow_only,
    )


@app.get("/api/resources")
def resources(
    _: OperatorUser,
    hours: int = Query(24, ge=1, le=168),
) -> dict[str, Any]:
    return repository().resources(hours=hours)


@app.get("/api/incidents")
def incidents(
    _: OperatorUser,
    status: str = Query("all", pattern="^(all|open|resolved)$"),
    severity: str = Query("all", pattern="^(all|info|warning|critical)$"),
    category: str = Query(
        "all",
        pattern="^(all|channel|latency|resource|container|service|collector|provider|other)$",
    ),
    query: str = Query("", alias="q", max_length=200),
    window_hours: int = Query(0, ge=0, le=8760),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return repository().incidents(
        status=status,
        severity=severity,
        category=category,
        query=query,
        window_hours=window_hours,
        limit=limit,
        offset=offset,
    )


@app.get("/api/settings")
def get_settings(_: AdminUser) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    return {"values": runtime.settings.public_values(), "version": runtime.settings.version()}


@app.put("/api/settings")
def update_settings(payload: SettingsUpdatePayload, request: Request, user: AdminUser) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    try:
        candidate = runtime.settings.runtime_values()
        openai_status_was_enabled = bool(candidate.get("openai_status_enabled", True))
        updates = payload.model_dump(exclude_unset=True)
        candidate.update({
            key: value for key, value in updates.items()
            if not (key in SECRET_KEYS and (value is None or str(value) in {"", "********"}))
        })
        for key in (
            "dashboard_refresh_seconds", "channel_sync_interval_seconds", "channel_interval_seconds",
            "log_interval_seconds", "resource_interval_seconds", "report_interval_seconds",
            "log_overlap_seconds", "log_initial_lookback_seconds", "latency_reminder_seconds",
            "resource_sustain_seconds", "retention_days", "smtp_port",
            "openai_status_interval_seconds", "openai_status_timeout_seconds",
            "openai_status_failure_threshold", "openai_status_recovery_threshold",
        ):
            if int(candidate[key]) <= 0:
                raise ValueError(f"{key} must be greater than zero")
        for key in (
            "slow_request_seconds", "latency_hard_limit_seconds", "channel_slow_seconds",
            "system_cpu_threshold", "system_memory_threshold", "system_disk_threshold",
            "container_cpu_threshold", "container_memory_threshold",
        ):
            if float(candidate[key]) <= 0:
                raise ValueError(f"{key} must be greater than zero")
        candidate["state_db"] = runtime.state_db
        candidate["real_probe_rules"] = runtime.settings.real_probe_rules()
        candidate["channel_settings"] = runtime.settings.channel_settings()
        Config.from_values(candidate).validate()
        values = runtime.settings.update_settings(updates, user["username"], runtime.remote_addr(request))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    runtime.refresh_repository()
    if openai_status_was_enabled and not bool(values.get("openai_status_enabled", True)):
        state_store = StateStore(runtime.state_db)
        state_store.resolve_open_incidents(
            "provider:openai:",
            "OpenAI 官方状态监控已关闭，该事件因监控范围变更结束。",
        )
        state_store.set_json("openai_status_state", {})
        state_store.connection.close()
    return {"values": values, "version": runtime.settings.version(), "reloading": True}


@app.post("/api/notifications/test")
def test_notification(payload: NotificationTestPayload, user: AdminUser) -> dict[str, Any]:
    try:
        dispatcher = NotificationDispatcher(runtime.monitor_config(), test_channel=payload.channel)
        result = dispatcher.send(
            "测试通知",
            f"由 {user['username']} 从监控平台发送。\n时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            channel=payload.channel,
        )
    except (TypeError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"success": True, **result}


@app.get("/api/channel-settings")
def get_channel_settings(_: OperatorUser) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    return {"items": runtime.settings.decorate_channels(repository().channels(), include_hidden=True)}


@app.put("/api/channel-settings/visibility")
def update_channel_visibility(
    payload: ChannelVisibilityPayload,
    request: Request,
    user: AdminUser,
) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    updates = {
        item.channel_id: {
            "overview_admin_visible": item.overview_admin_visible,
            "overview_viewer_visible": item.overview_viewer_visible,
        }
        for item in payload.items
    }
    if len(updates) != len(payload.items):
        raise HTTPException(status_code=400, detail="duplicate channel id")
    try:
        configs = runtime.settings.update_channel_visibility(
            updates,
            user["username"],
            runtime.remote_addr(request),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"updated": len(configs), "version": runtime.settings.version()}


@app.put("/api/channel-settings/{channel_id}")
def update_channel_settings(
    channel_id: int,
    payload: ChannelSettingsPayload,
    request: Request,
    user: OperatorUser,
) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    updates = payload.model_dump(exclude_unset=True)
    if user["role"] != "admin" and {
        "display_enabled", "overview_admin_visible", "overview_viewer_visible"
    }.intersection(updates):
        raise HTTPException(status_code=403, detail="administrator permission required for overview visibility")
    try:
        config = runtime.settings.update_channel(
            channel_id,
            updates,
            user["username"],
            runtime.remote_addr(request),
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"channel_id": channel_id, "config": config, "version": runtime.settings.version()}


@app.get("/api/access/users")
def access_users(_: AdminUser) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    return {"items": runtime.settings.users()}


@app.put("/api/access/users/{username}")
def update_access_user(
    username: str,
    payload: AccessRolePayload,
    request: Request,
    user: AdminUser,
) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    role = payload.role
    try:
        runtime.settings.set_user_role(username, role, user["username"], runtime.remote_addr(request))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"updated": True}


@app.get("/api/config-audit")
def config_audit(_: AdminUser, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    return {"items": runtime.settings.audit(limit)}


@app.get("/{full_path:path}", include_in_schema=False)
def frontend(full_path: str):
    index_path = runtime.static_dir / "index.html"
    requested = (runtime.static_dir / full_path).resolve()
    if full_path and runtime.static_dir in requested.parents and requested.is_file():
        return FileResponse(requested)
    if not index_path.is_file():
        return JSONResponse(status_code=503, content={"detail": "dashboard frontend is not built"})
    return FileResponse(index_path, headers={"Cache-Control": "no-store"})
