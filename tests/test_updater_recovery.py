"""r120 update regressions: coordinator lifetime, cache collisions, rollback state.

All fixtures are synthetic; nothing here touches an installed Host, real Data or secrets.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools import bootstrap_delta
from tools.migrate_legacy_app_data import (
    MigrationConflict,
    inspect_legacy_data,
    move_legacy_data,
    residual_persistent_entries,
    rollback_data,
    runtime_cache_only,
)
from tools.migrate_legacy_app_data import (
    main as migration_main,
)

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1"
INSTALLER = ROOT / "scripts/Install-MeowKeeperPlus.ps1"
FONT_CACHE = json.dumps({"_version": 390, "afmlist": [], "ttflist": [{"fname": "a.ttf"}]})
ULTRA_SETTINGS = json.dumps({"settings_version": "0.0.6", "datasets_dir": "d", "weights_dir": "w", "runs_dir": "r"})


def tree_digest(root: Path) -> dict[str, str]:
    """Byte-level fingerprint of every file and folder under root."""
    result = {}
    for path in sorted(root.rglob("*")):
        key = path.relative_to(root).as_posix()
        result[key] = "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


class R116LikeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.old_app = self.root / "App.b-0123456789abcdef"
        self.source = self.old_app / "data"
        self.destination = self.root / "Data"
        self.journal = self.root / "work" / "legacy-data-migration.json"
        # Legacy r116-style install fixture: locks and three runtime caches.
        write(self.source / "locks/MeowKeeperPlusHost.json", "{}")
        write(self.source / "matplotlib/fontlist-v390.json", FONT_CACHE)
        write(self.source / "ultralytics/Ultralytics/settings.json", ULTRA_SETTINGS)
        (self.source / "torch/hub/checkpoints").mkdir(parents=True)
        # Same-named caches already rebuilt by the r116 Core under Data.
        write(self.destination / "matplotlib/fontlist-v390.json", FONT_CACHE.replace("a.ttf", "b.ttf"))
        write(self.destination / "ultralytics/settings.json", ULTRA_SETTINGS)
        (self.destination / "torch").mkdir(parents=True)
        write(self.destination / "events/event_1.mp4", b"\x00video")
        write(self.destination / "pets/cat_a/profile.json", '{"name":"fixture"}')
        write(self.destination / "config.json", '{"dashboard":{"port":8000}}')

    def tearDown(self) -> None:
        self._temporary.cleanup()


class RuntimeCacheCollisionTests(R116LikeFixture):
    def test_known_runtime_caches_are_retained_not_moved_not_deleted(self) -> None:
        write(self.source / "recordings/legacy.mp4", b"legacy")
        before_source, before_destination = tree_digest(self.source), tree_digest(self.destination)
        result = move_legacy_data(str(self.source), str(self.destination), str(self.journal))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["moved_entries"], 1)
        self.assertEqual(sorted(result["retained_runtime_entries"]), ["matplotlib", "torch", "ultralytics"])
        # Caches stay byte-identical on both sides: no merge, no overwrite, no delete.
        for name in ("matplotlib", "ultralytics", "torch"):
            self.assertEqual({k: v for k, v in tree_digest(self.source).items() if k.startswith(name)},
                             {k: v for k, v in before_source.items() if k.startswith(name)})
            self.assertEqual({k: v for k, v in tree_digest(self.destination).items() if k.startswith(name)},
                             {k: v for k, v in before_destination.items() if k.startswith(name)})
        self.assertEqual((self.destination / "recordings/legacy.mp4").read_bytes(), b"legacy")
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal["planned_entries"], ["recordings"])
        rollback_data(None, None, self.journal)
        self.assertEqual(tree_digest(self.source), before_source)
        self.assertEqual(tree_digest(self.destination), before_destination)

    def test_only_runtime_caches_is_a_clean_skip(self) -> None:
        before_source, before_destination = tree_digest(self.source), tree_digest(self.destination)
        result = move_legacy_data(str(self.source), str(self.destination), str(self.journal))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(sorted(result["retained_runtime_entries"]), ["matplotlib", "torch", "ultralytics"])
        self.assertEqual(tree_digest(self.source), before_source)
        self.assertEqual(tree_digest(self.destination), before_destination)

    def test_preflight_inspect_is_read_only_and_ready(self) -> None:
        before_source, before_destination = tree_digest(self.source), tree_digest(self.destination)
        report = inspect_legacy_data(str(self.source), str(self.destination), include_hash=True)
        self.assertEqual(report["status"], "ready")
        categories = {item["name"]: item["category"] for item in report["items"]}
        self.assertEqual(categories, {"locks": "ignored", "matplotlib": "runtime_cache_retained",
                                      "torch": "runtime_cache_retained", "ultralytics": "runtime_cache_retained"})
        matplotlib = next(item for item in report["items"] if item["name"] == "matplotlib")
        self.assertTrue(matplotlib["collision"])
        self.assertEqual(len(matplotlib["source"]["sha256"]), 64)
        self.assertNotIn("ttflist", json.dumps(report))  # metadata only, never contents
        self.assertEqual(tree_digest(self.source), before_source)
        self.assertEqual(tree_digest(self.destination), before_destination)

    def test_residual_check_lets_cache_only_backup_go_but_keeps_real_data(self) -> None:
        self.assertEqual(residual_persistent_entries(str(self.source))["persistent_entries"], [])
        write(self.source / "events/forgotten.mp4", b"keep me")
        self.assertEqual(residual_persistent_entries(str(self.source))["persistent_entries"], ["events"])
        write(self.source / "torch/hub/repo.py", "code")
        self.assertEqual(residual_persistent_entries(str(self.source))["persistent_entries"], ["events", "torch"])
        self.assertEqual(residual_persistent_entries(str(self.root / "missing"))["persistent_entries"], [])


class PersistentCollisionFailClosedTests(R116LikeFixture):
    def _assert_refused_unchanged(self, *expected_names: str, legacy_config: Path | None = None) -> MigrationConflict:
        before_source, before_destination = tree_digest(self.source), tree_digest(self.destination)
        config_before = legacy_config.read_bytes() if legacy_config else None
        with self.assertRaises(MigrationConflict) as caught:
            move_legacy_data(str(self.source), str(self.destination), str(self.journal),
                             str(legacy_config) if legacy_config else None)
        self.assertEqual(tree_digest(self.source), before_source)
        self.assertEqual(tree_digest(self.destination), before_destination)
        if legacy_config:
            self.assertEqual(legacy_config.read_bytes(), config_before)
        self.assertFalse(self.journal.exists())
        names = [item["name"] for item in caught.exception.collisions]
        self.assertEqual(sorted(names), sorted(expected_names))
        for name in expected_names:
            self.assertIn(name, str(caught.exception))
        self.assertIn("沒有覆寫任何資料", str(caught.exception))
        return caught.exception

    def test_events_pets_state_config_collisions_are_refused_bit_for_bit(self) -> None:
        write(self.source / "events/event_1.mp4", b"\x01older video")
        write(self.source / "pets/cat_a/profile.json", '{"name":"older"}')
        write(self.source / "state/runtime.json", "{}")
        write(self.destination / "state/runtime.json", '{"newer":true}')
        write(self.old_app / "config.json", '{"dashboard":{"port":8001}}')
        self._assert_refused_unchanged("events", "pets", "state", "config.json", legacy_config=self.old_app / "config.json")

    def test_corrupt_oversized_or_unknown_cache_content_is_persistent(self) -> None:
        cases = {
            "corrupt": ("matplotlib/fontlist-v390.json", "{not json"),
            "wrong_version": ("matplotlib/fontlist-v390.json", json.dumps({"_version": 1, "afmlist": [], "ttflist": []})),
            "oversized": ("ultralytics/Ultralytics/settings.json", " " * 65537),
            "unknown_file": ("ultralytics/Ultralytics/other.json", "{}"),
            "torch_file": ("torch/hub/checkpoints/weights.pth", b"weights"),
            "matplotlib_subfolder": ("matplotlib/tex.cache/a.json", "{}"),
        }
        for label, (relative, content) in cases.items():
            with self.subTest(label=label):
                target = self.source / relative
                original = target.read_bytes() if target.exists() else None
                write(target, content)
                top = relative.split("/", 1)[0]
                self.assertFalse(runtime_cache_only(self.source / top))
                self._assert_refused_unchanged(top)
                if original is None:
                    target.unlink()
                    if label == "matplotlib_subfolder":
                        target.parent.rmdir()
                else:
                    target.write_bytes(original)

    @unittest.skipIf(os.name == "nt", "建立符號連結在 Windows 需要額外權限")
    def test_link_inside_cache_is_refused(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text(ULTRA_SETTINGS, encoding="utf-8")
        os.symlink(outside, self.source / "ultralytics/linked.json")
        with self.assertRaisesRegex(ValueError, "連結"):
            move_legacy_data(str(self.source), str(self.destination), str(self.journal))
        self.assertEqual(outside.read_text(encoding="utf-8"), ULTRA_SETTINGS)

    def test_cli_reports_collision_names_without_contents(self) -> None:
        write(self.source / "events/event_1.mp4", b"secret-ish bytes")
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        with redirect_stdout(output):
            code = migration_main(["--mode", "move", "--source", str(self.source), "--destination",
                                   str(self.destination), "--journal", str(self.journal)])
        self.assertEqual(code, 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["error_type"], "MigrationConflict")
        self.assertEqual([item["name"] for item in payload["collisions"]], ["events"])
        self.assertNotIn("secret-ish", output.getvalue())
        inspect_output = StringIO()
        with redirect_stdout(inspect_output):
            self.assertEqual(migration_main(["--mode", "inspect", "--source", str(self.source),
                                             "--destination", str(self.destination)]), 0)
        report = json.loads(inspect_output.getvalue())
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["blocking_entries"], ["events"])


class CoordinatorLifetimeTests(unittest.TestCase):
    def test_worker_is_consoleless_breakaway_and_never_awaited(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            popen = Mock()
            mode = bootstrap_delta.launch_worker(root / "worker.ps1", root, root / "worker.log", popen=popen)
            self.assertEqual(mode, "breakaway")
            arguments, options = popen.call_args
            flags = options["creationflags"]
            self.assertTrue(flags & bootstrap_delta.CREATE_NO_WINDOW)
            self.assertTrue(flags & bootstrap_delta.CREATE_NEW_PROCESS_GROUP)
            self.assertTrue(flags & bootstrap_delta.CREATE_BREAKAWAY_FROM_JOB)
            self.assertFalse(options["shell"])
            self.assertTrue(arguments[0][0].lower().endswith("windowspowershell/v1.0/powershell.exe".replace("/", os.sep))
                            or arguments[0][0].lower().endswith("powershell.exe"))
            self.assertNotIn("python", arguments[0][0].lower())
            popen.return_value.wait.assert_not_called()
            popen.return_value.communicate.assert_not_called()

    def test_breakaway_denied_falls_back_to_new_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            popen = Mock(side_effect=[PermissionError("job forbids breakaway"), Mock()])
            self.assertEqual(bootstrap_delta.launch_worker(root / "w.ps1", root, root / "w.log", popen=popen), "new-process-group")
            self.assertTrue(popen.call_args_list[1].kwargs["creationflags"] & bootstrap_delta.CREATE_NO_WINDOW)
            self.assertFalse(popen.call_args_list[1].kwargs["creationflags"] & bootstrap_delta.CREATE_BREAKAWAY_FROM_JOB)

    def test_worker_start_failure_preserves_final_oserror(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            popen = Mock(side_effect=[PermissionError("breakaway denied"), OSError("worker cannot start")])
            with self.assertRaisesRegex(OSError, "worker cannot start"):
                bootstrap_delta.launch_worker(root / "w.ps1", root, root / "w.log", popen=popen)

    def test_waits_only_for_matching_request_then_returns(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            status = Path(folder) / "hotfix-status.json"
            status.write_text(json.dumps({"request_id": "b" * 32, "status": "applied"}), encoding="utf-8")
            ticks = iter(range(1000))
            sleeps = []
            def sleep(_seconds):
                sleeps.append(1)
                if len(sleeps) == 2:
                    status.write_text(json.dumps({"request_id": "a" * 32, "status": "applying"}), encoding="utf-8")
            found = bootstrap_delta.wait_for_worker_start(status, "a" * 32, timeout=50, sleep=sleep, clock=lambda: next(ticks))
            self.assertEqual(found["status"], "applying")
            self.assertIsNone(bootstrap_delta.wait_for_worker_start(status, "c" * 32, timeout=3, sleep=lambda _s: None,
                                                                     clock=lambda: next(ticks)))

    def test_bootstrap_no_longer_blocks_on_worker_from_app_python(self) -> None:
        source = (ROOT / "tools/bootstrap_delta.py").read_text(encoding="utf-8")
        self.assertNotIn('subprocess.run(["powershell.exe"', source)
        self.assertIn("launch_worker(worker, payload", source)
        # Stop rules are not relaxed: the installer still stops every bundled interpreter.
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('"AI／Python 子程序"', installer)
        self.assertIn("仍有此安裝目錄的 Python／AI 子程序執行；升級已中止。", installer)

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "需要 Windows PowerShell")
    def test_killing_app_python_coordinator_does_not_kill_worker(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            marker = root / "worker-finished.txt"
            worker_pid = root / "worker.pid"
            worker = root / "worker.ps1"
            worker.write_text("Set-Content -LiteralPath '" + str(worker_pid).replace("'", "''") + "' -Value $PID\n"
                              "Start-Sleep -Seconds 3\nSet-Content -LiteralPath '" + str(marker).replace("'", "''") + "' -Value done\n",
                              encoding="utf-8-sig")
            coordinator = root / "coordinator.py"
            coordinator.write_text(
                "import sys, time\nsys.path.insert(0, " + repr(str(ROOT)) + ")\n"
                "from pathlib import Path\nfrom tools.bootstrap_delta import launch_worker\n"
                "root = Path(" + repr(str(root)) + ")\n"
                "launch_worker(root / 'worker.ps1', root, root / 'worker.log')\n"
                "print('launched', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
            process = subprocess.Popen([sys.executable, "-I", str(coordinator)], stdout=subprocess.PIPE, shell=False)
            try:
                self.assertEqual(process.stdout.readline().strip(), b"launched")
                process.kill()  # what Stop-InstalledHostProcess does to an App-runtime Python
                process.wait(10)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline and not marker.exists():
                    time.sleep(0.25)
                self.assertTrue(marker.exists(), "worker died with its coordinator")
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
                kernel32.OpenProcess.restype = ctypes.c_void_p
                kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
                kernel32.WaitForSingleObject.restype = ctypes.c_uint32
                kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
                handle = kernel32.OpenProcess(0x00100000, False, int(worker_pid.read_text(encoding="utf-8")))
                if handle:
                    try:
                        self.assertEqual(kernel32.WaitForSingleObject(handle, 10000), 0, "worker retained temp files after timeout")
                    finally:
                        kernel32.CloseHandle(handle)
                else:
                    self.assertEqual(ctypes.get_last_error(), 87, "worker process could not be awaited")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(10)
                if process.stdout:
                    process.stdout.close()


class RollbackStateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = WORKER.read_text(encoding="utf-8")
        self.installer = INSTALLER.read_text(encoding="utf-8")

    def _function(self, name: str, until: str) -> str:
        start = self.worker.index("function " + name)
        return self.worker[start:self.worker.index("function " + until, start)]

    def test_running_state_comes_from_process_and_health_not_only_task(self) -> None:
        snapshot = self._function("Get-RuntimeStateSnapshot", "Test-RestoredRuntime")
        self.assertIn("core_was_active = ([bool]$taskRecords[0].WasRunning -or $coreProcesses.Count -gt 0 -or [bool]$healthy)", snapshot)
        self.assertIn("$taskRecords[0].WasRunning = [bool]$runtimeState.core_was_active", self.worker)
        # Snapshot happens before the installer can stop anything.
        self.assertLess(self.worker.index("$runtimeState = Get-RuntimeStateSnapshot"),
                        self.worker.index("Invoke-BoundedProcess $powershell $installerArgs"))

    def test_rollback_requires_original_principal_args_and_core_when_it_ran(self) -> None:
        restored = self._function("Test-RestoredRuntime", "Restore-TaskRecords")
        self.assertIn("-ExpectedPrincipalSid $Snapshot.core_principal_sid", restored)
        self.assertIn("-ExpectedPrincipalSid $Snapshot.tray_principal_sid", restored)
        self.assertIn("Join-Path $Snapshot.core_data_root 'config.json'", restored)
        self.assertIn("if (-not $Snapshot.core_was_active) { return '' }", restored)
        self.assertIn("'rollback_core_health_timeout'", restored)
        self.assertNotIn("old_dashboard_health_failed", self.worker)

    def test_rollback_failure_is_diagnosable_never_reported_as_success(self) -> None:
        self.assertIn("Write-UpdateStatus 'rollback_failed'", self.worker)
        self.assertIn("$rollbackCode ('更新失敗原因：' + $failureCode)", self.worker)
        self.assertIn("if (-not $rollbackCode) { $rollbackCode = 'rollback_unclassified_error' }", self.worker)

    def test_installer_preflight_blocks_before_any_stop(self) -> None:
        preflight = self.installer.index('"--mode", "inspect"')
        self.assertLess(preflight, self.installer.index("Stop-VerifiedRepositoryTray $packageRoot\n}"))
        self.assertLess(preflight, self.installer.index('& "$env:SystemRoot\\System32\\schtasks.exe" /End /TN $task'))
        self.assertIn("status = 'preflight_blocked'", self.installer)
        self.assertIn("$blocked.status -eq 'preflight_blocked'", self.worker)

    @unittest.skipUnless(shutil.which("powershell.exe"), "需要 Windows PowerShell")
    def test_powershell_rollback_matrix_running_ready_and_timeouts(self) -> None:
        restored = self._function("Test-RestoredRuntime", "Restore-TaskRecords")
        cases = [
            # (core_was_active, definitions_ok, healthy, core_processes, expected)
            ("$true", "$true", "$true", 1, ""),
            ("$false", "$true", "$false", 0, ""),
            ("$true", "$true", "$false", 1, "rollback_core_health_timeout"),
            ("$true", "$true", "$true", 0, "rollback_core_process_missing"),
            ("$true", "$false", "$true", 1, "rollback_core_task_definition_mismatch"),
        ]
        for active, definitions, healthy, processes, expected in cases:
            with self.subTest(active=active, definitions=definitions, healthy=healthy, processes=processes):
                script = (
                    "$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest\n"
                    "$AppRoot='C:\\ProgramData\\MeowKeeperPlus\\App'; $installRoot='C:\\ProgramData\\MeowKeeperPlus'\n"
                    "$coreTaskName='MeowKeeperPlus Core'; $trayTaskName='MeowKeeperPlus Tray'; $script:seen=@()\n"
                    "function Test-ExpectedTaskDefinition { param($Name,$ExpectedScript,$ExpectedRuntime,$RequiredArguments,$ExpectedPrincipalSid)\n"
                    "  $script:seen += $ExpectedPrincipalSid; return " + definitions + " }\n"
                    "function Wait-ForDashboard { param($TimeoutSeconds,$ConfigDataRoot)\n"
                    "  if (-not " + active + ") { throw 'health_checked_for_stopped_core' }; return " + healthy + " }\n"
                    "function Get-InstalledRoleProcesses { param($Runtime,$Script) if (" + str(processes) + " -gt 0) { return @(1.." + str(processes) + ") } }\n"
                    + restored +
                    "\n$snapshot=[pscustomobject]@{core_was_active=" + active + "; core_principal_sid='S-1-5-18';"
                    " tray_principal_sid='S-1-5-21-1-2-3-1001'; core_data_root='C:\\ProgramData\\MeowKeeperPlus\\Data'}\n"
                    "$result = Test-RestoredRuntime $snapshot\n"
                    "if ($result -ne '" + expected + "') { throw ('unexpected:' + $result) }\n"
                    "if (" + definitions + " -and ($script:seen -notcontains 'S-1-5-18' -or $script:seen -notcontains 'S-1-5-21-1-2-3-1001')) { throw 'principal_not_checked' }\n"
                )
                with tempfile.TemporaryDirectory() as folder:
                    command = Path(folder) / "matrix.ps1"
                    command.write_text(script, encoding="utf-8-sig")
                    run = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(command)],
                                         capture_output=True, timeout=30, shell=False, check=False)
                    self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
