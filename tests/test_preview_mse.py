from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from meowkeeper.app.web.preview_mse import (
    PreviewMseSlots,
    active_stream,
    proxy_mse_preview,
    safe_mse_mime,
    same_origin_websocket,
    validate_codec_offer,
    websocket_target,
)
from meowkeeper.app.web.viewers import LiveViewerTracker


class PreviewMseContractTests(unittest.TestCase):
    def test_async_preview_and_overlay_regressions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bundled = root / "vendor" / "javascript" / "node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if node is None:
            self.skipTest("缺少 JavaScript runtime")
        for script in ("preview_async_regression.cjs", "ai_overlay_runtime.cjs"):
            with self.subTest(script=script):
                result = subprocess.run([node, str(root / "tests/js" / script)], capture_output=True,
                                        text=True, timeout=15, check=False, shell=False)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_mse_buffer_pruning_does_not_repeat_updateend_removals(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bundled = root / "vendor" / "javascript" / "node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if node is None:
            self.skipTest("JavaScript runtime unavailable")
        result = subprocess.run(
            [node, str(root / "tests/js/mse_player_runtime.cjs")],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preview_controller_executes_mount_and_frame_lifecycle(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bundled = root / "vendor" / "javascript" / "node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if node is None:
            self.skipTest("JavaScript runtime unavailable")
        result = subprocess.run(
            [node, str(root / "tests" / "js" / "shared_preview_runtime.cjs")],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("passed: mount", result.stdout)

    def test_three_modes_are_wired_to_the_required_page_defaults(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "meowkeeper" / "app" / "web"
        shared = (root / "static" / "js" / "shared-preview.js").read_text(encoding="utf-8")
        player = (root / "static" / "js" / "mse-player.js").read_text(encoding="utf-8")
        dashboard = (root / "static" / "js" / "dashboard.js").read_text(encoding="utf-8")
        live = (root / "templates" / "live.html").read_text(encoding="utf-8")
        preview_css = (root / "static" / "css" / "experience.css").read_text(encoding="utf-8")
        redesign_css = (root / "static" / "css" / "redesign.css").read_text(encoding="utf-8")
        self.assertIn('sleep: { label: "休眠"', shared)
        self.assertIn('energy: { label: "節能"', shared)
        self.assertIn('smooth: { label: "流暢"', shared)
        self.assertIn('scope:"overview",defaultMode:"energy"', dashboard)
        self.assertIn('scope:"live",defaultMode:"smooth"', live)
        self.assertIn("/api/v1/preview/mse", player)
        self.assertIn('previewPaused = () => document.hidden || sharedPreview?.mode() === "sleep"', live)
        self.assertNotIn("127.0.0.1:1984", player + shared)
        self.assertIn(".preview-mode-select", preview_css)
        self.assertIn(".preview-mode-select:hover .preview-mode-tooltip", preview_css)
        self.assertIn(".preview-mode-select:focus-visible .preview-mode-tooltip", preview_css)
        self.assertIn(".preview-mode-hud>.preview-mode-button:not(.preview-mode-select) { padding:4px 8px; font-size:.72rem; }", preview_css)
        self.assertIn('font-size:.72rem', preview_css)
        self.assertIn('font-size:.72rem', redesign_css)
        self.assertIn(".preview-mode-button { display:inline-flex", preview_css,
                      "AI overlay controls keep their separate labeled-button styling")

    def test_only_fixed_video_codec_offer_is_accepted(self) -> None:
        self.assertEqual(validate_codec_offer("hvc1.1.6.L153.B0,avc1.640029"), "hvc1.1.6.L153.B0,avc1.640029")
        for value in ("rtsp://user:pass@camera", "hvc1.1.6.L153.B0&src=other", "hvc1.1.6.L153.B0,hvc1.1.6.L153.B0", "x" * 257):
            with self.subTest(value=value[:40]), self.assertRaises(ValueError):
                validate_codec_offer(value)

    def test_mime_and_source_must_be_safe_and_fresh(self) -> None:
        self.assertEqual(safe_mse_mime('video/mp4; codecs="hvc1.1.6.L153.B0"'), 'video/mp4; codecs="hvc1.1.6.L153.B0"')
        self.assertIsNone(safe_mse_mime('video/mp4; codecs="hvc1.1.6.L153.B0\",rtsp://evil"'))
        valid = {
            "source_is_xiaomi": True, "source_status": "running", "frame_age_seconds": 2.0,
            "source_connections": 1, "stream_name": "xiaomi_camera_3",
        }
        self.assertEqual(active_stream(valid), "xiaomi_camera_3")
        self.assertIsNone(active_stream({**valid, "frame_age_seconds": 16.0}))
        self.assertIsNone(active_stream({**valid, "source_connections": 0}))
        self.assertIsNone(active_stream({**valid, "stream_name": "../../api/config"}))

    def test_upstream_can_only_be_local_and_source_is_encoded(self) -> None:
        target = websocket_target("http://127.0.0.1:1984", "xiaomi_camera_3")
        self.assertEqual(target, ("ws://127.0.0.1:1984/api/ws?src=xiaomi_camera_3", "http://127.0.0.1:1984"))
        self.assertIsNone(websocket_target("http://192.168.1.8:1984", "xiaomi_camera_3"))
        self.assertIsNone(websocket_target("http://127.0.0.1:1984", "../../api/config"))

    def test_browser_origin_must_match_dashboard_host_and_scheme(self) -> None:
        ws = SimpleNamespace(headers={"origin": "http://127.0.0.1:8000", "host": "127.0.0.1:8000"}, url=SimpleNamespace(scheme="ws"))
        self.assertTrue(same_origin_websocket(ws))
        ws.headers.update({"origin": "http://127.0.0.1", "host": "127.0.0.1:80"})
        self.assertTrue(same_origin_websocket(ws))
        ws.headers["origin"] = "http://evil.example"
        self.assertFalse(same_origin_websocket(ws))
        ws.headers["origin"] = "https://127.0.0.1:8000"
        self.assertFalse(same_origin_websocket(ws))

    def test_preview_consumer_limit_is_bounded(self) -> None:
        async def check() -> None:
            slots = PreviewMseSlots(maximum=1)
            self.assertTrue(await slots.acquire())
            self.assertFalse(await slots.acquire())
            await slots.release()
            self.assertTrue(await slots.acquire())
            await slots.release()
        asyncio.run(check())


class _FakeWebSocket:
    def __init__(self, request: dict | None = None) -> None:
        self.headers = {"origin": "http://127.0.0.1:8000", "host": "127.0.0.1:8000"}
        self.url = SimpleNamespace(scheme="ws")
        self.cookies = {"meowkeeper_session": "valid-session"}
        self.request = request or {"type": "mse", "value": "hvc1.1.6.L153.B0"}
        self.accepted = False
        self.closed: tuple[int, str] | None = None
        self.texts: list[str] = []
        self.binary: list[bytes] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def receive(self) -> dict:
        if self.request is not None:
            request, self.request = self.request, None
            return {"type": "websocket.receive", "text": json.dumps(request)}
        await asyncio.Event().wait()
        return {"type": "websocket.disconnect"}

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.binary.append(data)


class PreviewMseProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_proxy_pins_stream_and_forwards_mse_bytes_only(self) -> None:
        socket = _FakeWebSocket()
        socket.client = SimpleNamespace(host="100.100.1.2")
        core = SimpleNamespace(preview_status=lambda: {
            "source_is_xiaomi": True, "source_status": "running", "frame_age_seconds": 1.2,
            "source_connections": 1, "stream_name": "xiaomi_camera_3",
        })
        sessions = SimpleNamespace(authenticate=lambda cookie: SimpleNamespace(session_id="hashed-for-test") if cookie == "valid-session" else None)
        upstream = _FakeUpstream()
        tracker = LiveViewerTracker()
        watched_during_delivery: list[int] = []
        original_send_bytes = socket.send_bytes

        async def capture_delivery(data: bytes) -> None:
            watched_during_delivery.append(tracker.snapshot([{
                "device_ref": "ts_0123456789abcdef", "tailnet_ips": ["100.100.1.2"],
            }])["live_viewer_connection_count"])
            await original_send_bytes(data)

        socket.send_bytes = capture_delivery
        called: dict[str, object] = {}

        async def fake_connect(uri: str, **kwargs):
            called["uri"] = uri
            called["kwargs"] = kwargs
            return upstream

        with patch("websockets.asyncio.client.connect", side_effect=fake_connect):
            await proxy_mse_preview(socket, core=core, sessions=sessions,
                                    go2rtc=SimpleNamespace(
                                        api_url="http://127.0.0.1:1984",
                                        status=lambda: self.fail("MSE proxy must not trigger go2rtc lifecycle status checks"),
                                    ),
                                    slots=PreviewMseSlots(), viewer_tracker=tracker)

        self.assertTrue(socket.accepted)
        self.assertEqual(called["uri"], "ws://127.0.0.1:1984/api/ws?src=xiaomi_camera_3")
        self.assertIs(called["kwargs"]["proxy"], None)
        self.assertEqual(upstream.sent, [{"type": "mse", "value": "hvc1.1.6.L153.B0"}])
        self.assertEqual(json.loads(socket.texts[0]), {"type": "mse", "value": 'video/mp4; codecs="hvc1.1.6.L153.B0"'})
        self.assertEqual(socket.binary, [b"fragment"])
        self.assertEqual(watched_during_delivery, [1])
        self.assertEqual(tracker.snapshot()["live_viewer_connection_count"], 0)
        self.assertEqual(upstream.close_count, 1)
        self.assertEqual(socket.closed[0], 1000)

    async def test_missing_session_and_cross_origin_are_rejected_before_accept(self) -> None:
        for cookie, origin in ((None, "http://127.0.0.1:8000"), ("valid-session", "http://evil.example")):
            socket = _FakeWebSocket()
            if cookie is None:
                socket.cookies = {}
            else:
                socket.headers["origin"] = origin
            await proxy_mse_preview(
                socket,
                core=SimpleNamespace(preview_status=dict),
                sessions=SimpleNamespace(authenticate=lambda value: SimpleNamespace() if value == "valid-session" else None),
                go2rtc=SimpleNamespace(api_url="http://127.0.0.1:1984", status=lambda: SimpleNamespace(healthy=True)),
                slots=PreviewMseSlots(),
            )
            self.assertFalse(socket.accepted)
            self.assertEqual(socket.closed[0], 4401 if cookie is None else 1008)

    def test_fastapi_websocket_route_requires_session_before_proxying(self) -> None:
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        from meowkeeper.app.web.api import create_app
        from meowkeeper.app.web.session import SessionManager
        from meowkeeper.app.web.state import CoreStateFacade

        sessions = SessionManager()
        sessions.configure_admin("admin", "strong-password-123")
        integration = SimpleNamespace(go2rtc=SimpleNamespace(
            api_url="http://127.0.0.1:1984", status=lambda: SimpleNamespace(healthy=True),
        ))
        facade = CoreStateFacade()
        facade.preview_status = Mock(return_value={})
        app = create_app(facade=facade, session_manager=sessions, integrations=integration)
        with TestClient(app) as client, self.assertRaises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/api/v1/preview/mse", headers={"Origin": "http://testserver"}):
                pass
            self.assertEqual(caught.exception.code, 4401)
            facade.preview_status.assert_not_called()

    def test_authenticated_fastapi_route_negotiates_and_delivers_media(self) -> None:
        from fastapi.testclient import TestClient

        from meowkeeper.app.web.api import create_app
        from meowkeeper.app.web.session import SessionManager
        from meowkeeper.app.web.state import CoreStateFacade

        sessions = SessionManager()
        sessions.configure_admin("preview-tester", "test-preview-password")
        facade = CoreStateFacade()
        facade.preview_status = Mock(return_value={
            "source_is_xiaomi": True, "source_status": "running", "frame_age_seconds": 0.2,
            "source_connections": 1, "stream_name": "xiaomi_camera_test",
        })
        integration = SimpleNamespace(go2rtc=SimpleNamespace(api_url="http://127.0.0.1:1984"))
        upstream = _FakeUpstream()

        async def connect(*_args, **_kwargs):
            return upstream

        app = create_app(facade=facade, session_manager=sessions, integrations=integration)
        with TestClient(app) as client, patch("websockets.asyncio.client.connect", side_effect=connect):
            response = client.post("/api/v1/auth/login", json={
                "username": "preview-tester", "password": "test-preview-password",
            })
            self.assertEqual(response.status_code, 200)
            with client.websocket_connect("/api/v1/preview/mse", headers={"Origin": "http://testserver"}) as ws:
                ws.send_json({"type": "mse", "value": "hvc1.1.6.L153.B0"})
                self.assertEqual(ws.receive_json()["type"], "mse")
                self.assertEqual(ws.receive_bytes(), b"fragment")
        self.assertEqual(upstream.close_count, 1)


class _FakeUpstream:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.messages: list[str | bytes] = [
            json.dumps({"type": "mse", "value": 'video/mp4; codecs="hvc1.1.6.L153.B0"'}),
            b"fragment",
        ]
        self.close_count = 0

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str | bytes:
        if self.messages:
            return self.messages.pop(0)
        raise RuntimeError("test stream finished")

    async def close(self) -> None:
        self.close_count += 1


if __name__ == "__main__":
    unittest.main()
