from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meowkeeper.integrations.go2rtc.ptz_backend import (
    PTZ_SHA256,
    VerifiedPtzBackend,
    ptz_backend_selected,
)


class PtzBackendTests(unittest.TestCase):
    def test_selection_is_explicit_and_rollback_is_official(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(ptz_backend_selected(root))
            selection = root / "vendor/go2rtc/backend.json"
            selection.parent.mkdir(parents=True)
            selection.write_text(json.dumps({"backend": "ptz-feedback"}))
            self.assertTrue(ptz_backend_selected(root))
            selection.write_text(json.dumps({"backend": "official"}))
            self.assertFalse(ptz_backend_selected(root))

    def test_hash_mismatch_fails_without_download(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = VerifiedPtzBackend(Path(directory))
            backend.executable.parent.mkdir(parents=True)
            backend.executable.write_bytes(b"invalid")
            backend.executable.with_name("manifest.json").write_text(json.dumps({"binary_sha256": PTZ_SHA256}))
            with self.assertRaisesRegex(ValueError, "校驗失敗"):
                backend.ensure_installed()
            with patch("meowkeeper.integrations.go2rtc.ptz_backend.sha256_file", return_value=PTZ_SHA256):
                self.assertIn("experimental", backend.ensure_installed().release_tag)
            with self.assertRaises(ValueError):
                backend.ensure_installed(force=True)
