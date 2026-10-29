from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.web.main import create_app
from meowkeeper.app.web.session import SessionManager
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.core.events.analysis_pipeline import AnalysisPipeline
from meowkeeper.storage.database import SQLiteStore


class Phase3ABringupTests(unittest.TestCase):
    def test_dashboard_html_and_health_are_real_routes(self) -> None:
        app = create_app(token="a" * 32)
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("喵喵守護者+", page.text)
            self.assertIn("監控狀態", page.text)
            self.assertEqual(client.get("/static/css/app.css").status_code, 200)
            self.assertEqual(client.get("/static/js/app.js").status_code, 200)
            live = client.get("/live")
            self.assertEqual(live.status_code, 200)
            self.assertIn("shared-preview.js", live.text)
            self.assertIn("MeowKeeperPreview.mount", live.text)
            shared = client.get("/static/js/shared-preview.js")
            self.assertEqual(shared.status_code, 200)
            self.assertIn('image.removeAttribute("src")', shared.text)
            self.assertIn('id="preview-image" class="preview-image" alt="MeowKeeper+ 即時影格" hidden', live.text)
            self.assertIn('id="preview-empty" class="preview-empty"', live.text)
            self.assertIn("Mock 僅供流程測試，不會產生真實影像", live.text)

    def test_setup_is_local_and_single_use(self) -> None:
        sessions = SessionManager()
        app = create_app(token="b" * 32, session_manager=sessions)
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            setup = client.get("/setup")
            self.assertEqual(setup.status_code, 200)
            match = re.search(r"const setupToken = (.+?);", setup.text)
            self.assertIsNotNone(match)
            token = match.group(1).strip().strip('"')
            payload = {"username": "phase3-admin", "password": "a-strong-password-123", "setup_token": token}
            result = client.post(
                "/api/v1/auth/setup",
                json=payload,
                headers={"X-MeowKeeper-CSRF": token},
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(client.post("/api/v1/auth/setup", json=payload).status_code, 409)
            self.assertEqual(client.get("/setup", follow_redirects=False).status_code, 307)
            root = client.get("/", follow_redirects=False)
            self.assertEqual(root.status_code, 307)
            self.assertEqual(root.headers.get("location"), "/login")

    def test_setup_page_explains_safe_onboarding_and_accessible_recovery(self) -> None:
        app = create_app(session_manager=SessionManager())
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            response = client.get("/setup")
            self.assertEqual(response.status_code, 200)
            for guidance in (
                "唯一的本機管理者帳號",
                "至少 12 個字元",
                "使用剛建立的管理者帳號登入",
                "RTSP 或 YouTube",
                "Telegram",
                "不要把密碼、Token 或攝影機網址貼到 Codex 或其他聊天",
            ):
                with self.subTest(guidance=guidance):
                    self.assertIn(guidance, response.text)
            self.assertIn('role="status" aria-live="polite"', response.text)
            self.assertIn("建立失敗。請確認帳號與密碼格式後重試", response.text)
            self.assertIn("目前無法連線到 Dashboard", response.text)
            self.assertIn("submit.disabled = true", response.text)

    def test_unconfigured_protected_pages_redirect_to_local_setup(self) -> None:
        sessions = SessionManager()
        app = create_app(session_manager=sessions)
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            self.assertEqual(client.get("/setup").status_code, 200)
            for path in ("/", "/dashboard", "/live", "/events", "/settings/source", "/settings/telegram"):
                with self.subTest(path=path):
                    response = client.get(path, follow_redirects=False)
                    self.assertEqual(response.status_code, 307)
                    self.assertEqual(response.headers.get("location"), "/setup")

    def test_controller_is_connected_and_does_not_duplicate_without_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-phase3a-") as directory:
            root = Path(directory)
            store = SQLiteStore(root / "events.sqlite3")
            try:
                controller = RepositoryCoreController(store=store, source_url=None, event_directory=root / "events")
                facade = CoreStateFacade(store=store, event_directory=root / "events", callbacks=controller.callbacks())
                app = create_app(facade=facade, token="c" * 32)
                headers = {
                    "Authorization": "Bearer " + "c" * 32,
                    "X-MeowKeeper-CSRF": hashlib.sha256(("c" * 32 + ":csrf").encode()).hexdigest(),
                }
                with TestClient(app) as client:
                    first = client.post("/api/v1/monitor/start", headers=headers)
                    second = client.post("/api/v1/monitor/start", headers=headers)
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(second.status_code, 200)
                    self.assertEqual(first.json()["status"], "waiting_for_source")
                    self.assertEqual(client.get("/api/v1/status", headers=headers).json()["lifecycle_capability"], "connected")
                    stopped = client.post(
                        "/api/v1/monitor/stop",
                        json={"confirm": True},
                        headers=headers,
                    )
                    self.assertEqual(stopped.status_code, 200)
                    self.assertEqual(stopped.json()["status"], "stopped")
            finally:
                store.close()

    def test_events_require_authentication(self) -> None:
        app = create_app(token="d" * 32)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/events").status_code, 401)
            self.assertEqual(client.get("/api/v1/events", headers={"Authorization": "Bearer " + "d" * 32}).status_code, 200)

    def test_stopped_or_changed_source_cannot_serve_previous_preview(self) -> None:
        with tempfile.TemporaryDirectory(prefix="meowkeeper-preview-lifecycle-") as directory:
            root = Path(directory)
            store = SQLiteStore(root / "events.sqlite3")
            try:
                controller = RepositoryCoreController(store=store, source_url=None)
                previous = root / "previous.jpg"
                previous.write_bytes(b"previous frame")
                facade = CoreStateFacade(store=store, callbacks=controller.callbacks())
                app = create_app(facade=facade, token="e" * 32)
                headers = {"Authorization": "Bearer " + "e" * 32}
                controller._latest_jpeg = previous
                with TestClient(app) as client:
                    self.assertEqual(client.get("/api/v1/preview/jpeg", headers=headers).status_code, 200)
                    controller.start_monitor()
                    self.assertIsNone(controller.create_snapshot())
                    self.assertEqual(client.get("/api/v1/preview/jpeg", headers=headers).status_code, 404)

                    controller._latest_jpeg = previous
                    controller.stop_monitor(confirm=True)
                    self.assertIsNone(controller.create_snapshot())
                    self.assertEqual(client.get("/api/v1/preview/jpeg", headers=headers).status_code, 404)

                    controller._latest_jpeg = previous
                    controller.set_source(None)
                    self.assertIsNone(controller.create_snapshot())
                    self.assertEqual(client.get("/api/v1/preview/jpeg", headers=headers).status_code, 404)
            finally:
                store.close()

    def test_real_video_records_observed_time_without_fabricating_cat_analysis(self) -> None:
        ffmpeg = Path(__file__).resolve().parents[1] / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
        self.assertTrue(ffmpeg.is_file())
        with tempfile.TemporaryDirectory(prefix="meowkeeper-daily-ingest-") as directory:
            root = Path(directory)
            source = root / "test-pattern.mp4"
            generated = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                 "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=False, timeout=30,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            database_path = root / "events.sqlite3"
            store = SQLiteStore(database_path)
            controller = RepositoryCoreController(
                store=store, source_url=str(source), analyzer_mode="noop",
                ffmpeg_path=str(ffmpeg), ring_directory=root / "ring",
                event_directory=root / "events", preview_directory=root / "preview",
            )
            try:
                controller.start_monitor()
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if controller._daily_monitoring is not None:
                        controller._daily_monitoring.flush()
                    summary = store.daily_monitoring_summary(day=datetime.now().astimezone().date().isoformat())
                    if (summary["monitoring_seconds"] or 0) > 0:
                        break
                    time.sleep(0.05)
                self.assertGreater(summary["monitoring_seconds"] or 0, 0, controller.get_status())
                self.assertIsNone(summary["cat_active_seconds"])
                jpeg = controller.create_snapshot()
                self.assertIsNotNone(jpeg)
                self.assertTrue(jpeg.read_bytes().startswith(b"\xff\xd8"))
                # A finite local file can reach EOF before the assertions;
                # completed ingest has zero connections, never a second one.
                self.assertIn(controller.get_status()["metrics"]["source"]["connections"], (0, 1))
            finally:
                controller.close()
                store.close()
            reopened = SQLiteStore(database_path)
            try:
                persisted = reopened.daily_monitoring_summary(day=datetime.now().astimezone().date().isoformat())
                self.assertGreater(persisted["monitoring_seconds"] or 0, 0)
                self.assertIsNone(persisted["cat_active_seconds"])
                self.assertIsNone(persisted["sleep_seconds"])
            finally:
                reopened.close()

    def test_authenticated_start_serves_real_ffmpeg_preview_from_one_ingest(self) -> None:
        ffmpeg = Path(__file__).resolve().parents[1] / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
        self.assertTrue(ffmpeg.is_file())
        with tempfile.TemporaryDirectory(prefix="meowkeeper-dashboard-ingest-") as directory:
            root = Path(directory)
            source = root / "synthetic.mp4"
            generated = subprocess.run(
                [
                    str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                    "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=False, timeout=30,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            store = SQLiteStore(root / "events.sqlite3")
            controller = RepositoryCoreController(
                store=store, source_url=str(source), analyzer_mode="noop",
                ffmpeg_path=str(ffmpeg), ring_directory=root / "ring",
                event_directory=root / "events", preview_directory=root / "preview",
            )
            facade = CoreStateFacade(store=store, callbacks=controller.callbacks())
            app = create_app(facade=facade, token="f" * 32)
            headers = {
                "Authorization": "Bearer " + "f" * 32,
                "X-MeowKeeper-CSRF": hashlib.sha256(("f" * 32 + ":csrf").encode()).hexdigest(),
            }
            frame_ready = threading.Event()
            release_analysis = threading.Event()

            def hold_first_analysis(_pipeline: AnalysisPipeline, _frame: object) -> None:
                frame_ready.set()
                release_analysis.wait(timeout=10)

            try:
                with patch.object(AnalysisPipeline, "process", hold_first_analysis), TestClient(app) as client:
                    try:
                        self.assertEqual(client.get("/api/v1/preview/jpeg").status_code, 401)
                        started = client.post("/api/v1/monitor/start", headers=headers, json={})
                        self.assertEqual(started.status_code, 200)
                        self.assertTrue(frame_ready.wait(timeout=10), controller.get_status())
                        preview = client.get("/api/v1/preview/jpeg", headers=headers)
                        self.assertEqual(preview.status_code, 200)
                        self.assertTrue(preview.content.startswith(b"\xff\xd8"))
                        self.assertTrue(preview.content.endswith(b"\xff\xd9"))
                        self.assertEqual(controller.get_status()["metrics"]["source"]["connections"], 1)
                    finally:
                        release_analysis.set()
                    stopped = client.post("/api/v1/monitor/stop", headers=headers, json={"confirm": True})
                    self.assertEqual(stopped.status_code, 200)
                    self.assertEqual(client.get("/api/v1/preview/jpeg", headers=headers).status_code, 404)
            finally:
                controller.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
