from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
STATE = tempfile.TemporaryDirectory(prefix="newapi-monitor-e2e-")
DATABASE = Path(STATE.name) / "monitor.db"

os.environ.update({
    "STATE_DB": str(DATABASE),
    "DASHBOARD_STATIC_DIR": str(ROOT / "web" / "dist"),
    "DASHBOARD_ADMIN_USERNAME": "admin",
    "DASHBOARD_ADMIN_PASSWORD": "E2E-Admin-Password-2026!",
    "DASHBOARD_COOKIE_SECURE": "false",
    "DASHBOARD_ALLOWED_HOSTS": "127.0.0.1,localhost",
    "MONITOR_WORKER_ENABLED": "false",
    "MONITOR_SECRET_KEY": "fullstack-e2e-secret-key",
    "NEW_API_BASE_URL": "http://127.0.0.1:9",
    "NEW_API_ACCESS_TOKEN": "synthetic-management-token",
    "NEW_API_USER_ID": "1",
    "RELAY_API_TOKEN": "synthetic-relay-token",
    "OPENAI_STATUS_ENABLED": "false",
})

from monitoring_core.alerting import AlertEvent, ChannelObservation  # noqa: E402
from monitoring_core.state_store import StateStore  # noqa: E402


now = int(time.time())
store = StateStore(str(DATABASE))
store.upsert_channels([
    {
        "id": 1,
        "name": "Synthetic OpenAI",
        "type": 1,
        "status": 1,
        "models": "gpt-synthetic",
        "group": "default",
        "base_url": "https://redacted.invalid",
    }
], now=now)
store.insert_channel_observations([
    ChannelObservation(1, "Synthetic OpenAI", True, 1.2, "验证通过", "real", 320),
], observed_at=now)
store.insert_resource_sample(
    {
        "system_cpu": 12,
        "system_memory": 38,
        "system_disk": 42,
        "system_available_mb": 4096,
        "system_swap": 0,
    },
    {"containers": {}},
    created_at=now,
)
incident_id = store.record_alert_events([
    AlertEvent(
        "channel_failed",
        "Synthetic upstream timeout",
        "The synthetic upstream returned a timeout during the full-stack test.",
        key="channel:1",
        severity="warning",
    )
], now=now - 300)[0]

pending_id = store.enqueue_notifications(
    "等待首次投递",
    "这是一条真实后端 E2E 产生的合成待投递告警。",
    ["email"],
    incident_ids=[incident_id],
    priority="warning",
    now=now - 120,
)[0]
dead_id = store.enqueue_notifications(
    "Webhook 持续失败",
    "这是一条可恢复的合成死信。",
    ["wecom_webhook"],
    incident_ids=[incident_id],
    priority="critical",
    now=now - 240,
)[0]
sending_id = store.enqueue_notifications(
    "正在发送的告警",
    "该记录用于验证发送中筛选。",
    ["feishu_webhook"],
    now=now - 30,
)[0]
delivered_id = store.enqueue_notifications(
    "渠道恢复通知",
    "该记录已送达。",
    ["email"],
    now=now - 600,
)[0]
cancelled_id = store.enqueue_notifications(
    "已取消旧告警",
    "该记录已取消。",
    ["wecom_app"],
    now=now - 700,
)[0]
store.connection.execute(
    "UPDATE notification_outbox SET status='dead', attempts=8, last_error='synthetic webhook timeout', updated_at=? WHERE id=?",
    (now - 60, dead_id),
)
store.connection.execute(
    "UPDATE notification_outbox SET status='sending', attempts=1, lease_until=?, updated_at=? WHERE id=?",
    (now + 600, now - 10, sending_id),
)
store.connection.execute(
    "UPDATE notification_outbox SET status='delivered', delivered_at=?, updated_at=? WHERE id=?",
    (now - 500, now - 500, delivered_id),
)
store.connection.execute(
    "UPDATE notification_outbox SET status='cancelled', last_error='cancelled by administrator', updated_at=? WHERE id=?",
    (now - 650, cancelled_id),
)
store.connection.execute(
    "UPDATE notification_outbox SET next_attempt_at=? WHERE id=?",
    (now + 300, pending_id),
)
store.connection.commit()
store.connection.close()

from dashboard_app import app  # noqa: E402


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18083, log_level="warning")
