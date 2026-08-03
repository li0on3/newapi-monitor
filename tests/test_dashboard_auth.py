import tempfile
import unittest
from pathlib import Path
from typing import get_type_hints
from unittest import mock

import dashboard_app
from fastapi import HTTPException, Response
from starlette.requests import Request

from dashboard_auth import AuthStore


class HealthEndpointTests(unittest.TestCase):
    def test_health_exposes_running_release_version(self):
        snapshot = {"status": "ok", "timestamp": 1_700_000_000, "version": "1.12.0"}

        with mock.patch.object(dashboard_app, "system_health_snapshot", return_value=snapshot):
            response = dashboard_app.health()

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            b'{"status":"ok","timestamp":1700000000,"version":"1.12.0"}',
            response.body,
        )

    def test_setup_health_also_exposes_running_release_version(self):
        snapshot = {"status": "setup_required", "timestamp": 1_700_000_000, "version": "1.12.0"}

        with mock.patch.object(dashboard_app, "system_health_snapshot", return_value=snapshot):
            response = dashboard_app.health()

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            b'{"status":"setup_required","timestamp":1700000000,"version":"1.12.0"}',
            response.body,
        )

    def test_embedded_release_version_matches_version_file(self):
        expected = Path(dashboard_app.__file__).with_name("VERSION").read_text(encoding="utf-8").strip()

        self.assertEqual(expected, dashboard_app.APP_VERSION)


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "auth.db")
        self.store = AuthStore(self.db_path, session_seconds=3600)
        self.store.bootstrap_admin("admin", "a-secure-dashboard-password")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_authenticates_bootstrapped_admin(self):
        self.assertTrue(self.store.verify_password("admin", "a-secure-dashboard-password"))
        self.assertFalse(self.store.verify_password("admin", "wrong-password"))

    def test_session_can_be_resolved_and_revoked(self):
        token = self.store.create_session("admin", now=100)

        self.assertEqual("admin", self.store.resolve_session(token, now=200))
        self.store.revoke_session(token)
        self.assertIsNone(self.store.resolve_session(token, now=201))

    def test_expired_session_is_rejected(self):
        token = self.store.create_session("admin", now=100)

        self.assertIsNone(self.store.resolve_session(token, now=3_701))


class DashboardSessionBoundaryTests(unittest.TestCase):
    @staticmethod
    def request(cookie: str = "", user_id: str = "1") -> Request:
        headers = []
        if cookie:
            headers.append((b"cookie", cookie.encode("utf-8")))
        if user_id:
            headers.append((b"new-api-user", user_id.encode("utf-8")))
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/auth/me",
                "headers": headers,
                "client": ("127.0.0.1", 12345),
                "server": ("monitor.test", 443),
                "scheme": "https",
            }
        )

    def test_explicit_monitor_logout_suppresses_automatic_new_api_sso(self):
        auth = mock.Mock()
        auth.resolve_session.return_value = None
        sso = mock.Mock()
        sso.verify.return_value = {
            "username": "root",
            "display_name": "Root User",
            "source_role": 100,
            "source": "new_api",
        }
        settings = mock.Mock()
        settings.resolve_role.return_value = "admin"

        with mock.patch.object(dashboard_app.runtime, "auth", auth), mock.patch.object(
            dashboard_app.runtime, "sso", sso
        ), mock.patch.object(dashboard_app.runtime, "settings", settings), mock.patch.object(
            dashboard_app.runtime,
            "sso_suppressed_cookie_name",
            "newapi_monitor_sso_suppressed",
            create=True,
        ):
            with self.assertRaises(HTTPException) as raised:
                dashboard_app.require_auth(
                    self.request(
                        "session=new-api-session; newapi_monitor_sso_suppressed=1"
                    )
                )

        self.assertEqual(401, raised.exception.status_code)
        sso.verify.assert_not_called()

    def test_new_api_sso_still_authenticates_when_not_explicitly_suppressed(self):
        auth = mock.Mock()
        auth.resolve_session.return_value = None
        sso = mock.Mock()
        sso.verify.return_value = {
            "username": "admin",
            "display_name": "Admin",
            "source_role": 10,
            "source": "newapi",
        }
        settings = mock.Mock()
        settings.resolve_role.return_value = "admin"

        with mock.patch.object(dashboard_app.runtime, "auth", auth), mock.patch.object(
            dashboard_app.runtime, "sso", sso
        ), mock.patch.object(dashboard_app.runtime, "settings", settings):
            identity = dashboard_app.require_auth(self.request("session=new-api-session"))

        self.assertEqual("admin", identity["username"])
        self.assertEqual("admin", identity["role"])
        sso.verify.assert_called_once_with("new-api-session", "1")

    def test_logout_sets_monitor_only_sso_suppression_cookie(self):
        auth = mock.Mock()
        response = Response()

        with mock.patch.object(dashboard_app.runtime, "auth", auth), mock.patch.object(
            dashboard_app.runtime,
            "sso_suppressed_cookie_name",
            "newapi_monitor_sso_suppressed",
            create=True,
        ), mock.patch.object(dashboard_app.runtime, "cookie_secure", True), mock.patch.object(
            dashboard_app.runtime, "cookie_path", "/monitor"
        ), mock.patch.object(dashboard_app.runtime, "sso_suppression_seconds", 31536000):
            result = dashboard_app.logout(
                self.request("newapi_monitor_session=emergency-session; session=new-api-session"),
                response,
            )

        self.assertEqual({"authenticated": False}, result)
        cookies = response.headers.getlist("set-cookie")
        self.assertTrue(
            any(
                cookie.startswith("newapi_monitor_sso_suppressed=1;")
                and "Max-Age=31536000" in cookie
                and "Path=/monitor" in cookie
                and "HttpOnly" in cookie
                and "Secure" in cookie
                for cookie in cookies
            )
        )
        self.assertFalse(any(cookie.startswith("session=") for cookie in cookies))

    def test_sso_resume_endpoint_is_available_without_touching_new_api_session(self):
        matching_routes = [
            route
            for route in dashboard_app.app.routes
            if getattr(route, "path", "") == "/api/auth/sso"
            and "POST" in getattr(route, "methods", set())
        ]

        self.assertEqual(1, len(matching_routes))

        response = Response()
        with mock.patch.object(
            dashboard_app.runtime,
            "sso_suppressed_cookie_name",
            "newapi_monitor_sso_suppressed",
        ), mock.patch.object(dashboard_app.runtime, "cookie_secure", True), mock.patch.object(
            dashboard_app.runtime, "cookie_path", "/monitor"
        ):
            result = dashboard_app.resume_new_api_sso(response)

        self.assertEqual({"enabled": True}, result)
        cookies = response.headers.getlist("set-cookie")
        self.assertTrue(
            any(
                cookie.startswith("newapi_monitor_sso_suppressed=")
                and "Max-Age=0" in cookie
                and "Path=/monitor" in cookie
                for cookie in cookies
            )
        )
        self.assertFalse(any(cookie.startswith("session=") for cookie in cookies))


class DashboardRoleBoundaryTests(unittest.TestCase):
    def test_regular_user_can_access_monitor_overview_but_not_operator_modules(self):
        for endpoint, parameter in (
            (dashboard_app.dashboard_summary, "user"),
            (dashboard_app.channels, "user"),
            (dashboard_app.channel, "user"),
        ):
            with self.subTest(endpoint=endpoint.__name__):
                self.assertEqual(
                    dashboard_app.AuthenticatedUser,
                    get_type_hints(endpoint, include_extras=True)[parameter],
                )

        for endpoint, parameter in (
            (dashboard_app.openai_provider_status, "user"),
            (dashboard_app.query_key_usage, "user"),
        ):
            with self.subTest(endpoint=endpoint.__name__):
                self.assertEqual(
                    dashboard_app.OperatorUser,
                    get_type_hints(endpoint, include_extras=True)[parameter],
                )

        viewer = {
            "username": "alice",
            "display_name": "Alice",
            "role": "viewer",
            "source": "newapi",
            "source_role": 1,
            "user_id": 9,
        }
        with self.assertRaises(HTTPException) as denied:
            dashboard_app.require_operator(viewer)
        self.assertEqual(403, denied.exception.status_code)

    def test_viewer_overview_fails_closed_when_visibility_settings_are_unavailable(self):
        viewer = {
            "username": "alice",
            "display_name": "Alice",
            "role": "viewer",
            "source": "newapi",
            "source_role": 1,
            "user_id": 9,
        }
        repository = mock.Mock()
        repository.summary.return_value = {"channels": {"total": 1}}
        repository.channels.return_value = [{"channel_id": 1}]
        repository.channel.return_value = {"channel_id": 1}
        with mock.patch.object(dashboard_app.runtime, "settings", None), mock.patch(
            "dashboard_app.repository", return_value=repository
        ):
            for call in (
                lambda: dashboard_app.dashboard_summary(viewer),
                lambda: dashboard_app.channels(viewer),
                lambda: dashboard_app.channel(1, viewer),
            ):
                with self.subTest(call=call):
                    with self.assertRaises(HTTPException) as denied:
                        call()
                    self.assertEqual(503, denied.exception.status_code)

    def test_viewer_summary_uses_visible_scope_and_hides_collector_errors(self):
        viewer = {
            "username": "alice",
            "display_name": "Alice",
            "role": "viewer",
            "source": "newapi",
            "source_role": 1,
            "user_id": 9,
        }
        repository = mock.Mock()
        repository.enabled_channel_ids.return_value = {1, 2}
        repository.summary.return_value = {
            "channel_sync": {
                "status": "stale",
                "age_seconds": 120,
                "last_error": "Unauthorized, internal detail",
            }
        }
        settings = mock.Mock()
        settings.runtime_values.return_value = {"openai_status_enabled": False}
        settings.decorate_channels.return_value = [{"channel_id": 2}]

        with mock.patch.object(dashboard_app.runtime, "settings", settings), mock.patch(
            "dashboard_app.repository", return_value=repository
        ):
            result = dashboard_app.dashboard_summary(viewer)

        repository.summary.assert_called_once_with(
            channel_ids={2},
            include_operational_incidents=False,
        )
        self.assertNotIn("last_error", result["channel_sync"])

    def test_viewer_channel_payload_omits_operator_only_metadata(self):
        viewer = {
            "username": "alice",
            "display_name": "Alice",
            "role": "viewer",
            "source": "newapi",
            "source_role": 1,
            "user_id": 9,
        }
        item = {
            "channel_id": 1,
            "name": "Customer channel",
            "channel_type": 1,
            "enabled": True,
            "raw_status": 1,
            "models": ["model-a"],
            "group": "default",
            "synced_at": 100,
            "stale_after_seconds": 900,
            "slow_after_seconds": 30,
            "latest": {
                "observed_at": 100,
                "success": False,
                "elapsed_ms": 900,
                "frt_ms": None,
                "message": "upstream response body with private details",
                "source": "real",
            },
            "history": [{
                "observed_at": 99,
                "success": False,
                "elapsed_ms": 800,
                "frt_ms": None,
                "message": "another private error",
                "source": "real",
            }],
            "availability": {"total": 1, "successes": 0, "percentage": 0},
            "usage_24h": {"requests": 1, "slow": 0, "p95_seconds": 0.8},
            "source_name": "Internal upstream name",
            "monitor_config": {"probe_model": "internal-model"},
            "recent_logs": [{"username": "another-user", "request_id": "req-secret"}],
        }
        repository = mock.Mock()
        repository.channels.return_value = [item]
        repository.channel.return_value = item
        settings = mock.Mock()
        settings.decorate_channels.side_effect = lambda items, **_: items
        settings.runtime_values.return_value = {"retention_days": 90}

        with mock.patch.object(dashboard_app.runtime, "settings", settings), mock.patch(
            "dashboard_app.repository", return_value=repository
        ):
            listing = dashboard_app.channels(viewer)["items"][0]
            detail = dashboard_app.channel(1, viewer)

        for payload in (listing, detail):
            self.assertNotIn("source_name", payload)
            self.assertNotIn("monitor_config", payload)
            self.assertNotIn("recent_logs", payload)
            self.assertEqual("", payload["latest"]["message"])
            self.assertEqual("", payload["history"][0]["message"])
            self.assertEqual(
                {
                    "channel_id", "name", "channel_type", "enabled", "raw_status", "models",
                    "group", "synced_at", "stale_after_seconds", "slow_after_seconds",
                    "latest", "history", "availability", "usage_24h",
                },
                set(payload),
            )

if __name__ == "__main__":
    unittest.main()
