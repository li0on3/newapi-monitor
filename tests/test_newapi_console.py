import io
import json
import unittest
import urllib.error
import urllib.request

from dashboard_http import NoRedirectHandler
from dashboard_newapi_console import NewAPIConsoleClient, NewAPIConsoleError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


class RecordingOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class NewAPIConsoleClientTests(unittest.TestCase):
    def test_list_all_tokens_paginates_and_self_flow_never_uses_admin_scope(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": {"page": 1, "page_size": 2, "total": 3, "items": [
                {"id": 7, "name": "one", "key": "sk-1", "status": 1, "created_time": 10,
                 "accessed_time": 20, "expired_time": -1, "remain_quota": 100, "used_quota": 0,
                 "unlimited_quota": False, "model_limits_enabled": False, "model_limits": "",
                 "allow_ips": "", "group": "default", "cross_group_retry": False},
                {"id": 8, "name": "two", "key": "sk-2", "status": 1, "created_time": 10,
                 "accessed_time": 20, "expired_time": -1, "remain_quota": 100, "used_quota": 0,
                 "unlimited_quota": False, "model_limits_enabled": False, "model_limits": "",
                 "allow_ips": "", "group": "default", "cross_group_retry": False},
            ]}}),
            FakeResponse({"success": True, "data": {"page": 2, "page_size": 2, "total": 3, "items": [
                {"id": 9, "name": "three", "key": "sk-3", "status": 1, "created_time": 10,
                 "accessed_time": 20, "expired_time": -1, "remain_quota": 100, "used_quota": 0,
                 "unlimited_quota": False, "model_limits_enabled": False, "model_limits": "",
                 "allow_ips": "", "group": "default", "cross_group_retry": False},
            ]}}),
            FakeResponse({"success": True, "data": [
                {"token_id": 7, "token_name": "one", "use_group": "default", "model_name": "gpt-5.4", "count": 2, "quota": 50, "token_used": 20},
            ]}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        tokens = client.list_all_tokens("session", 9, page_size=2)
        flow = client.self_flow("session", 9, 100, 200)

        self.assertEqual([7, 8, 9], [item["id"] for item in tokens])
        self.assertEqual(7, flow[0]["token_id"])
        urls = [request.full_url for request, _ in opener.requests]
        self.assertEqual("https://newapi.example/api/token/?p=1&page_size=2", urls[0])
        self.assertEqual("https://newapi.example/api/token/?p=2&page_size=2", urls[1])
        self.assertIn("/api/data/flow/self?", urls[2])
        self.assertNotIn("username=", urls[2])

    def test_list_all_tokens_rejects_incomplete_or_duplicate_pages(self):
        token = {
            "id": 7, "name": "one", "key": "sk-1", "status": 1, "created_time": 10,
            "accessed_time": 20, "expired_time": -1, "remain_quota": 100, "used_quota": 0,
            "unlimited_quota": False, "model_limits_enabled": False, "model_limits": "",
            "allow_ips": "", "group": "default", "cross_group_retry": False,
        }
        incomplete = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {
                    "page": 1, "page_size": 1, "total": 2, "items": [token],
                }}),
                FakeResponse({"success": True, "data": {
                    "page": 2, "page_size": 1, "total": 2, "items": [],
                }}),
            ]),
        )
        duplicate = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {
                    "page": 1, "page_size": 1, "total": 2, "items": [token],
                }}),
                FakeResponse({"success": True, "data": {
                    "page": 2, "page_size": 1, "total": 2, "items": [token],
                }}),
            ]),
        )

        with self.assertRaisesRegex(NewAPIConsoleError, "invalid pagination data"):
            incomplete.list_all_tokens("session", 9, page_size=1)
        with self.assertRaisesRegex(NewAPIConsoleError, "token pagination is inconsistent"):
            duplicate.list_all_tokens("session", 9, page_size=1)

    def test_self_flow_splits_ranges_that_exceed_newapi_self_endpoint_limit(self):
        max_range = 30 * 86400
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": {
                "page": 1, "page_size": 1, "total": 2,
                "items": [{"created_at": max_range + 100, "type": 2}],
            }}),
            FakeResponse({"success": True, "data": {
                "page": 2, "page_size": 1, "total": 2,
                "items": [{"created_at": 100, "type": 2}],
            }}),
            FakeResponse({"success": True, "data": [
                {"token_id": 7, "use_group": "default", "model_name": "gpt-a", "count": 2, "quota": 20, "token_used": 10},
            ]}),
            FakeResponse({"success": True, "data": [
                {"token_id": 7, "use_group": "default", "model_name": "gpt-b", "count": 3, "quota": 30, "token_used": 15},
            ]}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        flow = client.self_flow("session", 9, 1, max_range + 200)

        self.assertEqual(["gpt-a", "gpt-b"], [item["model_name"] for item in flow])
        urls = [request.full_url for request, _ in opener.requests]
        flow_urls = [url for url in urls if "/api/data/flow/self?" in url]
        self.assertEqual(2, len(flow_urls))
        self.assertIn("start_timestamp=1", flow_urls[0])
        self.assertIn(f"end_timestamp={1 + max_range}", flow_urls[0])
        self.assertIn(f"start_timestamp={2 + max_range}", flow_urls[1])
        self.assertIn(f"end_timestamp={max_range + 200}", flow_urls[1])

    def test_long_self_analytics_chunks_projection_and_reuses_log_total(self):
        max_range = 30 * 86400
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": {
                "page": 1, "page_size": 1, "total": 2,
                "items": [{"created_at": max_range + 100, "type": 2}],
            }}),
            FakeResponse({"success": True, "data": {
                "page": 2, "page_size": 1, "total": 2,
                "items": [{"created_at": 100, "type": 2}],
            }}),
            FakeResponse({"success": True, "data": [
                {"created_at": 100, "model_name": "gpt-a", "count": 1, "quota": 10, "token_used": 5},
            ]}),
            FakeResponse({"success": True, "data": [
                {"created_at": max_range + 100, "model_name": "gpt-b", "count": 1, "quota": 20, "token_used": 10},
            ]}),
            FakeResponse({"success": True, "data": [
                {"token_id": 7, "use_group": "default", "model_name": "gpt-a", "count": 1, "quota": 10, "token_used": 5},
            ]}),
            FakeResponse({"success": True, "data": [
                {"token_id": 7, "use_group": "default", "model_name": "gpt-b", "count": 1, "quota": 20, "token_used": 10},
            ]}),
            FakeResponse({"success": True, "data": {"quota": 30, "rpm": 1, "tpm": 10}}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        result = client.analytics("session", 9, 1, 1, max_range + 200)

        self.assertEqual(2, result["summary"]["requests"])
        self.assertEqual(30, result["summary"]["quota"])
        self.assertEqual(7, len(opener.requests))
        self.assertEqual(2, sum("/api/data/self?" in request.full_url for request, _ in opener.requests))
        self.assertEqual(2, sum("/api/data/flow/self?" in request.full_url for request, _ in opener.requests))
        self.assertEqual(2, sum("/api/log/self?" in request.full_url for request, _ in opener.requests))

    def test_redirects_are_never_followed_with_the_session_cookie(self):
        handler = NoRedirectHandler()

        redirected = handler.redirect_request(
            urllib.request.Request(
                "https://newapi.example/api/user/self",
                headers={"Cookie": "session=sensitive", "New-Api-User": "9"},
            ),
            None,
            302,
            "Found",
            {},
            "https://attacker.example/collect",
        )

        self.assertIsNone(redirected)

    def test_token_list_forwards_only_the_verified_session_identity(self):
        opener = RecordingOpener([
            FakeResponse({
                "success": True,
                "data": {
                    "page": 2,
                    "page_size": 20,
                    "total": 21,
                    "items": [{
                        "id": 7,
                        "name": "Codex",
                        "key": "sk-a**********wxyz",
                        "status": 1,
                        "remain_quota": 500000,
                        "used_quota": 250000,
                        "unlimited_quota": False,
                        "expired_time": -1,
                        "model_limits_enabled": True,
                        "model_limits": "gpt-5.4,gpt-5.5",
                        "allow_ips": "1.1.1.1",
                        "group": "default",
                        "cross_group_retry": False,
                        "created_time": 100,
                        "accessed_time": 200,
                    }],
                },
            })
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        result = client.list_tokens("session-value", 42, page=2, page_size=20)

        request, timeout = opener.requests[0]
        self.assertEqual("https://newapi.example/api/token/?p=2&page_size=20", request.full_url)
        self.assertEqual("session=session-value", request.get_header("Cookie"))
        self.assertEqual("42", request.get_header("New-api-user"))
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(12, timeout)
        self.assertEqual(21, result["total"])
        self.assertEqual("sk-a**********wxyz", result["items"][0]["masked_key"])
        self.assertNotIn("key", result["items"][0])

    def test_paginated_console_data_fails_closed_when_totals_or_items_are_invalid(self):
        missing_total = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {"page": 1, "page_size": 20, "items": []}}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid pagination field: total"):
            missing_total.list_tokens("session", 9)

        invalid_items = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {"page": 1, "page_size": 20, "total": 1, "items": {}}}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid pagination data"):
            invalid_items.list_logs("session", 9, 1)

    def test_paginated_console_data_rejects_mismatched_page_metadata(self):
        wrong_page = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {
                    "page": 2, "page_size": 20, "total": 1, "items": [],
                }}),
            ]),
        )
        too_many_items = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {
                    "page": 1, "page_size": 1, "total": 2, "items": [{}, {}],
                }}),
            ]),
        )
        impossible_total = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {
                    "page": 2, "page_size": 20, "total": 20, "items": [{}],
                }}),
            ]),
        )

        with self.assertRaisesRegex(NewAPIConsoleError, "invalid pagination data"):
            wrong_page.list_tokens("session", 9, page=1, page_size=20)
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid pagination data"):
            too_many_items.list_logs("session", 9, 1, page=1, page_size=1)
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid pagination data"):
            impossible_total.list_logs("session", 9, 1, page=2, page_size=20)

    def test_unlimited_token_preserves_signed_remaining_quota(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": {
                "page": 1,
                "page_size": 20,
                "total": 1,
                "items": [{
                    "id": 7,
                    "name": "Unlimited",
                    "key": "sk-a**********wxyz",
                    "status": 1,
                    "created_time": 100,
                    "accessed_time": 200,
                    "expired_time": -1,
                    "remain_quota": -500000,
                    "used_quota": 500000,
                    "unlimited_quota": True,
                    "model_limits_enabled": False,
                    "model_limits": "",
                    "allow_ips": "",
                    "group": "default",
                    "cross_group_retry": False,
                }],
            }}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        result = client.list_tokens("session", 9)

        self.assertEqual(-500000, result["items"][0]["remain_quota"])

    def test_analytics_uses_self_endpoints_for_users_and_admin_endpoints_for_admins(self):
        user_opener = RecordingOpener([
            FakeResponse({"success": True, "data": [{"created_at": 100, "model_name": "gpt-5.4", "count": 2, "quota": 50, "token_used": 20}]}),
            FakeResponse({"success": True, "data": [{"token_id": 3, "use_group": "default", "model_name": "gpt-5.4", "count": 2, "quota": 50, "token_used": 20}]}),
            FakeResponse({"success": True, "data": {"quota": 50, "rpm": 2, "tpm": 20}}),
            FakeResponse({"success": True, "data": {"page": 1, "page_size": 1, "total": 2, "items": [{}]}}),
        ])
        user_client = NewAPIConsoleClient("https://newapi.example", opener=user_opener)

        user_result = user_client.analytics("session", 9, 1, 100, 200)

        self.assertTrue(all("/self" in request.full_url for request, _ in user_opener.requests))
        self.assertEqual(2, user_result["summary"]["requests"])
        self.assertEqual(50, user_result["summary"]["quota"])

        admin_opener = RecordingOpener([
            FakeResponse({"success": True, "data": []}),
            FakeResponse({"success": True, "data": []}),
            FakeResponse({"success": True, "data": {"quota": 0, "rpm": 0, "tpm": 0}}),
            FakeResponse({"success": True, "data": {"page": 1, "page_size": 1, "total": 0, "items": []}}),
        ])
        admin_client = NewAPIConsoleClient("https://newapi.example", opener=admin_opener)

        admin_client.analytics("session", 10, 10, 100, 200, username="alice")

        urls = [request.full_url for request, _ in admin_opener.requests]
        self.assertIn("/api/data/?", urls[0])
        self.assertIn("username=alice", urls[0])
        self.assertIn("/api/data/flow?", urls[1])
        self.assertIn("/api/log/stat?", urls[2])
        self.assertIn("type=2", urls[2])
        self.assertIn("/api/log/?", urls[3])
        self.assertIn("page_size=1", urls[3])

    def test_analytics_uses_log_quota_as_total_and_reports_projection_gap(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": [
                {"created_at": 100, "model_name": "gpt-5.4", "count": 2, "quota": 50, "token_used": 20},
            ]}),
            FakeResponse({"success": True, "data": [
                {"token_id": 3, "use_group": "default", "model_name": "gpt-5.4", "count": 2, "quota": 40, "token_used": 20},
            ]}),
            FakeResponse({"success": True, "data": {"quota": 70, "rpm": 2, "tpm": 20}}),
            FakeResponse({"success": True, "data": {"page": 1, "page_size": 1, "total": 5, "items": [{}]}}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        result = client.analytics("session", 9, 1, 100, 200)

        self.assertEqual(5, result["summary"]["requests"])
        self.assertEqual(2, result["summary"]["attributed_requests"])
        self.assertEqual(3, result["summary"]["unattributed_requests"])
        self.assertEqual(70, result["summary"]["quota"])
        self.assertEqual(50, result["summary"]["attributed_quota"])
        self.assertEqual(20, result["summary"]["unattributed_quota"])
        self.assertEqual(40, result["summary"]["flow_quota"])
        self.assertTrue(result["reconciliation"]["requests_exact"])
        self.assertTrue(result["reconciliation"]["quota_exact"])
        self.assertEqual("hourly_projection", result["reconciliation"]["attribution_source"])

    def test_analytics_reports_when_hourly_projection_exceeds_live_log_totals(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": [
                {"created_at": 100, "model_name": "gpt-5.4", "count": 5, "quota": 100, "token_used": 40},
            ]}),
            FakeResponse({"success": True, "data": [
                {"token_id": 3, "use_group": "default", "model_name": "gpt-5.4", "count": 4, "quota": 90, "token_used": 35},
            ]}),
            FakeResponse({"success": True, "data": {"quota": 70, "rpm": 0, "tpm": 0}}),
            FakeResponse({"success": True, "data": {"page": 1, "page_size": 1, "total": 2, "items": [{}]}}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        result = client.analytics("session", 9, 1, 100, 200)

        self.assertEqual(-3, result["summary"]["model_request_delta"])
        self.assertEqual(-2, result["summary"]["flow_request_delta"])
        self.assertEqual(-30, result["summary"]["model_quota_delta"])
        self.assertEqual(-20, result["summary"]["flow_quota_delta"])

    def test_analytics_rejects_malformed_totals_and_projection_metrics(self):
        invalid_total = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": []}),
                FakeResponse({"success": True, "data": []}),
                FakeResponse({"success": True, "data": {"quota": 0, "rpm": 0, "tpm": 0}}),
                FakeResponse({"success": True, "data": {
                    "page": 1, "page_size": 1, "total": "not-a-number", "items": [],
                }}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid pagination field: total"):
            invalid_total.analytics("session", 9, 1, 100, 200)

        invalid_projection = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": [
                    {"created_at": 100, "model_name": "gpt-5.4", "count": 1.5, "quota": 10, "token_used": 5},
                ]}),
                FakeResponse({"success": True, "data": []}),
                FakeResponse({"success": True, "data": {"quota": 10, "rpm": 0, "tpm": 0}}),
                FakeResponse({"success": True, "data": {
                    "page": 1, "page_size": 1, "total": 1, "items": [{}],
                }}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid analytics field: count"):
            invalid_projection.analytics("session", 9, 1, 100, 200)

    def test_log_statistics_require_all_authoritative_integer_fields(self):
        missing_quota = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {"rpm": 1, "tpm": 20}}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid log statistics field: quota"):
            missing_quota.log_stat("session", 9, 1, type=2)

        fractional_rate = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {"quota": 10, "rpm": 1.5, "tpm": 20}}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid log statistics field: rpm"):
            fractional_rate.log_stat("session", 9, 1, type=2)

    def test_self_info_rejects_identity_mismatch_or_malformed_account_totals(self):
        mismatched = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {
                    "id": 10, "username": "alice", "role": 1, "status": 1,
                    "quota": 10, "used_quota": 20, "request_count": 3,
                }}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "account identity mismatch"):
            mismatched.self_info("session", 9)

        malformed = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([
                FakeResponse({"success": True, "data": {
                    "id": 9, "username": "alice", "role": 1, "status": 1,
                    "quota": "unknown", "used_quota": 20, "request_count": 3,
                }}),
            ]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid account field: quota"):
            malformed.self_info("session", 9)

    def test_admin_can_query_their_own_analytics_scope(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": []}),
            FakeResponse({"success": True, "data": []}),
            FakeResponse({"success": True, "data": {"quota": 0, "rpm": 0, "tpm": 0}}),
            FakeResponse({"success": True, "data": {"page": 1, "page_size": 1, "total": 0, "items": []}}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        result = client.analytics("session", 10, 10, 100, 200, scope="self")

        self.assertEqual("self", result["scope"])
        self.assertTrue(all("/self" in request.full_url for request, _ in opener.requests))
        self.assertTrue(all("username=" not in request.full_url for request, _ in opener.requests))

    def test_analytics_rejects_invalid_or_unauthorized_scope_before_request(self):
        opener = RecordingOpener([])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        with self.assertRaises(NewAPIConsoleError) as unauthorized:
            client.analytics("session", 9, 1, 100, 200, scope="global")
        with self.assertRaises(NewAPIConsoleError) as invalid:
            client.analytics("session", 9, 10, 100, 200, scope="invalid")

        self.assertEqual(403, unauthorized.exception.status_code)
        self.assertEqual(400, invalid.exception.status_code)
        self.assertEqual([], opener.requests)

    def test_models_uses_the_current_users_authoritative_model_list(self):
        opener = RecordingOpener([
            FakeResponse({
                "success": True,
                "data": ["gpt-5.4", "gpt-5.5", "gpt-5.5", "claude-opus-4-8"],
            })
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        models = client.models("session", 9)

        self.assertEqual(["gpt-5.4", "gpt-5.5", "claude-opus-4-8"], models)
        self.assertEqual(
            "https://newapi.example/api/user/models",
            opener.requests[0][0].full_url,
        )

    def test_status_fails_closed_on_non_finite_or_invalid_quota_units(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": {"quota_per_unit": "NaN"}}),
            FakeResponse({"success": True, "data": {"quota_per_unit": "invalid"}}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        with self.assertRaisesRegex(NewAPIConsoleError, "invalid status field: quota_per_unit"):
            client.status("session", 9)
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid status field: quota_per_unit"):
            client.status("session", 9)

    def test_console_catalog_and_rows_fail_closed_on_malformed_upstream_fields(self):
        invalid_models = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([FakeResponse({"success": True, "data": 42})]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid model catalog"):
            invalid_models.models("session", 9)

        invalid_token = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([FakeResponse({"success": True, "data": {
                "page": 1,
                "page_size": 20,
                "total": 1,
                "items": [{
                    "id": 7,
                    "name": "Codex",
                    "key": "sk-a**********wxyz",
                    "status": "enabled",
                    "created_time": 100,
                    "accessed_time": 200,
                    "expired_time": -1,
                    "remain_quota": 500000,
                    "used_quota": 250000,
                    "unlimited_quota": False,
                    "model_limits_enabled": False,
                    "model_limits": "",
                    "allow_ips": "",
                    "group": "default",
                    "cross_group_retry": False,
                }],
            }})]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid token field: status"):
            invalid_token.list_tokens("session", 9)

        invalid_log = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([FakeResponse({"success": True, "data": {
                "page": 1,
                "page_size": 20,
                "total": 1,
                "items": [{
                    "id": 1,
                    "created_at": 100,
                    "type": 2,
                    "quota": 50,
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "use_time": 3,
                    "is_stream": "false",
                    "channel": 7,
                }],
            }})]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid log field: is_stream"):
            invalid_log.list_logs("session", 9, 1)

    def test_user_logs_strip_admin_only_details_and_bound_large_text(self):
        opener = RecordingOpener([
            FakeResponse({
                "success": True,
                "data": {
                    "page": 1,
                    "page_size": 20,
                    "total": 1,
                    "items": [{
                        "id": 1,
                        "created_at": 100,
                        "type": 2,
                        "content": "x" * 5000,
                        "username": "alice",
                        "token_name": "main",
                        "model_name": "gpt-5.4",
                        "quota": 50,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "use_time": 3,
                        "is_stream": True,
                        "channel": 7,
                        "channel_name": "should-not-leak",
                        "group": "default",
                        "request_id": "req-1",
                        "upstream_request_id": "up-1",
                        "other": json.dumps({"frt": 1200, "admin_info": {"channel": "secret"}, "safe": "ok"}),
                    }],
                },
            })
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        result = client.list_logs("session", 9, 1, page=1, page_size=20)

        item = result["items"][0]
        self.assertEqual(4000, len(item["content"]))
        self.assertEqual("", item["channel_name"])
        self.assertNotIn("admin_info", item["other"])
        self.assertEqual("ok", item["other"]["safe"])

    def test_token_writes_use_allowlisted_routes_and_reveal_is_not_retained(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "message": ""}),
            FakeResponse({"success": True, "data": {"key": "sk-live-secret"}}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)
        payload = {
            "name": "Codex",
            "remain_quota": 500000,
            "expired_time": -1,
            "unlimited_quota": False,
            "model_limits_enabled": False,
            "model_limits": "",
            "allow_ips": "",
            "group": "default",
            "cross_group_retry": False,
        }

        client.create_token("session", 9, payload)
        revealed = client.reveal_token("session", 9, 7)

        create_request, _ = opener.requests[0]
        self.assertEqual("POST", create_request.method)
        self.assertEqual("https://newapi.example/api/token/", create_request.full_url)
        self.assertEqual(payload, json.loads(create_request.data.decode("utf-8")))
        reveal_request, _ = opener.requests[1]
        self.assertEqual("POST", reveal_request.method)
        self.assertEqual("https://newapi.example/api/token/7/key", reveal_request.full_url)
        self.assertEqual("sk-live-secret", revealed)
        self.assertFalse(hasattr(client, "last_response"))

    def test_upstream_business_errors_invalid_json_and_large_responses_are_rejected(self):
        business = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([FakeResponse({"success": False, "message": "New API permission denied"})]),
        )
        with self.assertRaises(NewAPIConsoleError) as business_error:
            business.self_info("session", 9)
        self.assertIn("permission denied", str(business_error.exception))
        self.assertNotIn("New API", str(business_error.exception))

        invalid = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([FakeResponse(b"not-json")]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "invalid JSON"):
            invalid.self_info("session", 9)

        large = NewAPIConsoleClient(
            "https://newapi.example",
            max_response_bytes=16,
            opener=RecordingOpener([FakeResponse(b"{" + b"x" * 32 + b"}")]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "too large"):
            large.self_info("session", 9)

    def test_http_errors_are_mapped_without_echoing_response_bodies(self):
        error = urllib.error.HTTPError(
            "https://newapi.example/api/user/self",
            502,
            "Bad Gateway",
            {},
            io.BytesIO(b'{"message":"upstream secret body"}'),
        )
        client = NewAPIConsoleClient(
            "https://newapi.example",
            opener=RecordingOpener([error]),
        )

        with self.assertRaises(NewAPIConsoleError) as raised:
            client.self_info("session", 9)

        self.assertEqual(502, raised.exception.status_code)
        self.assertNotIn("upstream secret body", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
