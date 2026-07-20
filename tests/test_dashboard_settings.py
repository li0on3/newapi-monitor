import tempfile
import unittest
import json
import sqlite3
from pathlib import Path

from dashboard_settings import SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "settings.db")
        self.store = SettingsStore(
            self.db_path,
            defaults={
                "channel_sync_interval_seconds": 5,
                "slow_request_seconds": 60.0,
                "smtp_password": "bootstrap-secret",
            },
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_updates_settings_and_masks_secrets(self):
        before_version = self.store.version()

        result = self.store.update_settings(
            {"channel_sync_interval_seconds": 10, "smtp_password": "new-secret"},
            actor="root",
            remote_addr="127.0.0.1",
        )

        self.assertGreater(self.store.version(), before_version)
        self.assertEqual(10, self.store.runtime_values()["channel_sync_interval_seconds"])
        self.assertEqual("new-secret", self.store.runtime_values()["smtp_password"])
        self.assertEqual("********", result["smtp_password"])
        self.assertEqual(1, len(self.store.audit(limit=10)))

    def test_empty_secret_preserves_existing_value(self):
        self.store.update_settings({"smtp_password": ""}, actor="root")

        self.assertEqual("bootstrap-secret", self.store.runtime_values()["smtp_password"])

    def test_channel_settings_override_display_without_touching_source_channel(self):
        self.store.update_channel(
            7,
            {
                "display_enabled": False,
                "display_name": "Codex 专线",
                "sort_order": 20,
                "probe_enabled": True,
                "probe_model": "gpt-5.4",
                "probe_format": "responses",
            },
            actor="admin",
        )

        source = {"channel_id": 7, "name": "upstream-name", "enabled": True}
        decorated = self.store.decorate_channels([source], include_hidden=True)[0]

        self.assertEqual("upstream-name", source["name"])
        self.assertEqual("Codex 专线", decorated["name"])
        self.assertFalse(decorated["display_enabled"])
        self.assertEqual("gpt-5.4", decorated["monitor_config"]["probe_model"])

    def test_role_mapping_and_user_override(self):
        self.assertEqual("admin", self.store.resolve_role("alice", 100))
        self.assertEqual("operator", self.store.resolve_role("alice", 10))
        self.assertEqual("viewer", self.store.resolve_role("alice", 1))

        self.store.set_user_role("alice", "viewer", actor="root")

        self.assertEqual("viewer", self.store.resolve_role("alice", 100))

    def test_bootstrap_channel_settings_does_not_overwrite_existing_configuration(self):
        self.store.update_channel(7, {"display_name": "Existing"}, actor="admin")

        self.store.bootstrap_channel_settings({7: {"probe_enabled": True, "probe_model": "gpt-5.4"}, 8: {"probe_enabled": True}})

        settings = self.store.channel_settings()
        self.assertEqual("Existing", settings[7]["display_name"])
        self.assertTrue(settings[8]["probe_enabled"])

    def test_anthropic_channel_uses_messages_default_path(self):
        self.store.update_channel(
            5,
            {
                "probe_enabled": True,
                "probe_model": "claude-opus-4-8",
                "probe_format": "anthropic",
                "probe_prompt": "1",
                "max_output_tokens": 1,
            },
            actor="admin",
        )

        rule = self.store.real_probe_rules()["5"]
        self.assertEqual("/v1/messages", rule["path"])
        self.assertEqual("anthropic", rule["format"])
        self.assertEqual("1", rule["prompt"])
        self.assertEqual(1, rule["max_output_tokens"])

    def test_encrypts_secret_settings_at_rest_when_key_is_configured(self):
        encrypted_db = str(Path(self.temp_dir.name) / "encrypted.db")
        store = SettingsStore(
            encrypted_db,
            defaults={"smtp_password": "bootstrap-secret"},
            secret_key="test-secret-key-with-at-least-32-bytes",
        )

        self.assertEqual("bootstrap-secret", store.runtime_values()["smtp_password"])
        connection = sqlite3.connect(encrypted_db)
        try:
            raw = connection.execute(
                "SELECT value_json FROM monitor_settings WHERE key = 'smtp_password'"
            ).fetchone()[0]
        finally:
            connection.close()
        payload = json.loads(raw)
        self.assertIn("$encrypted", payload)
        self.assertNotIn("bootstrap-secret", raw)


if __name__ == "__main__":
    unittest.main()
