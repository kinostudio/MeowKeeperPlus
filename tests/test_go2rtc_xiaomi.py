from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.integrations.go2rtc.config import build_yaml, sanitize_stream_name
from meowkeeper.integrations.go2rtc.manifest import (
    OFFICIAL_RELEASE_TAG,
    OFFICIAL_REPOSITORY,
    verify_manifest,
)
from meowkeeper.integrations.go2rtc.models import Go2RtcStatus
from meowkeeper.integrations.go2rtc.process import Go2RtcProcess
from meowkeeper.integrations.go2rtc.redaction import redact_secrets
from meowkeeper.integrations.xiaomi.models import XiaomiQualityProfile
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService


class FakeGo2Rtc:
    api_url = "http://127.0.0.1:1984"
    rtsp_port = 8554

    def __init__(self) -> None:
        self.started = False

    def install(self):
        return Go2RtcStatus("ready", True, False, version=OFFICIAL_RELEASE_TAG)

    def start(self):
        self.started = True
        return Go2RtcStatus("running", True, True, version=OFFICIAL_RELEASE_TAG)

    def stop(self):
        self.started = False
        return Go2RtcStatus("ready", True, False, version=OFFICIAL_RELEASE_TAG)

    def status(self):
        return Go2RtcStatus("running" if self.started else "ready", True, self.started, version=OFFICIAL_RELEASE_TAG)


class Go2RtcXiaomiTests(unittest.TestCase):
    def test_child_environment_finds_bundled_ffmpeg_without_rewriting_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-go2rtc-ffmpeg-") as directory:
            root = Path(directory)
            ffmpeg_dir = root / "vendor" / "ffmpeg" / "bin"
            ffmpeg_dir.mkdir(parents=True)
            (ffmpeg_dir / "ffmpeg.exe").write_bytes(b"test")
            process = Go2RtcProcess(root, api_port=21984, rtsp_port=28554)
            with patch.dict(os.environ, {"PATH": "existing-tools"}):
                child = process._child_environment()
                self.assertEqual(os.environ["PATH"], "existing-tools")
            self.assertEqual(child["PATH"], str(ffmpeg_dir) + os.pathsep + "existing-tools")
            self.assertFalse(process.config_path.exists())

    def test_official_manifest_and_binary_hash_are_pinned(self) -> None:
        executable = ROOT / "vendor" / "go2rtc" / OFFICIAL_RELEASE_TAG / "go2rtc.exe"
        if not executable.is_file():
            self.skipTest("official go2rtc binary not downloaded")
        manifest = verify_manifest(ROOT, executable)
        self.assertEqual(manifest.repository, OFFICIAL_REPOSITORY)
        self.assertEqual(manifest.release_tag, OFFICIAL_RELEASE_TAG)

    def test_real_go2rtc_process_localhost_health_and_stop(self) -> None:
        if not (ROOT / "vendor" / "go2rtc" / OFFICIAL_RELEASE_TAG / "go2rtc.exe").is_file():
            self.skipTest("official go2rtc binary not downloaded")
        with tempfile.TemporaryDirectory(prefix="meowkeeper-go2rtc-process-") as directory:
            temporary_root = Path(directory)
            process = Go2RtcProcess(
                ROOT,
                api_port=1994,
                rtsp_port=8564,
                log_path=temporary_root / "go2rtc.log",
            )
            process.config_path = temporary_root / "config.yaml"
            started = process.start()
            try:
                self.assertTrue(started.healthy, started.message_zh_tw)
                self.assertEqual(started.api_url, "http://127.0.0.1:1994")
                self.assertEqual(started.rtsp_url, "rtsp://127.0.0.1:8564")
            finally:
                stopped = process.stop()
            self.assertFalse(stopped.healthy)

    def test_go2rtc_config_is_localhost_only_and_stream_name_sanitized(self) -> None:
        self.assertEqual(sanitize_stream_name("Mi Camera / 2K"), "Mi-Camera-2K")
        yaml = build_yaml(type("Config", (), {"api_host": "127.0.0.1", "api_port": 1984, "rtsp_host": "127.0.0.1", "rtsp_port": 8554, "streams": {"cam": "rtsp://127.0.0.1:554/cam"}})())
        self.assertIn("listen: 127.0.0.1:1984", yaml)
        self.assertNotIn("0.0.0.0", yaml)

    def test_existing_session_config_is_reused_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-go2rtc-config-") as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                "api:\n  listen: :1984\nrtsp:\n  listen: :8554\nxiaomi:\n  account-redacted: SESSION-REDACTED\n",
                encoding="utf-8",
            )
            process = Go2RtcProcess(directory)
            process.config_path = config_path
            before = config_path.read_bytes()
            self.assertTrue(process._existing_config_matches_ports(config_path))
            self.assertEqual(config_path.read_bytes(), before)

    def test_unknown_session_config_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-go2rtc-session-guard-") as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("xiaomi:\n  account-redacted: SESSION-REDACTED\n", encoding="utf-8")
            process = Go2RtcProcess(ROOT, api_port=23984, rtsp_port=28554)
            process.config_path = config_path
            before = config_path.read_bytes()
            with patch.object(process, "status", return_value=Go2RtcStatus("stopped", True, False)), \
                 patch.object(process, "install", return_value=Go2RtcStatus("ready", True, False)), \
                 patch("meowkeeper.integrations.go2rtc.process.port_owner", return_value=[]), \
                 patch("meowkeeper.integrations.go2rtc.process.verify_manifest"):
                result = process.start()
            self.assertFalse(result.healthy)
            self.assertIn("未覆寫", result.message_zh_tw)
            self.assertEqual(config_path.read_bytes(), before)

    def test_nonstandard_ports_use_scoped_lock_and_repository_config(self) -> None:
        process = Go2RtcProcess(ROOT, api_port=1994, rtsp_port=8564)
        self.assertEqual(process.lock_name, "MeowKeeperPlusGo2Rtc_1994_8564")
        self.assertIn(str(ROOT / "data" / "go2rtc"), str(process.config_path))

    def test_redaction_removes_password_and_rtsp_secret(self) -> None:
        value = "rtsp://admin:secret@127.0.0.1:554/cam password=abc token=xyz"
        redacted = redact_secrets(value)
        self.assertNotIn("secret", redacted)
        self.assertNotIn("abc", redacted)
        self.assertNotIn("xyz", redacted)

    def test_setup_waits_for_user_login_without_persisting_secret(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-") as directory:
            state_path = Path(directory) / "state.json"
            service = XiaomiSetupService(directory, go2rtc=FakeGo2Rtc(), state_path=state_path)
            with patch("webbrowser.open", return_value=True):
                prepared = service.prepare()
                login = service.open_login()
            self.assertEqual(prepared["setup_stage"], "waiting_for_login")
            self.assertTrue(login["browser_opened"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("password", json.dumps(state).lower())
            self.assertNotIn("token", json.dumps(state).lower())

    def test_setup_mock_profiles_choose_highest_stable_and_source_callback(self) -> None:
        switched: list[str] = []
        def discovery():
            return [{"device_id": "abc123", "source_url": "xiaomi://test@example.invalid?did=test", "source_reference_present": True, "display_name": "小米攝影機", "model": "MJSXJ03HL", "region": "TW"}]
        def probe(_device, _names):
            return [
                XiaomiQualityProfile("sd", 640, 360, 15, "h264", None, True),
                XiaomiQualityProfile("hd", 1920, 1080, 10, "h264", None, True),
            ]
        def switch(url, _device, _profile):
            switched.append(url)
            return {"status": "connected"}
        with tempfile.TemporaryDirectory(prefix="meowkeeper-xiaomi-") as directory:
            service = XiaomiSetupService(directory, go2rtc=FakeGo2Rtc(), state_path=Path(directory) / "state.json", discovery=discovery, profile_probe=probe, source_switch=switch)
            service.prepare()
            service.refresh()
            self.assertEqual(service.status()["device_count"], 1)
            service.probe_device("abc123")
            result = service.activate("abc123")
            self.assertEqual(result["setup_stage"], "connected")
            self.assertTrue(switched[0].startswith("rtsp://127.0.0.1:8554/"))


if __name__ == "__main__":
    unittest.main()
