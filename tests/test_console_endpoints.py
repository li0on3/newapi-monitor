import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from starlette.routing import Match

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

    def list_all_tokens(self, session, user_id):
        self.calls.append(("all_tokens", session, user_id))
        return [{"id": 7, "name": "personal"}, {"id": 8, "name": "customer"}]

    def self_flow(self, session, user_id, start, end):
        self.calls.append(("self_flow", session, user_id, start, end))
        return [{
            "token_id": 8,
            "token_name": "customer",
            "use_group": "default",
            "model_name": "gpt-5.4",
            "count": 3,
            "quota": 50,
            "token_used": 20,
        }]

    def log_stat(self, session, user_id, source_role, **filters):
        self.calls.append(("stat", session, user_id, source_role, filters))
        return {"quota": 0, "rpm": 0, "tpm": 0}

    def analytics(self, session, user_id, source_role, start, end, username="", scope="auto"):
        self.calls.append(("analytics", session, user_id, source_role, start, end, username, scope))
        return {
            "start_timestamp": start,
            "end_timestamp": end,
            "scope": "self" if scope == "self" or source_role < 10 else "global",
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


class FakeKeyGroupStore:
    def __init__(self):
        self.assignments = []

    def list_groups(self, user_id):
        return [{
            "id": 3,
            "owner_user_id": user_id,
            "name": "客户项目",
            "color": "blue",
            "key_count": 1,
        }]

    def membership_map(self, user_id):
        return {8: [3]}

    def assign_tokens(self, user_id, token_ids, group_ids):
        self.assignments.append((user_id, token_ids, group_ids))
        return len(token_ids)

    def set_group_members(self, user_id, group_id, token_ids):
        self.assignments.append((user_id, group_id, token_ids))
        return len(token_ids)


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
        self.key_groups = FakeKeyGroupStore()
        self.user = {
            "username": "alice",
            "role": "viewer",
            "source": "newapi",
            "source_role": 1,
            "user_id": 9,
        }
        dashboard_app.console_reveal_limiter.buckets.clear()

    def test_key_group_assignment_route_is_not_shadowed_by_group_id_route(self):
        scope = request("/api/console/key-groups/assignments", "PUT").scope

        matching_path = next(
            route.path
            for route in dashboard_app.app.routes
            if route.matches(scope)[0] is Match.FULL
        )

        self.assertEqual("/api/console/key-groups/assignments", matching_path)

    def test_key_group_member_route_is_not_shadowed_by_group_id_route(self):
        scope = request("/api/console/key-groups/3/members", "PUT").scope

        matching_path = next(
            route.path
            for route in dashboard_app.app.routes
            if route.matches(scope)[0] is Match.FULL
        )

        self.assertEqual("/api/console/key-groups/{group_id}/members", matching_path)

    def test_key_group_workspace_uses_current_account_tokens_and_self_usage(self):
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ), patch("dashboard_app.key_group_store", return_value=self.key_groups), patch(
            "dashboard_app.time.time", return_value=1_000_000
        ):
            result = dashboard_app.get_console_key_groups(
                request("/api/console/key-groups"), self.user, days=7
            )

        self.assertEqual(3, result["groups"][0]["id"])
        self.assertEqual(3, result["groups"][0]["usage"]["requests"])
        self.assertEqual(7, result["days"])
        self.assertIn(("all_tokens", "newapi-session", 9), self.client.calls)
        self.assertTrue(any(call[0] == "self_flow" for call in self.client.calls))

    def test_key_assignment_rejects_token_ids_not_owned_by_current_user(self):
        payload = dashboard_app.ConsoleKeyGroupAssignmentPayload(token_ids=[7, 99], group_ids=[3])
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ), patch("dashboard_app.key_group_store", return_value=self.key_groups):
            with self.assertRaises(HTTPException) as raised:
                dashboard_app.assign_console_key_group(
                    payload,
                    request("/api/console/key-groups/assignments", "PUT"),
                    self.user,
                )

        self.assertEqual(404, raised.exception.status_code)
        self.assertEqual([], self.key_groups.assignments)

    def test_key_assignment_accepts_multiple_monitor_groups(self):
        payload = dashboard_app.ConsoleKeyGroupAssignmentPayload(token_ids=[7, 8], group_ids=[3, 4, 3])
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ), patch("dashboard_app.key_group_store", return_value=self.key_groups):
            result = dashboard_app.assign_console_key_group(
                payload,
                request("/api/console/key-groups/assignments", "PUT"),
                self.user,
            )

        self.assertEqual({"assigned": 2}, result)
        self.assertEqual([(9, [7, 8], [3, 4])], self.key_groups.assignments)

    def test_group_member_editor_replaces_only_the_selected_group_members(self):
        payload = dashboard_app.ConsoleKeyGroupMembersPayload(token_ids=[8, 7, 8])
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ), patch("dashboard_app.key_group_store", return_value=self.key_groups):
            result = dashboard_app.update_console_key_group_members(
                3,
                payload,
                request("/api/console/key-groups/3/members", "PUT"),
                self.user,
            )

        self.assertEqual({"changed": 2}, result)
        self.assertEqual([(9, 3, [8, 7])], self.key_groups.assignments)
        self.assertEqual("console.key-group.members", self.settings.audits[0]["action"])

    def test_group_member_editor_rejects_keys_not_owned_by_current_user(self):
        payload = dashboard_app.ConsoleKeyGroupMembersPayload(token_ids=[7, 99])
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ), patch("dashboard_app.key_group_store", return_value=self.key_groups):
            with self.assertRaises(HTTPException) as raised:
                dashboard_app.update_console_key_group_members(
                    3,
                    payload,
                    request("/api/console/key-groups/3/members", "PUT"),
                    self.user,
                )

        self.assertEqual(404, raised.exception.status_code)
        self.assertEqual([], self.key_groups.assignments)

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
        stat_call = next(call for call in self.client.calls if call[0] == "stat")
        self.assertEqual(2, stat_call[-1]["type"])

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

    def test_admin_overview_keeps_account_metrics_in_self_scope(self):
        admin = {**self.user, "role": "admin", "source_role": 10}
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.get_console_overview(request("/api/console/overview"), admin)

        self.assertEqual("self", result["scope"])
        stat_call = next(call for call in self.client.calls if call[0] == "stat")
        self.assertEqual(1, stat_call[3])

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

    def test_admin_can_select_self_analytics_scope(self):
        admin = {**self.user, "role": "admin", "source_role": 10}
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.get_console_analytics(
                request("/api/console/analytics"), admin, 100, 200, "", False, "self"
            )

        self.assertEqual("self", result["scope"])
        self.assertEqual("self", self.client.calls[0][-1])

    def test_regular_user_cannot_select_global_analytics_scope(self):
        with patch.object(dashboard_app.runtime, "settings", self.settings):
            with self.assertRaises(HTTPException) as raised:
                dashboard_app.get_console_analytics(
                    request("/api/console/analytics"), self.user, 100, 200, "", False, "global"
                )

        self.assertEqual(403, raised.exception.status_code)

    def test_admin_self_scope_cannot_filter_another_username(self):
        admin = {**self.user, "role": "admin", "source_role": 10}
        with patch.object(dashboard_app.runtime, "settings", self.settings):
            with self.assertRaises(HTTPException) as raised:
                dashboard_app.get_console_analytics(
                    request("/api/console/analytics"), admin, 100, 200, "alice", False, "self"
                )

        self.assertEqual(422, raised.exception.status_code)

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

    def test_non_consumption_log_filters_do_not_show_consumption_aggregates(self):
        with patch.object(dashboard_app.runtime, "settings", self.settings), patch(
            "dashboard_app.console_client", return_value=self.client
        ):
            result = dashboard_app.get_console_logs(
                request("/api/console/logs"),
                self.user,
                page=1,
                page_size=20,
                log_type=1,
                start_timestamp=100,
                end_timestamp=200,
                username="",
                token_name="",
                model_name="",
                channel=0,
                group="",
                request_id="",
                upstream_request_id="",
            )

        self.assertIsNone(result["stat"])
        self.assertFalse(result["stat_filters_complete"])
        self.assertEqual("consume_only", result["stat_scope"])
        self.assertEqual("non_consume_type", result["stat_unavailable_reason"])
        self.assertFalse(any(call[0] == "stat" for call in self.client.calls))


if __name__ == "__main__":
    unittest.main()
