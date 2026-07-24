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
        self.assertFalse(decorated["overview_admin_visible"])
        self.assertFalse(decorated["overview_viewer_visible"])
        self.assertEqual("gpt-5.4", decorated["monitor_config"]["probe_model"])

    def test_overview_visibility_is_scoped_by_audience_and_updated_atomically(self):
        before_version = self.store.version()

        result = self.store.update_channel_visibility(
            {
                7: {"overview_admin_visible": True, "overview_viewer_visible": False},
                8: {"overview_admin_visible": False, "overview_viewer_visible": True},
            },
            actor="root",
            remote_addr="127.0.0.1",
        )

        channels = [
            {"channel_id": 7, "name": "admin-only", "enabled": True},
            {"channel_id": 8, "name": "viewer-only", "enabled": True},
        ]
        admin_items = self.store.decorate_channels(channels, audience="admin")
        viewer_items = self.store.decorate_channels(channels, audience="viewer")

        self.assertEqual([7], [item["channel_id"] for item in admin_items])
        self.assertEqual([8], [item["channel_id"] for item in viewer_items])
        self.assertTrue(result[7]["overview_admin_visible"])
        self.assertFalse(result[7]["overview_viewer_visible"])
        self.assertEqual(before_version, self.store.version())
        audit = self.store.audit(limit=10)
        self.assertEqual(1, len(audit))
        self.assertEqual("overview.visibility.update", audit[0]["action"])

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

    def test_encrypts_notification_credentials_and_webhook_urls(self):
        encrypted_db = str(Path(self.temp_dir.name) / "notification-settings.db")
        store = SettingsStore(
            encrypted_db,
            defaults={
                "wecom_app_secret": "wecom-secret",
                "wecom_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=secret-key",
                "feishu_app_secret": "feishu-secret",
                "feishu_webhook_secret": "sign-secret",
            },
            secret_key="test-secret-key-with-at-least-32-bytes",
        )

        public = store.public_values()
        self.assertEqual("********", public["wecom_app_secret"])
        self.assertEqual("********", public["wecom_webhook_url"])
        self.assertEqual("********", public["feishu_app_secret"])
        self.assertEqual("********", public["feishu_webhook_secret"])

        connection = sqlite3.connect(encrypted_db)
        try:
            rows = connection.execute(
                "SELECT key, value_json FROM monitor_settings ORDER BY key"
            ).fetchall()
        finally:
            connection.close()
        serialized = "\n".join(value for _, value in rows)
        self.assertNotIn("wecom-secret", serialized)
        self.assertNotIn("secret-key", serialized)
        self.assertNotIn("feishu-secret", serialized)

    def test_operational_audit_redacts_nested_credentials_without_reloading_collectors(self):
        before_version = self.store.version()

        self.store.record_audit(
            actor="alice",
            action="console.token.reveal",
            target="token:7",
            before={"name": "Codex", "key": "sk-before-secret"},
            after={"revealed": True, "nested": {"api_key": "sk-after-secret"}},
            remote_addr="127.0.0.1",
        )

        self.assertEqual(before_version, self.store.version())
        entry = self.store.audit(limit=1)[0]
        self.assertEqual("console.token.reveal", entry["action"])
        self.assertEqual("alice", entry["actor"])
        self.assertEqual("********", json.loads(entry["before_json"])["key"])
        self.assertEqual("********", json.loads(entry["after_json"])["nested"]["api_key"])
        self.assertTrue(json.loads(entry["after_json"])["revealed"])


if __name__ == "__main__":
    unittest.main()
