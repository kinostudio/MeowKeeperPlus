"""Signed update and real filesystem rollback tests; never touch an installed Host."""
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from meowkeeper.app.updates import REPOSITORY, canonical
from meowkeeper.security.credentials import TelegramCredentialStore
from tools.apply_verified_update import verify_and_stage
from tools.hotfix_data_snapshot import capture, restore
from tools.verify_public_source import verify_public_source

ROOT = Path(__file__).resolve().parents[1]


class HotfixTransactionTests(unittest.TestCase):
    def test_signed_package_version_must_match_envelope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, stage = root / "App", root / "work"
            (app / "configs").mkdir(parents=True)
            (app / "tools").mkdir()
            stage.mkdir()
            key = ECC.generate(curve="Ed25519")
            (app / "configs/update-public.pem").write_text(key.public_key().export_key(format="PEM"))
            (app / "build_manifest.json").write_text(json.dumps({"package_type": "windows_portable", "output": "MeowKeeperPlus-portable-2026-11-05-r116"}))
            (app / "tools/verify_portable.py").write_text("def verify_portable(package):\n    return None\n")
            archive = stage / "package.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("build_manifest.json", json.dumps({"package_type": "windows_portable", "output": "MeowKeeperPlus-portable-2026-11-05-r118"}))
            manifest = {"schema": 1, "repository": REPOSITORY, "version": "r117", "tag": "r117",
                        "asset": "MeowKeeperPlus-r117-windows-x64.zip", "size": archive.stat().st_size,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
            envelope = stage / "update-manifest.json"
            envelope.write_text(json.dumps({"manifest": manifest, "signature": base64.b64encode(eddsa.new(key, "rfc8032").sign(canonical(manifest))).decode()}))
            with self.assertRaisesRegex(ValueError, "版本"):
                verify_and_stage(archive, envelope, app_root=app, stage_root=stage)

    @unittest.skipUnless(shutil.which("powershell.exe"), "需要 Windows PowerShell")
    def test_bounded_worker_process_success_failure_and_timeout(self):
        text = (ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1").read_text(encoding="utf-8")
        begin = text.index("function Invoke-BoundedProcess")
        function = text[begin:text.index("function Stop-UpdateProcesses", begin)]
        with tempfile.TemporaryDirectory() as folder:
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            script = ("$ErrorActionPreference='Stop'\n" + function + "\n$workRoot=" + quote(folder) + "\n"
                      + "$python=" + quote(sys.executable) + "\n"
                      + "if ((Invoke-BoundedProcess $python @('-c','print(7)') 10) -ne '7') { throw 'output_mismatch' }\n"
                      + "foreach ($code in @('import sys;sys.exit(1)', 'import time;time.sleep(4)')) {\n"
                      + "$failed=$false; try { Invoke-BoundedProcess $python @('-c',$code) 1 } catch { $failed=$true }; if (-not $failed) { throw 'accepted_failed_process' }\n}\n")
            command = Path(folder) / "test.ps1"
            command.write_text(script, encoding="utf-8-sig")
            run = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(command)],
                                 capture_output=True, timeout=30, shell=False, check=False)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", errors="replace"))

    def test_source_credential_scan_blocks_without_echoing_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            secret = "1234567890:" + "AA" + "x" * 35
            (root / "bad.ps1").write_text(secret)
            with self.assertRaises(ValueError) as error:
                verify_public_source(root)
            self.assertNotIn(secret, str(error.exception))
            (root / "bad.ps1").unlink()
            self.assertEqual(verify_public_source(root), 0)
        self.assertGreater(verify_public_source(ROOT), 100)

    @unittest.skipUnless(os.name == "nt", "需要 Windows DPAPI")
    def test_merged_shared_vision_save_is_encrypted_and_deletable(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PROGRAMDATA": folder}):
            store = TelegramCredentialStore(fallback_path=Path(folder) / "unused.dpapi")
            values = {}
            store._write = lambda key, value: values.update({key: value})
            store._read = lambda key: values.get(key, "")
            store._delete = lambda key: values.pop(key, None)
            reference = "b" * 64
            store.save_vision_key(reference, "synthetic-test-key")
            self.assertNotIn(b"synthetic-test-key", store._shared_vision_path.read_bytes())
            values.clear()
            self.assertEqual(store.read_vision_key(reference), "synthetic-test-key")
            store.delete_vision_key(reference)
            self.assertEqual(store.read_vision_key(reference), "")

    def test_data_schema_and_deleted_media_are_restored_and_failed_data_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, backup = root / "Data", root / "data-backup"
            (data / "events").mkdir(parents=True)
            (data / "events" / "fixture.mp4").write_bytes(b"test-only-media")
            (data / "config.json").write_text('{"enabled": true}')
            (data / "updates").mkdir()
            (data / "updates" / "keep.zip").write_bytes(b"download-cache")
            with closing(sqlite3.connect(data / "meowkeeper.db")) as db, db:
                db.execute("CREATE TABLE example(value)")
                db.execute("INSERT INTO example VALUES (1)")
                db.execute("PRAGMA user_version=29")
            capture(data, backup)
            with closing(sqlite3.connect(data / "meowkeeper.db")) as db, db:
                db.execute("ALTER TABLE example ADD COLUMN changed")
                db.execute("PRAGMA user_version=30")
            (data / "events" / "fixture.mp4").unlink()
            (data / "config.json").write_text('{"enabled": false}')
            restore(data, backup)
            with closing(sqlite3.connect(data / "meowkeeper.db")) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 29)
                self.assertEqual(len(db.execute("PRAGMA table_info(example)").fetchall()), 1)
            self.assertEqual((data / "events" / "fixture.mp4").read_bytes(), b"test-only-media")
            self.assertEqual(json.loads((data / "config.json").read_text()), {"enabled": True})
            self.assertTrue((root / "failed-data" / "meowkeeper.db").is_file())
            self.assertTrue((data / "updates" / "keep.zip").is_file())

    def test_tampered_snapshot_refused_before_data_is_moved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "Data"
            data.mkdir()
            (data / "config.json").write_text("original")
            capture(data, root / "data-backup")
            (root / "data-backup" / "config.json").write_text("tampered")
            with self.assertRaises(ValueError):
                restore(data, root / "data-backup")
            self.assertEqual((data / "config.json").read_text(), "original")

    def test_insufficient_disk_does_not_create_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "Data"
            data.mkdir()
            with patch("tools.hotfix_data_snapshot.shutil.disk_usage", return_value=shutil._ntuple_diskusage(1, 1, 0)), self.assertRaises(ValueError):
                capture(data, root / "data-backup")
            self.assertFalse((root / "data-backup").exists())

    def test_unsigned_source_overwrite_paths_are_disabled(self):
        api = (ROOT / "src/meowkeeper/app/web/api.py").read_text(encoding="utf-8")
        self.assertNotIn("def _run_github_hotfix", api)
        for name in ("github_hotfix.ps1", "apply_hotfix.ps1", "upgrade_to_latest_installed.ps1"):
            text = (ROOT / "tools" / name).read_text(encoding="utf-8")
            self.assertNotIn("robocopy", text.lower())
            self.assertNotIn("schtasks", text.lower())
            self.assertIn("exit 1", text)

    @unittest.skipUnless(shutil.which("powershell.exe"), "需要 Windows PowerShell")
    def test_real_powershell_app_restore_and_wrong_hash_refusal(self):
        self._check_app_restore("installed_health_passed")
        self._check_app_restore("switch_started")

    def _check_app_restore(self, status):
        text = (ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1").read_text(encoding="utf-8")
        begin = text.index("function Assert-PlainPath")
        plain = text[begin:text.index("function Get-TaskRecord", begin)]
        begin = text.index("function Restore-PreviousApp")
        restore_function = text[begin:text.index("function Wait-ForDashboard", begin)]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app, old = root / "App", root / ("App.upgrade-backup-" + "a" * 32)
            app.mkdir()
            old.mkdir()
            (app / "build_manifest.json").write_text("new")
            (old / "build_manifest.json").write_text("old")
            expected = hashlib.sha256(b"old").hexdigest()
            receipt = root / "result.json"
            receipt.write_text(json.dumps({"schema": 1, "status": status,
                "old_manifest_sha256": expected, "old_backup_app": str(old), "migration_journal": ""}))
            quote = lambda p: "'" + str(p).replace("'", "''") + "'"
            script = ("$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest\nImport-Module ($env:SystemRoot + '\\System32\\WindowsPowerShell\\v1.0\\Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1')\n"
                      + plain + restore_function + "\nfunction Stop-UpdateProcesses {}\n"
                      + "$installRoot=" + quote(root) + "; $AppRoot=" + quote(app) + "\n"
                      + "try { Restore-PreviousApp " + quote(receipt) + " ('f'*64); throw 'accepted_wrong_hash' } "
                      + "catch { if ($_.Exception.Message -eq 'accepted_wrong_hash') { throw } }\n"
                      + "if (-not (Restore-PreviousApp " + quote(receipt) + " '" + expected + "')) { throw 'restore_failed' }\n"
                      + "if ((Get-Content -Raw -LiteralPath (Join-Path $AppRoot 'build_manifest.json')) -ne 'old') { throw 'wrong_version' }\n")
            command = root / "test.ps1"
            command.write_text(script, encoding="utf-8-sig")
            run = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(command)],
                                 capture_output=True, timeout=30, shell=False, check=False)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
