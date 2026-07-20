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
        store.record_alert_events(
            [AlertEvent("channel_failed", "channel failed", "timeout", key="channel:2", severity="critical")],
            now=1_230,
        )
        store.connection.close()
        self.repository = DashboardRepository(self.db_path, slow_seconds=60)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_summary_reports_current_health_and_slow_requests(self):
        summary = self.repository.summary(now=1_300, request_window_seconds=600)

        self.assertEqual(2, summary["channels"]["total"])
        self.assertEqual(1, summary["channels"]["healthy"])
        self.assertEqual(1, summary["channels"]["failed"])
        self.assertEqual(0, summary["channels"]["unknown"])
        self.assertEqual(2, summary["requests"]["total"])
        self.assertEqual(1, summary["requests"]["slow"])
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


if __name__ == "__main__":
    unittest.main()
