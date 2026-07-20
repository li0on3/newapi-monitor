import io
import json
import unittest

from dashboard_key_usage import KeyUsageClient, KeyUsageError, role_allows_key_lookup


class FakeResponse:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body.read()


class KeyUsageClientTests(unittest.TestCase):
    def test_queries_usage_and_recent_calls_without_returning_the_key(self):
        requests = []
        responses = {
            "/api/usage/token/": {
                "code": True,
                "data": {
                    "name": "production-key",
                    "total_granted": 1_000_000,
                    "total_used": 250_000,
                    "total_available": 750_000,
                    "unlimited_quota": False,
                    "expires_at": 1_800_000_000,
                    "model_limits_enabled": True,
                    "model_limits": {"gpt-5.4": True},
                },
            },
            "/api/log/token": {
                "success": True,
                "data": [
                    {
                        "id": 1,
                        "created_at": 1_700_000_000,
                        "type": 2,
                        "model_name": "gpt-5.4",
                        "quota": 5000,
                        "prompt_tokens": 120,
                        "completion_tokens": 30,
                        "use_time": 2,
                        "is_stream": True,
                        "channel": 7,
                        "request_id": "req-1",
                        "upstream_request_id": "up-1",
                        "group": "default",
                        "content": "consume",
                        "other": "{\"frt\": 345}",
                    },
                    {
                        "id": 2,
                        "created_at": 1_699_999_900,
                        "type": 2,
                        "model_name": "claude-opus-4-1",
                        "quota": 7000,
                        "prompt_tokens": 80,
                        "completion_tokens": 20,
                        "use_time": 4,
                        "is_stream": False,
                        "channel": 9,
                        "request_id": "req-2",
                        "other": {},
                    },
                ],
            },
        }

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(responses[request.full_url.removeprefix("https://newapi.example")])

        result = KeyUsageClient("https://newapi.example", opener=opener).query(
            "sk-super-secret", log_limit=1, quota_per_unit=500_000
        )

        self.assertEqual("production-key", result["usage"]["name"])
        self.assertEqual(25.0, result["usage"]["used_percentage"])
        self.assertEqual(0.5, result["usage"]["total_used_display"])
        self.assertEqual(1, result["summary"]["calls"])
        self.assertEqual(150, result["summary"]["total_tokens"])
        self.assertEqual(345.0, result["calls"][0]["frt_ms"])
        self.assertEqual("req-1", result["calls"][0]["request_id"])
        self.assertNotIn("super-secret", json.dumps(result))
        self.assertEqual(2, len(requests))
        self.assertTrue(all(request.headers["Authorization"] == "Bearer sk-super-secret" for request, _ in requests))

    def test_rejects_upstream_application_errors_without_leaking_key(self):
        def opener(_request, timeout):
            self.assertGreater(timeout, 0)
            return FakeResponse({"success": False, "message": "invalid token sk-super-secret"})

        with self.assertRaises(KeyUsageError) as error:
            KeyUsageClient("https://newapi.example", opener=opener).query("sk-super-secret", 100, 500_000)

        self.assertNotIn("super-secret", str(error.exception))

    def test_role_policy_is_ordered_and_defaults_to_admin_only(self):
        self.assertTrue(role_allows_key_lookup("admin", "admin"))
        self.assertFalse(role_allows_key_lookup("operator", "admin"))
        self.assertTrue(role_allows_key_lookup("operator", "operator"))
        self.assertTrue(role_allows_key_lookup("admin", "viewer"))
        self.assertFalse(role_allows_key_lookup("viewer", "invalid"))


if __name__ == "__main__":
    unittest.main()
