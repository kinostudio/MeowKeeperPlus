import base64
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from fastapi.testclient import TestClient

from meowkeeper.app.updates import (
    REPOSITORY,
    UpdateManager,
    canonical,
    safe_extract,
    validate_hotfix_task_xml,
    verify_envelope,
)
from meowkeeper.app.web import create_app
from tools.create_task_scheduler_xml import create_xml


class UpdateTests(unittest.TestCase):
    def envelope(self):
        key = ECC.generate(curve="Ed25519")
        manifest = {"schema": 1, "repository": REPOSITORY, "version": "r97", "tag": "r97",
                    "asset": "MeowKeeperPlus-r97-windows-x64.zip", "size": 10, "sha256": "a" * 64}
        envelope = {"manifest": manifest, "signature": base64.b64encode(
            eddsa.new(key, "rfc8032").sign(canonical(manifest))).decode()}
        return envelope, key.public_key().export_key(format="PEM")

    def test_signature_and_tamper(self):
        envelope, key = self.envelope()
        self.assertEqual(verify_envelope(envelope, key)["version"], "r97")
        envelope["manifest"]["sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            verify_envelope(envelope, key)

    def test_wrong_repo_even_if_signed(self):
        envelope, _ = self.envelope()
        key = ECC.generate(curve="Ed25519")
        envelope["manifest"]["repository"] = "other/repo"
        envelope["signature"] = base64.b64encode(eddsa.new(key, "rfc8032").sign(canonical(envelope["manifest"]))).decode()
        with self.assertRaises(ValueError):
            verify_envelope(envelope, key.public_key().export_key(format="PEM"))

    def test_signed_part_sizes_and_names(self):
        envelope, _ = self.envelope()
        key = ECC.generate(curve="Ed25519")
        manifest = envelope["manifest"]
        manifest["parts"] = [{"name": manifest["asset"] + ".part01", "size": 10, "sha256": "c" * 64}]
        def sign():
            envelope["signature"] = base64.b64encode(eddsa.new(key, "rfc8032").sign(canonical(manifest))).decode()
        sign()
        verify_envelope(envelope, key.public_key().export_key(format="PEM"))
        manifest["parts"][0]["size"] = 9
        sign()
        with self.assertRaises(ValueError):
            verify_envelope(envelope, key.public_key().export_key(format="PEM"))

    def test_zip_paths_and_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for names in (("../escape",), ("C:/escape",), ("CON.txt",), ("a", "A"), ("a\\b",)):
                archive = root / "test.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    for name in names:
                        item = zipfile.ZipInfo()
                        item.filename = name  # 保留 Windows zipfile 會自動正規化的惡意反斜線。
                        bundle.writestr(item, "test")
                with self.assertRaises(ValueError):
                    safe_extract(archive, root / "target")
            self.assertFalse((root / "target").exists())

    def test_safe_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with zipfile.ZipFile(root / "test.zip", "w") as bundle:
                bundle.writestr("src/file.py", "fixture")
            safe_extract(root / "test.zip", root / "app")
            self.assertEqual((root / "app/src/file.py").read_text(), "fixture")

    def test_safe_extract_reports_unpacked_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            payload = b"x" * (1024 * 1024 + 17)
            archive = root / "progress.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("payload.bin", payload)
            reports = []
            safe_extract(archive, root / "expanded", on_progress=lambda done, total: reports.append((done, total)))
            self.assertGreaterEqual(len(reports), 2)
            self.assertEqual(reports[-1], (len(payload), len(payload)))
            self.assertEqual((root / "expanded/payload.bin").read_bytes(), payload)

    def test_download_reports_received_chunks_without_logging_url(self):
        import httpx

        payload_chunks = [b"first", b"-second"]

        class ChunkedBody(httpx.SyncByteStream):
            def __iter__(self):
                yield from payload_chunks

        def handler(request):
            return httpx.Response(200, stream=ChunkedBody(), request=request)

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = UpdateManager(root, root)
            received = []
            with patch("httpx.Client", side_effect=lambda **kwargs: real_client(transport=transport, **kwargs)):
                manager._download("https://github.com/kinostudio/MeowKeeperPlus/test.zip",
                                  root / "download.zip", 100, on_chunk=received.append)
            self.assertEqual(received, [len(chunk) for chunk in payload_chunks])
            self.assertEqual((root / "download.zip").read_bytes(), b"".join(payload_chunks))
            self.assertNotIn("github.com", str(manager.snapshot()))

    def test_progress_snapshot_reports_stage_and_percent(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(folder, folder)
            manager._report_progress("r106", "download", "下載更新第 2/3 卷",
                                     completed=50, total=100, downloaded=50, total_download=100,
                                     part_index=2, part_count=3)
            state = manager.snapshot()
            self.assertEqual(state["status"], "downloading")
            self.assertEqual(state["progress_percent"], 50.0)
            self.assertEqual(state["downloaded_bytes"], 50)
            self.assertEqual(state["stage_label_zh_tw"], "下載更新第 2/3 卷")

    def test_prepare_publishes_download_hash_extract_and_manifest_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app_root = root / "installed"
            verifier = app_root / "tools/verify_portable.py"
            verifier.parent.mkdir(parents=True)
            verifier.write_text(
                "def verify_portable(path):\n    assert (path / 'payload.bin').is_file()\n",
                encoding="utf-8",
            )
            fixture_archive = root / "fixture.zip"
            with zipfile.ZipFile(fixture_archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("payload.bin", b"x" * (8 * 1024 * 1024))
            archive_bytes = fixture_archive.read_bytes()
            manager = UpdateManager(app_root, root / "data")
            manager._manifest = {
                "version": "r107", "tag": "r107", "asset": "MeowKeeperPlus-r107-windows-x64.zip",
                "size": len(archive_bytes), "sha256": hashlib.sha256(archive_bytes).hexdigest(), "parts": [],
            }
            manager._envelope = {"manifest": manager._manifest, "signature": "fixture-signature"}
            stages = []
            report = manager._report_progress

            def capture(*args, **kwargs):
                report(*args, **kwargs)
                stages.append(manager.snapshot()["stage"])

            manager._report_progress = capture

            def fake_download(_url, destination, _limit, on_chunk=None):
                with destination.open("xb") as output:
                    for offset in range(0, len(archive_bytes), 1024 * 1024):
                        chunk = archive_bytes[offset:offset + 1024 * 1024]
                        output.write(chunk)
                        if on_chunk is not None:
                            on_chunk(len(chunk))

            with patch.object(manager, "_download", side_effect=fake_download), patch(
                "meowkeeper.app.updates.shutil.disk_usage",
                return_value=SimpleNamespace(free=20 * 1024**3),
            ):
                manager.prepare()

            self.assertEqual(manager.snapshot()["status"], "prepared")
            self.assertIn("download", stages)
            self.assertIn("verify_archive", stages)
            self.assertIn("extract", stages)
            self.assertIn("verify_package", stages)
            self.assertEqual(manager.snapshot()["version"], "r107")

    def test_failed_check_no_secret_or_runtime_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = UpdateManager(folder, folder)
            manager.check()  # no trust key: fail closed, no network
            self.assertEqual(manager.snapshot()["status"], "failed")
            self.assertNotIn(folder, str(manager.snapshot()))
            manager.prepare()
            self.assertEqual(manager.snapshot()["status"], "failed")

    def test_auth_csrf_and_network_not_automatic(self):
        with patch.object(UpdateManager, "check") as check:
            with TestClient(create_app(token="update-fixture-token")) as client:
                self.assertEqual(client.get("/api/v1/updates").status_code, 401)
                self.assertEqual(client.post("/api/v1/updates/check").status_code, 401)
                self.assertEqual(client.post("/api/v1/updates/check", headers={"Authorization": "Bearer update-fixture-token"}).status_code, 403)
                self.assertEqual(client.get("/api/v1/updates", headers={"Authorization": "Bearer update-fixture-token"}).json()["status"], "not_checked")
            check.assert_not_called()

    def test_hotfix_task_requires_versioned_system_worker_and_fixed_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app_root, data_root, updater_root = root / "App", root / "Data", root / "Updater"
            app_root.mkdir()
            data_root.mkdir()
            updater_root.mkdir()
            content = b"# pinned worker fixture\n"
            suffix = hashlib.sha256(content).hexdigest()[:16]
            script = updater_root / f"Apply-MeowKeeperPlusUpdate-{suffix}.ps1"
            script.write_bytes(content)
            xml = create_xml(
                app_root, task_name="MeowKeeperPlusUpdateWorker",
                source_env="MEOWKEEPER_MEDIA_SOURCE_URL", role="updater",
                data_root=data_root, updater_script=script,
            )
            # The task builder uses Windows PowerShell's canonical path. Match
            # its command in this platform-independent validator fixture.
            command_start = xml.index("<Command>") + len("<Command>")
            command_end = xml.index("</Command>", command_start)
            expected_runtime = Path(xml[command_start:command_end])
            self.assertTrue(validate_hotfix_task_xml(
                xml, expected_runtime=expected_runtime, app_root=app_root,
                data_root=data_root, updater_root=updater_root,
            ))
            self.assertFalse(validate_hotfix_task_xml(
                xml.replace("-NoProfile -ExecutionPolicy", "-NoProfile  -ExecutionPolicy"),
                expected_runtime=expected_runtime, app_root=app_root,
                data_root=data_root, updater_root=updater_root,
            ))
            self.assertFalse(validate_hotfix_task_xml(
                xml.replace("S-1-5-18", "S-1-5-21-1-2-3-1001"),
                expected_runtime=expected_runtime, app_root=app_root,
                data_root=data_root, updater_root=updater_root,
            ))
            self.assertFalse(validate_hotfix_task_xml(
                xml.replace("<Triggers></Triggers>", "<Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>"),
                expected_runtime=expected_runtime, app_root=app_root,
                data_root=data_root, updater_root=updater_root,
            ))
            script.write_bytes(b"# altered worker fixture\n")
            self.assertFalse(validate_hotfix_task_xml(
                xml, expected_runtime=expected_runtime, app_root=app_root,
                data_root=data_root, updater_root=updater_root,
            ))

    def test_hotfix_status_is_correlated_to_current_prepared_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = UpdateManager(root / "App", root / "Data", hotfix_task_available=True)
            prepared_id = "prepared-0123456789abcdef"
            manager.state = {
                "status": "prepared", "version": "r120", "prepared_id": prepared_id,
                "package_path": str(root / "Data" / "updates" / prepared_id / "app"),
            }
            manager.worker_status_path.parent.mkdir(parents=True)
            manager.worker_status_path.write_text(json.dumps({
                "schema": 1, "status": "applied", "version": "r119",
                "request_id": "a" * 32, "prepared_id": "prepared-ffeeddccbbaa9988",
                "message_zh_tw": "舊更新結果",
            }), encoding="utf-8")
            state = manager.snapshot()
            self.assertEqual(state["status"], "prepared")
            self.assertEqual(state["version"], "r120")
            self.assertTrue(state["apply_available"])

    def test_failed_hotfix_keeps_verified_package_retryable_after_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = UpdateManager(root / "App", root / "Data", hotfix_task_available=True)
            prepared_id = "prepared-0123456789abcdef"
            request_id = "a" * 32
            manager.state = {
                "status": "applying", "version": "r120", "prepared_id": prepared_id,
                "request_id": request_id, "package_path": str(root / "app"),
            }
            manager.worker_status_path.parent.mkdir(parents=True)
            manager.worker_status_path.write_text(json.dumps({
                "schema": 1, "status": "failed", "version": "r120",
                "request_id": request_id, "prepared_id": prepared_id,
                "message_zh_tw": "舊版已恢復，可重試。",
            }), encoding="utf-8")
            state = manager.snapshot()
            self.assertEqual(state["status"], "prepared")
            self.assertEqual(state["hotfix_result"], "failed")
            self.assertEqual(state["package_path"], str(root / "app"))
            self.assertTrue(state["apply_available"])

    def test_apply_queues_only_fixed_task_request_without_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepared = root / "Data" / "updates" / "prepared-0123456789abcdef"
            prepared.mkdir(parents=True)
            manager = UpdateManager(root / "App", root / "Data", hotfix_task_available=True)
            manager._manifest = {"version": "r120"}
            manager._envelope = {"manifest": {}, "signature": "fixture"}
            manager._prepared_dir = prepared
            manager.state = {
                "status": "prepared", "version": "r120", "prepared_id": prepared.name,
                "package_path": str(prepared / "app"),
            }
            result = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch("meowkeeper.app.updates.subprocess.run", return_value=result) as run:
                state = manager.apply()
            self.assertEqual(state["status"], "applying")
            self.assertEqual(state["prepared_id"], prepared.name)
            self.assertEqual(state["request_id"], json.loads((manager.root / "apply-request.json").read_text())["request_id"])
            self.assertFalse(run.call_args.kwargs["shell"])
            self.assertEqual(run.call_args.args[0][-3:], ["/Run", "/TN", "MeowKeeperPlus Update Worker"])
