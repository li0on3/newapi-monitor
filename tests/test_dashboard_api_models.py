import unittest

import dashboard_app
from fastapi import HTTPException
from starlette.requests import Request
from pydantic import ValidationError

from dashboard_app import (
    ChannelSettingsPayload,
    ConsoleBatchPayload,
    ConsoleTokenPayload,
    ConsoleTokenStatusPayload,
    KeyUsageQueryPayload,
    NotificationTestPayload,
    SetupCompletePayload,
    SettingsUpdatePayload,
    console_capabilities,
    console_time_range,
    require_admin,
    require_console_access,
    require_operator,
)


class DashboardApiModelTests(unittest.TestCase):
    def test_trusted_proxy_address_prefers_unspoofable_real_ip_header(self):
        request = Request(
            {
                "type": "http",
                "client": ("127.0.0.1", 1234),
                "headers": [
                    (b"x-real-ip", b"203.0.113.8"),
                    (b"x-forwarded-for", b"198.51.100.9, 203.0.113.8"),
                ],
            }
        )
        original = dashboard_app.runtime.trust_proxy_headers
        try:
            dashboard_app.runtime.trust_proxy_headers = True
            self.assertEqual("203.0.113.8", dashboard_app.runtime.remote_addr(request))
        finally:
            dashboard_app.runtime.trust_proxy_headers = original

    def test_setup_requires_credentials_or_explicit_tokens(self):
        with self.assertRaises(ValidationError):
            SetupCompletePayload.model_validate(
                {"setup_token": "setup-token", "new_api_base_url": "https://newapi.example"}
            )
        credentials = SetupCompletePayload.model_validate(
            {
                "setup_token": "setup-token",
                "new_api_base_url": "https://newapi.example",
                "username": "root",
                "password": "strong-password",
            }
        )
        self.assertEqual("root", credentials.username)
        tokens = SetupCompletePayload.model_validate(
            {
                "setup_token": "setup-token",
                "new_api_base_url": "https://newapi.example",
                "new_api_access_token": "access-token",
                "new_api_user_id": 1,
                "relay_api_token": "probe-token",
            }
        )
        self.assertEqual(1, tokens.new_api_user_id)

        with self.assertRaises(ValidationError):
            SetupCompletePayload.model_validate(
                {
                    "setup_token": "setup-token",
                    "new_api_base_url": "https://newapi.example",
                    "username": "root",
                }
            )
        with self.assertRaises(ValidationError):
            SetupCompletePayload.model_validate(
                {
                    "setup_token": "setup-token",
                    "new_api_base_url": "https://newapi.example",
                    "new_api_access_token": "access-token",
                    "new_api_user_id": 1,
                }
            )

    def test_settings_reject_unknown_fields_and_credentials_in_base_url(self):
        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"unexpected": True})
        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"new_api_base_url": "https://user:pass@example.com"})

    def test_channel_probe_rejects_external_url_and_oversized_prompt(self):
        with self.assertRaises(ValidationError):
            ChannelSettingsPayload.model_validate({"probe_path": "https://evil.example/v1/responses"})
        with self.assertRaises(ValidationError):
            ChannelSettingsPayload.model_validate({"probe_prompt": "x" * 257})

    def test_key_usage_configuration_and_query_are_bounded(self):
        settings = SettingsUpdatePayload.model_validate({
            "key_usage_enabled": True,
            "key_usage_min_role": "operator",
            "key_usage_log_limit": 250,
            "key_usage_attempts_per_minute": 12,
        })
        self.assertEqual("operator", settings.key_usage_min_role)

        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"key_usage_min_role": "public"})
        with self.assertRaises(ValidationError):
            KeyUsageQueryPayload.model_validate({"api_key": "bad key"})
        with self.assertRaises(ValidationError):
            KeyUsageQueryPayload.model_validate({"api_key": "sk-" + "x" * 600})

    def test_notification_settings_validate_official_webhook_hosts(self):
        settings = SettingsUpdatePayload.model_validate(
            {
                "wecom_app_enabled": True,
                "wecom_corp_id": "ww-test",
                "wecom_agent_id": 1000004,
                "wecom_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
                "feishu_receive_id_type": "chat_id",
                "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
            }
        )
        self.assertTrue(settings.wecom_app_enabled)
        self.assertEqual("chat_id", settings.feishu_receive_id_type)

        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate(
                {"wecom_webhook_url": "https://internal.example/webhook"}
            )
        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate(
                {"feishu_webhook_url": "http://open.feishu.cn/open-apis/bot/v2/hook/test"}
            )

        payload = NotificationTestPayload.model_validate({"channel": "wecom_app"})
        self.assertEqual("wecom_app", payload.channel)
        with self.assertRaises(ValidationError):
            NotificationTestPayload.model_validate({"channel": "unknown"})

    def test_openai_status_settings_are_bounded(self):
        settings = SettingsUpdatePayload.model_validate(
            {
                "openai_status_enabled": True,
                "openai_status_interval_seconds": 60,
                "openai_status_timeout_seconds": 10,
                "openai_status_min_impact": "major",
                "openai_status_component_ids": ["responses-id", "codex-api-id"],
                "openai_status_failure_threshold": 2,
                "openai_status_recovery_threshold": 2,
                "openai_status_include_in_overall": False,
                "openai_status_admin_visible": True,
                "openai_status_viewer_visible": True,
            }
        )
        self.assertEqual(["responses-id", "codex-api-id"], settings.openai_status_component_ids)

        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"openai_status_interval_seconds": 5})
        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"openai_status_min_impact": "unknown"})
        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"openai_status_component_ids": ["x" * 129]})

    def test_viewer_cannot_use_privileged_dependencies(self):
        viewer = {"username": "viewer", "role": "viewer"}

        with self.assertRaises(HTTPException) as operator_error:
            require_operator(viewer)
        self.assertEqual(403, operator_error.exception.status_code)

        with self.assertRaises(HTTPException) as admin_error:
            require_admin(viewer)
        self.assertEqual(403, admin_error.exception.status_code)

        operator = {"username": "operator", "role": "operator"}
        self.assertEqual(operator, require_operator(operator))

    def test_console_token_payloads_are_strict_and_bounded(self):
        token = ConsoleTokenPayload.model_validate(
            {
                "name": "Codex",
                "remain_quota": 500000,
                "expired_time": -1,
                "unlimited_quota": False,
                "model_limits_enabled": True,
                "model_limits": "gpt-5.4,gpt-5.5",
                "allow_ips": "1.1.1.1",
                "group": "default",
                "cross_group_retry": False,
            }
        )
        self.assertEqual("Codex", token.name)
        self.assertEqual(1, ConsoleTokenStatusPayload.model_validate({"status": 1}).status)
        self.assertEqual([7, 8], ConsoleBatchPayload.model_validate({"ids": [7, 7, 8]}).ids)

        with self.assertRaises(ValidationError):
            ConsoleTokenPayload.model_validate({"name": "", "remain_quota": 0, "expired_time": -1})
        with self.assertRaises(ValidationError):
            ConsoleTokenPayload.model_validate({"name": "   ", "remain_quota": 0, "expired_time": -1})
        with self.assertRaises(ValidationError):
            ConsoleTokenPayload.model_validate({"name": "x", "remain_quota": -1, "expired_time": -1})
        with self.assertRaises(ValidationError):
            ConsoleTokenStatusPayload.model_validate({"status": 3})
        with self.assertRaises(ValidationError):
            ConsoleBatchPayload.model_validate({"ids": list(range(1, 102))})

    def test_console_access_requires_newapi_session_and_never_elevates_source_role(self):
        values = {
            "console_enabled": True,
            "console_min_role": "viewer",
            "console_keys_enabled": True,
        }
        source_user = {
            "username": "alice",
            "role": "admin",
            "source": "newapi",
            "source_role": 1,
            "user_id": 9,
        }

        allowed = require_console_access(source_user, values, "keys")

        self.assertEqual(1, allowed["source_role"])
        with self.assertRaises(HTTPException) as emergency_error:
            require_console_access({**source_user, "source": "emergency"}, values, "keys")
        self.assertEqual(403, emergency_error.exception.status_code)
        with self.assertRaises(HTTPException) as disabled_error:
            require_console_access(source_user, {**values, "console_keys_enabled": False}, "keys")
        self.assertEqual(404, disabled_error.exception.status_code)

    def test_console_capabilities_are_page_scoped_and_emergency_admin_is_excluded(self):
        values = {
            "console_enabled": True,
            "console_min_role": "viewer",
            "console_overview_enabled": True,
            "console_analytics_enabled": True,
            "console_keys_enabled": False,
            "console_logs_enabled": True,
        }
        user = {"role": "viewer", "source": "newapi", "source_role": 1, "user_id": 9}

        result = console_capabilities(user, values)

        self.assertTrue(result["available"])
        self.assertEqual(
            {"overview": True, "analytics": True, "keys": False, "logs": True},
            result["pages"],
        )
        self.assertFalse(result["global_scope"])
        emergency = console_capabilities({**user, "role": "admin", "source": "emergency"}, values)
        self.assertFalse(emergency["available"])
        self.assertEqual({}, emergency["pages"])

        disabled_pages = console_capabilities(
            user,
            {
                **values,
                "console_overview_enabled": False,
                "console_analytics_enabled": False,
                "console_keys_enabled": False,
                "console_logs_enabled": False,
            },
        )
        self.assertFalse(disabled_pages["available"])
        self.assertEqual({}, disabled_pages["pages"])

    def test_console_settings_are_bounded(self):
        settings = SettingsUpdatePayload.model_validate({
            "console_enabled": True,
            "console_min_role": "viewer",
            "console_default_days": 7,
            "console_write_attempts_per_minute": 30,
            "console_reveal_attempts_per_minute": 6,
        })
        self.assertEqual(7, settings.console_default_days)

        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"console_default_days": 31})
        with self.assertRaises(ValidationError):
            SettingsUpdatePayload.model_validate({"console_reveal_attempts_per_minute": 31})

        with self.assertRaises(HTTPException):
            console_time_range(1, 1 + 30 * 86400 + 1, 1, 7)


if __name__ == "__main__":
    unittest.main()
