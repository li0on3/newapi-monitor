import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import dashboard_app


class FakeSettings:
    def __init__(self):
        self.audits = []

    def runtime_values(self):
        return {
            "new_api_base_url": "https://newapi.example",
            "console_enabled": True,
            "console_min_role": "viewer",
            "console_overview_enabled": True,
            "console_analytics_enabled": True,
            "console_keys_enabled": True,
            "console_logs_enabled": True,
            "console_default_days": 7,
            "console_write_attempts_per_minute": 30,
            "console_reveal_attempts_per_minute": 6,
        }

    def record_audit(self, actor, action, target, before, after, remote_addr=""):
        self.audits.append({
            "actor": actor,
            "action": action,
            "target": target,
            "before": before,
            "after": after,
            "remote_addr": remote_addr,
        })


class FakeConsoleClient:
    def __init__(self):
        self.calls = []

    def status(self, session, user_id):
        self.calls.append(("status", session, user_id))
        return {"version": "0.9.0", "system_name": "New API", "quota_per_unit": 500000}

    def self_info(self, session, user_id):
        self.calls.append(("self", session, user_id))
        return {"id": user_id, "username": "alice", "quota": 500000}

    def models(self, session, user_id):
        self.calls.append(("models", session, user_id))
        return ["gpt-5.4"]

    def list_tokens(self, session, user_id, page, page_size):
        self.calls.append(("tokens", session, user_id, page, page_size))
        return {"page": 1, "page_size": 5, "total": 0, "items": []}

    def log_stat(self, session, user_id, source_role, **filters):
        self.calls.append(("stat", session, user_id, source_role, filters))
        return {"quota": 0, "rpm": 0, "tpm": 0}

    def analytics(self, session, user_id, source_role, start, end, username=""):
        self.calls.append(("analytics", session, user_id, source_role, start, end, username))
        return {
            "start_timestamp": start,
            "end_timestamp": end,
            "scope": "global" if source_role >= 10 else "self",
            "series": [],
            "flow": [],
            "stat": {"quota": 0, "rpm": 0, "tpm": 0},
            "summary": {"requests": 0, "quota": 0, "tokens": 0, "models": 0},
        }

    def list_logs(self, session, user_id, source_role, page, page_size, **filters):
        self.calls.append(("logs", session, user_id, source_role, page, page_size, filters))
        return {"page": page, "page_size": page_size, "total": 1, "items": []}

    def reveal_token(self, session, user_id, token_id):
        self.calls.append(("reveal", session, user_id, token_id))
        return "sk-one-time-secret"


def request(path: str, method: str = "GET") -> Request:
    return Request({
        "type": "http",
        "method": method,
        "scheme": "https",
        "path": path,
        "query_string": b"",
        "headers": [(b"cookie", b"session=newapi-session")],
        "client": ("127.0.0.1", 12345),
        "server": ("monitor.example", 443),
    })


class ConsoleEndpointTests(unittest.TestCase):
    def setUp(self):
        self.settings = FakeSettings()
        self.client = FakeConsoleClient()
        self.user = {
            "username": "alice",
            "role": "viewer",
            "source": "newapi",
            "source_role": 1,
            "user_id": 9,
        }
        dashboard_app.console_reveal_limiter.buckets.clear()

    def test_overview_uses_the_current_newapi_session_for_every_source_call(self):
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.get_console_overview(request("/api/console/overview"), self.user)

        self.assertEqual("self", result["scope"])
        self.assertEqual(1, result["models"]["total"])
        self.assertTrue(self.client.calls)
        for call in self.client.calls:
            self.assertEqual("newapi-session", call[1])
            self.assertEqual(9, call[2])

    def test_overview_does_not_fetch_key_metadata_when_key_page_is_disabled(self):
        values = self.settings.runtime_values()
        values["console_keys_enabled"] = False
        self.settings.runtime_values = lambda: values
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.get_console_overview(request("/api/console/overview"), self.user)

        self.assertEqual([], result["keys"]["items"])
        self.assertFalse(any(call[0] == "tokens" for call in self.client.calls))

    def test_emergency_admin_cannot_use_customer_console(self):
        emergency = {**self.user, "role": "admin", "source": "emergency"}
        with patch.object(dashboard_app.runtime, "settings", self.settings):
            with self.assertRaises(HTTPException) as raised:
                dashboard_app.get_console_overview(request("/api/console/overview"), emergency)

        self.assertEqual(403, raised.exception.status_code)

    def test_reveal_returns_secret_once_but_audits_only_the_action(self):
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.reveal_console_key(
                7, request("/api/console/keys/7/reveal", "POST"), self.user
            )

        self.assertEqual("sk-one-time-secret", result["key"])
        self.assertEqual(1, len(self.settings.audits))
        self.assertEqual("console.token.reveal", self.settings.audits[0]["action"])
        self.assertEqual({"revealed": True}, self.settings.audits[0]["after"])
        self.assertNotIn("sk-one-time-secret", repr(self.settings.audits))

    def test_analytics_uses_newapi_quota_unit_for_human_readable_totals(self):
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.get_console_analytics(
                request("/api/console/analytics"), self.user, 100, 200, ""
            )

        self.assertEqual(500000, result["quota_per_unit"])

    def test_request_id_log_search_does_not_claim_unsupported_aggregate_metrics(self):
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.get_console_logs(
                request("/api/console/logs"),
                self.user,
                page=1,
                page_size=20,
                log_type=0,
                start_timestamp=100,
                end_timestamp=200,
                username="",
                token_name="",
                model_name="",
                channel=0,
                group="",
                request_id="req-1",
                upstream_request_id="",
            )

        self.assertIsNone(result["stat"])
        self.assertFalse(result["stat_filters_complete"])
        self.assertEqual(500000, result["quota_per_unit"])
        self.assertFalse(any(call[0] == "stat" for call in self.client.calls))


if __name__ == "__main__":
    unittest.main()
