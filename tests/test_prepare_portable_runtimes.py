"""官方可攜式 runtime 準備流程的離線安全測試。"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.prepare_portable_runtimes import _enable_embedded_packages, _extract_verified


class PreparePortableRuntimesTests(unittest.TestCase):
    def test_zip_parent_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside.txt", "bad")
            with self.assertRaisesRegex(ValueError, "不安全路徑"):
                _extract_verified(archive, root / "runtime")
            self.assertFalse((root / "outside.txt").exists())

    def test_node_archive_removes_only_expected_top_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "node.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("node-v22.23.2-win-x64/node.exe", b"placeholder")
            target = root / "javascript"
            _extract_verified(archive, target, node=True)
            self.assertEqual((target / "node.exe").read_bytes(), b"placeholder")

    def test_embedded_python_path_contains_local_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "python313._pth").write_text("python313.zip\n.\n", encoding="utf-8")
            _enable_embedded_packages(runtime)
            content = (runtime / "python313._pth").read_text(encoding="utf-8")
            self.assertIn("Lib\\site-packages", content)
            self.assertTrue((runtime / "Lib" / "site-packages").is_dir())


if __name__ == "__main__":
    unittest.main()
