from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MonitorSnackbarTests(unittest.TestCase):
    def test_capture_snackbar_is_nonblocking_and_auto_dismisses(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js 未安裝，略過瀏覽器 JavaScript runtime 測試。")
        result = subprocess.run(
            [node, str(ROOT / "tests" / "js" / "monitor_ui_runtime.cjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("passed:", result.stdout)


if __name__ == "__main__":
    unittest.main()
