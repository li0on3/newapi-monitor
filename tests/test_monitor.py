import time
import sqlite3
import tempfile
import threading
import unittest
import base64
import hashlib
import hmac
import json
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
    FeishuWebhookNotifier,
    NewAPIClient,
    NotificationDispatcher,
    RealProbeRule,
    RelayProbeClient,
    ResourceStateTracker,
    ServiceStateTracker,
    StateStore,
    WeComAppNotifier,
    WeComWebhookNotifier,
    build_auth_headers,
    evaluate_latency_window,
    is_channel_test_log,
    parse_real_probe_rules,
    summarize_logs,
)


class CollectorFreshnessTests(unittest.TestCase):
    def test_state_store_migrates_legacy_incident_without_inventing_a_cause(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = str(Path(temp_dir) / "monitor.db")
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                CREATE TABLE incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    resolved_at INTEGER,
                    last_notified_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO incidents(
                    incident_key, kind, severity, title, body, status,
                    started_at, updated_at, resolved_at, last_notified_at
                ) VALUES ('resource:memory', 'resource_recovered', 'warning', '内存恢复',
                          '当前值 62%', 'resolved', 100, 120, 120, 100)
                """
            )
            connection.commit()
            connection.close()

            store = StateStore(database_path)
            row = store.connection.execute(
                "SELECT body, resolution_body, legacy_cause_missing FROM incidents"
            ).fetchone()

            self.assertEqual("当前值 62%", row["body"])
            self.assertEqual("当前值 62%", row["resolution_body"])
            self.assertEqual(1, row["legacy_cause_missing"])
            store.connection.close()

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
            row = store.connection.execute(
                "SELECT body, resolution_body FROM incidents WHERE incident_key = ? ORDER BY id DESC LIMIT 1",
                ("container:new-api",),
            ).fetchone()
            self.assertEqual("down", row["body"])
            self.assertEqual("running", row["resolution_body"])
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
    def test_requires_two_failures_before_alerting(self):
        tracker = ChannelStateTracker(failure_threshold=2, recovery_threshold=2)
        failed = ChannelObservation(1, "mock", False, 0.4, "upstream 500")

        self.assertEqual([], tracker.evaluate([failed]))
        alerts = tracker.evaluate([failed])

        self.assertEqual(1, len(alerts))
        self.assertEqual("channel_failed", alerts[0].kind)
        self.assertEqual("warning", alerts[0].severity)

    def test_requires_two_successes_before_recovery(self):
        tracker = ChannelStateTracker(failure_threshold=2, recovery_threshold=2)
        healthy = ChannelObservation(1, "mock", True, 0.2, "")
        failed = ChannelObservation(1, "mock", False, 0.4, "upstream 500")

        self.assertEqual([], tracker.evaluate([healthy]))
        self.assertEqual([], tracker.evaluate([failed]))
        alerts = tracker.evaluate([failed])
        self.assertEqual(1, len(alerts))
        self.assertEqual("channel_failed", alerts[0].kind)

        self.assertEqual([], tracker.evaluate([failed]))
        self.assertEqual([], tracker.evaluate([healthy]))
        alerts = tracker.evaluate([healthy])
        self.assertEqual(1, len(alerts))
        self.assertEqual("channel_recovered", alerts[0].kind)

    def test_persistent_failure_is_critical(self):
        tracker = ChannelStateTracker(failure_threshold=2, recovery_threshold=2)
        failed = ChannelObservation(1, "mock", False, 0.4, "invalid model configuration")

        tracker.evaluate([failed])
        alerts = tracker.evaluate([failed])

        self.assertEqual("critical", alerts[0].severity)

    def test_common_auth_failure_is_one_probe_incident(self):
        tracker_class = getattr(newapi_monitor, "ProbeCredentialStateTracker", None)
        self.assertIsNotNone(tracker_class)
        tracker = tracker_class(recovery_threshold=2)
        failures = [
            ChannelObservation(1, "one", False, 0.1, "HTTP 403: 无权访问 default 分组"),
            ChannelObservation(2, "two", False, 0.1, "HTTP 403: 无权访问 default 分组"),
            ChannelObservation(3, "three", True, 0.1, ""),
        ]

        alerts, suppressed = tracker.evaluate(failures)

        self.assertEqual(1, len(alerts))
        self.assertEqual("probe_auth_failed", alerts[0].kind)
        self.assertEqual({1, 2}, suppressed)
        self.assertEqual(([], set()), tracker.evaluate([ChannelObservation(1, "one", True, 0.1, "")]))
        alerts, suppressed = tracker.evaluate([ChannelObservation(1, "one", True, 0.1, "")])
        self.assertEqual("probe_auth_recovered", alerts[0].kind)
        self.assertEqual(set(), suppressed)


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

    def test_channel_alert_noise_controls_are_dynamic(self):
        config = Config.from_values(
            {
                "channel_probe_concurrency": 4,
                "channel_failure_threshold": 3,
                "channel_recovery_threshold": 2,
            }
        )

        self.assertEqual(4, config.channel_probe_concurrency)
        self.assertEqual(3, config.channel_failure_threshold)
        self.assertEqual(2, config.channel_recovery_threshold)

    def test_dynamic_resource_thresholds_are_loaded(self):
        config = Config.from_values({"system_cpu_threshold": 72, "system_memory_threshold": 74})

        self.assertEqual(72, config.system_cpu_threshold)
        self.assertEqual(74, config.system_memory_threshold)

    def test_openai_status_configuration_is_dynamic(self):
        config = Config.from_values(
            {
                "openai_status_enabled": True,
                "openai_status_alert_enabled": False,
                "openai_status_interval_seconds": 120,
                "openai_status_timeout_seconds": 8,
                "openai_status_min_impact": "minor",
                "openai_status_component_ids": ["responses", "codex-api", "responses"],
                "openai_status_failure_threshold": 3,
                "openai_status_recovery_threshold": 2,
                "openai_status_include_in_overall": True,
                "openai_status_admin_visible": True,
                "openai_status_viewer_visible": False,
            }
        )

        self.assertEqual(120, config.openai_status_interval_seconds)
        self.assertEqual(("responses", "codex-api"), config.openai_status_component_ids)
        self.assertEqual("minor", config.openai_status_min_impact)
        self.assertFalse(config.openai_status_alert_enabled)
        self.assertTrue(config.openai_status_include_in_overall)
        self.assertFalse(config.openai_status_viewer_visible)

    def test_dynamic_channel_settings_are_loaded(self):
        config = Config.from_values({"channel_settings": {"7": {"maintenance_mode": True}}})

        self.assertTrue(config.channel_settings[7]["maintenance_mode"])

    def test_notification_configuration_does_not_require_smtp(self):
        config = Config.from_values(
            {
                "new_api_access_token": "admin-token",
                "new_api_user_id": 1,
                "email_enabled": False,
                "wecom_app_enabled": True,
                "wecom_corp_id": "ww-test",
                "wecom_agent_id": 1000004,
                "wecom_app_secret": "secret",
                "wecom_to_user": "@all",
            }
        )

        config.validate()
        self.assertTrue(config.wecom_app_enabled)
        self.assertFalse(config.email_enabled)


class NotificationTests(unittest.TestCase):
    def test_notification_html_turns_report_sections_into_scannable_cards(self):
        html = newapi_monitor.notification_html(
            "周期报告 · 需要关注",
            "🟠 New API 监控周期报告\n结论：渠道正常，但延迟升高。\n\n【请求性能】\n🔴 Demo <unsafe>\n   P95 2分01秒",
        )

        self.assertIn("<h1>周期报告 · 需要关注</h1>", html)
        self.assertIn("<h2>请求性能</h2>", html)
        self.assertIn("Demo &lt;unsafe&gt;", html)
        self.assertNotIn("Demo <unsafe>", html)

    def test_periodic_report_prioritizes_risk_and_uses_human_readable_units(self):
        channels = [
            ChannelObservation(1, "Primary", True, 2.118, "ok"),
            ChannelObservation(2, "Backup", True, 33.743, "ok"),
        ]
        latency = [
            newapi_monitor.LatencySummary(1, "Primary", "gpt-demo", 19, 62.526, 307.0, 3990.5, 7),
            newapi_monitor.LatencySummary(1, "Primary", "gpt-fast", 42, 16.095, 34.0, 6253.3, 1),
        ]

        subject, body = newapi_monitor.build_periodic_report(
            channels,
            latency,
            {
                "system_cpu": 12.5,
                "system_memory": 42.4,
                "system_disk": 28.9,
                "system_available_mb": 1133.4,
                "container_cpu": 0.2,
                "container_memory": 5.7,
                "system_swap": 6.9,
            },
            {"container_status": "running", "container_restarts": 0},
            slow_seconds=60,
            period_seconds=86400,
            generated_at=1_750_000_000,
        )

        self.assertEqual("周期报告 · 需要关注", subject)
        self.assertIn("结论：渠道全部可用，但发现 1 个高延迟模型", body)
        self.assertIn("P95 5分07秒", body)
        self.assertIn("慢请求 7/19（36.8%）", body)
        self.assertIn("可用内存 1.1 GB", body)
        self.assertNotIn("system_available_mb: 1133.4%", body)
        self.assertLess(body.index("gpt-demo"), body.index("gpt-fast"))

    def test_periodic_report_surfaces_failed_channels_before_healthy_channels(self):
        channels = [
            ChannelObservation(1, "Healthy", True, 1.2, "ok"),
            ChannelObservation(2, "Broken", False, 0.5, "upstream 502"),
        ]

        subject, body = newapi_monitor.build_periodic_report(
            channels,
            [],
            {},
            {},
            slow_seconds=60,
            period_seconds=3600,
            generated_at=1_750_000_000,
        )

        self.assertEqual("周期报告 · 存在异常", subject)
        self.assertIn("异常渠道 1 个", body)
        self.assertLess(body.index("Broken"), body.index("Healthy"))

    def test_periodic_report_uses_runtime_resource_thresholds(self):
        subject, body = newapi_monitor.build_periodic_report(
            [],
            [],
            {"system_memory": 75.0},
            {},
            slow_seconds=60,
            period_seconds=3600,
            resource_thresholds={"system_memory": 70.0},
            generated_at=1_750_000_000,
        )

        self.assertEqual("周期报告 · 存在异常", subject)
        self.assertIn("1 项资源超过阈值", body)
        self.assertIn("🔴 内存 75.0%", body)

    def test_wecom_application_fetches_token_and_sends_text_message(self):
        notifier = WeComAppNotifier("ww-test", 1000004, "app-secret", "@all", "", "")

        with mock.patch(
            "newapi_monitor.request_json",
            side_effect=[
                {"errcode": 0, "access_token": "tenant-token", "expires_in": 7200},
                {"errcode": 0, "errmsg": "ok"},
            ],
        ) as request_json:
            notifier.send("渠道异常", "上游返回 502")

        token_call, message_call = request_json.call_args_list
        self.assertIn("/cgi-bin/gettoken?", token_call.args[0])
        self.assertNotIn("app-secret", message_call.args[0])
        self.assertEqual("@all", message_call.args[1]["touser"])
        self.assertEqual(1000004, message_call.args[1]["agentid"])
        self.assertIn("渠道异常", message_call.args[1]["text"]["content"])

    def test_wecom_webhook_uses_fixed_text_payload(self):
        notifier = WeComWebhookNotifier(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key"
        )

        with mock.patch(
            "newapi_monitor.request_json",
            return_value={"errcode": 0, "errmsg": "ok"},
        ) as request_json:
            notifier.send("资源告警", "内存超过 85%")

        payload = request_json.call_args.args[1]
        self.assertEqual("text", payload["msgtype"])
        self.assertIn("内存超过 85%", payload["text"]["content"])

    def test_feishu_webhook_adds_documented_signature(self):
        notifier = FeishuWebhookNotifier(
            "https://open.feishu.cn/open-apis/bot/v2/hook/test-hook",
            "sign-secret",
        )
        expected = base64.b64encode(
            hmac.new(b"1700000000\nsign-secret", digestmod=hashlib.sha256).digest()
        ).decode("ascii")

        with mock.patch("newapi_monitor.time.time", return_value=1700000000), mock.patch(
            "newapi_monitor.request_json",
            return_value={"code": 0, "msg": "success"},
        ) as request_json:
            notifier.send("恢复通知", "渠道已经恢复")

        payload = request_json.call_args.args[1]
        self.assertEqual("1700000000", payload["timestamp"])
        self.assertEqual(expected, payload["sign"])
        self.assertEqual("text", payload["msg_type"])

    def test_dispatcher_keeps_successful_delivery_when_an_optional_channel_fails(self):
        successful = mock.Mock(name="wecom_app")
        successful.name = "wecom_app"
        failed = mock.Mock(name="email")
        failed.name = "email"
        failed.send.side_effect = RuntimeError("smtp unavailable")
        dispatcher = NotificationDispatcher.__new__(NotificationDispatcher)
        dispatcher.senders = [successful, failed]

        result = dispatcher.send("测试通知", "正文")

        self.assertEqual(["wecom_app"], result["succeeded"])
        self.assertEqual(["email"], result["failed"])
        successful.send.assert_called_once_with("测试通知", "正文")

    def test_dispatcher_can_test_a_configured_but_disabled_channel(self):
        config = Config.from_values(
            {
                "email_enabled": False,
                "wecom_webhook_enabled": False,
                "wecom_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
            }
        )

        with mock.patch.object(WeComWebhookNotifier, "send") as send:
            dispatcher = NotificationDispatcher(config, test_channel="wecom_webhook")
            result = dispatcher.send("测试告警", "验证通知链路", channel="wecom_webhook")

        self.assertEqual(["wecom_webhook"], result["succeeded"])
        send.assert_called_once_with("测试告警", "验证通知链路")


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

        worker = worker_class(
            client,
            store,
            lambda _channels: None,
            lambda success, error: results.append((success, error)),
            stale_after_seconds=60,
        )
        worker.run(TwoIterations(), 1)

        self.assertEqual([(True, ""), (True, "")], results)
        self.assertEqual(2, client.get_channels.call_count)
        self.assertEqual(2, store.record_collector_result.call_count)
        store.record_collector_result.assert_called_with(
            "channel_sync", True, "", stale_after_seconds=60
        )
        store.connection.close.assert_called_once_with()

    def test_freshness_write_failure_does_not_stop_channel_sync(self):
        worker_class = getattr(newapi_monitor, "ChannelSyncWorker", None)
        client = mock.Mock()
        client.get_channels.return_value = [{"id": 1, "name": "enabled", "status": 1}]
        store = mock.Mock()
        store.record_collector_result.side_effect = [sqlite3.OperationalError("database is locked"), None]

        class TwoIterations:
            def __init__(self):
                self.waits = 0

            def is_set(self):
                return False

            def wait(self, _seconds):
                self.waits += 1
                return self.waits >= 2

        worker = worker_class(client, store, lambda _channels: None, stale_after_seconds=60)
        worker.run(TwoIterations(), 1)

        self.assertEqual(2, client.get_channels.call_count)
        self.assertEqual(2, store.record_collector_result.call_count)


class ChannelProbeWorkerTests(unittest.TestCase):
    def test_empty_enabled_channel_set_is_a_successful_probe_cycle(self):
        worker_class = getattr(newapi_monitor, "ChannelProbeWorker", None)
        store = mock.Mock()
        store.get_json.side_effect = lambda key, default=None: default
        config = mock.Mock(
            real_probe_rules={},
            channel_settings={},
            channel_slow_seconds=60,
            channel_failure_threshold=2,
            channel_recovery_threshold=2,
            channel_probe_concurrency=3,
        )
        published = []
        worker = worker_class(
            config,
            mock.Mock(),
            None,
            store,
            mock.Mock(),
            lambda: [{"id": 1, "name": "disabled", "status": 2}],
            published.append,
            stale_after_seconds=900,
        )

        self.assertEqual([], worker.check_once())

        self.assertEqual([[]], published)
        store.record_collector_result.assert_called_once_with(
            "channel_probe", True, "", stale_after_seconds=900
        )

    def test_checks_channels_concurrently_and_records_freshness(self):
        worker_class = getattr(newapi_monitor, "ChannelProbeWorker", None)
        self.assertIsNotNone(worker_class)
        active = 0
        max_active = 0
        lock = threading.Lock()

        class ProbeClient:
            def probe(self, _rule):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.03)
                with lock:
                    active -= 1
                return newapi_monitor.RealProbeResult(True, 0.03, 10.0, "ok")

        store = mock.Mock()
        store.get_json.side_effect = lambda key, default=None: default
        config = mock.Mock(
            real_probe_rules={
                1: newapi_monitor.RealProbeRule("gpt", "/v1/responses", "responses", "1", 1),
                2: newapi_monitor.RealProbeRule("gpt", "/v1/responses", "responses", "1", 1),
                3: newapi_monitor.RealProbeRule("gpt", "/v1/responses", "responses", "1", 1),
            },
            channel_settings={},
            channel_slow_seconds=60,
            channel_failure_threshold=2,
            channel_recovery_threshold=2,
            channel_probe_concurrency=3,
        )
        worker = worker_class(
            config,
            mock.Mock(),
            ProbeClient(),
            store,
            mock.Mock(),
            lambda: [
                {"id": 1, "name": "one", "status": 1},
                {"id": 2, "name": "two", "status": 1},
                {"id": 3, "name": "three", "status": 1},
            ],
            lambda _items: None,
            stale_after_seconds=900,
        )

        observations = worker.check_once()

        self.assertEqual(3, len(observations))
        self.assertGreaterEqual(max_active, 2)
        store.record_collector_result.assert_called_once_with(
            "channel_probe", True, "", stale_after_seconds=900
        )


class OpenAIStatusTests(unittest.TestCase):
    @staticmethod
    def _summary(component_status: str = "operational", incidents: list[dict] | None = None) -> dict:
        return {
            "page": {
                "name": "OpenAI",
                "url": "https://status.openai.com/",
                "updated_at": "2026-07-22T02:50:19Z",
            },
            "status": {"indicator": "major", "description": "Partial System Outage"},
            "components": [
                {
                    "id": "responses-id",
                    "name": "Responses",
                    "status": component_status,
                    "updated_at": "2026-07-22T02:49:00Z",
                },
                {
                    "id": "unselected-id",
                    "name": "Sora",
                    "status": "operational",
                    "updated_at": "2026-07-22T02:49:00Z",
                },
            ],
            "incidents": incidents or [],
        }

    @staticmethod
    def _incidents(status: str = "investigating", body: str = "We are investigating.") -> dict:
        return {
            "incidents": [
                {
                    "id": "incident-1",
                    "name": "Responses API errors",
                    "status": status,
                    "impact": "major",
                    "created_at": "2026-07-22T02:40:00Z",
                    "updated_at": "2026-07-22T02:50:00Z",
                    "resolved_at": "2026-07-22T03:10:00Z" if status == "resolved" else None,
                    "incident_updates": [
                        {
                            "id": "update-1",
                            "status": status,
                            "body": body,
                            "created_at": "2026-07-22T02:50:00Z",
                            "updated_at": "2026-07-22T02:50:00Z",
                        }
                    ],
                }
            ]
        }

    def test_client_normalizes_official_status_without_configurable_urls(self):
        client_class = getattr(newapi_monitor, "OpenAIStatusClient", None)
        self.assertIsNotNone(client_class)
        requested_urls: list[str] = []

        def fetch_json(url: str, timeout_seconds: int) -> dict:
            requested_urls.append(url)
            self.assertEqual(7, timeout_seconds)
            self.assertTrue(url.endswith("summary.json"))
            return self._summary(incidents=self._incidents()["incidents"])

        snapshot = client_class(fetch_json=fetch_json).fetch(timeout_seconds=7, observed_at=1234)

        self.assertEqual(
            [
                "https://status.openai.com/api/v2/summary.json",
            ],
            requested_urls,
        )
        self.assertEqual("openai", snapshot["provider"])
        self.assertEqual(1234, snapshot["observed_at"])
        self.assertEqual("major", snapshot["indicator"])
        self.assertEqual("Responses", snapshot["components"][0]["name"])
        self.assertEqual("We are investigating.", snapshot["incidents"][0]["latest_update"]["body"])

    def test_client_accepts_summary_without_incidents_key(self):
        payload = self._summary()
        payload.pop("incidents")

        snapshot = newapi_monitor.OpenAIStatusClient(
            fetch_json=lambda _url, _timeout: payload
        ).fetch(observed_at=1234)

        self.assertEqual([], snapshot["incidents"])

    def test_tracker_deduplicates_updates_and_resolves_official_incident(self):
        tracker_class = getattr(newapi_monitor, "OpenAIStatusTracker", None)
        client_class = getattr(newapi_monitor, "OpenAIStatusClient", None)
        self.assertIsNotNone(tracker_class)
        self.assertIsNotNone(client_class)

        payloads = {"summary": self._summary(incidents=self._incidents()["incidents"])}
        client = client_class(
            fetch_json=lambda _url, _timeout: payloads["summary"]
        )
        tracker = tracker_class(
            component_ids=("responses-id",),
            min_impact="major",
            failure_threshold=2,
            recovery_threshold=2,
        )
        snapshot = client.fetch(observed_at=100)

        events = tracker.evaluate(snapshot, {"total": 2, "failed": 1, "healthy": 1, "unknown": 0})
        self.assertEqual(1, len(events))
        self.assertEqual("provider:openai:incident:incident-1", events[0].key)
        self.assertEqual("critical", events[0].severity)
        self.assertEqual("openai", events[0].metadata["provider"])
        self.assertIn("本地影响：1/2", events[0].body)
        self.assertEqual([], tracker.evaluate(snapshot))

        payloads["summary"]["incidents"] = self._incidents(
            "identified", "The issue has been identified."
        )["incidents"]
        update = tracker.evaluate(client.fetch(observed_at=120))
        self.assertEqual(1, len(update))
        self.assertEqual("identified", update[0].metadata["phase"])

        payloads["summary"]["incidents"] = self._incidents(
            "resolved", "The issue has been resolved."
        )["incidents"]
        recovered = tracker.evaluate(client.fetch(observed_at=180))
        self.assertEqual(1, len(recovered))
        self.assertTrue(recovered[0].recovery)
        self.assertEqual("provider:openai:incident:incident-1", recovered[0].key)

    def test_component_requires_consecutive_samples_and_recovers_without_incident(self):
        tracker_class = getattr(newapi_monitor, "OpenAIStatusTracker", None)
        client_class = getattr(newapi_monitor, "OpenAIStatusClient", None)
        self.assertIsNotNone(tracker_class)
        self.assertIsNotNone(client_class)
        summary = self._summary("degraded_performance")
        client = client_class(fetch_json=lambda _url, _timeout: summary)
        tracker = tracker_class(
            component_ids=("responses-id",),
            min_impact="major",
            failure_threshold=2,
            recovery_threshold=2,
        )

        self.assertEqual([], tracker.evaluate(client.fetch(observed_at=100)))
        events = tracker.evaluate(client.fetch(observed_at=160))
        self.assertEqual(1, len(events))
        self.assertEqual("provider:openai:component:responses-id", events[0].key)

        summary["components"][0]["status"] = "operational"
        self.assertEqual([], tracker.evaluate(client.fetch(observed_at=220)))
        recovered = tracker.evaluate(client.fetch(observed_at=280))
        self.assertEqual(1, len(recovered))
        self.assertTrue(recovered[0].recovery)

    def test_state_store_correlates_only_openai_model_channels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(str(Path(temp_dir) / "monitor.db"))
            store.upsert_channels(
                [
                    {"id": 1, "name": "openai", "type": 1, "status": 1, "models": "gpt-5.4", "group": "default", "base_url": ""},
                    {"id": 2, "name": "claude", "type": 14, "status": 1, "models": "claude-opus-4-8", "group": "default", "base_url": ""},
                ],
                now=100,
            )
            store.insert_channel_observations(
                [
                    ChannelObservation(1, "openai", False, 2, "timeout"),
                    ChannelObservation(2, "claude", False, 2, "timeout"),
                ],
                observed_at=110,
            )

            impact = store.provider_local_impact("openai", now=120, stale_after_seconds=60)

            self.assertEqual({"total": 1, "healthy": 0, "failed": 1, "unknown": 0}, impact)
            store.connection.close()

    def test_state_store_keeps_only_latest_provider_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(str(Path(temp_dir) / "monitor.db"))
            store.record_provider_status("openai", {"indicator": "none"}, observed_at=100)
            store.record_provider_status("openai", {"indicator": "minor"}, observed_at=200)

            rows = store.connection.execute(
                "SELECT observed_at, payload_json FROM provider_status_samples WHERE provider = 'openai'"
            ).fetchall()

            self.assertEqual(1, len(rows))
            self.assertEqual(200, int(rows[0]["observed_at"]))
            self.assertEqual("minor", json.loads(rows[0]["payload_json"])["indicator"])
            store.connection.close()

    def test_alert_delivery_can_be_enabled_after_incident_was_recorded(self):
        tracker = newapi_monitor.OpenAIStatusTracker(
            component_ids=("responses-id",),
            min_impact="major",
            alerts_enabled=False,
        )
        snapshot = self._summary(incidents=self._incidents()["incidents"])

        recorded = tracker.evaluate(snapshot)
        self.assertEqual(1, len(recorded))
        self.assertFalse(recorded[0].notify)
        self.assertEqual([], tracker.evaluate(snapshot))

        tracker.alerts_enabled = True
        notifiable = tracker.evaluate(snapshot)
        self.assertEqual(1, len(notifiable))
        self.assertTrue(notifiable[0].notify)

    def test_component_scope_change_resolves_recorded_incident_without_notification(self):
        tracker = newapi_monitor.OpenAIStatusTracker(
            component_ids=("responses-id",),
            min_impact="critical",
            failure_threshold=1,
        )
        snapshot = self._summary("degraded_performance")
        failed = tracker.evaluate(snapshot)
        self.assertEqual(1, len(failed))
        self.assertFalse(failed[0].recovery)

        tracker.component_ids = {"__none__"}
        recovered = tracker.evaluate(snapshot)

        self.assertEqual(1, len(recovered))
        self.assertTrue(recovered[0].recovery)
        self.assertFalse(recovered[0].notify)

    def test_state_store_can_resolve_provider_incidents_when_monitoring_is_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(str(Path(temp_dir) / "monitor.db"))
            store.record_alert_events(
                [
                    AlertEvent(
                        "provider_incident",
                        "OpenAI incident",
                        "upstream outage",
                        key="provider:openai:incident:one",
                        severity="critical",
                    )
                ],
                now=100,
            )

            resolved = store.resolve_open_incidents(
                "provider:openai:",
                "monitoring disabled",
                now=200,
            )
            row = store.connection.execute(
                "SELECT status, resolved_at, resolution_body FROM incidents WHERE incident_key = ?",
                ("provider:openai:incident:one",),
            ).fetchone()

            self.assertEqual(1, resolved)
            self.assertEqual("resolved", row["status"])
            self.assertEqual(200, int(row["resolved_at"]))
            self.assertEqual("monitoring disabled", row["resolution_body"])
            store.connection.close()

    def test_monitor_collection_persists_snapshot_state_and_events(self):
        app = object.__new__(newapi_monitor.MonitorApp)
        app.config = mock.Mock(openai_status_timeout_seconds=8, channel_interval_seconds=300)
        app.openai_status_client = mock.Mock()
        app.openai_status_client.fetch.return_value = {
            "provider": "openai",
            "observed_at": 500,
            "indicator": "major",
            "description": "Partial System Outage",
            "components": [],
            "incidents": [],
        }
        app.openai_status_tracker = mock.Mock(state={"incidents": {}, "components": {}})
        app.openai_status_tracker.evaluate.return_value = [AlertEvent("provider_incident", "title", "body")]
        app.store = mock.Mock()
        app.store.provider_local_impact.return_value = {"total": 1, "healthy": 1, "failed": 0, "unknown": 0}
        app._send_events = mock.Mock()

        snapshot = app.collect_openai_status()

        self.assertEqual(500, snapshot["observed_at"])
        app.openai_status_client.fetch.assert_called_once_with(timeout_seconds=8)
        app._send_events.assert_called_once()
        app.store.record_provider_status.assert_called_once_with("openai", snapshot, observed_at=500)
        app.store.set_json.assert_called_once_with("openai_status_state", app.openai_status_tracker.state)

if __name__ == "__main__":
    unittest.main()
