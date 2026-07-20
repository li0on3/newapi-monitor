import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import newapi_monitor
from newapi_monitor import (
    AlertEvent,
    ChannelObservation,
    ChannelStateTracker,
    CollectorFreshnessTracker,
    Config,
    LatencyStateTracker,
    NewAPIClient,
    RealProbeRule,
    RelayProbeClient,
    ResourceStateTracker,
    ServiceStateTracker,
    StateStore,
    build_auth_headers,
    evaluate_latency_window,
    is_channel_test_log,
    parse_real_probe_rules,
    summarize_logs,
)


class CollectorFreshnessTests(unittest.TestCase):
    def test_state_store_records_success_failure_and_freshness(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(str(Path(temp_dir) / "monitor.db"))
            store.ensure_collector("resources", stale_after_seconds=90, now=100)
            self.assertEqual("starting", store.collector_health(now=150)["resources"]["status"])
            self.assertEqual("stale", store.collector_health(now=191)["resources"]["status"])
            store.record_collector_result("logs", True, stale_after_seconds=120, now=100)
            store.record_collector_result("logs", False, "upstream timeout", stale_after_seconds=120, now=150)

            snapshot = store.collector_health(now=180)["logs"]

            self.assertEqual("ok", snapshot["status"])
            self.assertEqual(80, snapshot["age_seconds"])
            self.assertEqual(1, snapshot["consecutive_failures"])
            self.assertEqual("upstream timeout", snapshot["last_error"])

            stale = store.collector_health(now=221)["logs"]
            self.assertEqual("stale", stale["status"])
            store.connection.close()

    def test_recovery_event_reconciles_stale_open_incident(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(str(Path(temp_dir) / "monitor.db"))
            store.record_alert_events(
                [AlertEvent("container_failed", "failed", "down", key="container:new-api", severity="critical")],
                now=100,
            )

            self.assertTrue(store.has_open_incident("container:new-api"))
            store.record_alert_events(
                [
                    AlertEvent(
                        "container_recovered",
                        "recovered",
                        "running",
                        key="container:new-api",
                        severity="info",
                        recovery=True,
                    )
                ],
                now=120,
            )

            self.assertFalse(store.has_open_incident("container:new-api"))
            store.connection.close()

    def test_freshness_tracker_alerts_once_and_recovers(self):
        tracker = CollectorFreshnessTracker()
        stale = {"logs": {"status": "stale", "age_seconds": 181, "stale_after_seconds": 120, "last_error": "timeout"}}
        healthy = {"logs": {"status": "ok", "age_seconds": 2, "stale_after_seconds": 120, "last_error": ""}}

        alerts = tracker.evaluate(stale)
        self.assertEqual(1, len(alerts))
        self.assertEqual("collector_stale", alerts[0].kind)
        self.assertEqual([], tracker.evaluate(stale))

        alerts = tracker.evaluate(healthy)
        self.assertEqual(1, len(alerts))
        self.assertEqual("collector_recovered", alerts[0].kind)


class ServiceStateTrackerTests(unittest.TestCase):
    def test_alerts_on_initial_failure_and_later_recovery(self):
        tracker = ServiceStateTracker()

        alerts = tracker.evaluate(False, "connection refused")
        self.assertEqual(1, len(alerts))
        self.assertEqual("service_failed", alerts[0].kind)

        self.assertEqual([], tracker.evaluate(False, "connection refused"))
        alerts = tracker.evaluate(True)
        self.assertEqual(1, len(alerts))
        self.assertEqual("service_recovered", alerts[0].kind)

    def test_initial_success_does_not_send_recovery(self):
        tracker = ServiceStateTracker()

        self.assertEqual([], tracker.evaluate(True))


class ChannelStateTrackerTests(unittest.TestCase):
    def test_initial_failure_alerts_immediately(self):
        tracker = ChannelStateTracker()
        failed = ChannelObservation(1, "mock", False, 0.4, "upstream 500")

        alerts = tracker.evaluate([failed])

        self.assertEqual(1, len(alerts))
        self.assertEqual("channel_failed", alerts[0].kind)

    def test_alerts_only_on_failure_and_recovery_transitions(self):
        tracker = ChannelStateTracker()
        healthy = ChannelObservation(1, "mock", True, 0.2, "")
        failed = ChannelObservation(1, "mock", False, 0.4, "upstream 500")

        self.assertEqual([], tracker.evaluate([healthy]))
        alerts = tracker.evaluate([failed])
        self.assertEqual(1, len(alerts))
        self.assertEqual("channel_failed", alerts[0].kind)

        self.assertEqual([], tracker.evaluate([failed]))
        alerts = tracker.evaluate([healthy])
        self.assertEqual(1, len(alerts))
        self.assertEqual("channel_recovered", alerts[0].kind)


class LogSummaryTests(unittest.TestCase):
    def test_identifies_new_api_channel_test_logs(self):
        self.assertTrue(is_channel_test_log({"token_name": "模型测试", "content": "模型测试"}))
        self.assertFalse(is_channel_test_log({"token_name": "production", "content": ""}))

    def test_groups_latency_by_channel_and_model(self):
        logs = [
            {
                "request_id": "r1",
                "channel": 1,
                "channel_name": "mock",
                "model_name": "gpt-test",
                "use_time": 1,
                "other": '{"frt":200}',
            },
            {
                "request_id": "r2",
                "channel": 1,
                "channel_name": "mock",
                "model_name": "gpt-test",
                "use_time": 5,
                "other": '{"frt":800}',
            },
            {
                "request_id": "r3",
                "channel": 1,
                "channel_name": "mock",
                "model_name": "gpt-test",
                "use_time": 3,
                "other": "not-json",
            },
        ]

        summary = summarize_logs(logs, slow_seconds=4)

        self.assertEqual(1, len(summary))
        row = summary[0]
        self.assertEqual(3, row.count)
        self.assertEqual(3.0, row.average_seconds)
        self.assertEqual(5.0, row.p95_seconds)
        self.assertEqual(500.0, row.average_frt_ms)
        self.assertEqual(1, row.slow_count)


class NewAPIClientLogTests(unittest.TestCase):
    def test_log_pagination_uses_new_api_p_parameter(self):
        config = mock.Mock(base_url="https://newapi.example", access_token="token", user_id=1)
        client = NewAPIClient(config)
        paths: list[str] = []

        def fake_request(path: str, allow_failure: bool = False):
            paths.append(path)
            page = newapi_monitor.urllib.parse.parse_qs(
                newapi_monitor.urllib.parse.urlsplit(path).query
            ).get("p", [""])[0]
            if page == "1":
                return {"data": {"items": [{"id": index} for index in range(100)], "total": 101}}
            if page == "2":
                return {"data": {"items": [{"id": 100}], "total": 101}}
            self.fail(f"unexpected pagination path: {path}")

        client._request = fake_request

        logs = client.get_logs(100, 200)

        self.assertEqual(101, len(logs))
        self.assertIn("p=1", paths[0])
        self.assertIn("p=2", paths[1])


class LatencyWindowTests(unittest.TestCase):
    def test_triggers_when_three_of_last_five_are_slow(self):
        decision = evaluate_latency_window(
            [
                {"use_time": 61, "frt_ms": None},
                {"use_time": 10, "frt_ms": 1000},
                {"use_time": 62, "frt_ms": None},
                {"use_time": 20, "frt_ms": 2000},
                {"use_time": 63, "frt_ms": None},
            ]
        )

        self.assertTrue(decision.triggered)
        self.assertFalse(decision.critical)
        self.assertEqual(3, decision.bad_last5)

    def test_triggers_when_five_of_last_ten_are_slow(self):
        samples = [
            {"use_time": value, "frt_ms": None}
            for value in (61, 10, 62, 20, 30, 63, 40, 64, 50, 65)
        ]

        decision = evaluate_latency_window(samples)

        self.assertTrue(decision.triggered)
        self.assertEqual(2, decision.bad_last5)
        self.assertEqual(5, decision.bad_last10)

    def test_single_extreme_sample_is_critical(self):
        decision = evaluate_latency_window([{"use_time": 181, "frt_ms": 1000}])

        self.assertTrue(decision.triggered)
        self.assertTrue(decision.critical)

    def test_tracker_sends_recovery_after_five_normal_samples(self):
        tracker = LatencyStateTracker()
        slow_samples = [{"use_time": value, "frt_ms": None} for value in (61, 62, 63, 10, 20)]
        normal_samples = [{"use_time": 10, "frt_ms": 1000} for _ in range(5)]

        alerts = tracker.evaluate("4:gpt-test", "mock/gpt-test", slow_samples, now=100)
        self.assertEqual("latency_high", alerts[0].kind)
        self.assertEqual([], tracker.evaluate("4:gpt-test", "mock/gpt-test", slow_samples, now=101))
        alerts = tracker.evaluate("4:gpt-test", "mock/gpt-test", normal_samples, now=200)

        self.assertEqual(1, len(alerts))
        self.assertEqual("latency_recovered", alerts[0].kind)


class RealProbeRuleTests(unittest.TestCase):
    def test_parses_channel_specific_real_probe(self):
        rules = parse_real_probe_rules(
            '{"4":{"model":"gpt-5.6-sol","path":"/v1/responses","format":"responses"}}'
        )

        self.assertEqual("gpt-5.6-sol", rules[4].model)
        self.assertEqual("/v1/responses", rules[4].path)
        self.assertEqual("responses", rules[4].request_format)

    def test_parses_anthropic_probe_with_messages_default_path(self):
        rules = parse_real_probe_rules(
            '{"5":{"model":"claude-opus-4-8","format":"anthropic","prompt":"1","max_output_tokens":1}}'
        )

        self.assertEqual("/v1/messages", rules[5].path)
        self.assertEqual("anthropic", rules[5].request_format)
        self.assertEqual("1", rules[5].prompt)
        self.assertEqual(1, rules[5].max_output_tokens)

    def test_real_probe_forces_the_configured_channel(self):
        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return b'{"status":"completed"}'

        config = mock.Mock(base_url="https://newapi.example", relay_api_token="probe-token")
        client = RelayProbeClient(config)
        rule = RealProbeRule(7, "gpt-5.4", "/v1/responses", "responses")

        with mock.patch("newapi_monitor.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            result = client.probe(rule)

        request = urlopen.call_args.args[0]
        self.assertTrue(result.success)
        self.assertEqual("Bearer sk-probe-token-7", request.get_header("Authorization"))

    def test_anthropic_probe_uses_minimal_messages_request(self):
        class FakeResponse:
            headers = {"Content-Type": "text/event-stream"}

            def __init__(self):
                self.lines = iter([
                    b'event: message_start\n',
                    b'data: {"type":"message_start"}\n',
                    b'event: message_stop\n',
                    b'data: {"type":"message_stop"}\n',
                    b'',
                ])

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def readline(self):
                return next(self.lines)

        config = mock.Mock(base_url="https://newapi.example", relay_api_token="probe-token")
        client = RelayProbeClient(config)
        rule = RealProbeRule(5, "claude-opus-4-8", "/v1/messages", "anthropic", "1", 1)

        with mock.patch("newapi_monitor.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            result = client.probe(rule)

        request = urlopen.call_args.args[0]
        payload = newapi_monitor.json.loads(request.data)
        self.assertTrue(result.success)
        self.assertEqual("Bearer sk-probe-token-5", request.get_header("Authorization"))
        self.assertEqual("2023-06-01", request.get_header("Anthropic-version"))
        self.assertEqual(1, payload["max_tokens"])
        self.assertEqual([{"role": "user", "content": "1"}], payload["messages"])
        self.assertTrue(payload["stream"])


class ResourceStateTrackerTests(unittest.TestCase):
    def test_below_threshold_resets_unalerted_sustain_timer(self):
        tracker = ResourceStateTracker({"cpu": 80.0}, sustain_seconds=10)

        self.assertEqual([], tracker.evaluate({"cpu": 90.0}, now=0))
        self.assertEqual([], tracker.evaluate({"cpu": 79.0}, now=5))
        self.assertEqual([], tracker.evaluate({"cpu": 90.0}, now=11))

    def test_requires_sustained_threshold_and_sends_recovery(self):
        tracker = ResourceStateTracker({"cpu": 80.0}, sustain_seconds=60)
        now = time.time()

        self.assertEqual([], tracker.evaluate({"cpu": 90.0}, now=now))
        alerts = tracker.evaluate({"cpu": 91.0}, now=now + 61)
        self.assertEqual(1, len(alerts))
        self.assertEqual("resource_high", alerts[0].kind)

        self.assertEqual([], tracker.evaluate({"cpu": 92.0}, now=now + 120))
        alerts = tracker.evaluate({"cpu": 70.0}, now=now + 121)
        self.assertEqual(1, len(alerts))
        self.assertEqual("resource_recovered", alerts[0].kind)


class AuthenticationTests(unittest.TestCase):
    def test_builds_management_access_token_headers(self):
        self.assertEqual(
            {
                "Authorization": "Bearer secret-token",
                "New-Api-User": "7",
            },
            build_auth_headers("secret-token", 7),
        )


class ConfigTests(unittest.TestCase):
    def test_channel_configuration_sync_has_independent_fast_interval(self):
        with mock.patch.dict(
            "os.environ",
            {
                "CHANNEL_SYNC_INTERVAL_SECONDS": "5",
                "CHANNEL_INTERVAL_SECONDS": "300",
            },
            clear=True,
        ):
            config = Config.from_env()

        self.assertEqual(5, getattr(config, "channel_sync_interval_seconds", None))
        self.assertEqual(300, config.channel_interval_seconds)

    def test_dynamic_values_override_environment_and_channel_probe_rules(self):
        with mock.patch.dict("os.environ", {"CHANNEL_INTERVAL_SECONDS": "300"}, clear=True):
            config = Config.from_values(
                {
                    "channel_interval_seconds": 90,
                    "real_probe_rules": {
                        "7": {"model": "gpt-5.4", "format": "responses"}
                    },
                }
            )

        self.assertEqual(90, config.channel_interval_seconds)
        self.assertEqual("gpt-5.4", config.real_probe_rules[7].model)

    def test_dynamic_resource_thresholds_are_loaded(self):
        config = Config.from_values({"system_cpu_threshold": 72, "system_memory_threshold": 74})

        self.assertEqual(72, config.system_cpu_threshold)
        self.assertEqual(74, config.system_memory_threshold)

    def test_dynamic_channel_settings_are_loaded(self):
        config = Config.from_values({"channel_settings": {"7": {"maintenance_mode": True}}})

        self.assertTrue(config.channel_settings[7]["maintenance_mode"])


class ChannelSyncWorkerTests(unittest.TestCase):
    def test_sync_once_persists_and_publishes_the_latest_snapshot(self):
        worker_class = getattr(newapi_monitor, "ChannelSyncWorker", None)
        self.assertIsNotNone(worker_class)
        channels = [{"id": 1, "name": "enabled", "status": 1}]
        client = mock.Mock()
        client.get_channels.return_value = channels
        store = mock.Mock()
        snapshots = []

        worker = worker_class(client, store, snapshots.append)
        result = worker.sync_once()

        self.assertEqual(channels, result)
        store.upsert_channels.assert_called_once_with(channels)
        self.assertEqual([channels], snapshots)

    def test_run_reports_every_successful_attempt_for_freshness(self):
        worker_class = getattr(newapi_monitor, "ChannelSyncWorker", None)
        client = mock.Mock()
        client.get_channels.return_value = [{"id": 1, "name": "enabled", "status": 1}]
        store = mock.Mock()
        results = []

        class TwoIterations:
            def __init__(self):
                self.waits = 0

            def is_set(self):
                return False

            def wait(self, _seconds):
                self.waits += 1
                return self.waits >= 2

        worker = worker_class(client, store, lambda _channels: None, lambda success, error: results.append((success, error)))
        worker.run(TwoIterations(), 1)

        self.assertEqual([(True, ""), (True, "")], results)
        self.assertEqual(2, client.get_channels.call_count)
        store.connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
