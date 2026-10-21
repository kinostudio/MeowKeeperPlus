from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.build_portable import (
    PUBLIC_PACKAGE_DOCS,
    PUBLIC_RUNTIME_TOOLS,
    _copy_public_package_docs,
    _copy_public_runtime_tools,
    build_portable,
)


class PublicPackageDocumentationTests(unittest.TestCase):
    def test_package_copies_only_general_user_facing_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "package"
            (source / "docs").mkdir(parents=True)
            (source / "README.md").write_text("public overview", encoding="utf-8")
            (source / "docs" / "PUBLIC_USER_GUIDE.md").write_text("general help", encoding="utf-8")
            (source / "docs" / "CUDA_DIAGNOSTICS_PRIVATE.md").write_text("real hardware log", encoding="utf-8")
            (source / "docs" / "AI_OVERLAY_REAL_ACCEPTANCE_PRIVATE.md").write_text("camera log", encoding="utf-8")

            _copy_public_package_docs(source, target)

            copied = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
            self.assertEqual(copied, set(PUBLIC_PACKAGE_DOCS))
            self.assertFalse((target / "docs" / "CUDA_DIAGNOSTICS_PRIVATE.md").exists())
            self.assertFalse((target / "docs" / "AI_OVERLAY_REAL_ACCEPTANCE_PRIVATE.md").exists())

    def test_runtime_tool_allowlist_excludes_local_diagnostics_and_package_path_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "package"
            tools = source / "tools"
            tools.mkdir(parents=True)
            for name in PUBLIC_RUNTIME_TOOLS:
                (tools / name).write_text("runtime helper", encoding="utf-8")
            (tools / "Diagnose-R11Upgrade.ps1").write_text(
                r"C:\Users\local-user\private-upgrade-trace", encoding="utf-8"
            )

            _copy_public_runtime_tools(source, target)

            copied = {path.name for path in (target / "tools").iterdir() if path.is_file()}
            self.assertEqual(copied, set(PUBLIC_RUNTIME_TOOLS))
            self.assertFalse((target / "tools" / "Diagnose-R11Upgrade.ps1").exists())

            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "python.exe").write_bytes(b"placeholder")
            (runtime / "pythonw.exe").write_bytes(b"placeholder")
            output = Path("C:/Users/local-user/MeowKeeperPlus-portable-2026-10-20-r111")
            with (
                patch("tools.build_portable.ROOT", source),
                patch("tools.build_portable._validate_runtime", return_value=[]),
                patch("tools.build_portable.validate_model_file", return_value={"sha256": "test", "file_size_bytes": 1}),
                patch("tools.build_portable._required_files", return_value=()),
            ):
                plan = build_portable(python_runtime=runtime, output=output, dry_run=True)

            self.assertEqual(plan["output"], output.name)
            self.assertNotIn("local-user", str(plan))


if __name__ == "__main__":
    unittest.main()
