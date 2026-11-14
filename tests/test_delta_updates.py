import base64
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock, patch

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from meowkeeper.app.delta_update import (
    assemble,
    digest,
    plain,
    validate_base,
    validate_manifest,
    verify_delta,
    wide,
)
from meowkeeper.app.updates import UpdateManager, canonical, verify_envelope
from tools.apply_verified_update import verify_and_stage
from tools.bootstrap_delta import BOOTSTRAP_FILES, complete_bootstrap, prepare
from tools.bootstrap_delta import main as bootstrap_main
from tools.prepare_delta_release import build_delta, validate_release_baseline_overrides
from tools.prepare_delta_release import main as prepare_release_main
from tools.verify_portable import verify_portable

ROOT = Path(__file__).resolve().parents[1]


class DeltaUpdateTests(unittest.TestCase):
    def test_worker_lock_blocks_duplicate_update_and_releases_after_exit(self):
        worker = (ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1").read_text(encoding="utf-8")
        start = worker.index("function Enter-UpdateLock")
        function = worker[start:worker.index("function Invoke-BoundedProcess", start)]
        quoted = "'" + str(self.root).replace("'", "''") + "'"
        command = "$ErrorActionPreference='Stop';function Assert-PlainPath($Path){};" + function
        command += "$first=Enter-UpdateLock " + quoted + ";$blocked=$false;try{$second=Enter-UpdateLock " + quoted
        command += "}catch [IO.IOException]{$blocked=$true};if(-not $blocked){throw 'duplicate_update_allowed'};$first.Dispose();"
        command += "$third=Enter-UpdateLock " + quoted + ";$third.Dispose()"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, timeout=10, shell=False, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertIn("if ($workerLock) { $workerLock.Dispose() }", worker)

    def test_classic_windows_transaction_paths_fit_bundled_200_character_names(self):
        for root in (r"C:\ProgramData\MeowKeeperPlus\Updater\work-aaaaaaaa\a",
                     r"C:\ProgramData\MeowKeeperPlus\App.b-aaaaaaaaaaaaaaaa",
                     r"C:\ProgramData\MeowKeeperPlus\App.f-aaaaaaaaaaaaaaaa"):
            path = PureWindowsPath(root) / ("x" * 191) / "file.txt"
            self.assertEqual(len(str(path)) - len(root) - 1, 200)
            self.assertLess(len(str(path)), 260)
            self.assertLess(len(str(path.parent)), 248)
        worker = (ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1").read_text(encoding="utf-8")
        self.assertIn("$requestId.Substring(0, 8)", worker)

    def test_extended_python_paths_are_idempotent_and_preserve_bytes(self):
        root = self.root / "long-path-fixture"
        path = root / ("a" * 100) / ("b" * 100) / "value.txt"
        try:
            wide(path).parent.mkdir(parents=True)
            wide(path).write_bytes(b"unchanged")
            self.assertEqual(wide(wide(path)), wide(path))
            self.assertEqual(digest(path), digest(wide(path)))
            if os.name == "nt":
                self.assertTrue(str(wide(path)).startswith("\\\\?\\"))
        finally:
            if wide(root).exists():
                shutil.rmtree(wide(root))

    def test_admin_bootstrap_stages_requests_only_under_protected_updater(self):
        install = self.root / "MeowKeeperPlus"
        app, data, updater = install / "App", install / "Data", install / "Updater"
        shutil.copytree(self.base, app)
        data.mkdir()
        updater.mkdir()
        commands, launched = [], []
        def fetch(_manager, url, destination, _limit):
            shutil.copy2(self.out / url.rsplit("/", 1)[1], destination)
        def execute(arguments, **_options):
            commands.append(arguments)
            return subprocess.CompletedProcess(arguments, 0)
        def launch(arguments, **options):
            launched.append((arguments, options))
            self.assertTrue(arguments[0].lower().endswith("powershell.exe"))
            payload = Path(arguments[arguments.index("-VerifierRoot") + 1])
            self.assertEqual(payload.parent, updater)
            request = json.loads((payload / "apply-request.json").read_text())
            prepared = payload / request["prepared_id"]
            self.assertEqual(digest(prepared / "package.zip"), digest(self.archive))
            self.assertFalse((data / "updates/apply-request.json").exists())
            (updater / "hotfix-status.json").write_text(json.dumps({"request_id": request["request_id"], "status": "applying"}))
            return Mock()
        with patch.dict(os.environ, {"ProgramData": str(self.root)}), \
                patch("sys.argv", ["bootstrap", "--version", "r118", "--apply"]), \
                patch("tools.bootstrap_delta.ctypes.windll.shell32.IsUserAnAdmin", return_value=True), \
                patch.object(UpdateManager, "_download", fetch), \
                patch("tools.bootstrap_delta.subprocess.run", side_effect=execute), \
                patch("tools.bootstrap_delta.subprocess.Popen", side_effect=launch):
            self.assertEqual(bootstrap_main(), 0)
        self.assertEqual(len(commands), 1)  # icacls only; the worker is never awaited from App Python
        self.assertEqual(len(launched), 1)
        self.assertEqual(list(data.rglob("*")), [])
        worker = (ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1").read_text(encoding="utf-8")
        self.assertIn("$updatesRoot = $VerifierRoot", worker)

    def test_managed_hotfix_never_opens_driver_install_prompt(self):
        installer = (ROOT / "scripts/Install-MeowKeeperPlus.ps1").read_text(encoding="utf-8")
        guard = re.search(r"if \((-not \$HotfixManaged -and -not \$WhatIf.*?)\) \{\s*Add-Type -AssemblyName System.Windows.Forms", installer, re.DOTALL).group(1)
        worker = (ROOT / "scripts/Apply-MeowKeeperPlusUpdate.ps1").read_text(encoding="utf-8")
        arguments = next(line for line in worker.splitlines() if "$installerArgs = @(" in line)
        self.assertIn("'-SkipCpuTemperature'", arguments)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "vendor/cpu-sensor").mkdir(parents=True)
            (root / "vendor/cpu-sensor/manifest.json").write_text("{}")
            quoted = "'" + str(root).replace("'", "''") + "'"
            command = "$packageRoot=" + quoted + ";$WhatIf=$false;$EnableCpuTemperature=$false;$SkipCpuTemperature=$false;"
            command += "$HotfixManaged=$true;if(" + guard + "){throw 'managed_update_prompt'};"
            command += "$HotfixManaged=$false;if(-not(" + guard + ")){throw 'manual_install_prompt_missing'}"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                    capture_output=True, timeout=10, check=False, shell=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_download_size_labels_use_correct_decimal_units(self):
        source = (ROOT / "src/meowkeeper/app/web/static/js/updates.js").read_text(encoding="utf-8")
        function = re.search(r"  function formatBytes\(value\) \{.*?\n  \}", source, re.DOTALL).group(0)
        node = next(path for path in (ROOT / "vendor/javascript/node.exe", ROOT / "../../vendor/javascript/node.exe") if path.is_file())
        result = subprocess.run([str(node.resolve()), "-e", function + ";console.log(JSON.stringify([1000,1000000,1000000000,1366019].map(formatBytes)))"],
                                check=True, capture_output=True, text=True, timeout=10, shell=False)
        self.assertEqual(json.loads(result.stdout), ["1.00 KB", "1.00 MB", "1.00 GB", "1.37 MB"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base, self.target = self.root / "base", self.root / "target"
        self.key = ECC.generate(curve="Ed25519")
        content = {
            "runtime/python/python.exe": b"unchanged-python",
            "runtime/python/pythonw.exe": b"unchanged-pythonw",
            "runtime/python/cuda.dll": b"unchanged-cuda",
            "vendor/ffmpeg/bin/ffmpeg.exe": b"unchanged-ffmpeg",
            "vendor/ffmpeg/bin/ffprobe.exe": b"unchanged-ffprobe",
            "models/yolo11n.pt": b"unchanged-model",
            "models/model_manifest.json": b"{}",
            "tools/run_host.py": b"# fixture",
            "tools/run_tray.py": b"# fixture",
            "configs/botsort_reid.yaml": b"# fixture",
            "configs/update-public.pem": self.key.public_key().export_key(format="PEM").encode(),
            "src/demo.py": b"old", "src/deleted.py": b"delete",
            "tools/verify_portable.py": (ROOT / "tools/verify_portable.py").read_bytes(),
        }
        content.update({name: (ROOT / name).read_bytes() for name in BOOTSTRAP_FILES})
        for name, data in content.items():
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.manifest(self.base, "r116")
        shutil.copytree(self.base, self.target)
        (self.target / "src/demo.py").write_bytes(b"new")
        (self.target / "src/deleted.py").unlink()
        (self.target / "src/added.py").write_bytes(b"added")
        module = self.target / "src/meowkeeper/app/delta_update.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_bytes((ROOT / "src/meowkeeper/app/delta_update.py").read_bytes())
        self.manifest(self.target, "r118")
        self.out = self.root / "output"
        with patch("tools.prepare_delta_release.verify_package_evidence", return_value={}):
            self.report = build_delta(self.base, self.target, self.out, self.key)
        self.envelope = json.loads((self.out / "update-manifest-delta.json").read_text(encoding="utf-8"))
        self.delta = self.envelope["manifest"]
        self.archive = self.out / self.delta["asset"]

    def manifest(self, root, version):
        files = [{"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": digest(path)}
                 for path in sorted(root.rglob("*")) if path.is_file() and path.name != "build_manifest.json"]
        (root / "build_manifest.json").write_text(json.dumps({"package_type": "windows_portable",
            "output": f"MeowKeeperPlus-portable-2026-11-05-{version}", "files": files,
            "model": {"sha256": digest(root / "models/yolo11n.pt")}}), encoding="utf-8")

    def test_delta_contains_only_changed_files_and_rebuilds_exact_target(self):
        self.assertEqual(self.report["heavy_files"], [])
        with zipfile.ZipFile(self.archive) as bundle:
            self.assertEqual(set(bundle.namelist()), {item["path"] for item in self.delta["changed"]})
            self.assertFalse(any(name.startswith(("runtime/", "vendor/", "models/")) for name in bundle.namelist()))
        key = self.key.public_key().export_key(format="PEM").encode()
        self.assertEqual(verify_envelope(self.envelope, key), verify_delta(self.envelope, key))
        assembled = self.root / "rebuilt"
        assemble(self.base, self.archive, self.delta, assembled)
        for item in json.loads((self.target / "build_manifest.json").read_text())["files"]:
            self.assertEqual(digest(assembled / item["path"]), item["sha256"])
        self.assertFalse((assembled / "src/deleted.py").exists())
        self.assertEqual((self.base / "src/demo.py").read_bytes(), b"old")

    def test_r116_delta_base_allows_only_retired_launchers(self):
        retired = {"MeowKeeperPlusDashboard.cmd", "MeowKeeperPlusTray.cmd"}
        for name in retired:
            (self.base / name).write_text("legacy entry", encoding="utf-8")
        self.manifest(self.base, "r116")
        with self.assertRaisesRegex(ValueError, "不得安裝"):
            verify_portable(self.base)

        output = self.root / "r116-delta"
        with patch("tools.prepare_delta_release.verify_package_evidence", return_value={}):
            result = build_delta(self.base, self.target, output, self.key)
        manifest = json.loads((output / "update-manifest-delta.json").read_text(encoding="utf-8"))["manifest"]
        self.assertEqual(set(manifest["deleted"]) & retired, retired)
        self.assertTrue(result["status"] == "passed")

        untrusted = self.root / "untrusted-r116"
        shutil.copytree(self.base, untrusted)
        (untrusted / ".env").write_text("not a release input", encoding="utf-8")
        self.manifest(untrusted, "r116")
        with self.assertRaisesRegex(ValueError, "不得安裝"):
            build_delta(untrusted, self.target, self.root / "untrusted-output", self.key)

    def test_baseline_mismatch_blocks_before_zip_and_never_falls_back(self):
        (self.base / "src/demo.py").write_bytes(b"modified")
        manager = UpdateManager(self.base, self.root / "Data")
        manager._manifest, manager._envelope = self.delta, self.envelope
        manager._download = Mock()
        manager.prepare()
        manager._download.assert_not_called()
        self.assertEqual(manager.state["status"], "failed")
        calls = []
        downloads = self.root / "download"
        downloads.mkdir()
        def fetch(url, path, _limit):
            calls.append(url)
            shutil.copy2(self.out / "update-manifest-delta.json", path)
        with self.assertRaises(ValueError):
            prepare(self.base, downloads, self.root / "payload", "r118", fetch)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("update-manifest-delta.json"))

    def test_wrong_base_manifest_and_wrong_signature_are_refused(self):
        bad = copy.deepcopy(self.delta)
        bad["base_manifest_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "基準"):
            validate_base(self.base, bad)
        tampered = copy.deepcopy(self.envelope)
        tampered["manifest"]["version"] = "r119"
        with self.assertRaises(ValueError):
            verify_delta(tampered, self.key.public_key().export_key(format="PEM").encode())

    def _runtime_files(self):
        return {
            "data/matplotlib/fontlist-v390.json": json.dumps({"_version": 390, "afmlist": [], "ttflist": []}),
            "data/ultralytics/Ultralytics/settings.json": json.dumps({
                "settings_version": "0.0.6", "datasets_dir": "local", "weights_dir": "local", "runs_dir": "local"}),
            "logs/go2rtc.log": "local runtime diagnostic",
            "logs/go2rtc.log.1": "previous runtime diagnostic",
        }

    def test_known_runtime_outputs_are_preserved_but_not_reused_or_downloaded(self):
        runtime = self._runtime_files()
        for name, content in runtime.items():
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        downloads = self.root / "runtime-downloads"
        downloads.mkdir()
        calls = []
        def fetch(url, path, _limit):
            calls.append(url.rsplit("/", 1)[1])
            shutil.copy2(self.out / calls[-1], path)
        prepare(self.base, downloads, self.root / "runtime-payload", "r118", fetch)
        self.assertEqual(calls, ["update-manifest-delta.json", self.delta["asset"]])
        destination = self.root / "runtime-assembled"
        assemble(self.base, self.archive, self.delta, destination)
        for name, content in runtime.items():
            self.assertEqual((self.base / name).read_text(encoding="utf-8"), content)
            self.assertFalse((destination / name).exists())

    def test_unknown_and_malformed_runtime_files_still_block_before_zip(self):
        bad = {
            "data/matplotlib/plugin.py": "code",
            "data/matplotlib/fontlist-v390.json": "{}",
            "data/ultralytics/Ultralytics/settings.json": "{}",
            "logs/unknown.log": "unknown",
            "logs/go2rtc.log.py": "code",
            "data/ultralytics/Ultralytics/other.json": "{}",
            "data/ultralytics/Ultralytics/settings.json.bak": "{}",
        }
        for index, (name, content) in enumerate(bad.items()):
            with self.subTest(name=name):
                path = self.base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                downloads = self.root / f"bad-downloads-{index}"
                downloads.mkdir()
                calls = []
                def fetch(url, destination, _limit, *, calls=calls):
                    calls.append(url)
                    shutil.copy2(self.out / "update-manifest-delta.json", destination)
                with self.assertRaises(ValueError):
                    prepare(self.base, downloads, self.root / f"bad-payload-{index}", "r118", fetch)
                self.assertEqual(len(calls), 1)
                path.unlink()

    def test_runtime_file_limits_links_and_modified_program_still_fail_closed(self):
        path = self.base / "data/ultralytics/Ultralytics/settings.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(b" " * 65537)
        with self.assertRaises(ValueError):
            validate_base(self.base, self.delta)
        path.unlink()
        log = self.base / "logs/go2rtc.log"
        log.parent.mkdir()
        log.write_text("runtime", encoding="utf-8")
        def reject_link(candidate):
            if Path(candidate).name == "go2rtc.log":
                raise ValueError("reparse fixture")
            plain(candidate)
        with patch("meowkeeper.app.delta_update.plain", side_effect=reject_link), self.assertRaises(ValueError):
            validate_base(self.base, self.delta)
        (self.base / "src/demo.py").write_bytes(b"modified")
        with self.assertRaises(ValueError):
            validate_base(self.base, self.delta)

    def test_update_manager_checks_and_downloads_only_small_delta(self):
        manager = UpdateManager(self.base, self.root / "Data")
        calls = []
        def fetch(url, destination, limit, on_chunk=None):
            if url.endswith("update-manifest.json"):
                raise OSError("fixture: full manifest not published")
            name = "update-manifest-delta.json" if url.endswith("update-manifest-delta.json") else self.delta["asset"]
            calls.append(name)
            data = (self.out / name).read_bytes()
            self.assertLessEqual(len(data), limit)
            destination.write_bytes(data)
            if on_chunk:
                on_chunk(len(data))
        manager._download = fetch
        manager.check()
        self.assertEqual(manager.state["status"], "available")
        manager.prepare()
        self.assertEqual(manager.state["status"], "prepared", manager.state)
        self.assertEqual(calls, ["update-manifest-delta.json", self.delta["asset"]])

    def test_full_release_is_offered_but_never_downloaded_without_explicit_choice(self):
        manifest = {"schema": 1, "repository": "kinostudio/MeowKeeperPlus", "version": "r120", "tag": "r120",
                    "asset": "MeowKeeperPlus-r120-windows-x64.zip", "size": 10, "sha256": "a" * 64}
        envelope = {"manifest": manifest, "signature": base64.b64encode(eddsa.new(self.key, "rfc8032").sign(canonical(manifest))).decode()}
        manager = UpdateManager(self.base, self.root / "Data")
        calls = []
        def fetch(url, destination, _limit, on_chunk=None):
            calls.append(url.rsplit("/", 1)[1])
            if url.endswith("update-manifest-delta.json"):
                raise OSError("fixture: no delta asset")
            if url.endswith("update-manifest.json"):
                destination.write_text(json.dumps(envelope))
                return
            destination.write_bytes(b"0123456789")
        manager._download = fetch
        manager.check()
        self.assertEqual(manager.state["status"], "available")
        self.assertEqual(manager.state["package_kind"], "full")
        self.assertTrue(manager.state["full_available"])
        self.assertEqual(manager.state["reason"], "delta_unavailable")
        manager.prepare()
        self.assertEqual(manager.state["status"], "failed")
        self.assertEqual(calls, ["update-manifest-delta.json", "update-manifest.json"])
        manager.prepare(allow_full=True)
        self.assertEqual(calls[-1], manifest["asset"])
        self.assertEqual(manager.state["status"], "failed")  # fixture bytes fail SHA-256: never installed

    def test_delta_base_mismatch_offers_full_package_with_reason(self):
        full = {"schema": 1, "repository": "kinostudio/MeowKeeperPlus", "version": "r118", "tag": "r118",
                "asset": "MeowKeeperPlus-r118-windows-x64.zip", "size": 1_900_000_000, "sha256": "a" * 64}
        full_envelope = {"manifest": full, "signature": base64.b64encode(eddsa.new(self.key, "rfc8032").sign(canonical(full))).decode()}
        (self.base / "src/demo.py").write_bytes(b"locally modified")
        manager = UpdateManager(self.base, self.root / "Data")
        def fetch(url, destination, _limit, on_chunk=None):
            if url.endswith("update-manifest-delta.json"):
                shutil.copy2(self.out / "update-manifest-delta.json", destination)
            else:
                destination.write_text(json.dumps(full_envelope))
        manager._download = fetch
        manager.check()
        self.assertEqual(manager.state["package_kind"], "full")
        self.assertEqual(manager.state["reason"], "delta_base_mismatch")
        self.assertEqual(manager.state["download_bytes"], 1_900_000_000)
        self.assertIn("不會被覆蓋", manager.state["message_zh_tw"])

    def test_traversal_case_alias_and_data_secret_paths_are_refused(self):
        for name in ("../escape.py", "C:/escape", "src/CON.py", "src/demo.py.", "data/secrets/key.dpapi"):
            value = copy.deepcopy(self.delta)
            value["changed"].append({"path": name, "size_bytes": 1, "sha256": "a" * 64})
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_manifest(value)
        value = copy.deepcopy(self.delta)
        value["changed"].append({**value["changed"][0], "path": value["changed"][0]["path"].upper()})
        with self.assertRaises(ValueError):
            validate_manifest(value)

    def test_payload_corruption_extra_files_and_incomplete_deletes_are_refused(self):
        self.archive.write_bytes(self.archive.read_bytes() + b"tamper")
        with self.assertRaises(ValueError):
            assemble(self.base, self.archive, self.delta, self.root / "bad1")
        with zipfile.ZipFile(self.archive, "a") as bundle:
            bundle.writestr("extra.py", b"extra")
        changed = {**self.delta, "size": self.archive.stat().st_size, "sha256": digest(self.archive)}
        with self.assertRaises(ValueError):
            assemble(self.base, self.archive, changed, self.root / "bad2")

    def test_bootstrap_downloads_only_manifest_and_delta_and_keeps_host_untouched(self):
        downloads = self.root / "download"
        downloads.mkdir()
        calls = []
        def fetch(url, destination, limit):
            name = url.rsplit("/", 1)[1]
            calls.append(name)
            file = self.out / name
            self.assertLessEqual(file.stat().st_size, limit)
            shutil.copy2(file, destination)
        prepare(self.base, downloads, self.root / "bootstrap", "r118", fetch)
        self.assertEqual(calls, ["update-manifest-delta.json", self.delta["asset"]])
        self.assertEqual((self.base / "src/demo.py").read_bytes(), b"old")

    def test_bootstrap_reuses_unchanged_signed_control_files_without_full_download(self):
        downloads = self.root / "control-download"
        downloads.mkdir()
        payload = self.root / "control-payload"
        def fetch(url, destination, _limit):
            shutil.copy2(self.out / url.rsplit("/", 1)[1], destination)
        prepare(self.base, downloads, payload, "r118", fetch)
        self.assertFalse((payload / BOOTSTRAP_FILES[0]).exists())
        complete_bootstrap(self.base, payload, self.delta)
        for name in BOOTSTRAP_FILES:
            self.assertEqual(digest(payload / name), digest(self.target / name))
        self.assertFalse((payload / "runtime").exists())
        (payload / BOOTSTRAP_FILES[0]).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            complete_bootstrap(self.base, payload, self.delta)

    def test_bootstrap_rejects_modified_local_control_file(self):
        from meowkeeper.app.delta_update import extract_payload
        payload = self.root / "bad-control"
        extract_payload(self.archive, self.delta, payload)
        (self.base / BOOTSTRAP_FILES[0]).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            complete_bootstrap(self.base, payload, self.delta)

    def test_copy_failure_never_changes_installed_files(self):
        before = digest(self.base / "build_manifest.json")
        with patch("meowkeeper.app.delta_update.os.link", side_effect=OSError("fixture no hardlinks")), patch("meowkeeper.app.delta_update.shutil.copy2", side_effect=OSError("fixture interruption")), self.assertRaises(OSError):
            assemble(self.base, self.archive, self.delta, self.root / "interrupted")
        self.assertEqual(digest(self.base / "build_manifest.json"), before)
        self.assertEqual((self.base / "src/deleted.py").read_bytes(), b"delete")

    def test_signed_known_source_baseline_is_exact_not_a_bypass(self):
        with patch("tools.prepare_delta_release.verify_package_evidence", return_value={}):
            build_delta(self.base, self.target, self.root / "variant", self.key,
                        base_overrides={"src/demo.py": self.target / "src/demo.py"})
        envelope = json.loads((self.root / "variant/update-manifest-delta.json").read_text(encoding="utf-8"))
        manifest = envelope["manifest"]
        with self.assertRaises(ValueError):
            validate_base(self.base, manifest)
        (self.base / "src/demo.py").write_bytes(b"new")
        validate_base(self.base, manifest)
        with zipfile.ZipFile(self.root / "variant" / manifest["asset"]) as archive:
            self.assertNotIn("src/demo.py", archive.namelist())
        assemble(self.base, self.root / "variant" / manifest["asset"], manifest, self.root / "variant-app")
        (self.base / "src/demo.py").write_bytes(b"other")
        with self.assertRaises(ValueError):
            validate_base(self.base, manifest)

    def test_r116_release_requires_exact_previously_supported_source_override(self):
        source = ROOT / "src/meowkeeper/app/web/state.py"
        validate_release_baseline_overrides("r116", "r121", {"src/meowkeeper/app/web/state.py": source})
        with self.assertRaises(ValueError):
            validate_release_baseline_overrides("r116", "r121", {})
        with self.assertRaises(ValueError):
            validate_release_baseline_overrides("r116", "r121", {"src/meowkeeper/app/web/state.py": self.target / "src/demo.py"})
        with self.assertRaises(ValueError):
            validate_release_baseline_overrides("r116", "r121", {
                "src/meowkeeper/app/web/state.py": source,
                "src/extra.py": self.target / "src/demo.py",
            })

    def test_release_cli_rejects_missing_r116_override_before_signing_or_output(self):
        output = self.root / "must-not-exist"
        argv = ["prepare_delta_release", "--base", str(self.base), "--target", str(self.target),
                "--output", str(output), "--signing-root", str(self.root / "no-signer")]
        with patch("sys.argv", argv), patch("Crypto.PublicKey.ECC.import_key") as import_key, \
                self.assertRaisesRegex(ValueError, "r116 差量必須"):
            prepare_release_main()
        import_key.assert_not_called()
        self.assertFalse(output.exists())

    def test_protected_worker_reverifies_and_stages_delta(self):
        for name, content in self._runtime_files().items():
            path = self.base / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        stage = self.root / "work-fixture"
        stage.mkdir()
        archive = stage / "package.zip"
        envelope = stage / "update-manifest.json"
        shutil.copy2(self.archive, archive)
        shutil.copy2(self.out / "update-manifest-delta.json", envelope)
        result = verify_and_stage(archive, envelope, app_root=self.base, stage_root=stage)
        self.assertEqual(result["status"], "staged")
        self.assertEqual(result["version"], "r118")
        self.assertEqual(Path(result["package_path"]).name, "a")
        self.assertEqual(digest(Path(result["package_path"]) / "build_manifest.json"), digest(self.target / "build_manifest.json"))
        self.assertEqual((self.base / "src/demo.py").read_bytes(), b"old")



if __name__ == "__main__":
    unittest.main()
