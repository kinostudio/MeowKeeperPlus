"""Transport safety, no live Cloud writes in automated tests."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from meowkeeper.integrations.xiaomi.miot_power import MiotPowerClient


class MiotPowerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / "vendor/go2rtc/miot"
        folder.mkdir(parents=True)
        (folder / "xiaomi-miot.exe").write_bytes(b"test-binary")
        (folder / "manifest.json").write_text(json.dumps({"sha256": hashlib.sha256(b"test-binary").hexdigest()}))
        self.api = Mock()
        self.api.request_json.return_value = "xiaomi: {test: secret}"
        self.client = MiotPowerClient(self.root, self.root / "missing.yaml", self.api)

    def test_stdin_secrets_not_argv_and_reads_are_cached(self):
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"success":true,"on":true}')) as run:
            self.assertTrue(self.client.request("xiaomi://private")["on"])
            self.assertTrue(self.client.request("xiaomi://private")["on"])
            self.assertEqual(run.call_count, 1)
            kwargs = run.call_args.kwargs
            self.assertFalse(kwargs["shell"])
            self.assertNotIn("private", " ".join(run.call_args.args[0]))
            self.assertIn("config_yaml", json.loads(kwargs["input"]))
            self.client.request("xiaomi://private", on=False)
            self.assertEqual(run.call_count, 2)

    def test_checksum_failure_never_executes(self):
        (self.client.directory / "xiaomi-miot.exe").write_bytes(b"changed")
        with patch("subprocess.run") as run:
            self.assertFalse(self.client.request("private")["success"])
            run.assert_not_called()

    def test_read_only_diagnostic_uses_its_timeout_without_sending_power_write(self):
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"success":true,"on":false}')) as run:
            result = self.client.request("xiaomi://private", timeout_seconds=20)
            self.assertIs(result["on"], False)
            self.assertEqual(run.call_args.kwargs["timeout"], 20)
            self.assertNotIn("on", json.loads(run.call_args.kwargs["input"]))

    def test_raw_errors_and_response_fields_not_exposed(self):
        with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"token":"private-secret"}')):
            result = self.client.request("private")
            self.assertFalse(result["success"])
            self.assertNotIn("private-secret", json.dumps(result))
