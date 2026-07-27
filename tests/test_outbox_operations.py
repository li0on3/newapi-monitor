import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import newapi_monitor
from dashboard_settings import SettingsStore
from monitoring_core.policies import channel_maintenance_state, quiet_hours_defer_until


class NotificationOutboxOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = newapi_monitor.StateStore(str(Path(self.temp_dir.name) / "monitor.db"))

    def tearDown(self):
        self.store.connection.close()
        self.temp_dir.cleanup()

    def test_list_filter_retry_cancel_and_dead_letter_recovery(self):
        ids = self.store.enqueue_notifications(
            "渠道异常",
            "upstream timeout",
            ["email", "wecom_webhook", "feishu_webhook"],
            now=100,
        )
        self.store.connection.execute(
            "UPDATE notification_outbox SET status = 'dead', attempts = 8, last_error = 'smtp offline', updated_at = 110 WHERE id = ?",
            (ids[0],),
        )
        self.store.connection.execute(
            "UPDATE notification_outbox SET status = 'delivered', delivered_at = 120, updated_at = 120 WHERE id = ?",
            (ids[1],),
        )
        self.store.connection.commit()

        payload = self.store.notifications(status="dead", query="smtp", now=130)

        self.assertEqual(1, payload["total"])
        self.assertEqual("smtp offline", payload["items"][0]["last_error"])
        self.assertEqual(
            {"pending": 1, "sending": 0, "delivered": 1, "dead": 1, "cancelled": 0},
            payload["counts"],
        )

        self.store.retry_notifications([ids[0]], now=140)
        recovered = self.store.notification(ids[0])
        self.assertEqual("pending", recovered["status"])
        self.assertEqual(0, recovered["attempts"])
        self.assertEqual("", recovered["last_error"])
        self.assertEqual(140, recovered["next_attempt_at"])

        self.store.cancel_notifications([ids[2]], now=150)
        self.assertEqual("cancelled", self.store.notification(ids[2])["status"])

    def test_sending_and_delivered_notifications_cannot_be_manually_mutated(self):
        pending_id = self.store.enqueue_notifications("subject", "body", ["email"], now=100)[0]
        claimed = self.store.claim_due_notifications(now=100)
        self.assertEqual(pending_id, claimed[0]["id"])

        with self.assertRaisesRegex(ValueError, "sending"):
            self.store.cancel_notifications([pending_id], now=110)

        self.store.mark_notification_delivered(pending_id, now=120)
        with self.assertRaisesRegex(ValueError, "delivered"):
            self.store.retry_notifications([pending_id], now=130)

    def test_delivery_history_supports_custom_time_ranges(self):
        self.store.enqueue_notifications("old", "body", ["email"], now=100)
        self.store.enqueue_notifications("new", "body", ["email"], now=200)
        payload = self.store.notifications(start_timestamp=150, end_timestamp=250, now=300)
        self.assertEqual(1, payload["total"])
        self.assertEqual("new", payload["items"][0]["subject"])

    def test_acknowledge_incident_records_actor_note_and_time(self):
        incident_id = self.store.record_alert_events(
            [newapi_monitor.AlertEvent("channel_failed", "渠道异常", "upstream 502", key="channel:7")],
            now=100,
        )[0]

        acknowledged = self.store.acknowledge_incident(
            incident_id,
            actor="operator",
            note="已联系上游",
            now=120,
        )

        self.assertEqual("operator", acknowledged["acknowledged_by"])
        self.assertEqual("已联系上游", acknowledged["acknowledgement_note"])
        self.assertEqual(120, acknowledged["acknowledged_at"])

    def test_experience_policy_records_all_events_but_only_notifies_channel_and_latency(self):
        publisher = newapi_monitor.AlertPublisher(
            self.store,
            ["wecom_webhook"],
            notifiable_kinds={"channel_failed", "channel_recovered", "latency_high", "latency_recovered"},
        )

        notification_ids = publisher.publish(
            [
                newapi_monitor.AlertEvent("resource_high", "资源异常", "CPU 95%", key="resource:cpu"),
                newapi_monitor.AlertEvent("channel_failed", "渠道不可用", "5/5 failed", key="channel:7"),
            ],
            now=100,
        )

        self.assertEqual(2, self.store.connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0])
        self.assertEqual(1, len(notification_ids))
        notification = self.store.notification(notification_ids[0])
        self.assertEqual("渠道不可用", notification["subject"])
        self.assertNotIn("资源异常", notification["body"])


class NotificationPolicyTests(unittest.TestCase):
    def test_quiet_hours_defer_noncritical_until_window_end(self):
        timezone = ZoneInfo("Asia/Shanghai")
        now = int(datetime(2026, 7, 26, 23, 30, tzinfo=timezone).timestamp())
        expected = int(datetime(2026, 7, 27, 8, 0, tzinfo=timezone).timestamp())
        settings = {
            "notification_quiet_hours_enabled": True,
            "notification_quiet_hours_start": "22:00",
            "notification_quiet_hours_end": "08:00",
            "notification_quiet_hours_timezone": "Asia/Shanghai",
            "notification_quiet_hours_allow_critical": True,
        }

        self.assertEqual(expected, quiet_hours_defer_until(settings, "warning", now))
        self.assertIsNone(quiet_hours_defer_until(settings, "critical", now))

    def test_worker_defers_claimed_item_without_consuming_an_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = newapi_monitor.StateStore(str(Path(temp_dir) / "monitor.db"))
            notification_id = store.enqueue_notifications(
                "subject",
                "body",
                ["email"],
                priority="warning",
                now=100,
            )[0]
            dispatcher = mock.Mock()
            worker = newapi_monitor.NotificationOutboxWorker(
                store,
                dispatcher,
                quiet_until=lambda priority, _now: 500 if priority == "warning" else None,
            )

            result = worker.run_once(now=100)

            self.assertEqual({"delivered": 0, "failed": 0, "deferred": 1}, result)
            row = store.notification(notification_id)
            self.assertEqual("pending", row["status"])
            self.assertEqual(0, row["attempts"])
            self.assertEqual(500, row["next_attempt_at"])
            dispatcher.send.assert_not_called()
            store.connection.close()

    def test_scheduled_channel_maintenance_is_active_only_inside_window(self):
        config = {
            "maintenance_window_enabled": True,
            "maintenance_window_start": 100,
            "maintenance_window_end": 200,
            "maintenance_window_reason": "上游升级",
        }

        self.assertEqual((True, "上游升级"), channel_maintenance_state(config, now=150))
        self.assertEqual((False, ""), channel_maintenance_state(config, now=250))

    def test_channel_settings_reject_invalid_maintenance_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = SettingsStore(str(Path(temp_dir) / "monitor.db"))
            with self.assertRaisesRegex(ValueError, "later than start"):
                settings.update_channel(
                    1,
                    {
                        "maintenance_window_enabled": True,
                        "maintenance_window_start": 200,
                        "maintenance_window_end": 100,
                    },
                    actor="admin",
                )


if __name__ == "__main__":
    unittest.main()
