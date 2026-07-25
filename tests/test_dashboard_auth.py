import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard_app
from fastapi import HTTPException, Response
from starlette.requests import Request

from dashboard_auth import AuthStore


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
        settings.resolve_role.return_value = "operator"

        with mock.patch.object(dashboard_app.runtime, "auth", auth), mock.patch.object(
            dashboard_app.runtime, "sso", sso
        ), mock.patch.object(dashboard_app.runtime, "settings", settings):
            identity = dashboard_app.require_auth(self.request("session=new-api-session"))

        self.assertEqual("admin", identity["username"])
        self.assertEqual("operator", identity["role"])
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


if __name__ == "__main__":
    unittest.main()
