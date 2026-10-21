from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from meowkeeper.core.health import (
    cleanup_retained_files,
    configure_rotating_logging,
    inspect_disk_space,
)
from meowkeeper.core.media.sources import BackoffPolicy, FfmpegFrameSource, MediaSourceError
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore
from tools.build_portable import build_portable
from tools.create_task_scheduler_xml import create_xml
from tools.verify_portable import verify_portable


class _FailedProcess:
    def __init__(self) -> None:
        self.stdout = io.BytesIO()
        self.returncode = 1

    def poll(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode


class MvpStabilityTests(unittest.TestCase):
    def test_sqlite_is_repeatable_wal_and_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            first = SQLiteStore(path)
            self.assertEqual(first.schema_version, SCHEMA_VERSION)
            self.assertEqual(first.journal_mode, "wal")
            self.assertEqual(first.connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            first.close()

            second = SQLiteStore(path)
            self.assertEqual(second.schema_version, SCHEMA_VERSION)
            self.assertEqual(
                second.connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            second.close()

    def test_sqlite_memory_database_has_schema_without_wal_requirement(self) -> None:
        store = SQLiteStore(":memory:")
        self.assertEqual(store.schema_version, SCHEMA_VERSION)
        self.assertEqual(store.journal_mode, "memory")
        store.close()

    def test_retention_removes_expired_files_but_preserves_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old.mp4"
            pinned = root / "pinned.mp4"
            current = root / "current.mp4"
            for path in (old, pinned, current):
                path.write_bytes(path.name.encode("ascii"))
            old_time = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
            os.utime(old, (old_time, old_time))
            os.utime(pinned, (old_time, old_time))
            result = cleanup_retained_files(
                root,
                max_age_days=7,
                pinned_paths=(pinned,),
                now=datetime.now(timezone.utc),
            )
            self.assertIn(str(old.resolve()), result.removed_files)
            self.assertTrue(pinned.exists())
            self.assertTrue(current.exists())

    def test_disk_space_snapshot_is_read_only(self) -> None:
        snapshot = inspect_disk_space(ROOT, minimum_free_bytes=0)
        self.assertGreater(snapshot.total_bytes, 0)
        self.assertFalse(snapshot.below_minimum)

    def test_rotating_logging_writes_utf8_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logs" / "service.log"
            logger = configure_rotating_logging(
                path,
                logger_name=f"meowkeeper-test-{id(self)}",
                max_bytes=128,
                backup_count=1,
            )
            logger.info("測試繁體中文日誌")
            for handler in logger.handlers:
                handler.flush()
            self.assertIn("測試繁體中文日誌", path.read_text(encoding="utf-8"))
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)

    def test_backoff_is_bounded(self) -> None:
        policy = BackoffPolicy(max_retries=3, initial_seconds=0.5, maximum_seconds=1.0)
        self.assertEqual([policy.delay_seconds(i) for i in range(1, 4)], [0.5, 1.0, 1.0])

    def test_ffmpeg_network_source_restarts_with_exponential_backoff(self) -> None:
        sleeps: list[float] = []
        source = FfmpegFrameSource(
            "rtsp://192.0.2.10:554/live",
            ffmpeg_path="ffmpeg.exe",
            retry_policy=BackoffPolicy(max_retries=1, initial_seconds=0.01, maximum_seconds=0.01),
            sleep=sleeps.append,
            popen=lambda *args, **kwargs: _FailedProcess(),
        )
        with self.assertRaises(MediaSourceError):
            list(source.iter_frames(resolved=source.resolver and None))
        self.assertEqual(source.restart_count, 1)
        self.assertEqual(sleeps, [0.01])

    def test_portable_build_dry_run_requires_explicit_runtime_but_copies_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            (runtime / "python.exe").write_bytes(b"placeholder")
            (runtime / "pythonw.exe").write_bytes(b"placeholder")
            output = Path(directory) / "package"
            plan = build_portable(
                python_runtime=runtime,
                output=output,
                dry_run=True,
            )
            self.assertTrue(plan["dry_run"])
            self.assertFalse(output.exists())
            self.assertEqual(plan["model"]["path"], "models/yolo11n.pt")

    def test_portable_bundle_does_not_include_venv_only_launchers(self) -> None:
        packaging = importlib.import_module("tools.build_portable")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-portable-contract-") as directory:
            root = Path(directory)
            javascript = root / "vendor" / "javascript"
            javascript.mkdir(parents=True)
            (javascript / "node.exe").write_bytes(b"test placeholder")
            output = root / "dist" / "portable"

            def fake_copy_tree(_source: Path, target: Path) -> None:
                target.mkdir(parents=True, exist_ok=True)

            def fake_copy_file(_source: Path, target: Path) -> None:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"test placeholder")

            def fake_copy_powershell(_source: Path, target: Path) -> None:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"\xef\xbb\xbftest placeholder")

            with (
                patch.object(packaging, "ROOT", root),
                patch.object(packaging, "_validate_runtime", return_value=[]),
                patch("tools.verify_regression_gate.verify_gate", return_value={"status": "passed", "source_sha256": "fixture"}),
                patch("tools.verify_regression_gate.source_digest", return_value="fixture"),
                patch.object(packaging, "validate_model_file", return_value={"sha256": "test", "file_size_bytes": 1}),
                patch.object(packaging, "_required_files", return_value=()),
                patch.object(packaging, "_copy_tree", side_effect=fake_copy_tree),
                patch.object(packaging, "_copy_file", side_effect=fake_copy_file),
                patch.object(packaging, "_copy_powershell_script", side_effect=fake_copy_powershell),
            ):
                packaging.build_portable(python_runtime=root / "runtime", output=output)

            for name in ("MeowKeeperPlus.cmd", "MeowKeeperPlusDashboard.cmd", "MeowKeeperPlusTray.cmd"):
                content = (output / name).read_text(encoding="utf-8")
                self.assertIn("runtime\\python", content)
                self.assertIn('cd /d "%MEOWKEEPER_ROOT%"', content)
                self.assertNotIn(".venv", content)
            self.assertFalse((output / "Start-MeowKeeperPlus.cmd").exists())
            self.assertFalse((output / "Start-MeowKeeperPlus-Tray.cmd").exists())
            self.assertTrue((output / "Install-MeowKeeperPlus.cmd").is_file())
            self.assertTrue((output / "Uninstall-MeowKeeperPlus.cmd").is_file())
            self.assertTrue((output / "scripts" / "Install-MeowKeeperPlus.ps1").is_file())
            self.assertTrue((output / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1").is_file())
            self.assertTrue((output / "scripts" / "Uninstall-MeowKeeperPlus.ps1").is_file())
            self.assertFalse((output / "scripts" / "Start-MeowKeeperPlus.ps1").exists())
            self.assertEqual(
                (output / "scripts" / "Install-MeowKeeperPlus.ps1").read_bytes()[:3],
                b"\xef\xbb\xbf",
            )
            self.assertEqual(
                (output / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1").read_bytes()[:3],
                b"\xef\xbb\xbf",
            )

    def test_portable_runtime_rejects_packages_that_exist_but_cannot_import(self) -> None:
        packaging = importlib.import_module("tools.build_portable")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-runtime-import-") as directory:
            runtime = Path(directory)
            (runtime / "python.exe").write_bytes(b"placeholder")
            (runtime / "pythonw.exe").write_bytes(b"placeholder")
            packages = runtime / "Lib" / "site-packages"
            for name in packaging.RUNTIME_PACKAGES:
                (packages / name).mkdir(parents=True, exist_ok=True)
            failed = type("Result", (), {"returncode": 1})()
            with (
                patch.object(packaging.subprocess, "run", return_value=failed),
                self.assertRaisesRegex(ValueError, "實際匯入"),
            ):
                packaging._validate_runtime(runtime, dry_run=False)

    def test_portable_runtime_rejects_missing_websocket_transport(self) -> None:
        packaging = importlib.import_module("tools.build_portable")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-runtime-websocket-") as directory:
            runtime = Path(directory)
            (runtime / "python.exe").write_bytes(b"placeholder")
            (runtime / "pythonw.exe").write_bytes(b"placeholder")
            packages = runtime / "Lib" / "site-packages"
            for name in packaging.RUNTIME_PACKAGES:
                if name != "websockets":
                    (packages / name).mkdir(parents=True, exist_ok=True)
            with self.assertRaisesRegex(ValueError, "websockets"):
                packaging._validate_runtime(runtime, dry_run=False)

    def test_embedded_runtime_finds_yt_dlp_cli_in_scripts(self) -> None:
        runtime_module = importlib.import_module("meowkeeper.runtime")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-yt-dlp-cli-") as directory:
            embedded = Path(directory)
            executable = embedded / "python.exe"
            script = embedded / "Scripts" / "yt-dlp.exe"
            executable.write_bytes(b"placeholder")
            script.parent.mkdir()
            script.write_bytes(b"placeholder")
            with patch.object(runtime_module.sys, "executable", str(executable)):
                self.assertEqual(runtime_module.installed_script("yt-dlp"), str(script))

    def test_portable_runtime_rejects_missing_yt_dlp_cli(self) -> None:
        packaging = importlib.import_module("tools.build_portable")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-runtime-cli-") as directory:
            runtime = Path(directory)
            (runtime / "python.exe").write_bytes(b"placeholder")
            (runtime / "pythonw.exe").write_bytes(b"placeholder")
            packages = runtime / "Lib" / "site-packages"
            for name in packaging.RUNTIME_PACKAGES:
                (packages / name).mkdir(parents=True, exist_ok=True)
            imported = type("Result", (), {"returncode": 0})()
            with (
                patch.object(packaging.subprocess, "run", return_value=imported),
                self.assertRaisesRegex(ValueError, "yt-dlp.exe"),
            ):
                packaging._validate_runtime(runtime, dry_run=False)

    def test_portable_copy_preserves_only_required_torch_data(self) -> None:
        packaging = importlib.import_module("tools.build_portable")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-runtime-data-") as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            required = source / "Lib" / "site-packages" / "torch" / "utils" / "data" / "__init__.py"
            ultralytics_data = source / "Lib" / "site-packages" / "ultralytics" / "data" / "__init__.py"
            private = source / "data" / "private.db"
            required.parent.mkdir(parents=True)
            ultralytics_data.parent.mkdir(parents=True)
            private.parent.mkdir(parents=True)
            required.write_text("# required package module\n", encoding="utf-8")
            ultralytics_data.write_text("# required package module\n", encoding="utf-8")
            private.write_bytes(b"private")
            packaging._copy_tree(source, target)
            self.assertTrue((target / required.relative_to(source)).is_file())
            self.assertTrue((target / ultralytics_data.relative_to(source)).is_file())
            self.assertFalse((target / private.relative_to(source)).exists())

    def test_task_scheduler_xml_uses_source_env_without_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            xml = create_xml(Path(directory), task_name="MeowKeeper+", source_env="MEOWKEEPER_MEDIA_SOURCE_URL")
            self.assertIn("MEOWKEEPER_MEDIA_SOURCE_URL", xml)
            self.assertIn("run_host.py", xml)
            self.assertIn("--no-open-browser", xml)
            self.assertIn("<UserId>S-1-5-18</UserId>", xml)
            self.assertNotIn("run_monitor.py", xml)
            self.assertNotIn("password", xml.lower())
            self.assertNotIn("token", xml.lower())

            tray_xml = create_xml(
                Path(directory), task_name="MeowKeeper+ Tray", source_env="MEOWKEEPER_MEDIA_SOURCE_URL", role="tray"
            )
            self.assertIn("run_tray.py", tray_xml)
            self.assertIn("pythonw.exe", tray_xml)
            self.assertIn("--background", tray_xml)
            self.assertIn("<LogonType>InteractiveToken</LogonType>", tray_xml)
            self.assertNotIn("<UserId>S-1-5-18</UserId>", tray_xml)
            user_core_xml = create_xml(
                Path(directory),
                task_name="MeowKeeper+ User Core",
                source_env="MEOWKEEPER_MEDIA_SOURCE_URL",
                role="user_core",
                user_id="S-1-5-21-1000",
            )
            self.assertIn("run_host.py", user_core_xml)
            self.assertIn("<LogonTrigger>", user_core_xml)
            self.assertIn("<LogonType>InteractiveToken</LogonType>", user_core_xml)
            self.assertNotIn("<BootTrigger>", user_core_xml)
            self.assertNotIn("<UserId>S-1-5-18</UserId>", user_core_xml)
            self.assertIn("<UserId>S-1-5-21-1000</UserId>", user_core_xml)
            with self.assertRaisesRegex(ValueError, "SID"):
                create_xml(
                    Path(directory),
                    task_name="MeowKeeper+ User Core",
                    source_env="MEOWKEEPER_MEDIA_SOURCE_URL",
                    role="user_core",
                    user_id="not-a-sid",
                )
            with self.assertRaisesRegex(ValueError, "有效名稱"):
                create_xml(Path(directory), task_name="MeowKeeper+", source_env="BAD --source-url secret")

    def test_task_scheduler_xml_can_separate_application_and_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "App"
            data = Path(directory) / "Data"
            root.mkdir()
            xml = create_xml(
                root,
                task_name="MeowKeeper+ Core",
                source_env="MEOWKEEPER_MEDIA_SOURCE_URL",
                data_root=data,
            )
            self.assertIn(str((data / "config.json").resolve()), xml)
            self.assertIn(str((data / "meowkeeper.db").resolve()), xml)
            self.assertIn(str((data / "events").resolve()), xml)
            self.assertIn(str(data.resolve().parent), xml)
            self.assertIn("--lock-root", xml)
            self.assertNotIn("--source-url ", xml)

    def test_portable_verifier_rejects_unlisted_or_tampered_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = (
                "runtime/python/python.exe",
                "runtime/python/pythonw.exe",
                "vendor/ffmpeg/bin/ffmpeg.exe",
                "vendor/ffmpeg/bin/ffprobe.exe",
                "models/yolo11n.pt",
                "models/model_manifest.json",
                "tools/run_host.py",
                "tools/run_tray.py",
                "configs/botsort_reid.yaml",
            )
            records = []
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = relative.encode("utf-8")
                path.write_bytes(payload)
                records.append(
                    {
                        "path": relative,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest().upper(),
                    }
                )
            manifest = {
                "package_type": "windows_portable",
                "model": {"sha256": records[4]["sha256"]},
                "files": records,
            }
            (root / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(verify_portable(root)["status"], "passed")
            # A self-consistent manifest must not hide an omitted tracker config.
            tracker_config = root / "configs" / "botsort_reid.yaml"
            saved_config = tracker_config.read_bytes()
            tracker_config.unlink()
            manifest["files"] = records[:-1]
            (root / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "必要檔案.*botsort_reid"):
                verify_portable(root)
            tracker_config.write_bytes(saved_config)
            manifest["files"] = records
            (root / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            cache = root / "tools" / "__pycache__" / "generated.pyc"
            cache.parent.mkdir()
            cache.write_bytes(b"derived")
            self.assertEqual(verify_portable(root)["status"], "passed")
            extra = root / "private.db"
            extra.write_bytes(b"private")
            with self.assertRaisesRegex(ValueError, "manifest 外"):
                verify_portable(root)
            extra.unlink()
            (root / "tools" / "run_host.py").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "篡改"):
                verify_portable(root)

    def test_portable_verifier_allows_official_torch_hub_trusted_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            required = (
                "runtime/python/python.exe",
                "runtime/python/pythonw.exe",
                "vendor/ffmpeg/bin/ffmpeg.exe",
                "vendor/ffmpeg/bin/ffprobe.exe",
                "models/yolo11n.pt",
                "models/model_manifest.json",
                "tools/run_host.py",
                "tools/run_tray.py",
                "configs/botsort_reid.yaml",
                "data/torch/hub/trusted_list",
            )
            records = []
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = b"" if relative.endswith("trusted_list") else relative.encode("utf-8")
                path.write_bytes(payload)
                records.append(
                    {
                        "path": relative,
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest().upper(),
                    }
                )
            manifest = {
                "package_type": "windows_portable",
                "model": {"sha256": records[4]["sha256"]},
                "files": records,
            }
            (root / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(verify_portable(root)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
