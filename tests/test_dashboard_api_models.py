import unittest

from pydantic import ValidationError

from dashboard_app import ChannelSettingsPayload, SettingsUpdatePayload


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


if __name__ == "__main__":
    unittest.main()
