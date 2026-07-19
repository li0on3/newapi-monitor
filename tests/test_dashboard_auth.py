import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
