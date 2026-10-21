from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.integrations.go2rtc.client import Go2RtcClient
from meowkeeper.integrations.xiaomi.models import XiaomiQualityProfile
from meowkeeper.integrations.xiaomi.setup_service import XiaomiSetupService


class FakePtzClient:
    def __init__(self) -> None:
        self.state_calls: list[tuple[str, bool]] = []
        self.action_calls: list[tuple[str, str, dict[str, object]]] = []

    def xiaomi_ptz_state(self, stream_name: str, *, refresh: bool = False) -> dict[str, object]:
        self.state_calls.append((stream_name, refresh))
        return {
            "connected": True,
            "position": {"angle": 50, "elevation": 50, "ret": 0},
            # The r107 fork advertises implementation capabilities as if each
            # camera firmware had been physically verified. The service must
            # gate absolute movement by real model acceptance evidence.
            "capabilities": {"relative_move": True, "absolute_move": True},
            "secret": "must not be returned",
        }

    def xiaomi_ptz_action(self, stream_name: str, action: str, **kwargs: object) -> dict[str, object]:
        self.action_calls.append((stream_name, action, kwargs))
        return {"connected": True, "position": {"angle": 51, "elevation": 50}}


class DisconnectedPtzClient(FakePtzClient):
    def xiaomi_ptz_action(self, stream_name: str, action: str, **kwargs: object) -> dict[str, object]:
        self.action_calls.append((stream_name, action, kwargs))
        return {"connected": False}


class XiaomiMissPtzTests(unittest.TestCase):
    def test_client_sends_only_allowlisted_local_ptz_payload(self) -> None:
        client = Go2RtcClient()
        with patch.object(Go2RtcClient, "request_json", return_value={"connected": True}) as request:
            result = client.xiaomi_ptz_action("xiaomi_camera_1", "move", direction="left")
        self.assertEqual(result, {"connected": True})
        request.assert_called_once()
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["params"], {"src": "xiaomi_camera_1"})
        self.assertEqual(json.loads(kwargs["data"].decode("utf-8")), {"action": "move", "direction": "left"})
        self.assertEqual(kwargs["content_type"], "application/json")
        self.assertEqual(kwargs["timeout_seconds"], 8.0)
        self.assertNotIn("xiaomi://", kwargs["data"].decode("utf-8"))

    def test_client_rejects_invalid_direction_and_position(self) -> None:
        client = Go2RtcClient()
        with self.assertRaises(ValueError):
            client.xiaomi_ptz_action("xiaomi_camera_1", "move", direction="diagonal")
        with self.assertRaises(ValueError):
            client.xiaomi_ptz_action("xiaomi_camera_1", "position", angle=0, elevation=50)

    def test_service_defaults_to_disabled_without_camera_operation(self) -> None:
        ptz = FakePtzClient()
        with tempfile.TemporaryDirectory(prefix="meowkeeper-ptz-disabled-") as directory:
            service = XiaomiSetupService(directory, ptz_client=ptz)
            result = service.ptz_action("unknown", action="move", direction="left")
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(ptz.action_calls, [])

    def test_service_requires_active_stream_and_returns_safe_state(self) -> None:
        ptz = FakePtzClient()
        source_manager = SimpleNamespace(client=ptz)
        discovery = lambda: [{
            "device_id": "opaque-device",
            "source_url": "xiaomi://private-source",
            "source_reference_present": True,
            "display_name": "測試攝影機",
            "model": "chuangmi.camera.026c02",
            "region": "sg",
        }]
        probe = lambda _device, _names: [XiaomiQualityProfile("subtype=sd", 640, 360, 20, "hevc", None, True)]
        with tempfile.TemporaryDirectory(prefix="meowkeeper-ptz-enabled-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=discovery,
                profile_probe=probe,
                source_manager=source_manager,
                source_switch=lambda _url, _device, _profile: {"status": "connected"},
                experimental_ptz_enabled=True,
            )
            service.refresh()
            device_id = service.devices()[0]["internal_id"]
            before = service.ptz_status(device_id)
            self.assertEqual(before["status"], "not_ready")
            service.probe_device(device_id)
            service.activate(device_id)
            state = service.ptz_status(device_id, refresh=True)
            moved = service.ptz_action(device_id, action="move", direction="left")
            self.assertEqual(state["status"], "available")
            self.assertTrue(state["success"])
            self.assertTrue(state["position_readback_verified"])
            self.assertFalse(state["state"]["capabilities"]["absolute_move"])
            absolute = service.ptz_action(device_id, action="position", angle=51, elevation=50)
            self.assertEqual(absolute["status"], "unsupported")
            self.assertFalse(absolute["success"])
            self.assertFalse(any(action == "position" for _, action, _ in ptz.action_calls))
            with patch.object(ptz, "xiaomi_ptz_state", return_value={
                "connected": True, "position": {"angle": 48, "elevation": 0, "ret": 0},
            }):
                self.assertTrue(service.ptz_status(device_id, refresh=True)["position_readback_verified"])
            with patch.object(ptz, "xiaomi_ptz_state", return_value={"connected": True}):
                missing = service.ptz_status(device_id, refresh=True)
                self.assertFalse(missing["success"])
                self.assertFalse(missing["position_readback_verified"])
            with patch.object(ptz, "xiaomi_ptz_state", return_value={
                "connected": True, "position": {"angle": 50, "elevation": 50, "ret": -5},
            }):
                self.assertFalse(service.ptz_status(device_id, refresh=True)["success"])
            self.assertNotIn("secret", json.dumps(state, ensure_ascii=False))
            with patch.object(ptz, "xiaomi_ptz_state", side_effect=RuntimeError("HTTP 503: xiaomi producer is not active")):
                unavailable = service.ptz_status(device_id, refresh=True)
                self.assertIn("尚未活動", unavailable["message_zh_tw"])
                self.assertFalse(unavailable["success"])
            self.assertEqual(moved["status"], "ok")
            self.assertTrue(moved["success"])
        self.assertIn(("xiaomi_camera_1", True), ptz.state_calls)
        self.assertIn(("xiaomi_camera_1", False), ptz.state_calls)
        self.assertEqual(ptz.action_calls[0][0:2], ("xiaomi_camera_1", "move"))
        self.assertEqual(ptz.action_calls[1][0:2], ("xiaomi_camera_1", "stop"))

    def test_service_reports_failure_when_ptz_endpoint_reports_disconnected(self) -> None:
        ptz = DisconnectedPtzClient()
        source_manager = SimpleNamespace(client=ptz)
        discovery = lambda: [{
            "device_id": "opaque-device",
            "source_url": "xiaomi://private-source",
            "source_reference_present": True,
            "display_name": "測試攝影機",
            "model": "chuangmi.camera.026c02",
            "region": "sg",
        }]
        probe = lambda _device, _names: [XiaomiQualityProfile("subtype=sd", 640, 360, 20, "hevc", None, True)]
        with tempfile.TemporaryDirectory(prefix="meowkeeper-ptz-failed-") as directory:
            service = XiaomiSetupService(
                directory,
                state_path=Path(directory) / "state.json",
                discovery=discovery,
                profile_probe=probe,
                source_manager=source_manager,
                source_switch=lambda _url, _device, _profile: {"status": "connected"},
                experimental_ptz_enabled=True,
            )
            service.refresh()
            device_id = service.devices()[0]["internal_id"]
            service.probe_device(device_id)
            service.activate(device_id)
            result = service.ptz_action(device_id, action="move", direction="left")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])

    def test_setup_omits_redundant_link_and_ptz_controls(self) -> None:
        template = (ROOT / "src" / "meowkeeper" / "app" / "web" / "templates" / "xiaomi_setup.html").read_text(encoding="utf-8")
        self.assertIn("連線並開始監控", template)
        self.assertIn('data-camera-role="provider"', template)
        self.assertNotIn('controlLink.href = "/live"', template)
        self.assertNotIn("讀取方向狀態", template)
        self.assertNotIn("/ptz", template)

    def test_live_template_exposes_protected_ptz_controls(self) -> None:
        template = (ROOT / "src" / "meowkeeper" / "app" / "web" / "templates" / "live.html").read_text(encoding="utf-8")
        self.assertIn("Xiaomi MISS PTZ 方向控制", template)
        self.assertIn("/api/v1/cameras/xiaomi/devices", template)
        self.assertIn("X-MeowKeeper-CSRF", template)
        self.assertIn('data-ptz-direction="left"', template)
        self.assertIn('data-ptz-direction="right"', template)
        self.assertIn('data-ptz-action="stop"', template)
        self.assertIn('id="preview-ptz-directions"', template)
        self.assertIn('document.addEventListener("keydown"', template)
        self.assertIn('document.addEventListener("keyup"', template)
        self.assertIn('event.repeat', template)
        self.assertIn('input, textarea, select, [contenteditable]', template)
        self.assertIn('subtype=sd', template)
        self.assertIn('subtype=hd', template)
        self.assertIn('/ptz?refresh=true', template)
        self.assertIn('讀取目前位置', template)
        self.assertIn('position_readback_verified === true', template)
        self.assertIn('tabindex="-1" role="img"', template)
        self.assertIn("map.setAttribute('tabindex',enabled?'0':'-1')", template)
        self.assertIn("此型號尚未實機驗證絕對定位", template)
        self.assertIn('id="privacy-diagnostics-link"', template)
        self.assertIn('diagnosticLink.hidden = response.ok && data.success === true', template)
        self.assertIn("preview-metadata", template)
        self.assertIn("/api/v1/preview/status", template)
        self.assertIn("subtype=3", template)
        self.assertIn('id="live-quality-panel"', template)
        self.assertIn('目前沒有已接入的 Xiaomi 來源', template)
        self.assertIn('source_is_xiaomi', template)
        self.assertIn('RTSP 已接通', template)
        self.assertIn('qualityProfile.disabled = true', template)
        self.assertIn("/api/v1/cameras/xiaomi/devices", template)


if __name__ == "__main__":
    unittest.main()
