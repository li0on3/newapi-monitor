import unittest

from fastapi import HTTPException
from pydantic import ValidationError

from dashboard_app import (
    ChannelSettingsPayload,
    SettingsUpdatePayload,
    require_admin,
    require_operator,
)


class DashboardApiModelTests(unittest.TestCase):
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
