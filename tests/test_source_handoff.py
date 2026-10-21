from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.source_handoff import ROOT, build_bundle, validate_paths


class SourceHandoffTests(unittest.TestCase):
    def test_canonical_manifest_includes_all_36_sources_and_four_tests(self):
        paths = json.loads((ROOT / "configs/source-handoff-paths.json").read_text(encoding="utf-8"))
        self.assertEqual(len(validate_paths(paths)), 40)
        self.assertEqual(sum(name.startswith("tests/") for name in paths), 4)

    def test_pinned_commits_produce_both_applyable_patches_and_ignore_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def run(*args, cwd=root):
                return subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", *args],
                                      cwd=cwd, capture_output=True, check=True, timeout=30, shell=False).stdout

            run("init", "-q")
            for name in ("src/main.py", "tests/test_main.py"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("old\n", encoding="utf-8")
            run("add", ".")
            run("-c", "user.name=Source Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base")
            base = run("rev-parse", "HEAD").decode().strip()
            (root / "src/main.py").write_text("new\n", encoding="utf-8")
            (root / "tests/test_main.py").write_text("new test without final newline", encoding="utf-8")
            run("add", ".")
            run("-c", "user.name=Source Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "done")
            head = run("rev-parse", "HEAD").decode().strip()
            (root / "src/main.py").write_text("UNCOMMITTED", encoding="utf-8")
            out = root / "data/bundle"
            manifest = build_bundle(root, base, head, ["src/main.py", "tests/test_main.py"], out)
            self.assertEqual((manifest["base_commit"], manifest["completed_commit"]), (base, head))
            self.assertEqual((out / "files/src/main.py").read_bytes(), b"new\n")
            for row in manifest["files"]:
                self.assertEqual(hashlib.sha256((out / "files" / row["path"]).read_bytes()).hexdigest(), row["sha256"])
            with tempfile.TemporaryDirectory() as empty, tempfile.TemporaryDirectory() as old_dir:
                run("apply", "--check", str(out / "snapshot.patch"), cwd=Path(empty))
                old = Path(old_dir)
                for row in manifest["files"]:
                    target = old / row["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(run("show", f"{base}:{row['path']}"))
                run("apply", "--check", str(out / "changes.patch"), cwd=old)
            with self.assertRaises(ValueError):
                build_bundle(root, base, head, ["src/main.py"], out)

    def test_private_and_escaping_paths_are_rejected(self):
        for name in ("data/config.json", "src/../secrets.json", "src/a.dpapi", "/src/a.py", "src/a.py:stream"):
            with self.subTest(path=name), self.assertRaises(ValueError):
                validate_paths([name])
