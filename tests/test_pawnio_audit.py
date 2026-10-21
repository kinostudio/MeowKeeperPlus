import hashlib
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools import audit_pawnio


class PawnIoAuditTests(unittest.TestCase):
    def test_cached_hash_checked_and_only_powershell_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "reports/cpu-sensor-prototype/pawnio-audit"
            folder.mkdir(parents=True)
            (folder / "PawnIO_setup-2.2.0.exe").write_bytes(b"fixture-not-executable")
            digest = hashlib.sha256(b"fixture-not-executable").hexdigest()
            response = {"sha256": digest, "status": "Valid", "executed": False}
            with (patch.object(audit_pawnio, "ROOT", root), patch.object(audit_pawnio, "SHA256", digest),
                  patch("tools.audit_pawnio.shutil.which", return_value="fixture-pwsh.exe"),
                  patch("tools.audit_pawnio.subprocess.run", return_value=subprocess.CompletedProcess(
                      [], 0, json.dumps(response).encode(), b"")) as run,
                  redirect_stdout(StringIO())):
                self.assertEqual(audit_pawnio.main(), 0)
                command = run.call_args.args[0]
                self.assertEqual(command[0], "fixture-pwsh.exe")
                self.assertIn("Get-AuthenticodeSignature", command[-1])
                self.assertNotIn("ExecutionPolicy", str(command))
                self.assertFalse(run.call_args.kwargs["shell"])
                self.assertLessEqual(run.call_args.kwargs["timeout"], 30)
            report = json.loads((folder / "signature.json").read_text(encoding="utf-8"))
            self.assertFalse(report["installed"])
            self.assertFalse(report["executed"])

    def test_hash_mismatch_never_launches_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "reports/cpu-sensor-prototype/pawnio-audit"
            folder.mkdir(parents=True)
            (folder / "PawnIO_setup-2.2.0.exe").write_bytes(b"wrong")
            with patch.object(audit_pawnio, "ROOT", root), patch("tools.audit_pawnio.subprocess.run") as run:
                with self.assertRaises(ValueError):
                    audit_pawnio.main()
                run.assert_not_called()
