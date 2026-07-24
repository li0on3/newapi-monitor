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

            def read(self, size=-1):
                body = json.dumps(
                    {
                        "success": True,
                        "data": {"id": 3, "username": "admin", "role": 10, "status": 1},
                    }
                ).encode()
                return body if size < 0 else body[:size]

        urlopen = mock.Mock(return_value=FakeResponse())
        verifier = NewAPISessionVerifier(
            lambda: "https://newapi.example",
            cache_seconds=30,
            opener=urlopen,
        )
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

            def read(self, size=-1):
                body = b'{"success":true,"data":{"id":3,"username":"admin","role":10,"status":2}}'
                return body if size < 0 else body[:size]

        verifier = NewAPISessionVerifier(
            lambda: "https://newapi.example",
            opener=mock.Mock(return_value=FakeResponse()),
        )
        self.assertIsNone(verifier.verify("signed-cookie", "3"))

    def test_rejects_identity_when_upstream_user_id_does_not_match_verified_header(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self, size=-1):
                body = b'{"success":true,"data":{"id":4,"username":"admin","role":10,"status":1}}'
                return body if size < 0 else body[:size]

        verifier = NewAPISessionVerifier(
            lambda: "https://newapi.example",
            opener=mock.Mock(return_value=FakeResponse()),
        )
        self.assertIsNone(verifier.verify("signed-cookie", "3"))

    def test_rejects_invalid_cookie_and_oversized_identity_response(self):
        opener = mock.Mock()
        verifier = NewAPISessionVerifier(
            lambda: "https://newapi.example",
            max_response_bytes=8,
            opener=opener,
        )

        self.assertIsNone(verifier.verify("bad\r\ncookie", "3"))
        opener.assert_not_called()

        class OversizedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size=-1):
                body = b'{"success":true,"data":{"id":3}}'
                return body if size < 0 else body[:size]

        opener.return_value = OversizedResponse()
        self.assertIsNone(verifier.verify("signed-cookie", "3"))


if __name__ == "__main__":
    unittest.main()
