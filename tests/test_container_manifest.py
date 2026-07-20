import unittest
from pathlib import Path


class ContainerManifestTests(unittest.TestCase):
    def test_runtime_image_includes_key_usage_module(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

        self.assertIn("dashboard_key_usage.py", dockerfile)


if __name__ == "__main__":
    unittest.main()
