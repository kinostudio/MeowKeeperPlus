import base64
import copy
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from meowkeeper.app.delta_update import (
    assemble,
    digest,
    validate_base,
    validate_manifest,
    verify_delta,
)
from meowkeeper.app.updates import UpdateManager, canonical, verify_envelope
from tools.apply_verified_update import verify_and_stage
from tools.bootstrap_delta import prepare
from tools.prepare_delta_release import build_delta

ROOT = Path(__file__).resolve().parents[1]


class DeltaUpdateTests(unittest.TestCase):
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
        module.parent.mkdir(parents=True)
        module.write_bytes((ROOT / "src/meowkeeper/app/delta_update.py").read_bytes())
        self.manifest(self.target, "r118")
        self.out = self.root / "output"
        with patch("tools.prepare_delta_release.verify_package_evidence", return_value={}):
            self.report = build_delta(self.base, self.target, self.out, self.key)
        self.envelope = json.loads((self.out / "update-manifest.json").read_text(encoding="utf-8"))
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
            shutil.copy2(self.out / "update-manifest.json", path)
        with self.assertRaises(ValueError):
            prepare(self.base, downloads, self.root / "payload", "r118", fetch)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("update-manifest.json"))

    def test_wrong_base_manifest_and_wrong_signature_are_refused(self):
        bad = copy.deepcopy(self.delta)
        bad["base_manifest_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "基準"):
            validate_base(self.base, bad)
        tampered = copy.deepcopy(self.envelope)
        tampered["manifest"]["version"] = "r119"
        with self.assertRaises(ValueError):
            verify_delta(tampered, self.key.public_key().export_key(format="PEM").encode())

    def test_update_manager_checks_and_downloads_only_small_delta(self):
        manager = UpdateManager(self.base, self.root / "Data")
        calls = []
        def fetch(url, destination, limit, on_chunk=None):
            name = "update-manifest.json" if url.endswith("update-manifest.json") else self.delta["asset"]
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
        self.assertEqual(calls, ["update-manifest.json", self.delta["asset"]])

    def test_full_release_is_not_an_automatic_fallback(self):
        manifest = {"schema": 1, "repository": "kinostudio/MeowKeeperPlus", "version": "r120", "tag": "r120",
                    "asset": "MeowKeeperPlus-r120-windows-x64.zip", "size": 10, "sha256": "a" * 64}
        envelope = {"manifest": manifest, "signature": base64.b64encode(eddsa.new(self.key, "rfc8032").sign(canonical(manifest))).decode()}
        manager = UpdateManager(self.base, self.root / "Data")
        calls = []
        def fetch(url, destination, _limit):
            calls.append(url)
            destination.write_text(json.dumps(envelope))
        manager._download = fetch
        manager.check()
        self.assertEqual(manager.state.get("reason"), "delta_unavailable")
        manager.prepare()
        self.assertEqual(len(calls), 1)

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
        self.assertEqual(calls, ["update-manifest.json", self.delta["asset"]])
        self.assertEqual((self.base / "src/demo.py").read_bytes(), b"old")

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
        envelope = json.loads((self.root / "variant/update-manifest.json").read_text(encoding="utf-8"))
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

    def test_protected_worker_reverifies_and_stages_delta(self):
        stage = self.root / "work-fixture"
        stage.mkdir()
        archive = stage / "package.zip"
        envelope = stage / "update-manifest.json"
        shutil.copy2(self.archive, archive)
        shutil.copy2(self.out / "update-manifest.json", envelope)
        result = verify_and_stage(archive, envelope, app_root=self.base, stage_root=stage)
        self.assertEqual(result["status"], "staged")
        self.assertEqual(result["version"], "r118")
        self.assertEqual(digest(Path(result["package_path"]) / "build_manifest.json"), digest(self.target / "build_manifest.json"))
        self.assertEqual((self.base / "src/demo.py").read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
