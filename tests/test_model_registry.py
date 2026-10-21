from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.runtime import (
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_SHA256,
    ModelValidationError,
    default_model_file,
    model_manifest_path,
    validate_model_file,
)


class ModelRegistryTests(unittest.TestCase):
    def test_default_manifest_matches_verified_weight(self) -> None:
        manifest = validate_model_file()
        self.assertEqual(manifest["model_name"], DEFAULT_MODEL_NAME)
        self.assertEqual(manifest["sha256"], DEFAULT_MODEL_SHA256)
        self.assertEqual(manifest["source_repository"], "ultralytics/assets")
        self.assertEqual(manifest["release_tag"], "v8.4.0")
        self.assertEqual(manifest["file_size_bytes"], default_model_file().stat().st_size)
        self.assertTrue(model_manifest_path().is_file())

    def test_tampered_manifest_is_rejected(self) -> None:
        manifest = json.loads(model_manifest_path().read_text(encoding="utf-8"))
        manifest["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ModelValidationError, "sha256"):
                validate_model_file(manifest_path=path)

    def test_missing_model_message_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.pt"
            with self.assertRaisesRegex(ModelValidationError, "開發／建置階段"):
                validate_model_file(missing, manifest_path=Path(directory) / "manifest.json")


if __name__ == "__main__":
    unittest.main()
