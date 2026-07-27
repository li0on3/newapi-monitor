from __future__ import annotations

import logging
from typing import Any, Iterable

from monitoring_core.alerting import AlertEvent


LOGGER = logging.getLogger("newapi-monitor.delivery")


def delivery_backoff_seconds(attempt: int) -> int:
    return min(3600, 30 * (2 ** max(0, attempt - 1)))


class AlertPublisher:
    def __init__(
        self,
        store: Any,
        destinations: Iterable[str],
        notifiable_kinds: Iterable[str] | None = None,
    ):
        self.store = store
        self.destinations = tuple(dict.fromkeys(str(item) for item in destinations if str(item)))
        self.notifiable_kinds = (
            frozenset(str(item) for item in notifiable_kinds if str(item))
            if notifiable_kinds is not None else None
        )

    def publish(self, events: Iterable[AlertEvent], now: int | None = None) -> list[int]:
        items = list(events)
        if not items:
            return []
        incident_ids = self.store.record_alert_events(items, now=now)
        notifiable = [
            event for event in items
            if event.notify
            and (self.notifiable_kinds is None or event.kind in self.notifiable_kinds)
        ]
        if not notifiable or not self.destinations:
            return []
        subject = "；".join(event.title for event in notifiable)
        body = "\n\n".join(f"[{event.title}]\n{event.body}" for event in notifiable)
        priorities = {"info": 0, "warning": 1, "critical": 2}
        priority = max(
            (str(event.severity) for event in notifiable),
            key=lambda value: priorities.get(value, 0),
        )
        return self.store.enqueue_notifications(
            subject,
            body,
            self.destinations,
            incident_ids=incident_ids,
            priority=priority,
            now=now,
        )

    def publish_message(
        self,
        subject: str,
        body: str,
        now: int | None = None,
    ) -> list[int]:
        if not self.destinations:
            return []
        return self.store.enqueue_notifications(
            subject,
            body,
            self.destinations,
            incident_ids=(),
            priority="info",
            now=now,
        )


class NotificationOutboxWorker:
    def __init__(
        self,
        store: Any,
        dispatcher: Any,
        max_attempts: int = 8,
        quiet_until: Any | None = None,
    ):
        self.store = store
        self.dispatcher = dispatcher
        self.max_attempts = max(1, int(max_attempts))
        self.quiet_until = quiet_until

    def run_once(self, now: int | None = None, limit: int = 20) -> dict[str, int]:
        delivered = 0
        failed = 0
        deferred = 0
        for item in self.store.claim_due_notifications(now=now, limit=limit):
            notification_id = int(item["id"])
            destination = str(item["destination"])
            attempt = int(item["attempts"])
            if self.quiet_until is not None:
                defer_until = self.quiet_until(str(item.get("priority") or "info"), now)
                if defer_until is not None:
                    deferred += 1
                    self.store.defer_notification(notification_id, int(defer_until), now=now)
                    continue
            try:
                self.dispatcher.send(
                    str(item["subject"]),
                    str(item["body"]),
                    channel=destination,
                )
            except Exception as error:
                failed += 1
                dead = attempt >= self.max_attempts
                self.store.mark_notification_failed(
                    notification_id,
                    str(error),
                    next_attempt_seconds=delivery_backoff_seconds(attempt),
                    dead=dead,
                    now=now,
                )
                if dead:
                    self.store.record_alert_events(
                        [
                            AlertEvent(
                                "notification_delivery_failed",
                                f"告警通道持续发送失败：{destination}",
                                f"连续尝试 {attempt} 次仍失败。最近错误：{str(error)[:500]}",
                                key=f"notification:delivery:{destination}",
                                severity="critical",
                                notify=False,
                            )
                        ],
                        now=now,
                    )
                LOGGER.warning(
                    "notification delivery failed: id=%s destination=%s attempt=%s dead=%s error=%s",
                    notification_id,
                    destination,
                    attempt,
                    dead,
                    error,
                )
                continue

            delivered += 1
            self.store.mark_notification_delivered(notification_id, now=now)
            if self.store.has_open_incident(f"notification:delivery:{destination}"):
                self.store.record_alert_events(
                    [
                        AlertEvent(
                            "notification_delivery_recovered",
                            f"告警通道恢复：{destination}",
                            "待发送告警已经成功投递。",
                            key=f"notification:delivery:{destination}",
                            severity="info",
                            recovery=True,
                            notify=False,
                        )
                    ],
                    now=now,
                )
        result = {"delivered": delivered, "failed": failed}
        if deferred:
            result["deferred"] = deferred
        return result
