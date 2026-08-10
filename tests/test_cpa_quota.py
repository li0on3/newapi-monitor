import io
import json
import tempfile
import unittest
from pathlib import Path

from dashboard_http import open_without_redirects
from monitoring_core.cpa_quota import (
    CpaQuotaAlertTracker,
    CpaQuotaClient,
    CpaQuotaError,
    aggregate_activity,
    forecast_quota,
    parse_antigravity_weekly_windows,
    parse_codex_weekly_windows,
)
from monitoring_core.state_store import StateStore


class CpaQuotaTests(unittest.TestCase):
    def test_client_rejects_redirects_and_bounds_management_responses(self):
        client = CpaQuotaClient("http://cpa.example:8317", "management-key")
        self.assertIs(open_without_redirects, client.opener)

        class FakeResponse:
            def __init__(self):
                self.body = io.BytesIO(json.dumps({"files": ["x" * 2048]}).encode("utf-8"))

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, size=-1):
                return self.body.read(size)

        bounded = CpaQuotaClient(
            "http://cpa.example:8317",
            "management-key",
            max_response_bytes=1024,
            opener=lambda _request, timeout: FakeResponse(),
        )
        with self.assertRaisesRegex(CpaQuotaError, "size limit"):
            bounded.credentials()

    def test_duplicate_account_labels_get_distinct_irreversible_ids(self):
        payload = {
            "files": [
                {"provider": "codex", "email": "same@example.com", "auth_index": "auth-a"},
                {"provider": "codex", "email": "same@example.com", "auth_index": "auth-b"},
            ]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, size=-1):
                return json.dumps(payload).encode("utf-8")

        credentials = CpaQuotaClient(
            "http://cpa.example:8317",
            "management-key",
            opener=lambda _request, timeout: FakeResponse(),
        ).credentials()

        self.assertEqual(2, len(credentials))
        self.assertEqual(2, len({credential.credential_id for credential in credentials}))
        self.assertTrue(all(credential.account_label == "sa***@example.com" for credential in credentials))

    def test_codex_parser_selects_weekly_windows_and_preserves_additional_limits(self):
        payload = {
            "plan_type": "pro",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 12,
                    "limit_window_seconds": 18_000,
                    "reset_at": 1_700_010_000,
                },
                "secondary_window": {
                    "used_percent": 33,
                    "limit_window_seconds": 604_800,
                    "reset_at": 1_700_600_000,
                },
            },
            "additional_rate_limits": [
                {
                    "limit_name": "GPT-5.3-Codex-Spark",
                    "metered_feature": "codex_bengalfox",
                    "rate_limit": {
                        "primary_window": {
                            "used_percent": 5,
                            "limit_window_seconds": 604_800,
                            "reset_at": 1_700_700_000,
                        }
                    },
                }
            ],
        }

        windows = parse_codex_weekly_windows(
            payload,
            credential_id="credential-a",
            account_label="li***@example.com",
            observed_at=1_700_000_000,
        )

        self.assertEqual(2, len(windows))
        self.assertEqual("base:secondary", windows[0]["window_id"])
        self.assertEqual(67.0, windows[0]["remaining_percent"])
        self.assertEqual("Pro", windows[0]["plan_label"])
        self.assertEqual("additional:codex-bengalfox:primary", windows[1]["window_id"])
        self.assertEqual(95.0, windows[1]["remaining_percent"])

    def test_antigravity_parser_keeps_only_weekly_buckets(self):
        payload = {
            "groups": [
                {
                    "displayName": "Gemini Models",
                    "buckets": [
                        {
                            "bucketId": "gemini-weekly",
                            "displayName": "Weekly Limit Remaining",
                            "window": "weekly",
                            "resetTime": "2026-08-17T03:54:51Z",
                            "remainingFraction": 0.42,
                        },
                        {
                            "bucketId": "gemini-5h",
                            "window": "5h",
                            "remainingFraction": 0.8,
                        },
                    ],
                },
                {
                    "displayName": "Claude and GPT models",
                    "buckets": [
                        {
                            "bucketId": "3p-weekly",
                            "displayName": "Weekly Limit Remaining",
                            "window": "weekly",
                            "resetTime": "2026-08-17T03:54:51Z",
                            "remainingFraction": 1,
                        }
                    ],
                },
            ]
        }

        windows = parse_antigravity_weekly_windows(
            payload,
            credential_id="credential-b",
            account_label="sn***@example.com",
            observed_at=1_700_000_000,
        )

        self.assertEqual(["gemini-weekly", "3p-weekly"], [item["window_id"] for item in windows])
        self.assertEqual(42.0, windows[0]["remaining_percent"])
        self.assertEqual("Gemini Models", windows[0]["window_name"])
        self.assertEqual(100.0, windows[1]["remaining_percent"])

    def test_activity_aggregation_uses_nearby_one_and_six_hour_windows(self):
        stats = {
            "series": [
                {"hour": "2026-08-10T04:00:00Z", "requests": 4, "total_tokens": 400},
                {"hour": "2026-08-10T08:30:00Z", "requests": 6, "total_tokens": 600},
                {"hour": "2026-08-10T09:30:00Z", "requests": 10, "total_tokens": 1_000},
            ]
        }

        activity = aggregate_activity(stats, now=1_786_356_000)  # 2026-08-10 10:00:00 UTC

        self.assertEqual(10, activity["requests_1h"])
        self.assertEqual(20, activity["requests_6h"])
        self.assertEqual(1_000, activity["tokens_1h"])
        self.assertEqual(2_000, activity["tokens_6h"])
        self.assertGreater(activity["pressure_ratio"], 1)

    def test_forecast_requires_history_then_projects_with_observed_burn(self):
        collecting = forecast_quota(
            [{"observed_at": 1_700_000_000, "remaining_percent": 80, "reset_at": 1_700_100_000}],
            threshold_percent=20,
        )
        self.assertEqual("collecting", collecting["status"])

        projected = forecast_quota(
            [
                {"observed_at": 1_700_000_000, "remaining_percent": 80, "reset_at": 1_700_100_000},
                {"observed_at": 1_700_007_200, "remaining_percent": 70, "reset_at": 1_700_100_000},
            ],
            threshold_percent=20,
            activity={"pressure_ratio": 1.5, "requests_1h": 10, "requests_6h": 30},
        )

        self.assertEqual("projected", projected["status"])
        self.assertGreater(projected["burn_percent_per_hour"], 0)
        self.assertGreater(projected["seconds_to_threshold"], 0)
        self.assertGreater(projected["seconds_to_exhaustion"], projected["seconds_to_threshold"])
        self.assertFalse(projected["reset_before_threshold"])

    def test_alert_tracker_uses_twenty_percent_threshold_and_recovery_hysteresis(self):
        tracker = CpaQuotaAlertTracker({}, threshold_percent=20, recovery_percent=25, repeat_seconds=21_600)
        sample = {
            "credential_id": "credential-a",
            "provider": "codex",
            "account_label": "li***@example.com",
            "window_id": "base:primary",
            "window_name": "Main weekly quota",
            "remaining_percent": 20.0,
            "used_percent": 80.0,
            "reset_at": 1_700_100_000,
        }

        first = tracker.evaluate(sample, {"status": "collecting"}, now=1_700_000_000)
        repeated_too_soon = tracker.evaluate(
            {**sample, "remaining_percent": 19.0, "used_percent": 81.0},
            {"status": "collecting"},
            now=1_700_001_000,
        )
        recovered = tracker.evaluate(
            {**sample, "remaining_percent": 26.0, "used_percent": 74.0},
            {"status": "stable"},
            now=1_700_002_000,
        )

        self.assertEqual(["cpa_quota_low"], [event.kind for event in first])
        self.assertEqual([], repeated_too_soon)
        self.assertEqual(["cpa_quota_recovered"], [event.kind for event in recovered])
        self.assertTrue(first[0].key.startswith("cpa:quota:"))

    def test_state_store_persists_quota_history_without_secret_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(str(Path(temporary_directory) / "monitor.db"))
            try:
                store.record_cpa_quota_account(
                    "credential-a",
                    provider="codex",
                    account_label="li***@example.com",
                    success=True,
                    observed_at=1_700_000_000,
                )
                store.insert_cpa_quota_samples(
                    [
                        {
                            "credential_id": "credential-a",
                            "provider": "codex",
                            "account_label": "li***@example.com",
                            "plan_label": "Pro",
                            "window_id": "base:primary",
                            "window_name": "Main weekly quota",
                            "remaining_percent": 68,
                            "used_percent": 32,
                            "reset_at": 1_700_600_000,
                            "window_seconds": 604_800,
                            "observed_at": 1_700_000_000,
                            "activity": {"requests_1h": 4, "tokens_1h": 400},
                        }
                    ]
                )

                snapshot = store.cpa_quota_snapshot(now=1_700_000_100)
            finally:
                store.connection.close()

        self.assertEqual(1, snapshot["account_count"])
        self.assertEqual("li***@example.com", snapshot["accounts"][0]["account_label"])
        self.assertEqual(68.0, snapshot["accounts"][0]["windows"][0]["remaining_percent"])
        self.assertNotIn("auth_index", str(snapshot))

    def test_removed_cpa_credentials_leave_the_active_dashboard_scope(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(str(Path(temporary_directory) / "monitor.db"))
            try:
                store.record_cpa_quota_account("credential-a", "codex", "a***@example.com", True, observed_at=100)
                store.record_cpa_quota_account("credential-b", "codex", "b***@example.com", True, observed_at=100)
                store.reconcile_cpa_quota_accounts({"credential-a"})
                snapshot = store.cpa_quota_snapshot(now=200)
            finally:
                store.connection.close()

        self.assertEqual(["credential-a"], [account["credential_id"] for account in snapshot["accounts"]])

    def test_disabled_cpa_collector_is_removed_from_platform_health(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = StateStore(str(Path(temporary_directory) / "monitor.db"))
            try:
                store.record_collector_result("cpa_quota", False, "offline", stale_after_seconds=300, now=100)
                self.assertIn("cpa_quota", store.collector_health(now=500))
                store.remove_collector("cpa_quota")
                self.assertNotIn("cpa_quota", store.collector_health(now=500))
            finally:
                store.connection.close()


if __name__ == "__main__":
    unittest.main()
