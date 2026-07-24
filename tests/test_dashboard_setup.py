import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard_http import NoRedirectHandler
from dashboard_setup import NewAPIProvisioner, SetupError, hash_setup_token, verify_setup_token
from dashboard_settings import SettingsStore
import dashboard_app
from dashboard_app import SetupCompletePayload
from fastapi import HTTPException
from starlette.requests import Request


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        body = json.dumps(self.payload).encode("utf-8")
        return body if size < 0 else body[:size]


class SetupTokenTests(unittest.TestCase):
    def test_setup_token_is_compared_by_hash(self):
        digest = hash_setup_token("single-use-token")

        self.assertTrue(verify_setup_token("single-use-token", digest))
        self.assertFalse(verify_setup_token("wrong-token", digest))
        self.assertFalse(verify_setup_token("single-use-token", ""))


class NewAPIProvisionerTests(unittest.TestCase):
    def test_default_provisioner_rejects_redirects_and_bounds_responses(self):
        provisioner = NewAPIProvisioner()
        self.assertTrue(any(isinstance(handler, NoRedirectHandler) for handler in provisioner.opener.handlers))

        opener = mock.Mock()
        opener.open.return_value = FakeResponse({"success": True, "data": "x" * 64})
        with self.assertRaisesRegex(SetupError, "oversized response"):
            NewAPIProvisioner(opener=opener, max_response_bytes=8).provision(
                "https://newapi.example", "root", "password"
            )

    def test_admin_credentials_are_exchanged_without_being_returned(self):
        opener = mock.Mock()
        opener.open.side_effect = [
            FakeResponse({"success": True, "data": {"id": 7}}),
            FakeResponse({"success": True, "data": "management-token"}),
            FakeResponse({"success": True, "data": {"items": []}}),
            FakeResponse({"success": True}),
            FakeResponse({"success": True, "data": {"items": [{"id": 11, "name": "newapi-monitor-probe"}]}}),
            FakeResponse({"success": True, "data": {"key": "probe-token"}}),
        ]
        provisioner = NewAPIProvisioner(opener=opener)

        result = provisioner.provision("https://newapi.example", "root", "super-secret")

        self.assertEqual(
            {
                "new_api_base_url": "https://newapi.example",
                "new_api_user_id": 7,
                "new_api_access_token": "management-token",
                "relay_api_token": "probe-token",
            },
            result,
        )
        sent_bodies = [
            json.loads(call.args[0].data.decode("utf-8"))
            for call in opener.open.call_args_list
            if call.args[0].data
        ]
        self.assertIn({"username": "root", "password": "super-secret"}, sent_bodies)
        self.assertNotIn("super-secret", json.dumps(result))

    def test_rejects_new_api_application_error(self):
        opener = mock.Mock()
        opener.open.return_value = FakeResponse({"success": False, "message": "invalid credentials"})

        with self.assertRaisesRegex(SetupError, "invalid credentials"):
            NewAPIProvisioner(opener=opener).provision(
                "https://newapi.example", "root", "wrong-password"
            )


class SetupMetadataTests(unittest.TestCase):
    def test_setup_completion_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SettingsStore(
                str(Path(temp_dir) / "monitor.db"),
                {"new_api_base_url": "", "new_api_access_token": "", "new_api_user_id": 0},
                secret_key="test-secret-key",
            )

            self.assertFalse(store.is_setup_complete())
            store.complete_setup("installer")
            self.assertTrue(store.is_setup_complete())


class SetupEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_monitor_prefix_is_normalized_for_api_routes(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "http",
                "server": ("localhost", 80),
                "client": ("127.0.0.1", 1234),
                "path": "/monitor/api/setup/status",
                "raw_path": b"/monitor/api/setup/status",
                "query_string": b"",
                "headers": [],
            }
        )

        async def capture_path(current):
            return current.scope["path"]

        normalized = await dashboard_app.direct_monitor_prefix(request, capture_path)

        self.assertEqual("/api/setup/status", normalized)

    async def test_direct_monitor_prefix_is_normalized_for_frontend_routes_and_assets(self):
        for original, expected in (
            ("/monitor", "/"),
            ("/monitor/console/keys", "/console/keys"),
            ("/monitor/assets/index.js", "/assets/index.js"),
        ):
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "scheme": "http",
                    "server": ("localhost", 80),
                    "client": ("127.0.0.1", 1234),
                    "path": original,
                    "raw_path": original.encode("utf-8"),
                    "query_string": b"",
                    "headers": [],
                }
            )

            async def capture_path(current):
                return current.scope["path"]

            normalized = await dashboard_app.direct_monitor_prefix(request, capture_path)
            self.assertEqual(expected, normalized)

    async def test_invalid_setup_token_is_rejected_before_new_api_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = {
                "settings": dashboard_app.runtime.settings,
                "setup_required": dashboard_app.runtime.setup_required,
                "setup_token_hash": dashboard_app.runtime.setup_token_hash,
                "setup_token_expires_at": dashboard_app.runtime.setup_token_expires_at,
                "monitor_enabled": dashboard_app.runtime.monitor_enabled,
            }
            try:
                dashboard_app.runtime.settings = SettingsStore(
                    str(Path(temp_dir) / "monitor.db"),
                    {
                        "new_api_base_url": "",
                        "new_api_access_token": "",
                        "new_api_user_id": 0,
                        "relay_api_token": "",
                    },
                    secret_key="setup-test-secret",
                )
                dashboard_app.runtime.setup_required = True
                dashboard_app.runtime.setup_token_hash = hash_setup_token("correct-token")
                dashboard_app.runtime.setup_token_expires_at = 4_102_444_800
                dashboard_app.runtime.monitor_enabled = False
                request = Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []})
                payload = SetupCompletePayload.model_validate(
                    {
                        "setup_token": "wrong-token",
                        "new_api_base_url": "https://newapi.example",
                        "username": "root",
                        "password": "strong-password",
                    }
                )

                with mock.patch.object(dashboard_app, "NewAPIProvisioner") as provisioner:
                    with self.assertRaises(HTTPException) as raised:
                        await dashboard_app.complete_setup(payload, request)

                self.assertEqual(403, raised.exception.status_code)
                provisioner.assert_not_called()
            finally:
                dashboard_app.setup_limiter.clear("127.0.0.1")
                for key, value in original.items():
                    setattr(dashboard_app.runtime, key, value)
