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
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dashboard_auth import AuthStore
from dashboard_data import DashboardRepository
from dashboard_settings import SettingsStore
from dashboard_sso import NewAPISessionVerifier
from newapi_monitor import Config, MonitorApp, StateStore, env_bool, env_int


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("newapi-monitor-dashboard")


class LoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class SettingsUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_api_base_url: str | None = Field(None, min_length=8, max_length=2048)
    new_api_access_token: str | None = Field(None, max_length=4096)
    new_api_user_id: int | None = Field(None, ge=1)
    relay_api_token: str | None = Field(None, max_length=4096)
    dashboard_refresh_seconds: int | None = Field(None, ge=2, le=3600)
    channel_sync_interval_seconds: int | None = Field(None, ge=2, le=3600)
    channel_interval_seconds: int | None = Field(None, ge=5, le=86400)
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
    send_startup_email: bool | None = None
    subject_prefix: str | None = Field(None, max_length=256)

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


class ChannelSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_enabled: bool | None = None
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
        if self.monitor_enabled:
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
            "send_startup_email": env_bool("SEND_STARTUP_EMAIL", True),
            "subject_prefix": os.getenv("SUBJECT_PREFIX", "[New API监控]"),
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
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded[:128]
        return request.client.host[:128] if request.client else "unknown"


runtime = Runtime()
login_limiter = LoginRateLimiter()


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


OperatorUser = Annotated[dict[str, Any], Depends(require_operator)]
AdminUser = Annotated[dict[str, Any], Depends(require_admin)]


def repository() -> DashboardRepository:
    if runtime.repository is None:
        raise HTTPException(status_code=503, detail="dashboard is starting")
    return runtime.repository


@app.get("/api/health")
def health() -> JSONResponse:
    details = system_health_snapshot()
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
    monitor_alive = not runtime.monitor_enabled or bool(
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
    collectors_ok = all(item.get("status") != "stale" for item in collectors.values())
    healthy = database_ok and monitor_ok and collectors_ok
    return {
        "status": "ok" if healthy else "degraded",
        "database": "ok" if database_ok else "degraded",
        "database_error": database_error,
        "monitor_worker": "disabled" if not runtime.monitor_enabled else ("running" if monitor_ok else "degraded"),
        "monitor_error": runtime.monitor_error,
        "collectors": collectors,
        "timestamp": int(time.time()),
    }


@app.get("/api/system/status")
def system_status(_: AuthenticatedUser) -> dict[str, Any]:
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
    return {"authenticated": True, **user, "dashboard_refresh_seconds": refresh_seconds}


@app.get("/api/dashboard/summary")
def dashboard_summary(_: AuthenticatedUser) -> dict[str, Any]:
    summary = repository().summary()
    if runtime.settings is None:
        return summary
    visible = runtime.settings.decorate_channels(repository().channels(), include_hidden=False)
    now = time.time()
    healthy = failed = unknown = 0
    for item in visible:
        latest = item.get("latest")
        if not latest or now - int(latest.get("observed_at") or 0) > 900:
            unknown += 1
        elif latest.get("success"):
            healthy += 1
        else:
            failed += 1
    summary["channels"].update(total=len(visible), healthy=healthy, failed=failed, unknown=unknown)
    return summary


@app.get("/api/channels")
def channels(_: AuthenticatedUser) -> dict[str, Any]:
    items = repository().channels()
    if runtime.settings is not None:
        items = runtime.settings.decorate_channels(items)
    return {"items": items}


@app.get("/api/channels/{channel_id}")
def channel(channel_id: int, _: AuthenticatedUser) -> dict[str, Any]:
    item = repository().channel(channel_id)
    if item is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return item


@app.get("/api/logs")
def logs(
    _: AuthenticatedUser,
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
    _: AuthenticatedUser,
    hours: int = Query(24, ge=1, le=168),
) -> dict[str, Any]:
    return repository().resources(hours=hours)


@app.get("/api/incidents")
def incidents(
    _: AuthenticatedUser,
    status: str = Query("all", pattern="^(all|open|resolved)$"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    return {"items": repository().incidents(status=status, limit=limit)}


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
        updates = payload.model_dump(exclude_unset=True)
        candidate.update({
            key: value for key, value in updates.items()
            if not (key in {"new_api_access_token", "relay_api_token", "smtp_password"} and (value is None or str(value) in {"", "********"}))
        })
        for key in (
            "dashboard_refresh_seconds", "channel_sync_interval_seconds", "channel_interval_seconds",
            "log_interval_seconds", "resource_interval_seconds", "report_interval_seconds",
            "log_overlap_seconds", "log_initial_lookback_seconds", "latency_reminder_seconds",
            "resource_sustain_seconds", "retention_days", "smtp_port",
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
    return {"values": values, "version": runtime.settings.version(), "reloading": True}


@app.get("/api/channel-settings")
def get_channel_settings(_: OperatorUser) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    return {"items": runtime.settings.decorate_channels(repository().channels(), include_hidden=True)}


@app.put("/api/channel-settings/{channel_id}")
def update_channel_settings(
    channel_id: int,
    payload: ChannelSettingsPayload,
    request: Request,
    user: OperatorUser,
) -> dict[str, Any]:
    if runtime.settings is None:
        raise HTTPException(status_code=503, detail="settings are unavailable")
    try:
        config = runtime.settings.update_channel(
            channel_id,
            payload.model_dump(exclude_unset=True),
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
