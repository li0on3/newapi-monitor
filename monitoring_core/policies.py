from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def channel_maintenance_state(
    config: dict[str, Any] | None,
    now: int | None = None,
) -> tuple[bool, str]:
    values = config or {}
    if bool(values.get("maintenance_mode")):
        return True, str(values.get("maintenance_window_reason") or "手动维护模式")
    if not bool(values.get("maintenance_window_enabled")):
        return False, ""
    timestamp = int(datetime.now().timestamp()) if now is None else int(now)
    start = int(values.get("maintenance_window_start") or 0)
    end = int(values.get("maintenance_window_end") or 0)
    if start <= 0 or end <= start or timestamp < start or timestamp >= end:
        return False, ""
    return True, str(values.get("maintenance_window_reason") or "计划维护窗口")


def quiet_hours_defer_until(
    settings: dict[str, Any] | None,
    priority: str,
    now: int | None = None,
) -> int | None:
    values = settings or {}
    if not bool(values.get("notification_quiet_hours_enabled")):
        return None
    if priority == "critical" and bool(values.get("notification_quiet_hours_allow_critical", True)):
        return None
    try:
        timezone = ZoneInfo(str(values.get("notification_quiet_hours_timezone") or "Asia/Shanghai"))
    except ZoneInfoNotFoundError:
        return None
    try:
        start_hour, start_minute = _parse_clock(str(values.get("notification_quiet_hours_start") or "22:00"))
        end_hour, end_minute = _parse_clock(str(values.get("notification_quiet_hours_end") or "08:00"))
    except ValueError:
        return None
    if (start_hour, start_minute) == (end_hour, end_minute):
        return None

    current = datetime.fromtimestamp(
        int(datetime.now().timestamp()) if now is None else int(now),
        timezone,
    )
    start_today = current.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end_today = current.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if start_today < end_today:
        if start_today <= current < end_today:
            return int(end_today.timestamp())
        return None
    if current >= start_today:
        return int((end_today + timedelta(days=1)).timestamp())
    if current < end_today:
        return int(end_today.timestamp())
    return None


def _parse_clock(value: str) -> tuple[int, int]:
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise ValueError("clock must use HH:MM")
    hour, minute = (int(part) for part in parts)
    if hour not in range(24) or minute not in range(60):
        raise ValueError("clock is out of range")
    return hour, minute
