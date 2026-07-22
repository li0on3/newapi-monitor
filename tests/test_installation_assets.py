import unittest
from pathlib import Path


class InstallationAssetTests(unittest.TestCase):
    def test_installer_uses_verified_release_bundle_and_prebuilt_image(self):
        installer = Path("install.sh").read_text(encoding="utf-8")

        self.assertIn("newapi-monitor-bundle-", installer)
        self.assertIn("sha256sum --check", installer)
        self.assertIn("ghcr.io/li0on3/newapi-monitor", installer)
        self.assertIn("SETUP_TOKEN_HASH", installer)
        self.assertNotIn("docker compose build", installer)

    def test_monitorctl_exposes_safe_lifecycle_commands(self):
        command = Path("monitorctl").read_text(encoding="utf-8")

        for subcommand in (
            "status",
            "logs",
            "doctor",
            "backup",
            "update",
            "rollback",
            "restart",
            "reset-admin",
            "renew-setup",
            "uninstall",
        ):
            self.assertIn(subcommand, command)
        self.assertIn("sqlite3", command)
        self.assertIn(".previous-image", command)
        self.assertIn("--purge", command)

    def test_release_publishes_one_click_assets(self):
        workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

        self.assertIn("newapi-monitor-bundle-", workflow)
        self.assertIn("install.sh", workflow)
        self.assertIn("sha256", workflow)
        self.assertIn("monitorctl", workflow)


if __name__ == "__main__":
    unittest.main()
