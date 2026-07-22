import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from dashboard_app import (
    ChannelSettingsPayload,
    KeyUsageQueryPayload,
    NotificationTestPayload,
    SetupCompletePayload,
    SettingsUpdatePayload,
    require_admin,
    require_operator,
)


class DashboardApiModelTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
