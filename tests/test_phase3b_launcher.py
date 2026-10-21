from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class Phase3BLauncherContractTests(unittest.TestCase):
    def test_saved_local_go2rtc_starts_before_ingest_and_preserves_config(self) -> None:
        from tools.run_host import _prepare_saved_go2rtc

        bridge = Mock()
        bridge.rtsp_port = 28554
        bridge.status.return_value = SimpleNamespace(healthy=False)
        bridge.config_path.is_file.return_value = True
        bridge.start.return_value = SimpleNamespace(healthy=True)
        logs = []
        self.assertTrue(_prepare_saved_go2rtc("rtsp://127.0.0.1:28554/saved_camera", bridge, logs.append))
        bridge.start.assert_called_once_with()
        self.assertNotIn("rtsp://", "".join(logs))
        bridge.reset.assert_not_called()

    def test_saved_bridge_reuses_healthy_instance_and_never_creates_empty_config(self) -> None:
        from tools.run_host import _prepare_saved_go2rtc

        bridge = Mock()
        bridge.rtsp_port = 8554
        bridge.status.return_value = SimpleNamespace(healthy=True)
        self.assertTrue(_prepare_saved_go2rtc("rtsp://127.0.0.1:8554/camera", bridge, lambda _: None))
        bridge.start.assert_not_called()
        bridge.status.return_value = SimpleNamespace(healthy=False)
        bridge.config_path.is_file.return_value = False
        self.assertFalse(_prepare_saved_go2rtc("rtsp://127.0.0.1:8554/camera", bridge, lambda _: None))
        bridge.start.assert_not_called()

    def test_non_go2rtc_sources_do_not_start_bridge(self) -> None:
        from tools.run_host import _prepare_saved_go2rtc

        bridge = Mock(rtsp_port=8554)
        for source in (None, "https://youtube.com/watch?v=test", "rtsp://192.168.1.2:554/live",
                       "rtsp://127.0.0.1:28554/camera", "rtsp://u:p@127.0.0.1:8554/camera"):
            self.assertFalse(_prepare_saved_go2rtc(source, bridge, lambda _: None))
        bridge.start.assert_not_called()

    def test_cmd_files_are_ascii_crlf_without_bom_or_machine_paths(self) -> None:
        for name in ("Start-MeowKeeperPlus.cmd", "Start-MeowKeeperPlus-Tray.cmd"):
            data = (ROOT / name).read_bytes()
            self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(data.decode("ascii").encode("ascii"), data)
            self.assertIn(b"\r\n", data)
            self.assertNotIn(b"C:\\Users\\Aaron", data)
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_powershell_bridges_use_script_root(self) -> None:
        for name in ("scripts/Start-MeowKeeperPlus.ps1", "scripts/Start-MeowKeeperPlus-Tray.ps1"):
            text = (ROOT / name).read_text(encoding="utf-8-sig")
            self.assertIn("$PSScriptRoot", text)
            self.assertIn("Resolve-Path", text)
            self.assertIn("Set-Location -LiteralPath", text)

    def test_formal_launchers_pin_both_windows_runtimes_to_repository_venv(self) -> None:
        host_text = (ROOT / "scripts/Start-MeowKeeperPlus.ps1").read_text(encoding="utf-8-sig")
        tray_text = (ROOT / "scripts/Start-MeowKeeperPlus-Tray.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(".venv\\Scripts\\python.exe", host_text)
        self.assertIn(".venv\\Scripts\\python.exe", tray_text)
        self.assertIn(".venv\\Scripts\\pythonw.exe", tray_text)
        for text in (host_text, tray_text):
            self.assertNotRegex(text, r"(?im)(?:&|-FilePath\s+)(?:python|py|pythonw)(?:\.exe)?(?:\s|$)")
            self.assertNotIn("Get-Command python", text)
            self.assertNotIn("Get-Command pythonw", text)

    def test_verify_launcher_runs_without_starting_a_second_host(self) -> None:
        from tools import launcher as launcher_module

        lock = Mock()
        lock.acquire.return_value = True
        go2rtc_process = Mock()
        go2rtc_process.process = None
        go2rtc_process.start.return_value = SimpleNamespace(
            healthy=True,
            message_zh_tw="",
        )
        dashboard_config = SimpleNamespace(port=18765, bind_host="127.0.0.1")
        dashboard_network = Mock()
        dashboard_network.snapshot.return_value = {
            "urls": [],
            "local_url": "http://127.0.0.1:18765",
        }
        runtime_probe = SimpleNamespace(versions={})
        args = launcher_module.build_parser().parse_args(
            [
                "--verify-launcher",
                "--no-browser",
                "--no-tray",
                "--root",
                str(ROOT),
                "--dashboard-config",
                "unused-dashboard-config.json",
            ]
        )
        stdout = StringIO()
        with (
            patch.dict(os.environ, {"YOLO_CONFIG_DIR": str(ROOT / "data" / "ultralytics")} ),
            patch.object(launcher_module, "bootstrap_runtime", return_value=runtime_probe),
            patch.object(launcher_module, "_verify_runtime_files"),
            patch("meowkeeper.app.web.network.resolve_dashboard_config", return_value=dashboard_config),
            patch(
                "meowkeeper.app.web.network.DashboardNetworkService",
                return_value=dashboard_network,
            ),
            patch("meowkeeper.app.web.network.listener_bind_matches", return_value=True) as bind_matches,
            patch("meowkeeper.process_guard.CrossProcessLock", return_value=lock) as lock_factory,
            patch("meowkeeper.process_guard.iter_meowkeeper_processes", return_value=[]) as processes,
            patch(
                "meowkeeper.integrations.go2rtc.process.Go2RtcProcess",
                return_value=go2rtc_process,
            ) as go2rtc_factory,
            patch.object(launcher_module, "check_health", return_value=True) as check_health,
            patch.object(launcher_module, "_append_log") as append_log,
            patch.object(launcher_module, "_start_host") as start_host,
            redirect_stdout(stdout),
        ):
            result = launcher_module.run_launcher(args)

        payload = json.loads(stdout.getvalue().splitlines()[-1])
        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["host_reused"])
        self.assertTrue(payload["health"])
        lock_factory.assert_called_once_with("MeowKeeperPlusLauncher", root=ROOT)
        lock.release.assert_called_once_with()
        processes.assert_called_once_with(ROOT)
        go2rtc_factory.assert_called_once_with(ROOT)
        go2rtc_process.start.assert_called_once_with()
        go2rtc_process.stop.assert_called_once_with()
        check_health.assert_called_once_with("http://127.0.0.1:18765")
        bind_matches.assert_called_once_with(18765, "127.0.0.1")
        append_log.assert_called()
        start_host.assert_not_called()

    def test_menu_contract_and_assets(self) -> None:
        from meowkeeper.tray.main import verify_menu_contract

        payload = verify_menu_contract()
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["item_count"], 10)
        from PIL import Image

        for name in ("ready", "waiting", "error"):
            with Image.open(ROOT / "assets" / "tray" / f"meowkeeper-{name}.ico") as image:
                self.assertIn((256, 256), image.info.get("sizes", set()))
                self.assertIn((16, 16), image.info.get("sizes", set()))

    def test_recent_clip_uses_session_media_clock_for_fast_local_ingest(self) -> None:
        import shutil
        import tempfile

        from meowkeeper.core.media import (
            ClipAssembler,
            DiskSegmentRingBuffer,
            EventClipService,
            FfmpegMediaIngest,
            validate_media,
        )

        ffmpeg = ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3b-timeline-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            generated = subprocess.run(
                [
                    str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                    "-t", "12", "-c:v", "libx264", "-g", "10", "-keyint_min", "10",
                    "-sc_threshold", "0", "-pix_fmt", "yuv420p", str(source),
                ], capture_output=True, text=True, check=False, timeout=60
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            ring = DiskSegmentRingBuffer(root / "ring", retention_seconds=60, segment_seconds=1, maximum_size_mb=64)
            ingest = FfmpegMediaIngest(str(source), ring, fps=5, width=320, prefer_stream_copy=True)
            frames = list(ingest.iter_frames(max_frames=60))
            self.assertGreaterEqual(len(frames), 20)
            ring.scan()
            self.assertGreater(ring.index.all().__len__(), 5)
            service = EventClipService(ring, ClipAssembler(), output_directory=root / "events", telegram_enabled=False)
            try:
                live2 = service.get_recent_clip(2, 0)
                live5 = service.get_recent_clip(5, 0)
            finally:
                service.close()
            self.assertEqual(live2.status, "completed")
            self.assertEqual(live5.status, "completed")
            self.assertIsNotNone(live2.path)
            self.assertIsNotNone(live5.path)
            self.assertTrue(validate_media(live2.path).valid)
            self.assertTrue(validate_media(live5.path).valid)
            self.assertLessEqual(live2.actual_duration_seconds or 999, 5.0)
            self.assertLessEqual(live5.actual_duration_seconds or 999, 8.0)
            self.assertGreater(live5.actual_duration_seconds or 0, 2.0)
            shutil.rmtree(root / "ring", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
