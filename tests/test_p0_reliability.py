import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import newapi_monitor
from dashboard_data import DashboardRepository


class LatencyStateMachineP0Tests(unittest.TestCase):
    @staticmethod
    def samples(values: list[float], start_id: int = 100) -> list[dict]:
        return [
            {
                "sample_key": f"request-{start_id - index}",
                "request_id": f"request-{start_id - index}",
                "created_at": 1_000 - index,
                "use_time": value,
                "frt_ms": 1000,
            }
            for index, value in enumerate(values)
        ]

    def test_active_incident_does_not_remind_without_a_new_sample(self):
        tracker = newapi_monitor.LatencyStateTracker(reminder_seconds=30)
        bad = self.samples([61, 62, 63, 10, 20])

        first = tracker.evaluate("1:gpt", "channel/gpt", bad, now=100)
        repeated = tracker.evaluate("1:gpt", "channel/gpt", bad, now=200)

        self.assertEqual("latency_high", first[0].kind)
        self.assertEqual([], repeated)

    def test_five_new_healthy_samples_recover_even_when_older_hard_limit_remains(self):
        tracker = newapi_monitor.LatencyStateTracker(reminder_seconds=30)
        initial = self.samples([306, 20, 20, 20, 20], start_id=100)
        tracker.evaluate("1:gpt", "channel/gpt", initial, now=100)
        recovered_window = self.samples([38, 5, 3, 7, 4, 306, 20, 20, 20, 20], start_id=105)

        events = tracker.evaluate("1:gpt", "channel/gpt", recovered_window, now=200)

        self.assertEqual(1, len(events))
        self.assertEqual("latency_recovered", events[0].kind)
        self.assertFalse(tracker.states["1:gpt"]["active"])

    def test_log_ingest_returns_only_groups_touched_by_new_samples(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = newapi_monitor.StateStore(str(Path(temp_dir) / "monitor.db"))
            logs = [
                {
                    "request_id": "request-1",
                    "created_at": 100,
                    "channel": 1,
                    "channel_name": "one",
                    "model_name": "gpt",
                    "use_time": 61,
                    "other": "{}",
                }
            ]

            inserted, groups = store.ingest_logs_with_groups(logs)
            duplicate_inserted, duplicate_groups = store.ingest_logs_with_groups(logs)

            self.assertEqual(1, inserted)
            self.assertEqual({(1, "one", "gpt")}, groups)
            self.assertEqual(0, duplicate_inserted)
            self.assertEqual(set(), duplicate_groups)
            store.connection.close()


class NotificationOutboxP0Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = newapi_monitor.StateStore(str(Path(self.temp_dir.name) / "monitor.db"))

    def tearDown(self):
        self.store.connection.close()
        self.temp_dir.cleanup()

    def test_failed_destination_stays_pending_and_successful_destination_is_delivered(self):
        event = newapi_monitor.AlertEvent(
            "channel_failed",
            "渠道异常",
            "upstream 502",
            key="channel:7",
            severity="critical",
        )
        publisher = newapi_monitor.AlertPublisher(
            self.store,
            destinations=["wecom_webhook", "email"],
        )
        publisher.publish([event], now=100)
        dispatcher = mock.Mock()

        def send(_subject, _body, channel="all"):
            if channel == "email":
                raise RuntimeError("smtp unavailable")
            return {"succeeded": [channel], "failed": []}

        dispatcher.send.side_effect = send
        worker = newapi_monitor.NotificationOutboxWorker(self.store, dispatcher, max_attempts=3)

        result = worker.run_once(now=100)
        rows = self.store.connection.execute(
            "SELECT destination, status, attempts, next_attempt_at FROM notification_outbox ORDER BY destination"
        ).fetchall()
        incident = self.store.connection.execute(
            "SELECT last_notified_at FROM incidents WHERE incident_key = 'channel:7'"
        ).fetchone()

        self.assertEqual({"delivered": 1, "failed": 1}, result)
        self.assertEqual("pending", rows[0]["status"])
        self.assertEqual(1, rows[0]["attempts"])
        self.assertGreater(rows[0]["next_attempt_at"], 100)
        self.assertEqual("delivered", rows[1]["status"])
        self.assertEqual(100, int(incident["last_notified_at"]))

    def test_all_failed_delivery_is_retried_after_restart(self):
        publisher = newapi_monitor.AlertPublisher(self.store, destinations=["email"])
        publisher.publish(
            [newapi_monitor.AlertEvent("service_failed", "服务异常", "timeout", key="service:newapi")],
            now=100,
        )
        failing = mock.Mock()
        failing.send.side_effect = RuntimeError("offline")
        first_worker = newapi_monitor.NotificationOutboxWorker(self.store, failing, max_attempts=3)
        first_worker.run_once(now=100)
        next_attempt = int(
            self.store.connection.execute(
                "SELECT next_attempt_at FROM notification_outbox"
            ).fetchone()[0]
        )

        succeeding = mock.Mock()
        succeeding.send.return_value = {"succeeded": ["email"], "failed": []}
        restarted_worker = newapi_monitor.NotificationOutboxWorker(self.store, succeeding, max_attempts=3)
        result = restarted_worker.run_once(now=next_attempt)

        row = self.store.connection.execute(
            "SELECT status, attempts, delivered_at FROM notification_outbox"
        ).fetchone()
        self.assertEqual({"delivered": 1, "failed": 0}, result)
        self.assertEqual("delivered", row["status"])
        self.assertEqual(2, row["attempts"])
        self.assertEqual(next_attempt, int(row["delivered_at"]))

    def test_monitor_event_path_persists_before_attempting_delivery(self):
        app = object.__new__(newapi_monitor.MonitorApp)
        app.alert_publisher = mock.Mock()
        app.alert_publisher.publish.return_value = []
        app.outbox_worker = mock.Mock()
        app.outbox_worker.run_once.return_value = {"delivered": 0, "failed": 0}
        events = [newapi_monitor.AlertEvent("failed", "异常", "cause", key="service:newapi")]

        app._send_events(events)

        app.alert_publisher.publish.assert_called_once_with(events)
        app.outbox_worker.run_once.assert_called_once_with()

    def test_periodic_and_startup_messages_use_the_same_durable_outbox(self):
        app = object.__new__(newapi_monitor.MonitorApp)
        app.alert_publisher = mock.Mock()
        app.alert_publisher.publish_message.return_value = [1]
        app.outbox_worker = mock.Mock()
        app.outbox_worker.run_once.return_value = {"delivered": 0, "failed": 1}

        app._send_message("监控程序启动", "target")

        app.alert_publisher.publish_message.assert_called_once_with("监控程序启动", "target")
        app.outbox_worker.run_once.assert_called_once_with()

    def test_channel_probe_worker_only_publishes_and_does_not_send_directly(self):
        store = mock.Mock()
        store.get_json.side_effect = lambda _key, default=None: default
        publisher = mock.Mock()
        publisher.publish.return_value = []
        config = mock.Mock(
            real_probe_rules={},
            channel_settings={},
            channel_slow_seconds=60,
            channel_failure_threshold=1,
            channel_recovery_threshold=1,
            channel_probe_concurrency=1,
        )
        client = mock.Mock()
        client.test_channel.return_value = {"success": False, "message": "upstream 502", "time": 1}
        worker = newapi_monitor.ChannelProbeWorker(
            config,
            client,
            None,
            store,
            publisher,
            lambda: [{"id": 1, "name": "one", "status": 1}],
            lambda _items: None,
            stale_after_seconds=900,
        )

        worker.check_once()

        publisher.publish.assert_called_once()
        self.assertEqual("channel_failed", publisher.publish.call_args.args[0][0].kind)


class IncidentLifecycleP0Tests(unittest.TestCase):
    def test_removed_disabled_and_maintenance_channels_resolve_open_incidents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = newapi_monitor.StateStore(str(Path(temp_dir) / "monitor.db"))
            for key in ("channel:1", "latency:1:gpt", "channel:2", "latency:3:gpt"):
                store.record_alert_events(
                    [newapi_monitor.AlertEvent("failed", "异常", "cause", key=key)],
                    now=100,
                )

            resolved = store.reconcile_channel_incidents(
                [
                    {"id": 1, "status": 2},
                    {"id": 2, "status": 1},
                    {"id": 3, "status": 1},
                ],
                {
                    2: {"maintenance_mode": True},
                    3: {"alert_enabled": False},
                },
                now=200,
            )

            open_count = store.connection.execute(
                "SELECT COUNT(*) FROM incidents WHERE status = 'open'"
            ).fetchone()[0]
            self.assertEqual(4, resolved)
            self.assertEqual(0, open_count)
            store.connection.close()


class ContainerStateP0Tests(unittest.TestCase):
    def test_first_restart_after_baseline_and_oom_transition_alert_once(self):
        tracker = newapi_monitor.ContainerStateTracker()
        self.assertEqual([], tracker.evaluate({"api": {"status": "running", "restarts": 0, "oom_killed": False}}))

        events = tracker.evaluate({"api": {"status": "running", "restarts": 1, "oom_killed": True}})
        repeated = tracker.evaluate({"api": {"status": "running", "restarts": 1, "oom_killed": True}})

        self.assertEqual({"container_restarted", "container_oom"}, {event.kind for event in events})
        self.assertEqual([], repeated)

    def test_restart_event_is_history_only_and_does_not_leave_an_open_incident(self):
        tracker = newapi_monitor.ContainerStateTracker(
            {"api": {"status": "running", "restarts": 0, "oom_killed": False}}
        )
        event = next(
            item
            for item in tracker.evaluate(
                {"api": {"status": "running", "restarts": 1, "oom_killed": False}}
            )
            if item.kind == "container_restarted"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = newapi_monitor.StateStore(str(Path(temp_dir) / "monitor.db"))

            store.record_alert_events([event], now=100)

            row = store.connection.execute(
                "SELECT status, resolved_at FROM incidents WHERE incident_key = ?",
                (event.key,),
            ).fetchone()
            self.assertEqual("resolved", row["status"])
            self.assertEqual(100, int(row["resolved_at"]))
            store.connection.close()


class ProbeProtocolP0Tests(unittest.TestCase):
    def test_responses_incomplete_at_output_limit_is_a_healthy_terminal_response(self):
        validator = newapi_monitor.ProbeProtocolValidator("responses")
        validator.feed("response.created", json.dumps({"type": "response.created"}))
        validator.feed(
            "response.incomplete",
            json.dumps({"type": "response.incomplete", "response": {"status": "incomplete"}}),
        )

        self.assertEqual((True, ""), validator.result())
        self.assertEqual(
            (True, "response ended at the configured output limit"),
            newapi_monitor.validate_probe_json("responses", {"status": "incomplete"}),
        )

    def test_stream_without_terminal_event_is_not_healthy(self):
        validator = newapi_monitor.ProbeProtocolValidator("responses")
        validator.feed("response.created", json.dumps({"type": "response.created"}))

        success, message = validator.result()

        self.assertFalse(success)
        self.assertIn("terminal", message)

    def test_responses_sse_failed_event_is_not_healthy(self):
        validator = newapi_monitor.ProbeProtocolValidator("responses")
        validator.feed("response.created", json.dumps({"type": "response.created"}))
        validator.feed(
            "response.failed",
            json.dumps({"type": "response.failed", "response": {"error": {"message": "upstream failed"}}}),
        )

        success, message = validator.result()

        self.assertFalse(success)
        self.assertIn("upstream failed", message)

    def test_anthropic_error_event_is_not_healthy(self):
        validator = newapi_monitor.ProbeProtocolValidator("anthropic")
        validator.feed("message_start", json.dumps({"type": "message_start"}))
        validator.feed("error", json.dumps({"type": "error", "error": {"message": "overloaded"}}))

        success, message = validator.result()

        self.assertFalse(success)
        self.assertIn("overloaded", message)

    def test_chat_requires_a_choice_in_json_response(self):
        success, message = newapi_monitor.validate_probe_json("chat", {"id": "response-without-choices"})

        self.assertFalse(success)
        self.assertIn("choices", message)


class ChannelSyncP0Tests(unittest.TestCase):
    def test_malformed_channel_response_does_not_become_an_empty_snapshot(self):
        config = mock.Mock(base_url="https://newapi.example", access_token="token", user_id=1)
        client = newapi_monitor.NewAPIClient(config)
        client._request = mock.Mock(return_value={"success": True, "data": {"unexpected": []}})

        with self.assertRaisesRegex(RuntimeError, "items"):
            client.get_channels()

    def test_slow_but_valid_probe_is_degraded_not_failed(self):
        store = mock.Mock()
        store.get_json.side_effect = lambda _key, default=None: default
        config = mock.Mock(
            real_probe_rules={1: newapi_monitor.RealProbeRule(1, "gpt", "/v1/responses", "responses")},
            channel_settings={},
            channel_slow_seconds=5,
            channel_failure_threshold=1,
            channel_recovery_threshold=1,
            channel_probe_concurrency=1,
        )
        relay = mock.Mock()
        relay.probe.return_value = newapi_monitor.RealProbeResult(True, 8, 100, "")
        worker = newapi_monitor.ChannelProbeWorker(
            config,
            mock.Mock(),
            relay,
            store,
            mock.Mock(publish=mock.Mock(return_value=[])),
            lambda: [],
            lambda _items: None,
            stale_after_seconds=900,
        )

        observation = worker._probe_channel({"id": 1, "name": "slow", "status": 1})

        self.assertTrue(observation.success)
        self.assertIn("耗时超过阈值", observation.message)

    def test_disabling_destination_cancels_pending_delivery_without_dead_letter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = newapi_monitor.StateStore(str(Path(temp_dir) / "monitor.db"))
            newapi_monitor.AlertPublisher(store, destinations=["email", "wecom_webhook"]).publish_message(
                "subject",
                "body",
                now=100,
            )

            cancelled = store.cancel_disabled_notifications(["email"], now=110)
            rows = store.connection.execute(
                "SELECT destination, status FROM notification_outbox ORDER BY destination"
            ).fetchall()

            self.assertEqual(1, cancelled)
            self.assertEqual([("email", "pending"), ("wecom_webhook", "cancelled")], [tuple(row) for row in rows])
            self.assertEqual(0, store.storage_health(now=120)["outbox_dead"])
            store.connection.close()


class CapacityP0Tests(unittest.TestCase):
    def test_database_maintenance_runs_without_log_collection(self):
        app = object.__new__(newapi_monitor.MonitorApp)
        app.config = SimpleNamespace(
            database_maintenance_interval_seconds=3600,
            retention_days=90,
            incident_retention_days=365,
            notification_retention_days=30,
            database_max_mb=2048,
        )
        app.store = mock.Mock()
        app.store.get_json.return_value = 0
        app.store.maintain.return_value = {"database_bytes": 1024, "wal_bytes": 512}
        app.store.has_open_incident.return_value = False
        app._send_events = mock.Mock()

        stats = app.maintain_database(now=10_000)

        self.assertEqual({"database_bytes": 1024, "wal_bytes": 512}, stats)
        app.store.maintain.assert_called_once_with(
            10_000 - 90 * 86400,
            10_000 - 365 * 86400,
            10_000 - 30 * 86400,
        )
        app.store.set_json.assert_any_call("last_database_maintenance_at", 10_000)
        app._send_events.assert_not_called()

    def test_resource_query_covers_requested_window_with_bounded_buckets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = str(Path(temp_dir) / "monitor.db")
            store = newapi_monitor.StateStore(database_path)
            now = 200_000
            for offset in range(0, 24 * 3600, 15):
                store.insert_resource_sample(
                    {"system_cpu": 10, "system_memory": 20, "system_disk": 30},
                    {"containers": {}},
                    created_at=now - 24 * 3600 + offset,
                )
            repository = DashboardRepository(database_path)

            payload = repository.resources(now=now, hours=24, limit=1440)

            self.assertLessEqual(len(payload["samples"]), 1440)
            self.assertGreaterEqual(payload["actual_start"], now - 24 * 3600)
            self.assertLessEqual(payload["actual_start"], now - 24 * 3600 + 60)
            self.assertGreater(payload["coverage_ratio"], 0.99)
            self.assertGreaterEqual(payload["bucket_seconds"], 60)
            store.connection.close()

    def test_prune_removes_old_resolved_incidents_and_delivered_notifications_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = newapi_monitor.StateStore(str(Path(temp_dir) / "monitor.db"))
            store.record_alert_events(
                [newapi_monitor.AlertEvent("failed", "old open", "cause", key="channel:1")],
                now=10,
            )
            store.record_alert_events(
                [newapi_monitor.AlertEvent("failed", "resolved", "cause", key="channel:2")],
                now=10,
            )
            store.record_alert_events(
                [newapi_monitor.AlertEvent("recovered", "resolved", "ok", key="channel:2", recovery=True)],
                now=20,
            )
            publisher = newapi_monitor.AlertPublisher(store, destinations=["email"])
            publisher.publish(
                [newapi_monitor.AlertEvent("service_failed", "delivered", "cause", key="service:newapi")],
                now=10,
            )
            store.connection.execute(
                "UPDATE notification_outbox SET status = 'delivered', delivered_at = 20, updated_at = 20"
            )
            store.connection.commit()

            store.prune(
                before_timestamp=100,
                incident_before_timestamp=100,
                delivery_before_timestamp=100,
            )

            remaining_incidents = {
                row[0] for row in store.connection.execute("SELECT incident_key FROM incidents")
            }
            outbox_count = store.connection.execute(
                "SELECT COUNT(*) FROM notification_outbox"
            ).fetchone()[0]
            self.assertIn("channel:1", remaining_incidents)
            self.assertNotIn("channel:2", remaining_incidents)
            self.assertEqual(0, outbox_count)
            store.connection.close()

    def test_storage_health_reports_database_size_and_outbox_backlog(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = newapi_monitor.StateStore(str(Path(temp_dir) / "monitor.db"))
            newapi_monitor.AlertPublisher(store, ["email"]).publish_message("subject", "body", now=100)

            health = store.storage_health(now=200, max_bytes=1)

            self.assertGreater(health["database_bytes"], 1)
            self.assertTrue(health["over_capacity"])
            self.assertEqual(1, health["outbox_pending"])
            self.assertEqual(100, health["oldest_pending_age_seconds"])
            store.connection.close()


if __name__ == "__main__":
    unittest.main()
