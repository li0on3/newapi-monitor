import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dashboard_sso import NewAPISessionVerifier


class NewAPISessionVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "sso.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_forwards_newapi_session_cookie_and_returns_identity(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "success": True,
                        "data": {"id": 3, "username": "admin", "role": 10, "status": 1},
                    }
                ).encode()

        verifier = NewAPISessionVerifier(lambda: "https://newapi.example", cache_seconds=30)
        with mock.patch("dashboard_sso.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            identity = verifier.verify("signed-cookie", "3")

        request = urlopen.call_args.args[0]
        self.assertEqual("session=signed-cookie", request.get_header("Cookie"))
        self.assertEqual("3", request.get_header("New-api-user"))
        self.assertEqual("admin", identity["username"])
        self.assertEqual(10, identity["source_role"])

    def test_rejects_disabled_user(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self):
                return b'{"success":true,"data":{"id":3,"username":"admin","role":10,"status":2}}'

        verifier = NewAPISessionVerifier(lambda: "https://newapi.example")
        with mock.patch("dashboard_sso.urllib.request.urlopen", return_value=FakeResponse()):
            self.assertIsNone(verifier.verify("signed-cookie", "3"))


if __name__ == "__main__":
    unittest.main()
