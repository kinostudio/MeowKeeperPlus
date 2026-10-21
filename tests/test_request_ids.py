import shutil
import subprocess
import unittest
from pathlib import Path


class RequestIdTests(unittest.TestCase):
    def test_lan_http_request_ids_execute_without_random_uuid(self):
        root = Path(__file__).resolve().parents[1]
        bundled = root / "vendor/javascript/node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if node is None:
            self.skipTest("JavaScript runtime unavailable")
        result = subprocess.run([node, str(root / "tests/js/request_id_runtime.cjs")],
                                capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        web = root / "src/meowkeeper/app/web"
        for name in ("event-review.js", "identity-detail.js"):
            source = (web / "static/js" / name).read_text(encoding="utf-8")
            self.assertNotIn("crypto.randomUUID", source)
            self.assertIn("MeowKeeperRequestId()", source)
        self.assertIn('/static/js/request-id.js?v=', (web / "templates/base.html").read_text(encoding="utf-8"))
