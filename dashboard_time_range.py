from __future__ import annotations

import time


def resolve_time_range(
    start_timestamp: int | None,
    end_timestamp: int | None,
    *,
    now: int | None = None,
    default_seconds: int = 7 * 86400,
    all_time: bool = False,
) -> tuple[int, int, bool]:
    """Normalize an inclusive query range without imposing an arbitrary history cap."""
    current_time = int(time.time()) if now is None else int(now)
    if all_time:
        return 0, int(end_timestamp or current_time), True
    end = int(end_timestamp or current_time)
    start = int(start_timestamp if start_timestamp is not None else end - max(1, int(default_seconds)))
    if start < 0 or end <= 0 or start > end:
        raise ValueError("invalid time range")
    return start, end, False
