from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.sync_to_argon import allowed_path, digest, inventory, plan, safe_local, working_diff


class ArgonSyncTests(unittest.TestCase):
    def test_scope_rejects_secrets_large_runtime_paths_and_windows_escape(self):
        for name in ("src/main.py", "tests/new.py", "configs/update-public.pem", "docs/HANDOFF_ARGON_TEST.md"):
            self.assertTrue(allowed_path(name), name)
        for name in (".git/config", "runtime/python.exe", "vendor/a.txt", "models/model.pt", "data/file.txt",
                     "dist/a.py", "build/a.py", ".venv/a.py", "src/__pycache__/a.py", "a.pyc", "a.zip",
                     "a.onnx", "a.db-wal", "a.sqlite", ".env.example", "secrets/config.txt", "a.dpapi",
                     "configs/private.pem", "id_ed25519", "../App/a.py", "C:/ProgramData/App/a.py",
                     "src/a:stream", "src/CON", "src/a. ", "src\\a.py"):
            self.assertFalse(allowed_path(name), name)

    def test_deletion_is_limited_to_previously_managed_allowed_paths(self):
        files = {"src/unchanged.py": b"one", "src/new.py": b"two"}
        remote = {"actual": {"src/unchanged.py": digest(b"one")},
                  "managed": ["src/unchanged.py", "src/deleted.py"]}
        self.assertEqual(plan(files, remote), (["src/new.py"], ["src/deleted.py"]))
        remote["managed"].append("../App/main.py")
        with self.assertRaises(ValueError):
            plan(files, remote)

    def test_inventory_and_full_diff_include_untracked_but_never_excluded_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def run(*args):
                return subprocess.run(["git", "-c", f"safe.directory={root}", *args], cwd=root,
                                      capture_output=True, timeout=15, check=True, shell=False)

            run("init", "-q")
            (root / "src").mkdir()
            (root / "src/main.py").write_text("initial\n", encoding="utf-8")
            (root / ".env").write_text("TEST_ONLY_EXCLUDED_ENV\n", encoding="utf-8")
            run("add", ".")
            run("-c", "user.name=Isolated Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
            (root / "src/main.py").write_text("staged\n", encoding="utf-8")
            run("add", "src/main.py")
            (root / "src/main.py").write_text("working\n", encoding="utf-8")
            (root / "src/new.py").write_text("new source\n", encoding="utf-8")
            (root / ".env").write_text("TEST_ONLY_CHANGED_ENV\n", encoding="utf-8")
            original = Path.read_bytes

            def guarded(path):
                self.assertNotEqual(path.name, ".env", "excluded files must never be read")
                return original(path)

            with patch.object(Path, "read_bytes", guarded):
                files, paths = inventory(root)
            self.assertEqual(set(files), {"src/main.py", "src/new.py"})
            diff = working_diff(files, paths, root)
            self.assertIn(b"+working", diff)
            self.assertIn(b"+new source", diff)
            self.assertNotIn(b"TEST_ONLY", diff)
            with self.assertRaises(ValueError):
                safe_local(root, "../escape")


if __name__ == "__main__":
    unittest.main()
