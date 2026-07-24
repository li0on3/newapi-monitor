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

    def test_analytics_uses_self_endpoints_for_users_and_admin_endpoints_for_admins(self):
        user_opener = RecordingOpener([
            FakeResponse({"success": True, "data": [{"created_at": 100, "model_name": "gpt-5.4", "count": 2, "quota": 50, "token_used": 20}]}),
            FakeResponse({"success": True, "data": [{"token_id": 3, "use_group": "default", "model_name": "gpt-5.4", "count": 2, "quota": 50, "token_used": 20}]}),
            FakeResponse({"success": True, "data": {"quota": 50, "rpm": 2, "tpm": 20}}),
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
        ])
        admin_client = NewAPIConsoleClient("https://newapi.example", opener=admin_opener)

        admin_client.analytics("session", 10, 10, 100, 200, username="alice")

        urls = [request.full_url for request, _ in admin_opener.requests]
        self.assertIn("/api/data/?", urls[0])
        self.assertIn("username=alice", urls[0])
        self.assertIn("/api/data/flow?", urls[1])
        self.assertIn("/api/log/stat?", urls[2])

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

    def test_status_rejects_non_finite_or_invalid_quota_units(self):
        opener = RecordingOpener([
            FakeResponse({"success": True, "data": {"quota_per_unit": "NaN"}}),
            FakeResponse({"success": True, "data": {"quota_per_unit": "invalid"}}),
        ])
        client = NewAPIConsoleClient("https://newapi.example", opener=opener)

        self.assertEqual(500000, client.status("session", 9)["quota_per_unit"])
        self.assertEqual(500000, client.status("session", 9)["quota_per_unit"])

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
            opener=RecordingOpener([FakeResponse({"success": False, "message": "permission denied"})]),
        )
        with self.assertRaisesRegex(NewAPIConsoleError, "permission denied"):
            business.self_info("session", 9)

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
