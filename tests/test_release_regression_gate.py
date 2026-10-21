"""已知故障門檻必須真的執行，失敗／略過／舊證據不得放行。"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import build_portable as packaging
from tools import verify_regression_gate as gate


class RegressionGateTests(unittest.TestCase):
    def passed_result(self):
        entries = gate.load_registry()
        return {"status": "passed", "tests_run": len({name for item in entries for name in item["tests"]}),
                "failed_tests": [], "skipped_tests": [], "incident_ids": [item["id"] for item in entries]}

    def test_registry_rejects_empty_duplicate_and_non_test_commands(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "registry.json"
            valid = {"id": "KNOWN-FAILURE", "title": "範例", "tests": [
                "tests.test_example.ExampleTests.test_safe"]}
            for items in ([], [valid, valid], [{**valid, "tests": ["os.system('unsafe')"]}]):
                path.write_text(json.dumps({"schema_version": 1, "incidents": items}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    gate.load_registry(path)

    def test_failure_timeout_malformed_and_skipped_results_block(self):
        for response in (
            subprocess.CompletedProcess([], 1, '{"status":"failed"}', 'secret-value'),
            subprocess.CompletedProcess([], 0, 'invalid secret-value', ''),
            subprocess.CompletedProcess([], 0, '{"status":"failed","skipped_tests":["example"]}', ''),
            subprocess.CompletedProcess([], 0, '[]', ''),
            subprocess.CompletedProcess([], 0, '{"status":"passed","tests_run":0}', ''),
        ):
            with patch.object(gate, "source_digest", return_value="same"), \
                    patch.object(gate.subprocess, "run", return_value=response):
                with self.assertRaises(ValueError) as caught:
                    gate.verify_gate()
                self.assertNotIn("secret-value", str(caught.exception))
        with (patch.object(gate.subprocess, "run", side_effect=subprocess.TimeoutExpired("test", 1)),
              self.assertRaisesRegex(ValueError, "逾時")):
            gate.verify_gate(timeout=1)

    def test_changed_source_invalidates_success(self):
        response = subprocess.CompletedProcess([], 0, json.dumps(self.passed_result()), '')
        with (patch.object(gate, "source_digest", side_effect=["before", "after"]),
              patch.object(gate.subprocess, "run", return_value=response),
              self.assertRaisesRegex(ValueError, "發生變更")):
            gate.verify_gate()

    def test_skipped_worker_test_is_failure(self):
        class SkippedTest(unittest.TestCase):
            @unittest.skip("fixture")
            def test_example(self):
                pass
        entries = [{"id": "FIXTURE", "tests": ["tests.test_example.Example.test_example"]}]
        with patch.object(gate, "load_registry", return_value=entries), \
                patch.object(unittest.TestLoader, "loadTestsFromNames", return_value=unittest.TestSuite([SkippedTest("test_example")])):
            self.assertEqual(gate.run_worker()["status"], "failed")

    def test_package_release_rejects_missing_stale_or_mismatched_evidence(self):
        with tempfile.TemporaryDirectory() as scratch:
            package = Path(scratch)
            with self.assertRaises(ValueError):
                gate.verify_package_evidence(package)
            report = {**self.passed_result(),
                      "registry_sha256": gate.hashlib.sha256(gate.REGISTRY.read_bytes()).hexdigest(),
                      "source_sha256": "fixture"}
            (package / "release_regression_gate.json").write_text(json.dumps(report), encoding="utf-8")
            (package / "build_manifest.json").write_text(json.dumps({"regression_gate": report}), encoding="utf-8")
            with patch.object(gate, "source_digest", return_value="fixture"):
                self.assertEqual(gate.verify_package_evidence(package), report)
            with patch.object(gate, "source_digest", return_value="changed"), self.assertRaises(ValueError):
                gate.verify_package_evidence(package)
            (package / "build_manifest.json").write_text('{"regression_gate":{}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                gate.verify_package_evidence(package)

    def test_build_gate_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            output = root / "package"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("old package", encoding="utf-8")
            with patch.object(packaging, "_validate_runtime"), \
                    patch.object(packaging, "validate_model_file", return_value={"sha256": "test", "file_size_bytes": 1}), \
                    patch.object(packaging, "_required_files", return_value=()), \
                    patch.object(gate, "verify_gate", side_effect=ValueError("blocked")), \
                    self.assertRaisesRegex(ValueError, "blocked"):
                packaging.build_portable(python_runtime=root, output=output, force=True)
            self.assertEqual(marker.read_text(encoding="utf-8"), "old package")
            self.assertEqual(list(root.glob("*.staging-*")), [])

    @unittest.skipUnless(shutil.which("powershell.exe"), "需要Windows PowerShell")
    def test_installer_receipts_allowlist_and_invalid_phase(self):
        installer = gate.ROOT / "scripts" / "Install-MeowKeeperPlus.ps1"
        with tempfile.TemporaryDirectory() as scratch:
            command = r'''
            $ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
            $tokens=$null; $errors=$null
            $ast=[System.Management.Automation.Language.Parser]::ParseFile('__SCRIPT__',[ref]$tokens,[ref]$errors)
            if ($errors.Count) { throw 'PowerShell parser failure' }
            $fn=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-InstallOutcome'},$true)
            Invoke-Expression $fn.Extent.Text
            Write-InstallOutcome '__DATA__' 'failed' 'archive' 'C:\PrivateAccount\release-r106'
            Write-InstallOutcome '__DATA__' 'passed' 'complete' 'C:\PrivateAccount\release-r106'
            $failedClosed=$false
            try { Write-InstallOutcome '__DATA__' 'failed' 'secret-token' 'C:\PrivateAccount' } catch { $failedClosed=$true }
            if (-not $failedClosed) { throw 'arbitrary phase accepted' }
            '''.replace("__SCRIPT__", str(installer).replace("'", "''")).replace("__DATA__", scratch.replace("'", "''"))
            result = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-Command", command],
                                    capture_output=True, text=True, timeout=20, shell=False, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipts = list((Path(scratch) / "install-history").glob("*.json"))
            self.assertEqual(len(receipts), 2)
            for path in receipts:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw)
                self.assertEqual(set(data), {"schema_version", "recorded_at_utc", "package_version", "status", "phase", "incident_code"})
                self.assertEqual(data["package_version"], "r106")
                self.assertNotIn("PrivateAccount", raw)
                self.assertNotIn("secret-token", raw)
