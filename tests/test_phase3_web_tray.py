from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from meowkeeper.app.web.auth import ApiAuthenticator, AuthError
from meowkeeper.app.web.contracts import SettingsValidationError, validate_settings_patch
from meowkeeper.app.web.events import EventHub
from meowkeeper.app.web.session import SessionAuthError, SessionManager
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.integrations.tailscale.client import TailscaleClient, _default_runner
from meowkeeper.integrations.tailscale.installer import OfficialTailscaleInstaller
from meowkeeper.integrations.tailscale.subnet_router import plan_subnet_route
from meowkeeper.integrations.xiaomi.models import XiaomiQualityProfile
from meowkeeper.integrations.xiaomi.quality_probe import choose_highest_stable_profile
from meowkeeper.integrations.xiaomi.security import public_device_payload
from meowkeeper.process_guard import ProcessInfo, is_project_python
from meowkeeper.storage.database import SQLiteStore
from meowkeeper.tray.client import TrayApiClient, TrayApiError
from meowkeeper.tray.main import _is_installed_runtime
from meowkeeper.tray.single_instance import SingleInstanceLock


class Phase3PureContractTests(unittest.TestCase):
    def test_auth_and_csrf_are_constant_time_contract(self) -> None:
        auth = ApiAuthenticator(token="a" * 32, csrf_token="b" * 32)
        self.assertTrue(auth.check_token("Bearer " + "a" * 32))
        self.assertFalse(auth.check_token("wrong"))
        self.assertTrue(auth.check_csrf("b" * 32))
        self.assertNotIn("a" * 32, auth.redact("a" * 32))

    def test_session_password_hash_cookie_contract_and_lockout(self) -> None:
        manager = SessionManager(max_failures=2, lockout_seconds=60)
        with self.assertRaises(SessionAuthError):
            manager.login("admin", "not-configured")
        manager.configure_admin("admin", "a-strong-password-123")
        with self.assertRaises(SessionAuthError):
            manager.login("admin", "wrong-password", now=1)
        record = manager.login("admin", "a-strong-password-123", now=2)
        self.assertEqual(manager.authenticate(record.session_id, now=2).username, "admin")
        manager.logout(record.session_id)
        self.assertIsNone(manager.authenticate(record.session_id, now=2))

    def test_missing_environment_token_is_not_checked(self) -> None:
        with self.assertRaises(AuthError):
            ApiAuthenticator.from_environment("MEOWKEEPER_TEST_MISSING_TOKEN_9A7D")

    def test_settings_reject_secret_and_command_fields(self) -> None:
        self.assertEqual(validate_settings_patch({"confidence_threshold": 0.45})["confidence_threshold"], 0.45)
        with self.assertRaises(SettingsValidationError):
            validate_settings_patch({"telegram_token": "secret"})
        with self.assertRaises(SettingsValidationError):
            validate_settings_patch({"ffmpeg_args": "-i anything"})
        with self.assertRaises(SettingsValidationError):
            validate_settings_patch({"analysis_fps": 60})

    def test_event_hub_is_bounded_and_streams_events(self) -> None:
        hub = EventHub(max_pending_per_client=1)
        stream = hub.stream(heartbeat_seconds=0.1)
        self.assertIn("connected", next(stream))
        hub.publish("status", {"value": "running"})
        self.assertIn("event: status", next(stream))
        stream.close()
        self.assertEqual(hub.subscriber_count, 0)

    def test_sqlite_events_are_paginated_and_path_is_contained(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3-db-") as directory:
            root = Path(directory) / "events"
            root.mkdir()
            event_file = root / "inside.mp4"
            event_file.write_bytes(b"not-real-mp4")
            outside = Path(directory) / "outside.mp4"
            outside.write_bytes(b"private")
            store = SQLiteStore(Path(directory) / "events.sqlite3")
            try:
                for index in range(3):
                    store.create_media_event(
                        event_id=f"event-{index}", event_type="cat_detected", trigger_at=datetime.now(timezone.utc),
                        source_id="local-test", track_id=None, pet_id=None, confidence=0.8,
                        requested_pre_seconds=5, requested_post_seconds=10,
                    )
                rows = store.list_media_events(limit=2, offset=0)
                self.assertEqual(len(rows), 2)
                self.assertEqual(len(store.list_media_events(limit=2, offset=2)), 1)
                store.update_media_event("event-0", clip_path=str(event_file))
                facade = CoreStateFacade(store=store, event_directory=root)
                self.assertEqual(facade.event_file("event-0"), event_file.resolve())
                store.update_media_event("event-1", clip_path=str(outside))
                self.assertIsNone(facade.event_file("event-1"))
            finally:
                store.close()

    def test_preview_contract_does_not_add_source_connection(self) -> None:
        facade = CoreStateFacade()
        preview = facade.preview_status()
        self.assertEqual(preview["source_connections_added"], 0)
        self.assertEqual(preview["mode"], "not_available")

    def test_preview_contract_reports_live_dimensions_and_measured_fps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-preview-metadata-") as directory:
            preview_path = Path(directory) / "latest.jpg"
            preview_path.write_bytes(b"jpeg")
            facade = CoreStateFacade(
                callbacks={
                    "snapshot": lambda: preview_path,
                    "status": lambda: {
                        "metrics": {
                            "source": {
                                "status": "running",
                                "source_id": "Xiaomi（本機 go2rtc）",
                                "stream_name": "xiaomi_camera_2",
                                "resolution": "640×360",
                                "width": 640,
                                "height": 360,
                                "fps": 4.98,
                            }
                        }
                    },
                }
            )
            preview = facade.preview_status()
        self.assertEqual(preview["source_connections_added"], 0)
        self.assertEqual(preview["resolution"], "640×360")
        self.assertEqual(preview["width"], 640)
        self.assertEqual(preview["height"], 360)
        self.assertEqual(preview["fps"], 4.98)
        self.assertEqual(preview["source_id"], "Xiaomi（本機 go2rtc）")
        self.assertEqual(preview["stream_name"], "xiaomi_camera_2")
        self.assertTrue(preview["source_is_xiaomi"])

    def test_tray_rejects_external_url_and_requires_stop_confirmation(self) -> None:
        with self.assertRaises(ValueError):
            TrayApiClient(base_url="http://0.0.0.0:8000")
        client = TrayApiClient()
        with self.assertRaises(TrayApiError):
            client.stop(confirm=False)
        if os.name != "nt":
            self.assertFalse(client.copy_dashboard_url())
        with self.assertRaises(TrayApiError):
            client._copy_url("https://example.com", allow_tailnet=True)

    def test_tray_single_instance_lock(self) -> None:
        name = "MeowKeeperPlusTestTrayLock"
        first = SingleInstanceLock(name)
        second = SingleInstanceLock(name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            second.release()
            first.release()

    def test_tray_installed_runtime_requires_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime" / "python"
            runtime.mkdir(parents=True)
            (runtime / "python.exe").write_bytes(b"placeholder")
            self.assertFalse(_is_installed_runtime(root))
            (root / "build_manifest.json").write_text("{}", encoding="utf-8")
            self.assertTrue(_is_installed_runtime(root))

    def test_process_guard_requires_repository_venv_for_runtime_identity(self) -> None:
        root = Path(r"C:\workspace\MeowKeeperPlus")
        project = ProcessInfo(
            10,
            1,
            "python.exe",
            str(root / ".venv" / "Scripts" / "python.exe"),
            (str(root / ".venv" / "Scripts" / "python.exe"), str(root / "tools" / "run_host.py")),
            "host",
        )
        system = ProcessInfo(
            11,
            1,
            "python.exe",
            r"C:\Python310\python.exe",
            (r"C:\Python310\python.exe", str(root / "tools" / "run_host.py")),
            "host",
        )
        self.assertTrue(is_project_python(project, root))
        self.assertFalse(is_project_python(system, root))

    def test_tailscale_fake_status_and_funnel_guard(self) -> None:
        def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            if command[-2:] == ["status", "--json"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({
                    "BackendState": "Running",
                    "Self": {"HostName": "meow-pc", "DNSName": "meow-pc.example.ts.net.", "TailscaleIPs": ["100.64.0.2"]},
                }), "")
            if command[-3:] == ["serve", "status", "--json"]:
                return subprocess.CompletedProcess(command, 0, "{}", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        client = TailscaleClient(executable="tailscale.exe", runner=runner)
        status = client.status()
        self.assertEqual(status.tailscale_ip, "100.64.0.2")
        self.assertEqual(status.magic_dns_name, "meow-pc.example.ts.net")
        self.assertEqual(client.build_serve_plan(enable=True, confirm=False)["status"], "confirmation_required")

    def test_tailscale_default_runner_decodes_utf8_on_windows(self) -> None:
        with patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["tailscale", "status", "--json"], 0, "{}", "")
            _default_runner(["tailscale", "status", "--json"], 5.0)

        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_tailscale_none_status_is_a_safe_error_not_type_error(self) -> None:
        from meowkeeper.integrations.tailscale.status import parse_status_json

        status = parse_status_json(None)
        self.assertEqual(status.status, "error")
        self.assertIn("沒有回傳內容", status.message_zh_tw or "")

    def test_tailscale_client_rediscovers_cli_installed_after_host_start(self) -> None:
        def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.2"]}}),
                "",
            )

        fake_executable = str(Path(__file__).resolve())
        with patch.object(TailscaleClient, "find_executable", side_effect=[None, fake_executable]):
            client = TailscaleClient(runner=runner)
            self.assertIsNone(client.executable)
            status = client.status()

        self.assertTrue(status.installed)
        self.assertTrue(status.logged_in)
        self.assertEqual(status.tailscale_ip, "100.64.0.2")

    def test_tailscale_cli_access_denied_is_not_reported_as_missing_or_logged_out(self) -> None:
        def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            return subprocess.CompletedProcess(command, 1, "", "Access is denied to the local Tailscale service")

        client = TailscaleClient(executable="tailscale.exe", runner=runner)
        status = client.status()

        self.assertTrue(status.installed)
        self.assertEqual(status.status, "permission_denied")
        self.assertIn("無權讀取 Tailscale 服務狀態", status.message_zh_tw or "")

    def test_tailscale_process_permission_exception_is_reported_as_permission_denied(self) -> None:
        def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del command, timeout
            raise PermissionError(5, "Access is denied")

        client = TailscaleClient(executable="tailscale.exe", runner=runner)
        status = client.status()

        self.assertTrue(status.installed)
        self.assertEqual(status.status, "permission_denied")
        self.assertIn("無法執行狀態檢查", status.message_zh_tw or "")

    def test_tailscale_status_timeout_is_not_reported_as_missing(self) -> None:
        def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(command, timeout)

        client = TailscaleClient(executable="tailscale.exe", runner=runner)
        status = client.status()

        self.assertTrue(status.installed)
        self.assertEqual(status.status, "timeout")
        self.assertIn("狀態檢查逾時", status.message_zh_tw or "")

    def test_tailscale_installer_requires_pinned_official_manifest(self) -> None:
        installer = OfficialTailscaleInstaller()
        self.assertEqual(installer.plan(confirm=True).status, "not_checked")
        self.assertEqual(
            installer.plan(
                source_url="https://pkgs.tailscale.com/stable/tailscale-1.2.3.exe",
                version="1.2.3",
                file_size_bytes=10,
                sha256="a" * 64,
                confirm=False,
            ).status,
            "confirmation_required",
        )
        self.assertEqual(installer.plan(source_url="https://example.invalid/x", version="1", file_size_bytes=1, sha256="a" * 64).status, "error")

    def test_subnet_router_rejects_broad_default(self) -> None:
        self.assertEqual(plan_subnet_route("192.168.0.0/16").status, "rejected")
        self.assertEqual(plan_subnet_route("192.168.31.10/32", enable=True).status, "confirmation_required")

    def test_xiaomi_quality_and_secret_filter(self) -> None:
        profiles = [
            XiaomiQualityProfile("low", 640, 360, 15, "h264", None, True),
            XiaomiQualityProfile("high", 2560, 1440, 10, "h265", None, True),
            XiaomiQualityProfile("broken", 3840, 2160, 15, "h265", None, False),
        ]
        self.assertEqual(choose_highest_stable_profile(profiles).name, "high")
        self.assertEqual(public_device_payload({"model": "x", "token": "secret", "region": "TW"}), {"model": "x", "region": "TW"})


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "FastAPI runtime 尚未安裝；Web smoke 為 not_checked")
class FastApiSmokeTests(unittest.TestCase):
    def test_health_and_authentication(self) -> None:
        from fastapi.testclient import TestClient

        from meowkeeper.app.web.api import create_app

        app = create_app(token="a" * 32)
        client = TestClient(app)
        self.assertEqual(client.get("/api/v1/health").status_code, 200)
        self.assertEqual(client.get("/api/v1/status").status_code, 401)
        self.assertEqual(client.get("/api/v1/logs").status_code, 401)
        self.assertEqual(client.get("/api/v1/status", headers={"Authorization": "Bearer " + "a" * 32}).status_code, 200)
        self.assertEqual(client.get("/api/v1/logs", headers={"Authorization": "Bearer " + "a" * 32}).json()["items"], [])
        self.assertEqual(client.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()
