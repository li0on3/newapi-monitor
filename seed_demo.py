from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

from newapi_monitor import AlertEvent, ChannelObservation, StateStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Create deterministic demo data for the monitor dashboard")
    parser.add_argument("--database", default="state/demo.db")
    args = parser.parse_args()
    database_path = Path(args.database)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(str(database_path) + suffix)
        if candidate.is_file():
            candidate.unlink()

    now = int(time.time())
    store = StateStore(str(database_path))
    channels = [
        {"id": 1, "name": "OpenAI-Plus", "type": 1, "status": 1, "models": "gpt-5.4,gpt-5.5", "group": "default", "base_url": "https://api.example/openai/plus"},
        {"id": 2, "name": "OpenAI-Pro", "type": 1, "status": 1, "models": "gpt-pro", "group": "default", "base_url": "https://api.example/openai/pro"},
        {"id": 3, "name": "t_0.13", "type": 1, "status": 1, "models": "gpt-5.6-sol", "group": "default", "base_url": "https://api.example/v1/responses"},
        {"id": 4, "name": "充值通道", "type": 1, "status": 1, "models": "gpt-5.4", "group": "default", "base_url": "https://api.example/recharge"},
        {"id": 5, "name": "Claude-Sonnet", "type": 14, "status": 1, "models": "claude-sonnet-4-6", "group": "vip", "base_url": "https://api.example/claude"},
        {"id": 6, "name": "备用渠道", "type": 1, "status": 2, "models": "gpt-4.1", "group": "backup", "base_url": "https://api.example/backup"},
    ]
    store.upsert_channels(channels, now=now)
    for index in range(60):
        timestamp = now - (59 - index) * 300
        observations = []
        for channel in channels:
            channel_id = int(channel["id"])
            if channel_id == 6:
                continue
            success = True
            elapsed = 1.2 + channel_id * 0.22 + abs(math.sin(index / 6 + channel_id)) * 1.4
            message = ""
            source = "real" if channel_id == 3 else "builtin"
            frt = elapsed * 450 if channel_id == 3 else None
            if channel_id == 2 and index in {8, 9, 24, 41, 55}:
                elapsed = 72
                success = False
                message = "probe latency exceeded 60s"
            if channel_id == 4 and index >= 52:
                elapsed = 65
                success = False
                message = "upstream timeout"
            if channel_id == 5 and index in {13, 31, 48}:
                elapsed = 32
            observations.append(
                ChannelObservation(
                    channel_id,
                    str(channel["name"]),
                    success,
                    elapsed,
                    message,
                    source,
                    frt,
                )
            )
        store.insert_channel_observations(observations, observed_at=timestamp)

    logs = []
    for index in range(85):
        channel_id = 1 + index % 5
        duration = 2 + abs(math.sin(index / 5)) * 9
        frt = 400 + abs(math.cos(index / 4)) * 1800
        if index in {3, 8, 19, 27, 43, 44, 58}:
            duration = 62 + index % 8
        if index in {12, 38, 61}:
            frt = 65000
        channel = channels[channel_id - 1]
        logs.append(
            {
                "request_id": f"demo-{index:03d}",
                "created_at": now - index * 540,
                "channel": channel_id,
                "channel_name": channel["name"],
                "model_name": str(channel["models"]).split(",")[0],
                "use_time": duration,
                "other": {"frt": frt},
                "username": ["alice", "bob", "carol"][index % 3],
                "token_name": "production",
                "token_id": 10 + index % 4,
                "is_stream": index % 2 == 0,
                "group": channel["group"],
            }
        )
    store.ingest_logs(logs)

    for index in range(180):
        timestamp = now - (179 - index) * 120
        cpu = 20 + abs(math.sin(index / 12)) * 30
        memory = 46 + abs(math.sin(index / 25)) * 11
        containers = {
            "new-api": {"status": "running", "restarts": 1, "cpu": cpu * 0.42, "memory": 18.2, "memory_mb": 372, "oom_killed": False},
            "postgres": {"status": "running", "restarts": 0, "cpu": 2.3, "memory": 7.4, "memory_mb": 152, "oom_killed": False},
            "redis": {"status": "running", "restarts": 0, "cpu": 0.4, "memory": 2.1, "memory_mb": 43, "oom_killed": False},
        }
        store.insert_resource_sample(
            {
                "system_cpu": cpu,
                "system_memory": memory,
                "system_disk": 58.4,
                "system_available_mb": 940,
                "system_swap": 0,
            },
            {"containers": containers},
            created_at=timestamp,
        )
    store.record_alert_events(
        [AlertEvent("channel_failed", "渠道异常：充值通道", "连续真实探测失败，upstream timeout", key="channel:4", severity="critical")],
        now=now - 2100,
    )
    store.record_alert_events(
        [AlertEvent("latency_high", "耗时异常：OpenAI-Pro/gpt-pro", "近5次有3次超过 60s", key="latency:2:gpt-pro", severity="warning")],
        now=now - 800,
    )
    store.connection.close()
    print(f"seeded dashboard database: {database_path}")


if __name__ == "__main__":
    main()
