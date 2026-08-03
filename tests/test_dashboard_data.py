import tempfile
import unittest
from pathlib import Path

from dashboard_data import DashboardRepository
from newapi_monitor import AlertEvent, ChannelObservation, StateStore


class DashboardRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "monitor.db")
        store = StateStore(self.db_path)
        store.upsert_channels(
            [
                {
                    "id": 1,
                    "name": "healthy-channel",
                    "type": 1,
                    "status": 1,
                    "models": "gpt-a,gpt-b",
                    "group": "default",
                    "base_url": "https://healthy.example",
                },
                {
                    "id": 2,
                    "name": "failed-channel",
                    "type": 1,
                    "status": 1,
                    "models": "gpt-c",
                    "group": "default",
                    "base_url": "https://failed.example",
                },
                {
                    "id": 3,
                    "name": "manually-disabled-channel",
                    "type": 1,
                    "status": 2,
                    "models": "gpt-d",
                    "group": "default",
                    "base_url": "https://manual-disabled.example",
                },
                {
                    "id": 4,
                    "name": "automatically-disabled-channel",
                    "type": 1,
                    "status": 3,
                    "models": "gpt-e",
                    "group": "default",
                    "base_url": "https://auto-disabled.example",
                },
            ],
            now=1_000,
        )
        store.insert_channel_observations(
            [
                ChannelObservation(1, "healthy-channel", True, 1.5, "", "real", 800),
                ChannelObservation(2, "failed-channel", False, 65, "timeout", "builtin", None),
            ],
            observed_at=1_100,
        )
        store.ingest_logs(
            [
                {
                    "request_id": "request-1",
                    "created_at": 1_200,
                    "channel": 1,
                    "channel_name": "healthy-channel",
                    "model_name": "gpt-a",
                    "use_time": 10,
                    "other": '{"frt": 1000}',
                    "username": "alice",
                    "token_name": "production",
                    "token_id": 7,
                    "is_stream": True,
                    "group": "default",
                },
                {
                    "request_id": "request-2",
                    "created_at": 1_210,
                    "channel": 1,
                    "channel_name": "healthy-channel",
                    "model_name": "gpt-a",
                    "use_time": 70,
                    "other": '{"frt": 61000}',
                    "username": "bob",
                    "token_name": "production",
                    "token_id": 8,
                    "is_stream": False,
                    "group": "default",
                },
            ]
        )
        store.insert_resource_sample(
            {
                "system_cpu": 31,
                "system_memory": 42,
                "system_disk": 53,
                "system_available_mb": 1024,
                "system_swap": 4,
            },
            {"containers": {"new-api": {"status": "running", "memory_mb": 300}}},
            created_at=1_220,
        )
        store.record_collector_result("logs", True, stale_after_seconds=120, now=1_210)
        store.record_collector_result("resources", True, stale_after_seconds=120, now=1_220)
        store.record_alert_events(
            [AlertEvent("channel_failed", "channel failed", "timeout", key="channel:2", severity="critical")],
            now=1_230,
        )
        store.connection.close()
        self.repository = DashboardRepository(self.db_path, slow_seconds=60)

    def test_logs_support_arbitrary_time_ranges(self):
        repository = DashboardRepository(self.db_path)
        result = repository.logs(start_timestamp=1_205, end_timestamp=1_220)
        self.assertEqual(1, result["total"])
        self.assertEqual("request-2", result["items"][0]["request_id"])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_summary_reports_current_health_and_slow_requests(self):
        summary = self.repository.summary(now=1_300, request_window_seconds=600)

        self.assertEqual(2, summary["channels"]["total"])
        self.assertEqual(1, summary["channels"]["healthy"])
        self.assertEqual(0, summary["channels"]["delayed"])
        self.assertEqual(1, summary["channels"]["failed"])
        self.assertEqual(0, summary["channels"]["unknown"])
        self.assertEqual(2, summary["requests"]["total"])
        self.assertEqual(1, summary["requests"]["slow"])
        self.assertEqual(60.0, summary["requests"]["slow_after_seconds"])
        self.assertEqual("ok", summary["requests"]["collector_status"])
        self.assertEqual("ok", summary["resources"]["collector_status"])
        self.assertEqual(1, summary["incidents"]["open"])
        self.assertEqual(31, summary["resources"]["system_cpu"])

    def test_summary_scope_excludes_hidden_channel_health_logs_and_incidents(self):
        store = StateStore(self.db_path)
        store.ingest_logs(
            [
                {
                    "request_id": "request-hidden",
                    "created_at": 1_220,
                    "channel": 2,
                    "channel_name": "failed-channel",
                    "model_name": "gpt-c",
                    "use_time": 80,
                    "other": '{"frt": 70000}',
                }
            ]
        )
        store.record_alert_events(
            [
                AlertEvent(
                    "latency_high",
                    "hidden latency",
                    "slow",
                    key="latency:2:gpt-c",
                    severity="critical",
                ),
                AlertEvent(
                    "resource_high",
                    "memory high",
                    "high",
                    key="resource:system_memory",
                    severity="warning",
                ),
            ],
            now=1_250,
        )
        store.connection.close()

        summary = self.repository.summary(
            now=1_300,
            request_window_seconds=600,
            channel_ids={1},
        )

        self.assertEqual(1, summary["channels"]["total"])
        self.assertEqual(1, summary["channels"]["healthy"])
        self.assertEqual(2, summary["requests"]["total"])
        self.assertEqual(1, summary["requests"]["slow"])
        self.assertEqual(1, summary["incidents"]["open"])
        self.assertEqual(0, summary["incidents"]["critical"])

    def test_summary_can_exclude_operational_incidents_from_viewer_scope(self):
        store = StateStore(self.db_path)
        store.record_alert_events(
            [
                AlertEvent(
                    "resource_high",
                    "memory high",
                    "high",
                    key="resource:system_memory",
                    severity="warning",
                )
            ],
            now=1_250,
        )
        store.connection.close()

        summary = self.repository.summary(
            now=1_300,
            request_window_seconds=600,
            channel_ids={1},
            include_operational_incidents=False,
        )

        self.assertEqual(0, summary["incidents"]["open"])
        self.assertEqual(0, summary["incidents"]["critical"])
        self.assertEqual(0, summary["incidents"]["warning"])

    def test_summary_never_mixes_provider_incidents_into_local_overall_health(self):
        before = self.repository.summary(now=1_700_000_100)
        store = StateStore(self.db_path)
        store.record_alert_events(
            [
                AlertEvent(
                    "provider_incident",
                    "OpenAI incident",
                    "official outage",
                    key="provider:openai:incident:one",
                    severity="critical",
                )
            ],
            now=1_700_000_000,
        )
        store.connection.close()

        summary = self.repository.summary(now=1_700_000_100)

        self.assertEqual(before["incidents"], summary["incidents"])

    def test_summary_marks_stale_channel_observations_unknown(self):
        repository = DashboardRepository(
            self.db_path,
            slow_seconds=60,
            channel_stale_seconds=120,
        )

        summary = repository.summary(now=1_300, request_window_seconds=600)

        self.assertEqual(2, summary["channels"]["total"])
        self.assertEqual(0, summary["channels"]["healthy"])
        self.assertEqual(0, summary["channels"]["failed"])
        self.assertEqual(2, summary["channels"]["unknown"])

    def test_summary_does_not_present_a_stale_channel_snapshot_as_current(self):
        store = StateStore(self.db_path)
        store.record_collector_result(
            "channel_sync",
            False,
            "Unauthorized, invalid access token",
            stale_after_seconds=60,
            now=1_200,
        )
        store.connection.close()

        summary = self.repository.summary(now=1_300, request_window_seconds=600)

        self.assertEqual("stale", summary["channel_sync"]["status"])
        self.assertEqual(0, summary["channels"]["healthy"])
        self.assertEqual(0, summary["channels"]["failed"])
        self.assertEqual(2, summary["channels"]["unknown"])

    def test_channels_include_latest_observation_and_history(self):
        channels = self.repository.channels(now=1_300, history_limit=60)

        self.assertEqual(2, len(channels))
        self.assertEqual([1, 2], sorted(item["channel_id"] for item in channels))
        self.assertTrue(all("base_url" not in item for item in channels))
        healthy = next(item for item in channels if item["channel_id"] == 1)
        self.assertTrue(healthy["latest"]["success"])
        self.assertEqual("real", healthy["latest"]["source"])
        self.assertEqual(["gpt-a", "gpt-b"], healthy["models"])
        self.assertEqual(1, len(healthy["history"]))
        self.assertEqual(900, healthy["stale_after_seconds"])
        self.assertEqual("real", healthy["availability"]["source"])
        self.assertEqual(1_100, healthy["availability"]["coverage_start_at"])
        self.assertEqual(1_100, healthy["availability"]["coverage_end_at"])

    def test_channel_availability_coverage_matches_the_requested_range(self):
        store = StateStore(self.db_path)
        store.insert_channel_observations(
            [ChannelObservation(1, "healthy-channel", True, 1.2, "", "real", 500)],
            observed_at=900,
        )
        store.connection.close()

        channels = self.repository.channels(
            now=1_300,
            availability_start_timestamp=1_000,
            availability_end_timestamp=1_300,
        )

        healthy = next(item for item in channels if item["channel_id"] == 1)
        self.assertEqual(1, healthy["availability"]["total"])
        self.assertEqual(1_100, healthy["availability"]["coverage_start_at"])
        self.assertEqual(1_100, healthy["availability"]["coverage_end_at"])

    def test_summary_counts_slow_successful_probes_as_delayed_not_healthy(self):
        store = StateStore(self.db_path)
        store.insert_channel_observations(
            [ChannelObservation(1, "healthy-channel", True, 31, "", "real", 500)],
            observed_at=1_290,
        )
        store.connection.close()

        repository = DashboardRepository(
            self.db_path,
            slow_seconds=60,
            channel_slow_seconds=30,
        )
        summary = repository.summary(now=1_300, request_window_seconds=600)

        self.assertEqual(0, summary["channels"]["healthy"])
        self.assertEqual(1, summary["channels"]["delayed"])
        self.assertEqual(1, summary["channels"]["failed"])

    def test_channels_do_not_mix_old_builtin_failures_into_real_probe_history(self):
        store = StateStore(self.db_path)
        store.insert_channel_observations(
            [ChannelObservation(2, "failed-channel", True, 2.5, "", "real", 900)],
            observed_at=1_250,
        )
        store.connection.close()

        channels = self.repository.channels(now=1_300, history_limit=60)

        channel = next(item for item in channels if item["channel_id"] == 2)
        self.assertEqual("real", channel["latest"]["source"])
        self.assertEqual(["real"], [item["source"] for item in channel["history"]])
        self.assertEqual(1, channel["availability"]["total"])
        self.assertEqual(100.0, channel["availability"]["percentage"])

    def test_channel_snapshot_removes_channels_missing_from_latest_sync(self):
        store = StateStore(self.db_path)
        store.upsert_channels(
            [
                {
                    "id": 2,
                    "name": "failed-channel",
                    "type": 1,
                    "status": 1,
                    "models": "gpt-c",
                    "group": "default",
                    "base_url": "https://failed.example",
                }
            ],
            now=1_400,
        )
        store.connection.close()

        channels = self.repository.channels(now=1_400)

        self.assertEqual([2], [item["channel_id"] for item in channels])

    def test_log_filters_use_total_or_first_response_latency(self):
        payload = self.repository.logs(limit=20, slow_only=True, slow_seconds=60)

        self.assertEqual(1, payload["total"])
        self.assertEqual("request-2", payload["items"][0]["request_id"])

    def test_logs_expose_monitor_collection_bounds(self):
        payload = self.repository.logs(limit=20)

        self.assertEqual(1_200, payload["collection_started_at"])
        self.assertEqual(1_210, payload["latest_sample_at"])
        self.assertEqual(1_200, payload["retained_from_at"])
        self.assertEqual(1_210, payload["retained_until_at"])
        self.assertEqual(60.0, payload["slow_after_seconds"])

    def test_resource_history_keeps_latest_container_state_without_repeating_it(self):
        store = StateStore(self.db_path)
        store.insert_resource_sample(
            {
                "system_cpu": 35,
                "system_memory": 45,
                "system_disk": 55,
                "system_available_mb": 900,
                "system_swap": 5,
            },
            {"containers": {"new-api": {"status": "restarting", "memory_mb": 320}}},
            created_at=1_280,
        )
        store.connection.close()

        payload = self.repository.resources(
            now=1_300,
            start_timestamp=1_200,
            end_timestamp=1_300,
            limit=10,
        )

        self.assertGreaterEqual(len(payload["samples"]), 2)
        self.assertTrue(all(not sample["containers"] for sample in payload["samples"][:-1]))
        self.assertEqual(
            "restarting",
            payload["samples"][-1]["containers"]["new-api"]["status"],
        )

    def test_resource_history_separates_exact_latest_values_from_bucket_averages(self):
        store = StateStore(self.db_path)
        store.insert_resource_sample(
            {
                "system_cpu": 91,
                "system_memory": 61,
                "system_disk": 71,
                "system_available_mb": 800,
                "system_swap": 7,
            },
            {"containers": {"new-api": {"status": "running", "memory_mb": 330}}},
            created_at=1_280,
        )
        store.connection.close()

        payload = self.repository.resources(
            now=1_300,
            start_timestamp=1_200,
            end_timestamp=1_300,
            limit=1,
        )

        self.assertEqual(2, payload["sample_count"])
        self.assertEqual("ok", payload["collector_status"])
        self.assertEqual(1_280, payload["latest"]["created_at"])
        self.assertEqual(91, payload["latest"]["system_cpu"])
        self.assertNotEqual(payload["latest"]["system_cpu"], payload["samples"][0]["system_cpu"])
        self.assertEqual(31, payload["summary"]["system_cpu"]["min"])
        self.assertEqual(61, payload["summary"]["system_cpu"]["average"])
        self.assertEqual(91, payload["summary"]["system_cpu"]["max"])

    def test_resource_history_does_not_bucket_future_empty_time(self):
        payload = self.repository.resources(
            now=1_300,
            start_timestamp=1_200,
            end_timestamp=2_000,
            limit=10,
        )

        self.assertEqual(1_300, payload["requested_end"])
        self.assertLessEqual(payload["bucket_seconds"], 15)

    def test_resource_coverage_measures_collected_samples_not_only_time_span(self):
        payload = self.repository.resources(
            now=1_300,
            start_timestamp=1_200,
            end_timestamp=1_300,
            sampling_interval_seconds=10,
        )

        self.assertEqual("expected_sample_count", payload["coverage_basis"])
        self.assertEqual(11, payload["expected_sample_count"])
        self.assertEqual(1, payload["sample_count"])
        self.assertEqual(round(1 / 11, 4), payload["coverage_ratio"])
        self.assertEqual(payload["coverage_ratio"], payload["sample_coverage_ratio"])

    def test_incident_query_supports_search_facets_and_resolution_context(self):
        store = StateStore(self.db_path)
        store.record_alert_events(
            [
                AlertEvent(
                    "resource_high",
                    "内存告警",
                    "当前值：92%\n阈值：85%",
                    key="resource:system_memory",
                    severity="warning",
                )
            ],
            now=1_240,
        )
        store.record_alert_events(
            [
                AlertEvent(
                    "resource_recovered",
                    "内存恢复",
                    "当前值：62%\n恢复阈值：68%",
                    key="resource:system_memory",
                    severity="info",
                    recovery=True,
                )
            ],
            now=1_300,
        )
        store.connection.close()

        payload = self.repository.incidents(
            status="resolved",
            category="resource",
            query="内存",
            now=1_320,
            limit=20,
        )

        self.assertEqual(1, payload["total"])
        self.assertEqual(1, payload["summary"]["resolved"])
        self.assertEqual(60, payload["summary"]["average_resolution_seconds"])
        item = payload["items"][0]
        self.assertEqual("resource", item["category"])
        self.assertEqual("当前值：92%\n阈值：85%", item["body"])
        self.assertEqual("当前值：62%\n恢复阈值：68%", item["resolution_body"])
        self.assertEqual(60, item["duration_seconds"])

    def test_provider_status_and_incident_metadata_are_available_to_dashboard(self):
        store = StateStore(self.db_path)
        store.record_provider_status(
            "openai",
            {
                "provider": "openai",
                "indicator": "major",
                "description": "Partial System Outage",
                "components": [{"id": "responses-id", "name": "Responses", "status": "degraded_performance"}],
                "incidents": [{"id": "incident-1", "name": "Responses API errors", "status": "investigating"}],
            },
            observed_at=1_250,
        )
        store.record_alert_events(
            [
                AlertEvent(
                    "provider_incident",
                    "OpenAI 官方状态异常：Responses API errors",
                    "官方阶段：Investigating",
                    key="provider:openai:incident:incident-1",
                    severity="critical",
                    metadata={
                        "provider": "openai",
                        "official_id": "incident-1",
                        "source_url": "https://status.openai.com/",
                        "phase": "investigating",
                    },
                )
            ],
            now=1_250,
        )
        store.connection.close()

        provider = self.repository.provider_status("openai", now=1_300, stale_after_seconds=180)
        incidents = self.repository.incidents(category="provider", now=1_300)

        self.assertEqual("major", provider["indicator"])
        self.assertFalse(provider["stale"])
        self.assertEqual(1, provider["active_incident_count"])
        self.assertEqual(1, incidents["total"])
        self.assertEqual("provider", incidents["items"][0]["category"])
        self.assertEqual("incident-1", incidents["items"][0]["metadata"]["official_id"])


if __name__ == "__main__":
    unittest.main()
